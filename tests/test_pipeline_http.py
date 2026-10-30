import json
import tempfile
import threading
import unittest
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from PIL import Image

from tests.test_comfyui import ComfyHandler
from videopipe.media import inspect_video, run_ffmpeg
from videopipe.models import ComfyConfig, MediaConfig, PipelineConfig, ReviewConfig, ScriptPackage, Shot
from videopipe.pipeline import Production


class SequencedReviewHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.server.requests.append(request)
        if getattr(self.server, "fail_third_generation", False) and len(self.server.requests) == 2:
            self.server.comfy.mode = "error"
        if getattr(self.server, "concurrency_gate", None):
            if len(self.server.requests) >= 2:
                self.server.concurrency_gate.set()
            self.server.overlapped = self.server.concurrency_gate.wait(timeout=3)
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
                sample_frames=2, max_frame_size=96, workers=1,
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

    def test_rejected_shot_retries_with_original_prompt_and_a_new_seed(self):
        self.review.responses.extend([
            (200, {"passed": False, "summary": "Subject missing", "issues": [{
                "shot_id": "shot-1", "category": "generation", "message": "Subject missing",
                "timestamps": [0.125],
            }]}), PASS, PASS,
        ])
        state = self.create(count=2).run()
        inputs = [item["prompt"]["conditioning"]["inputs"] for item in self.comfy.submissions]
        self.assertEqual([x["prompt"] for x in inputs].count("Original shot 1"), 2)
        self.assertEqual([x["prompt"] for x in inputs].count("Original shot 2"), 1)
        self.assertEqual([x["seed"] for x in inputs if x["prompt"] == "Original shot 1"], [42, 43])
        self.assertEqual(state["shots"]["shot-1"]["attempts"], 2)
        self.assertEqual(state["shots"]["shot-2"]["attempts"], 1)
        self.assertEqual(state["status"], "complete")
        self.assertGreater(inspect_video(self.run_dir / state["final"]).duration, 0.9)
        self.assertFalse(self.review.responses)

    def test_review_outage_selects_existing_take_and_never_regenerates(self):
        self.review.responses.append((503, "Reviewer temporarily unavailable"))
        state = self.create().run()
        self.assertEqual(state["status"], "complete")
        shot = state["shots"]["shot-1"]
        self.assertEqual(shot["selection"]["reason"], "review_unavailable")
        self.assertEqual(shot["selection"]["candidates"], [1])
        self.assertFalse(shot["takes"][0]["review"]["available"])
        resumed = Production(self.run_dir).run()
        self.assertEqual(resumed["shots"], state["shots"])
        self.assertEqual(len(self.comfy.submissions), 1)
        self.assertEqual(len(self.review.requests), 1)

    def test_three_rejections_select_random_take_and_preserve_selection_on_resume(self):
        self.review.responses.extend([(200, {
            "passed": False, "summary": "Wrong subject", "issues": [{
                "shot_id": "shot-1", "category": "generation", "message": "Wrong subject",
                "timestamps": [0.125],
            }],
        })] * 3)
        self.config.human_approval = True
        state = self.create().run()
        self.assertEqual(state["status"], "awaiting_approval")
        shot = state["shots"]["shot-1"]
        self.assertEqual(shot["attempts"], 3)
        self.assertEqual(shot["budget_used"], 3)
        self.assertEqual(shot["selection"]["candidates"], [1, 2, 3])
        self.assertEqual(shot["selection"]["reason"], "budget_exhausted")
        self.assertIn(shot["selection"]["take_id"], [1, 2, 3])
        self.assertEqual(len({take["prompt_hash"] for take in shot["takes"]}), 1)
        resumed = Production(self.run_dir).run()
        self.assertEqual(resumed["shots"]["shot-1"]["selection"], shot["selection"])
        final = Production(self.run_dir).approve()
        self.assertEqual(final["status"], "complete")
        self.assertEqual(len(self.comfy.submissions), 3)
        self.assertEqual(len(self.review.requests), 3)

    def test_generation_timeout_resumes_saved_job_without_another_submission(self):
        self.comfy.mode = "timeout"
        self.config.comfyui.timeout = 0.15
        first = self.create().run()
        self.assertEqual(first["status"], "needs_attention")
        self.assertEqual(first["shots"]["shot-1"]["attempts"], 1)
        self.comfy.mode = "success"
        self.review.responses.append(PASS)
        resumed = Production(self.run_dir).run()
        self.assertEqual(resumed["status"], "complete")
        self.assertEqual(len(self.comfy.submissions), 1)
        self.assertEqual(resumed["shots"]["shot-1"]["budget_used"], 1)

    def test_last_generation_failure_uses_previous_takes_without_an_extra_resume(self):
        self.review.fail_third_generation = True
        self.review.comfy = self.comfy
        self.review.responses.extend([(200, {
            "passed": False, "summary": "Wrong subject", "issues": [{
                "shot_id": "shot-1", "category": "generation", "message": "Wrong subject",
                "timestamps": [0.125],
            }],
        })] * 2)
        state = self.create().run()
        self.assertEqual(state["status"], "complete")
        shot = state["shots"]["shot-1"]
        self.assertEqual(shot["selection"]["reason"], "budget_exhausted")
        self.assertEqual(shot["selection"]["candidates"], [1, 2])
        self.assertEqual(shot["takes"][2]["status"], "failed")
        self.assertEqual(len(self.comfy.submissions), 3)

    def test_no_usable_take_marks_missing_but_other_shots_are_still_attempted(self):
        self.comfy.mode = "no-video"
        state = self.create(count=2).run()
        self.assertEqual(state["status"], "needs_attention")
        self.assertNotIn("final", state)
        for shot in state["shots"].values():
            self.assertEqual(shot["status"], "missing")
            self.assertEqual(shot["attempts"], 3)
        self.assertEqual(len(self.comfy.submissions), 6)
        self.assertEqual(len(self.review.requests), 0)

    def test_review_workers_overlap_while_generation_moves_to_the_next_shot(self):
        self.config.review.workers = 2
        self.review.concurrency_gate = threading.Event()
        self.review.overlapped = False
        self.review.responses.extend([PASS, PASS])
        state = self.create(count=2).run()
        self.assertEqual(state["status"], "complete")
        self.assertTrue(self.review.overlapped)
        self.assertEqual(len(self.review.requests), 2)
        self.assertEqual(len(self.comfy.submissions), 2)


if __name__ == "__main__":
    unittest.main()
