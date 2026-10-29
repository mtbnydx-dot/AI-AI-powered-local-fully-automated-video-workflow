import json
import tempfile
import threading
import unittest
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
from PIL import Image

from tests.test_comfyui import ComfyHandler
from videopipe.media import inspect_video, run_ffmpeg
from videopipe.models import ComfyConfig, MediaConfig, PipelineConfig, ReviewConfig, ScriptPackage, Shot
from videopipe.pipeline import Production


class SequencedReviewHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.server.requests.append(request)
        status, decision = self.server.responses.popleft()
        response = {"error": decision} if status != 200 else {
            "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(decision)}}],
        }
        body = json.dumps(response).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


PASS = (200, {"passed": True, "summary": "Visible requirements met", "issues": []})


class PipelineHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.assets = tempfile.TemporaryDirectory()
        directory = Path(cls.assets.name)
        cls.reference = directory / "reference.png"
        Image.new("RGB", (96, 64), "teal").save(cls.reference)
        video = directory / "source.mp4"
        run_ffmpeg([
            "-loop", "1", "-i", str(cls.reference), "-t", "0.5", "-r", "24",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-y", str(video),
        ])
        cls.video = video.read_bytes()
        cls.script = directory / "script.md"
        cls.script.write_text("Two views of a teal landscape.\n", encoding="utf-8")
        cls.workflow = directory / "workflow.json"
        cls.workflow.write_text(json.dumps({
            "conditioning": {"class_type": "ContractTestConditioning", "inputs": {
                "prompt": "{{prompt}}", "seed": "{{seed}}", "image": "{{reference_image}}",
            }},
        }), encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        cls.assets.cleanup()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.run_dir = Path(self.temp.name) / "run"
        self.comfy = ThreadingHTTPServer(("127.0.0.1", 0), ComfyHandler)
        self.comfy.mode = "success"
        self.comfy.video = self.video
        self.comfy.uploads = []
        self.comfy.upload_types = []
        self.comfy.submissions = []
        self.comfy.downloads = []
        self.comfy.polls = 0
        self.review = ThreadingHTTPServer(("127.0.0.1", 0), SequencedReviewHandler)
        self.review.requests = []
        self.review.responses = deque()
        self.threads = []
        for server in (self.comfy, self.review):
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            self.threads.append(thread)
        self.config = PipelineConfig(
            generator="comfyui",
            comfyui=ComfyConfig(
                urls=[f"http://127.0.0.1:{self.comfy.server_port}"],
                workflow=self.workflow, poll_interval=0.01, timeout=5,
            ),
            review=ReviewConfig(
                backend="vllm", url=f"http://127.0.0.1:{self.review.server_port}/v1",
                sample_frames=2, max_frame_size=96,
            ),
            media=MediaConfig(width=96, height=64, fps=24),
            human_approval=False,
        )

    def tearDown(self):
        for server in (self.comfy, self.review):
            server.shutdown()
            server.server_close()
        for thread in self.threads:
            thread.join()
        self.temp.cleanup()

    def create(self, count=1):
        package = ScriptPackage(title="HTTP pipeline", script=self.script, shots=[
            Shot(id=f"shot-{i}", prompt=f"Original shot {i}", reference_images=[self.reference],
                 duration=0.5, width=96, height=64, fps=24)
            for i in range(1, count + 1)
        ])
        return Production.create(package, self.config, self.run_dir)

    def test_model_feedback_changes_next_comfy_request_only_for_rejected_shot(self):
        replacement = "A distinct teal object centered in the landscape"
        self.review.responses.extend([
            (200, {"passed": False, "summary": "Subject missing", "issues": [{
                "shot_id": "shot-1", "category": "generation", "message": "0.125s: subject missing",
                "suggested_prompt": replacement,
            }]}), PASS, PASS, PASS,
        ])
        state = self.create(count=2).run()
        prompts = [item["prompt"]["conditioning"]["inputs"]["prompt"] for item in self.comfy.submissions]
        self.assertEqual(prompts, ["Original shot 1", replacement, "Original shot 2"])
        self.assertEqual(state["shots"]["shot-1"]["attempts"], 2)
        self.assertEqual(state["shots"]["shot-2"]["attempts"], 1)
        self.assertEqual(state["status"], "complete")
        self.assertGreater(inspect_video(self.run_dir / state["final"]).duration, 0.9)
        self.assertFalse(self.review.responses)

    def test_shot_review_outage_resumes_existing_clip_without_generation_budget(self):
        self.config.max_attempts = 1
        self.review.responses.append((503, "Reviewer temporarily unavailable"))
        first = self.create().run()
        self.assertNotEqual(first["status"], "complete")
        clip = self.run_dir / "shots" / "shot-1" / "attempt-001" / "clip.mp4"
        before = clip.stat().st_mtime_ns
        parameters = clip.parent / "parameters.json"
        original_parameters = parameters.read_text()
        self.review.responses.extend([PASS, PASS])
        resumed = Production(self.run_dir).run()
        self.assertEqual(resumed["status"], "complete")
        self.assertEqual(len(self.comfy.submissions), 1)
        self.assertEqual(clip.stat().st_mtime_ns, before)
        self.assertEqual(parameters.read_text(), original_parameters)
        self.assertEqual(resumed["shots"]["shot-1"]["attempts"], 1)
        self.assertEqual(resumed["shots"]["shot-1"]["budget_used"], 1)
        self.assertFalse(self.review.responses)

    def test_film_review_outage_resumes_existing_draft(self):
        self.config.max_film_reviews = 1
        self.review.responses.extend([PASS, (503, "Reviewer temporarily unavailable")])
        with self.assertRaises(httpx.HTTPStatusError):
            self.create().run()
        previous = json.loads((self.run_dir / "state.json").read_text())
        draft = self.run_dir / previous["draft"]
        before = draft.stat().st_mtime_ns
        self.review.responses.append(PASS)
        resumed = Production(self.run_dir).run()
        self.assertEqual(resumed["status"], "complete")
        self.assertEqual(len(self.comfy.submissions), 1)
        self.assertEqual(resumed["draft"], previous["draft"])
        self.assertEqual(draft.stat().st_mtime_ns, before)
        self.assertEqual(resumed["film_cycle"], 1)
        self.assertFalse(self.review.responses)


if __name__ == "__main__":
    unittest.main()
