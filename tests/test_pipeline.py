import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from videopipe.inputs import load_package
from videopipe.media import inspect_video, run_ffmpeg
from videopipe.models import PipelineConfig
from videopipe.pipeline import Production


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "script.md").write_text("# Two shots of a blue cup\nWide shot, then close-up.")
        Image.new("RGB", (160, 96), "teal").save(self.root / "reference.png")
        self.manifest = self.root / "package.json"
        self.data = {"title": "Test film", "script": "script.md", "shots": [
            {"id": name, "prompt": f"A blue cup, {name}", "reference_images": ["reference.png"],
             "width": 160, "height": 96, "duration": 0.25, "fps": 24}
            for name in ("wide", "close")
        ]}
        self.manifest.write_text(json.dumps(self.data))
        self.package = load_package(self.manifest)
        self.config = PipelineConfig.model_validate({
            "workers": 2, "human_approval": False, "review": {"sample_frames": 2},
            "media": {"width": 160, "height": 96, "upscale": "lanczos", "upscale_factor": 2},
        })
        self.run_dir = self.root / "run"

    def test_full_run_repairs_only_rejected_shots_and_keeps_audit_trail(self):
        self.config.review.reject_once = ["wide", "film"]
        state = Production.create(self.package, self.config, self.run_dir).run()
        self.assertEqual(state["status"], "complete")
        self.assertEqual([state["shots"][key]["attempts"] for key in ("wide", "close")], [3, 1])
        self.assertEqual(state["film_reviews"], 2)
        info = inspect_video(self.run_dir / state["final"])
        self.assertEqual((info.width, info.height), (320, 192))
        self.assertAlmostEqual(info.duration, 0.5, delta=0.1)
        run_ffmpeg(["-v", "error", "-i", str(self.run_dir / state["final"]), "-f", "null", "-"])
        events = [json.loads(line) for line in (self.run_dir / "events.jsonl").read_text().splitlines()]
        self.assertTrue(any(event["event"] == "run_completed" for event in events))
        stages = [event for event in events if event["event"] == "stage_finished"]
        self.assertEqual({event["stage"] for event in stages},
                         {"generate", "normalize", "shot_review", "assemble", "film_review", "finalize"})
        self.assertTrue(all(event["status"] == "complete" and event["elapsed"] >= 0 for event in stages))
        self.assertTrue((self.run_dir / "shots/wide/attempt-001/review/result.json").is_file())
        self.assertEqual(Production(self.run_dir).run()["shots"], state["shots"])

    def test_approval_and_targeted_retry_preserve_previous_delivery(self):
        self.config.human_approval = True
        state = Production.create(self.package, self.config, self.run_dir).run()
        self.assertEqual(state["status"], "awaiting_approval")
        self.assertNotIn("final", state)
        state = Production(self.run_dir).approve()
        old_final = self.run_dir / state["final"]
        old_bytes = old_final.read_bytes()
        production = Production(self.run_dir)
        production.retry(["close"], prompt="A closer blue cup")
        state = production.run(auto_approve=True)
        self.assertEqual(state["shots"]["wide"]["attempts"], 1)
        self.assertEqual(state["shots"]["close"]["attempts"], 2)
        self.assertNotEqual(old_final, self.run_dir / state["final"])
        self.assertEqual(old_bytes, old_final.read_bytes())
        params = json.loads((self.run_dir / "shots/close/attempt-002/shot.json").read_text())
        self.assertEqual(params["prompt"], "A closer blue cup")

    def test_budget_stops_then_explicit_retry_can_continue(self):
        self.config.max_attempts = 1
        self.config.review.reject_once = ["wide"]
        state = Production.create(self.package, self.config, self.run_dir).run()
        self.assertEqual(state["status"], "needs_attention")
        self.assertEqual(state["shots"]["wide"]["status"], "exhausted")
        production = Production(self.run_dir)
        production.retry(["wide"], feedback="Try again")
        state = production.run()
        self.assertEqual(state["status"], "complete")
        self.assertEqual(state["shots"]["close"]["attempts"], 1)

    def test_package_files_are_snapshotted_and_paths_resolve_from_manifest(self):
        production = Production.create(self.package, self.config, self.run_dir)
        (self.root / "reference.png").unlink()
        (self.root / "script.md").unlink()
        self.assertEqual(production.run()["status"], "complete")

    def test_cli_validation_rejects_bad_inputs_and_yaml_without_traceback(self):
        self.data["shots"][1]["id"] = "wide"
        self.manifest.write_text(json.dumps(self.data))
        config_path = self.root / "config.yaml"
        config_path.write_text("generator: cpu\n")
        command = [sys.executable, "-m", "videopipe", "validate", str(self.manifest),
                   "--config", str(config_path)]
        result = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(result.returncode, 1)
        self.assertIn("unique", result.stderr)
        self.data["shots"][1]["id"] = "close"
        self.manifest.write_text(json.dumps(self.data))
        config_path.write_text("generator: [not yaml\n")
        result = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(result.returncode, 1)
        self.assertNotIn("Traceback", result.stderr)

    def test_h3_constraints_are_checked_before_creating_run(self):
        self.config.generator = "comfyui"
        self.package.shots[0].fps = 12
        with self.assertRaisesRegex(ValueError, "24 fps"):
            Production.create(self.package, self.config, self.run_dir)
        self.assertFalse(self.run_dir.exists())

    def test_ui_workflow_is_rejected_at_entry_with_export_guidance(self):
        path = self.root / "ui-workflow.json"
        path.write_text(json.dumps({"nodes": [], "links": []}))
        self.config.comfyui.workflow = path
        with self.assertRaisesRegex(ValueError, "API format"):
            Production.create(self.package, self.config, self.run_dir)
        self.assertFalse(self.run_dir.exists())


if __name__ == "__main__":
    unittest.main()
