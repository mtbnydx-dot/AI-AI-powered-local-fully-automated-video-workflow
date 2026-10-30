import base64
import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PIL import Image

from videopipe.backends.review import CPUReviewer, VLLMReviewer
from videopipe.media import run_ffmpeg
from videopipe.models import ReviewConfig, Shot


class ReviewHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        self.server.requests.append((
            self.path,
            json.loads(self.rfile.read(int(self.headers["Content-Length"]))),
        ))
        body = json.dumps(self.server.response).encode()
        self.send_response(self.server.status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class ReviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name)
        cls.reference = cls.root / "reference.png"
        Image.new("RGB", (160, 96), "teal").save(cls.reference)
        cls.video = cls.root / "video.mp4"
        run_ffmpeg([
            "-loop", "1", "-i", str(cls.reference), "-t", "0.5", "-r", "12",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-y", str(cls.video),
        ])
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), ReviewHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()
        cls.temp.cleanup()

    def setUp(self):
        self.server.requests = []
        self.server.status = 200
        self.set_response({"passed": True, "summary": "Visible requirements met", "issues": []})
        self.shot = Shot(
            id="s1", prompt="A teal landscape", negative_prompt="blur",
            reference_images=[self.reference], acceptance=["Keep the teal palette"],
            duration=0.5, width=160, height=96, fps=12,
        )
        self.config = ReviewConfig(
            backend="vllm", url=f"http://127.0.0.1:{self.server.server_port}/v1",
            sample_frames=2, max_frame_size=128,
        )
        self.reviewer = VLLMReviewer(self.config, script="A quiet landscape sequence.")
        self.output = self.root / self._testMethodName

    def set_response(self, content, finish_reason="stop"):
        self.server.response = {"choices": [{
            "message": {"content": json.dumps(content)}, "finish_reason": finish_reason,
        }]}

    def test_real_http_request_contains_decodable_images_and_structured_schema(self):
        result = self.reviewer.review(
            self.video, shots=[self.shot], scope="shot", output_dir=self.output
        )
        self.assertTrue(result.passed)
        path, payload = self.server.requests[0]
        self.assertEqual(path, "/v1/chat/completions")
        self.assertEqual(payload["model"], "qwen-review")
        self.assertIn("passed", payload["structured_outputs"]["json"]["properties"])
        self.assertFalse(payload["chat_template_kwargs"]["enable_thinking"])
        self.assertNotIn("suggested_prompt", json.dumps(payload["structured_outputs"]))
        self.assertIn("Never infer audio", payload["messages"][0]["content"])
        parts = payload["messages"][1]["content"]
        texts = "\n".join(part["text"] for part in parts if part["type"] == "text")
        self.assertIn("A quiet landscape sequence", texts)
        self.assertIn("Keep the teal palette", texts)
        self.assertIn("Reference 1 for shot s1", texts)
        self.assertIn("Video frame at 0.125s", texts)
        self.assertIn("Video frame at 0.375s", texts)
        images = [part["image_url"]["url"] for part in parts if part["type"] == "image_url"]
        self.assertEqual(len(images), 3)
        for encoded in images:
            prefix, data = encoded.split(",", 1)
            self.assertEqual(prefix, "data:image/jpeg;base64")
            with Image.open(io.BytesIO(base64.b64decode(data))) as image:
                image.load()
                self.assertLessEqual(max(image.size), 128)
        self.assertEqual(json.loads((self.output / "request.json").read_text()), payload)
        self.assertTrue((self.output / "response.txt").is_file())
        self.assertTrue(json.loads((self.output / "result.json").read_text())["passed"])

    def test_evidenced_visual_issue_is_preserved_without_rewriting_authority(self):
        self.set_response({
            "passed": False, "summary": "Subject missing",
            "issues": [{"shot_id": "s1", "category": "generation", "message": "Subject missing in the supplied frame",
                        "timestamps": [0.125]}],
        })
        result = self.reviewer.review(self.video, shots=[self.shot], scope="shot", output_dir=self.output)
        self.assertTrue(result.available)
        self.assertFalse(result.passed)
        self.assertEqual(result.issues[0].timestamps, [0.125])
        self.assertNotIn("suggested_prompt", result.issues[0].model_dump())

    def test_uncertain_or_unlocated_findings_are_warnings(self):
        decisions = [
            {"passed": False, "summary": "Cannot establish a defect", "issues": []},
            {"passed": False, "summary": "Possibly different", "issues": [{
                "shot_id": "s1", "category": "generation", "message": "Identity may differ but evidence is unclear"}]},
            {"passed": True, "summary": "Acceptable", "issues": [], "warnings": ["Minor cosmetic variation"]},
        ]
        for index, decision in enumerate(decisions):
            with self.subTest(decision=decision):
                self.set_response(decision)
                result = self.reviewer.review(self.video, shots=[self.shot], scope="shot", output_dir=self.output / str(index))
                self.assertTrue(result.available)
                self.assertTrue(result.passed)
                self.assertFalse(result.issues)
                self.assertTrue(result.warnings)

    def test_whole_film_review_is_not_sent_to_the_model(self):
        result = self.reviewer.review(self.video, shots=[self.shot], scope="film", output_dir=self.output)
        self.assertFalse(result.available)
        self.assertFalse(result.passed)
        self.assertFalse(self.server.requests)

    def test_invalid_model_decisions_are_errors_not_passes(self):
        cases = [
            {"passed": False, "summary": "Bad", "issues": [
                {"shot_id": "missing", "category": "generation", "message": "Defect"}]},
            {"passed": False, "summary": "Bad", "issues": [
                {"category": "generation", "message": "Defect"}]},
            {"passed": True, "summary": "Bad", "issues": [
                {"shot_id": "s1", "category": "generation", "message": "Defect"}]},
            {"passed": "true", "summary": "Bad", "issues": []},
        ]
        for index, decision in enumerate(cases):
            with self.subTest(decision=decision):
                self.set_response(decision)
                directory = self.output / str(index)
                result = self.reviewer.review(self.video, shots=[self.shot], scope="shot", output_dir=directory)
                self.assertFalse(result.available)
                self.assertFalse(result.passed)
                self.assertTrue((directory / "error.txt").is_file())
                self.assertFalse(json.loads((directory / "result.json").read_text())["available"])

    def test_truncation_and_non_json_are_errors(self):
        for index, finish_reason in enumerate(["length", "stop"]):
            self.server.response = {"choices": [{
                "message": {"content": "Not JSON"}, "finish_reason": finish_reason,
            }]}
            with self.subTest(finish_reason=finish_reason):
                result = self.reviewer.review(
                    self.video, shots=[self.shot], scope="shot", output_dir=self.output / str(index)
                )
                self.assertFalse(result.available)
                self.assertFalse(result.passed)

    def test_http_failure_preserves_response(self):
        self.server.status = 503
        self.server.response = {"error": "Model unavailable"}
        result = self.reviewer.review(self.video, shots=[self.shot], scope="shot", output_dir=self.output)
        self.assertFalse(result.available)
        self.assertFalse(result.passed)
        self.assertIn("Model unavailable", (self.output / "response.txt").read_text())
        self.assertTrue((self.output / "error.txt").is_file())
        self.assertFalse(json.loads((self.output / "result.json").read_text())["available"])

    def test_cpu_rejection_survives_reviewer_recreation(self):
        config = ReviewConfig(sample_frames=2, reject_once=["s1", "film"])
        first = CPUReviewer(config).review(
            self.video, shots=[self.shot], scope="shot", output_dir=self.output / "attempt-001" / "review"
        )
        second = CPUReviewer(config).review(
            self.video, shots=[self.shot], scope="shot", output_dir=self.output / "attempt-002" / "review"
        )
        film = CPUReviewer(config).review(
            self.video, shots=[self.shot], scope="film", output_dir=self.output / "film" / "review-001"
        )
        self.assertFalse(first.passed)
        self.assertTrue(second.passed)
        self.assertIn("Semantic quality and motion were not assessed.", second.warnings)
        self.assertFalse(film.passed)
        self.assertFalse(film.available)

    def test_cpu_does_not_pass_an_undecodable_video(self):
        corrupt = self.root / "corrupt.mp4"
        corrupt.write_bytes(b"not a video")
        with self.assertRaises((ValueError, RuntimeError, OSError)):
            CPUReviewer(ReviewConfig()).review(
                corrupt, shots=[self.shot], scope="shot", output_dir=self.output
            )
        self.assertFalse((self.output / "result.json").exists())

    def test_prompt_rewriting_fields_are_rejected_at_the_service_boundary(self):
        self.set_response({"passed": False, "summary": "Wrong subject", "issues": [{
            "shot_id": "s1", "category": "generation", "message": "Different subject", "timestamps": [0.125],
            "suggested_prompt": "Replace the original brief",
        }]})
        result = self.reviewer.review(self.video, shots=[self.shot], scope="shot", output_dir=self.output)
        self.assertFalse(result.available)
        self.assertEqual(self.shot.prompt, "A teal landscape")

    def test_one_reviewer_can_handle_concurrent_http_requests(self):
        def call(index):
            return self.reviewer.review(self.video, shots=[self.shot], scope="shot", output_dir=self.output / str(index))
        with ThreadPoolExecutor(max_workers=2) as workers:
            results = list(workers.map(call, range(2)))
        self.assertTrue(all(result.passed and result.available for result in results))
        self.assertEqual(len(self.server.requests), 2)
        for index in range(2):
            self.assertTrue((self.output / str(index) / "response.txt").is_file())

    def test_transcript_comparison_is_text_only_and_advisory(self):
        self.set_response({"matches": False, "summary": "One spoken line is missing", "differences": ["Missing: hello"]})
        result = self.reviewer.compare_transcript("Character says hello.", {
            "status": "complete", "segments": [{"start": 0.0, "end": 0.5, "text": "Goodbye"}],
        }, self.output)
        self.assertTrue(result["available"])
        self.assertFalse(result["matches"])
        self.assertNotIn("passed", result)
        self.assertEqual(result["differences"], ["Missing: hello"])
        self.assertNotIn("image_url", json.dumps(self.server.requests[0]))
        self.assertTrue((self.output / "result.json").is_file())

    def test_transcript_or_comparison_service_unavailable_does_not_raise(self):
        skipped = self.reviewer.compare_transcript("Script", {"status": "disabled"}, self.output / "disabled")
        self.assertFalse(skipped["available"])
        self.assertIsNone(skipped["matches"])
        self.assertFalse(self.server.requests)
        self.server.status = 503
        unavailable = self.reviewer.compare_transcript("Script", {"status": "complete", "segments": []}, self.output / "failed")
        self.assertFalse(unavailable["available"])
        self.assertIsNone(unavailable["matches"])
        self.assertTrue((self.output / "failed" / "error.txt").is_file())



