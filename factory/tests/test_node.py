import json
import shutil
import subprocess

import httpx
import pytest

from drama_factory.node import backends, probe, telemetry

FFMPEG = shutil.which("ffmpeg") and shutil.which("ffprobe")
needs_ffmpeg = pytest.mark.skipif(not FFMPEG, reason="ffmpeg/ffprobe not installed")

FILTER_LOG = """
[blackdetect @ 0x1] black_start:0 black_end:0.5 black_duration:0.5
[freezedetect @ 0x2] lavfi.freezedetect.freeze_start: 2.25
[freezedetect @ 0x2] lavfi.freezedetect.freeze_duration: 1.5
[freezedetect @ 0x2] lavfi.freezedetect.freeze_end: 3.75
[freezedetect @ 0x2] lavfi.freezedetect.freeze_start: 4.5
[silencedetect @ 0x3] silence_start: 0
[silencedetect @ 0x3] silence_end: 1.2 | silence_duration: 1.2
[Parsed_volumedetect_0 @ 0x4] mean_volume: -21.4 dB
[Parsed_volumedetect_0 @ 0x4] max_volume: -0.0 dB
"""


def test_parse_filter_log():
    result = probe.parse_filters(FILTER_LOG, duration=5.0)
    assert result["black_s"] == 0.5 and result["black_ranges"] == [[0.0, 0.5]]
    assert result["freeze_ranges"] == [[2.25, 3.75], [4.5, 5.0]]  # trailing freeze runs to end of file
    assert result["longest_freeze_s"] == 1.5 and result["silence_s"] == 1.2
    assert result["max_volume_db"] == 0.0 and result["mean_volume_db"] == -21.4


def test_parse_ffprobe():
    data = {"streams": [{"codec_type": "video", "width": 768, "height": 1344, "avg_frame_rate": "24/1",
                         "codec_name": "h264"}, {"codec_type": "audio", "codec_name": "aac"}],
            "format": {"duration": "5.166667"}}
    assert probe.parse_ffprobe(data) == {"decodable": True, "width": 768, "height": 1344, "fps": 24.0,
                                         "duration_s": 5.167, "codec": "h264", "has_audio": True,
                                         "audio_codec": "aac"}
    assert not probe.parse_ffprobe({"streams": []})["decodable"]


