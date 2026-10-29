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

    def test_generation_feedback_is_preserved(self):
        self.set_response({
            "passed": False, "summary": "Subject missing",
            "issues": [{"shot_id": "s1", "category": "generation", "message": "0.125s: subject missing",
                        "suggested_prompt": "A clearly visible subject in a teal landscape"}],
        })
        result = self.reviewer.review(
            self.video, shots=[self.shot], scope="shot", output_dir=self.output
        )
        self.assertFalse(result.passed)
        self.assertEqual(result.issues[0].shot_id, "s1")
        self.assertIn("clearly visible", result.issues[0].suggested_prompt)

    def test_whole_film_edit_issue_may_omit_shot_id(self):
        self.set_response({"passed": False, "summary": "Edit problem", "issues": [{
            "category": "editing", "message": "The overall shot ordering disagrees with the script",
        }]})
        result = self.reviewer.review(
            self.video, shots=[self.shot], scope="film", output_dir=self.output
        )
        self.assertIsNone(result.issues[0].shot_id)

    def test_invalid_model_decisions_are_errors_not_passes(self):
        cases = [
            {"passed": False, "summary": "Bad", "issues": []},
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
                with self.assertRaises(ValueError):
                    self.reviewer.review(self.video, shots=[self.shot], scope="shot", output_dir=directory)
                self.assertTrue((directory / "error.txt").is_file())
                self.assertFalse((directory / "result.json").exists())

    def test_truncation_and_non_json_are_errors(self):
        for index, finish_reason in enumerate(["length", "stop"]):
            self.server.response = {"choices": [{
                "message": {"content": "Not JSON"}, "finish_reason": finish_reason,
            }]}
            with self.subTest(finish_reason=finish_reason), self.assertRaises(ValueError):
                self.reviewer.review(
                    self.video, shots=[self.shot], scope="shot", output_dir=self.output / str(index)
                )

    def test_http_failure_preserves_response(self):
        self.server.status = 503
        self.server.response = {"error": "Model unavailable"}
        import httpx

        with self.assertRaises(httpx.HTTPStatusError):
            self.reviewer.review(self.video, shots=[self.shot], scope="shot", output_dir=self.output)
        self.assertIn("Model unavailable", (self.output / "response.txt").read_text())
        self.assertFalse((self.output / "result.json").exists())

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
        self.assertIn("Semantic quality and motion were not assessed", second.summary)
        self.assertFalse(film.passed)

    def test_cpu_does_not_pass_an_undecodable_video(self):
        corrupt = self.root / "corrupt.mp4"
        corrupt.write_bytes(b"not a video")
        with self.assertRaises((ValueError, RuntimeError, OSError)):
            CPUReviewer(ReviewConfig()).review(
                corrupt, shots=[self.shot], scope="shot", output_dir=self.output
            )
        self.assertFalse((self.output / "result.json").exists())


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


if __name__ == "__main__":
    unittest.main()