class VLLMLaunchTests(unittest.TestCase):
    def test_launch_passes_local_model_gpu_and_offline_parameters(self):
        script = Path(__file__).resolve().parents[1] / "deploy" / "start-vllm.sh"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            model = root / "Qwen model"
            model.mkdir()
            executable = root / "vllm"
            executable.write_text(
                f"#!{sys.executable}\n"
                "import json, os, sys\n"
                "print(json.dumps({'argv': sys.argv[1:], 'gpu': os.environ['CUDA_VISIBLE_DEVICES'], "
                "'offline': os.environ['HF_HUB_OFFLINE'], 'stats': os.environ['VLLM_NO_USAGE_STATS']}))\n"
            )
            executable.chmod(0o755)
            environment = {
                **os.environ, "PATH": str(root) + os.pathsep + os.environ["PATH"],
                "QWEN_MODEL_PATH": str(model), "REVIEW_GPUS": "6,7", "REVIEW_TP": "2",
                "REVIEW_PORT": "8011", "REVIEW_MAX_IMAGES": "12",
            }
            result = subprocess.run(
                ["bash", str(script), "--dtype", "bfloat16"], env=environment,
                capture_output=True, text=True, check=True,
            )
            launch = json.loads(result.stdout)
            self.assertEqual(launch["gpu"], "6,7")
            self.assertEqual(launch["offline"], "1")
            self.assertEqual(launch["stats"], "1")
            args = launch["argv"]
            self.assertEqual(args[:2], ["serve", str(model)])
            self.assertEqual(args[args.index("--tensor-parallel-size") + 1], "2")
            self.assertEqual(args[args.index("--port") + 1], "8011")
            self.assertEqual(args[args.index("--served-model-name") + 1], "qwen-review")
            self.assertEqual(json.loads(args[args.index("--limit-mm-per-prompt") + 1]), {"image": 12, "video": 0})
            self.assertEqual(args[-2:], ["--dtype", "bfloat16"])
            self.assertEqual(args[args.index("--quantization") + 1], "modelopt")
            self.assertEqual(json.loads(args[args.index("--engram-config") + 1]), {"cpu_offload": True})
            self.assertEqual(args[args.index("--max-model-len") + 1], "16384")
            self.assertEqual(args[args.index("--gpu-memory-utilization") + 1], "0.80")
            self.assertEqual(args[args.index("--max-num-seqs") + 1], "2")
            self.assertNotIn("--speculative-config", args)
            # Defaults keep the shared review/ASR service on GPU7 with TP1.
            for name in ("REVIEW_GPUS", "REVIEW_TP", "REVIEW_CONTEXT", "REVIEW_MAX_SEQS", "REVIEW_GPU_MEMORY"):
                environment.pop(name, None)
            result = subprocess.run(["bash", str(script)], env=environment, capture_output=True, text=True, check=True)
            default_launch = json.loads(result.stdout)
            self.assertEqual(default_launch["gpu"], "7")
            self.assertEqual(default_launch["argv"][default_launch["argv"].index("--tensor-parallel-size") + 1], "1")


if __name__ == "__main__":
    unittest.main()
