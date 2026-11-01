import contextlib
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest

from videopipe.cli import main
from videopipe.inputs import load_config, load_package
from videopipe.media import inspect_video
from videopipe.pipeline import Production
from videopipe.records import write_json


class SeasonTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source, self.output = self.root / "season", self.root / "output"
        self.ids = ["ep02", "ep01"]
        write_json(self.source / "overview.json", {"episodes": [
            {"id": name, "package": f"episodes/{name}/package.json"} for name in self.ids]})
        for name in self.ids:
            folder = self.source / "episodes" / name
            write_json(folder / "package.json", {"title": name, "script": "script.md", "shots": [
                {"id": "s1", "prompt": "<d>[Chinese] 等一下。</d>", "duration": 0.25,
                 "width": 160, "height": 96, "fps": 24}]})
            (folder / "script.md").write_text("等一下。")
            write_json(folder / "production.yaml", {"generator": "cpu", "review": {"sample_frames": 2},
                                                      "media": {"width": 160, "height": 96, "fps": 24}})

    def cli(self, *args):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return main(list(map(str, args)))

    def test_cli_resumes_existing_run_uses_manifest_order_and_skips_completed_video(self):
        folder = self.source / "episodes/ep02"
        production = Production.create(load_package(folder / "package.json"), load_config(folder / "production.yaml"),
                                       self.output / "runs/ep02")
        production.events.close()
        self.assertEqual(self.cli("run-season", self.source, "--output", self.output), 0)
        events = [json.loads(line) for line in (self.output / "events.jsonl").read_text().splitlines()]
        self.assertEqual([event["episode"] for event in events if event["event"] == "episode_started"], self.ids)
        originals = {}
        for name in self.ids:
            state = json.loads((self.output / "runs" / name / "state.json").read_text())
            final = self.output / "runs" / name / state["final"]
            originals[name] = final.stat().st_mtime_ns
            self.assertGreater(inspect_video(final).duration, 0)
            self.assertIn("等一下。", (self.output / "delivery" / name / "episode.srt").read_text())
            (self.source / "episodes" / name / "script.md").unlink()
        self.assertEqual(self.cli("run-season", self.source, "--output", self.output), 0)
        for name in self.ids:
            state = json.loads((self.output / "runs" / name / "state.json").read_text())
            self.assertEqual(state["shots"]["s1"]["attempts"], 1)
            self.assertEqual((self.output / "runs" / name / state["final"]).stat().st_mtime_ns, originals[name])
        with tarfile.open(self.output / "delivery.tar.gz") as archive:
            self.assertIn("delivery/ep01/final.mp4", archive.getnames())
            self.assertIn("delivery/ep02/episode.srt", archive.getnames())
            self.assertFalse(any("/runs/" in name or "/shots/" in name for name in archive.getnames()))

    def test_failed_episode_does_not_stop_next_and_can_be_resumed(self):
        script = self.source / "episodes/ep02/script.md"
        script.unlink()
        self.assertEqual(self.cli("run-season", self.source, "--output", self.output), 2)
        report = json.loads((self.output / "state.json").read_text())
        self.assertEqual(report["cases"]["ep02"]["status"], "failed")
        self.assertEqual(report["cases"]["ep01"]["status"], "complete")
        self.assertEqual(self.cli("collect", self.output), 2)
        script.write_text("等一下。")
        self.assertEqual(self.cli("run-season", self.source, "--output", self.output), 0)
        exported = self.root / "exported"
        self.assertEqual(self.cli("collect", self.output, "--output", exported), 0)
        self.assertTrue((exported / "ep02/episode.srt").exists())
        self.assertTrue(exported.with_suffix(".tar.gz").exists())


if __name__ == "__main__":
    unittest.main()