@needs_ffmpeg
def test_real_probe_on_rendered_clip(tmp_path):
    out = tmp_path / "clip.mp4"
    payload = {"width": 384, "height": 672, "frames": 73, "fps": 24, "seed": 1}
    backends.SimulatedGenerator().generate(payload, {}, out)
    result = probe.probe_video(out)
    assert result["decodable"] and (result["width"], result["height"], result["fps"]) == (384, 672, 24.0)
    assert abs(result["duration_s"] - 73 / 24) < 0.1 and result["has_audio"]
    assert result["black_s"] == 0 and result["longest_freeze_s"] == 0
    (tmp_path / "broken.mp4").write_bytes(out.read_bytes()[: out.stat().st_size // 3])
    assert not probe.probe_video(tmp_path / "broken.mp4")["decodable"]


def test_http_video_backend_submit_poll_download(tmp_path):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        if request.method == "POST":
            body = json.loads(request.content)
            assert body["num_frames"] == 124 and body["size"] == "768x1344" and body["image"].endswith("first.png")
            assert body["references"][0]["token"] == "<Picture 1>"
            return httpx.Response(200, json={"id": "job1", "status": "queued"})
        if request.url.path == "/v1/videos/job1":
            return httpx.Response(200, json={"id": "job1", "status": "completed", "model": "vdn-h3@sha"})
        return httpx.Response(200, content=b"MP4DATA")

    client = httpx.Client(base_url="http://engine", transport=httpx.MockTransport(handler))
    backend = backends.HttpVideoBackend("http://engine", poll_s=0, client=client)
    payload = {"prompt": "p", "width": 768, "height": 1344, "frames": 124, "fps": 24, "seed": 7,
               "references": [{"kind": "image", "token": "<Picture 1>", "key": "k"}]}
    inputs = {"first_frame": tmp_path / "first.png", "<Picture 1>": tmp_path / "ref.png"}
    info = backend.generate(payload, inputs, tmp_path / "out.mp4")
    assert info["model_version"] == "vdn-h3@sha" and (tmp_path / "out.mp4").read_bytes() == b"MP4DATA"
    assert calls == [("POST", "/v1/videos"), ("GET", "/v1/videos/job1"), ("GET", "/v1/videos/job1/content")]


def test_http_video_backend_classifies_failures(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="CUDA error: out of memory")

    client = httpx.Client(base_url="http://engine", transport=httpx.MockTransport(handler))
    backend = backends.HttpVideoBackend("http://engine", client=client)
    with pytest.raises(backends.BackendError) as caught:
        backend.generate({"prompt": "p", "width": 8, "height": 8, "frames": 22, "fps": 24, "seed": 1}, {},
                         tmp_path / "x.mp4")
    assert caught.value.code == "OOM" and caught.value.retryable


@needs_ffmpeg
def test_vlm_reviewer_sends_frames_and_parses_findings(tmp_path):
    clip = tmp_path / "clip.mp4"
    backends.SimulatedGenerator().generate({"width": 256, "height": 448, "frames": 73, "fps": 24, "seed": 1}, {}, clip)
    ref = tmp_path / "ref.png"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=c=red:s=32x32", "-frames:v", "1", str(ref)],
                   check=True)
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen["images"] = sum(part["type"] == "image_url" for part in body["messages"][1]["content"])
        seen["schema"] = body["response_format"]["json_schema"]["name"]
        answer = {"verdict": "fail", "confidence": 0.8, "score": 0.3, "findings": [
            {"code": "IDENTITY_DRIFT", "severity": "blocker", "t_start": 1.0, "t_end": 2.0, "message": "脸变了"}]}
        return httpx.Response(200, json={"choices": [{"message": {"content": "```json\n" + json.dumps(answer) + "\n```"}}]})

    reviewer = backends.VlmReviewer("http://vlm", "qwen", fps=1.0, max_frames=4, height=128,
                                    client=httpx.Client(base_url="http://vlm", transport=httpx.MockTransport(handler)))
    payload = {"duration_s": 3.04, "shot": {"shot_id": "S1", "importance": "normal", "motion": "low", "characters": [],
                                            "scene": "", "description": "d", "action": "", "camera": "",
                                            "constraints": [], "dialogue": []}}
    report = reviewer.review(payload, clip, [{"kind": "image", "role": "face_front", "owner": "lin", "path": ref}])
    assert report["verdict"] == "fail" and report["findings"][0]["code"] == "IDENTITY_DRIFT"
    assert seen["schema"] == "shot_review" and seen["images"] >= 4  # reference + sampled + final frame


def test_claim_is_not_retried_after_a_read_timeout(monkeypatch):
    from drama_factory.node.agent import ControlClient
    monkeypatch.setattr("drama_factory.node.agent.time.sleep", lambda _s: None)
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if len(calls) == 1:
            raise httpx.ReadTimeout("response lost", request=request)
        return httpx.Response(200, json={"ok": True, "jobs": []})

    client = ControlClient("http://control", "t", client=httpx.Client(base_url="http://control",
                                                                       transport=httpx.MockTransport(handler)))
    with pytest.raises(httpx.ReadTimeout):  # the server may already have leased a job to us
        client.claim("n", "w", ["gen.vdn"])
    assert calls == ["/v1/jobs/claim"]
    assert client.job_heartbeat("j", "t")["ok"]  # idempotent calls do retry
    assert calls == ["/v1/jobs/claim", "/v1/jobs/j/heartbeat"]


def test_no_upload_after_the_lease_is_lost(tmp_path):
    from drama_factory.node.agent import Lease, generation_handler
    from drama_factory.storage import FileStore
    store = FileStore(tmp_path / "store")
    handle = generation_handler(backends.SimulatedGenerator(render=False), store, tmp_path / "work", 1.0,
                                backends.stub_probe)
    payload = {"width": 8, "height": 8, "frames": 22, "fps": 24, "seed": 1, "output_key": "takes/x/S1/r01c0-ab-a1.mp4"}
    lease = Lease("j", "t", confirm=lambda: {"ok": False, "cancelled": True})
    with pytest.raises(backends.BackendError) as caught:
        handle({"payload": payload}, lease)
    assert caught.value.code == "CANCELLED" and not store.exists(payload["output_key"])
    alive = Lease("j", "t", confirm=lambda: {"ok": True, "cancelled": False})
    assert handle({"payload": payload}, alive)["output_key"] == payload["output_key"]
    assert store.exists(payload["output_key"])


def test_char_error_rate_handles_mixed_scripts():
    assert backends.char_error_rate("原来你爱的是额度。", "原来你爱的是额度") == 0
    assert backends.char_error_rate("My card is frozen.", "my card is frozen") == 0
    assert round(backends.char_error_rate("原来你爱的是额度。", "原来你爱的是"), 2) == 0.25


def test_parse_nvidia_smi():
    csv = ("0, GPU-aaa, NVIDIA B200, 63, 97, 151000, 183359, 912.4, 0\n"
           "7, GPU-bbb, NVIDIA B200, 88, [N/A], 1000, 183359, [N/A], 2\n")
    gpus = telemetry.parse_gpus(csv)
    assert gpus[0]["temperature_c"] == 63 and gpus[0]["power_w"] == 912.4
    assert gpus[1]["utilization_pct"] is None and gpus[1]["ecc_uncorrected"] == 2
