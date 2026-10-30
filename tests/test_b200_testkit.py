import importlib.util
import json
import shutil
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from videopipe.media import MediaInfo, assemble, inspect_video, run_ffmpeg
from videopipe.models import MediaConfig
from videopipe.records import Events, write_json


spec = importlib.util.spec_from_file_location("b200_runner", Path(__file__).resolve().parents[1] / "scripts/run_b200_tests.py")
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


class B200TestkitTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for name in runner.CASES:
            run = self.root / "runs" / name
            run.mkdir(parents=True)
            (run / "final.mp4").write_bytes(b"test video placeholder")
            write_json(run / "state.json", {"status": "complete", "final": "final.mp4", "shots": {}})

    def test_wrong_metadata_or_decode_failure_never_delivers_video(self):
        (self.root / "delivery").mkdir()
        (self.root / "delivery/tide-40s.mp4").write_bytes(b"stale previous export")
        with patch.object(runner, "inspect_video", return_value=MediaInfo(40, 320, 240, 24, False)):
            report = runner.collect(self.root)
        self.assertEqual(report["status"], "incomplete")
        self.assertFalse(list((self.root / "delivery").glob("*.mp4")))
        with patch.object(runner, "inspect_video", side_effect=[MediaInfo(40, 576, 1024, 24, True),
                                                              MediaInfo(320, 576, 1024, 24, True)]), \
             patch.object(runner, "run_ffmpeg", side_effect=RuntimeError("corrupt video")):
            report = runner.collect(self.root)
        self.assertFalse(any(row["verified"] for row in report["cases"].values()))
        self.assertFalse(list((self.root / "delivery").glob("*.mp4")))

    def test_collect_includes_service_logs_and_preserves_failure_status(self):
        write_json(self.root / "runs/smoke/state.json", {"status": "needs_attention"})
        services = self.root / "services"
        services.mkdir()
        (services / "vllm.log").write_text("model load log")
        with patch.object(runner, "inspect_video", return_value=MediaInfo(320, 576, 1024, 24, True)), \
             patch.object(runner, "run_ffmpeg"):
            report = runner.collect(self.root)
        self.assertEqual(report["status"], "incomplete")
        self.assertFalse(report["cases"]["smoke"]["verified"])
        self.assertTrue(report["cases"]["full"]["verified"])
        self.assertEqual(report["cases"]["full"]["planned_seconds"], 320)
        self.assertEqual(report["cases"]["full"]["expected_seconds"], 320)
        with tarfile.open(self.root / "delivery/logs.tar.gz") as archive:
            names = archive.getnames()
        self.assertIn("services/vllm.log", names)
        self.assertFalse(any(name.endswith(".mp4") for name in names))

    def test_real_delivery_uses_measured_manifest_duration_and_reports_fallback(self):
        clip = self.root / "clip.mp4"
        run_ffmpeg([
            "-loglevel", "error", "-f", "lavfi", "-i", "color=c=navy:s=576x1024:r=24:d=0.5",
            "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=0.5",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-shortest", str(clip),
        ])
        measured = inspect_video(clip).duration
        final = assemble([clip, clip], self.root / "assembled.mp4", MediaConfig(width=576, height=1024, fps=24))
        manifest = [{"shot_id": "shot-1", "duration": measured},
                    {"shot_id": "shot-2", "duration": measured}]
        for name in runner.CASES:
            run = self.root / "runs" / name
            shutil.copy2(final, run / "final.mp4")
            write_json(run / "film/version-001/manifest.json", manifest)
            write_json(run / "state.json", {
                "status": "complete", "final": "final.mp4", "manifest": "film/version-001/manifest.json",
                "shots": {"shot-1": {"attempts": 1, "selection": {"reason": "review_pass"}},
                          "shot-2": {"attempts": 1, "selection": {"reason": "review_unavailable"}}},
            })

        report = runner.collect(self.root)
        self.assertEqual(report["status"], "complete")
        for name, (_, _, planned) in runner.CASES.items():
            row = report["cases"][name]
            self.assertTrue(row["verified"])
            self.assertEqual(row["planned_seconds"], planned)
            self.assertEqual(row["expected_seconds"], 2 * measured)
            self.assertEqual(row["visual_pass_count"], 1)
            self.assertEqual(row["fallback_shots"], ["shot-2"])
            self.assertTrue(row["media"]["has_audio"])
            self.assertTrue(Path(row["video"]).is_file())
        self.assertIn("技术媒体验证通过不等于画面审核通过", (self.root / "delivery/summary.md").read_text())

        # A measured manifest still constrains the actual delivery; it does not disable duration validation.
        write_json(self.root / "runs/smoke/film/version-001/manifest.json", [{"shot_id": "shot-1", "duration": 2.0}])
        report = runner.collect(self.root)
        self.assertFalse(report["cases"]["smoke"]["verified"])
        self.assertTrue(report["cases"]["full"]["verified"])
        self.assertFalse((self.root / "delivery/tide-40s.mp4").exists())

    def test_failed_stage_records_error_and_reraises(self):
        events = Events(self.root)
        try:
            with self.assertRaisesRegex(RuntimeError, "GPU failure"):
                with events.stage("generate", shot="shot-1", attempt=1):
                    raise RuntimeError("GPU failure")
        finally:
            events.close()
        records = [json.loads(line) for line in (self.root / "events.jsonl").read_text().splitlines()]
        self.assertEqual(records[-1]["status"], "failed")
        self.assertEqual(records[-1]["error"], "GPU failure")
        self.assertGreaterEqual(records[-1]["elapsed"], 0)


if __name__ == "__main__":
    unittest.main()
