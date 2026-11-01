import json
import tarfile
import tempfile
import unittest
from pathlib import Path

from videopipe.delivery import collect_delivery
from videopipe.records import Events, write_json

EPISODES = ("ep01", "ep02")

class DeliveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for name in EPISODES:
            run = self.root / "runs" / name
            write_json(run / "package.json", {"title": name, "script": "script.md", "shots": [
                {"id": "s1", "duration": 2, "prompt": "<d>[Chinese] 我还有一件事。</d>"}]})
            final = run / "delivery/version-001/final.mp4"
            final.parent.mkdir(parents=True)
            final.write_bytes(b"already generated video")
            write_json(run / "state.json", {
                "status": "complete", "final": "delivery/version-001/final.mp4",
                "draft": "film/version-001/draft.mp4",
                "shots": {"s1": {"duration": 2}},
                "audio": {"transcript": {"status": "unavailable", "segments": []}},
            })
            (run / "events.jsonl").write_text('{"event":"run_completed"}\n')

    def test_collect_exports_video_and_fallback_srt_without_nested_working_files(self):
        write_json(self.root / "runs/ep01/shots/s1/review/request.json", {"large": "image payload"})
        services = self.root / "services"
        services.mkdir()
        (services / "vllm.log").write_text("model load log")
        old = self.root / "delivery/logs/runs"
        old.mkdir(parents=True)
        (old / "old-copy.json").write_text("{}")
        (self.root / "delivery/logs.tar.gz").write_bytes(b"old archive")

        report = collect_delivery(self.root)
        self.assertEqual(report["status"], "complete")
        for name in EPISODES:
            folder = self.root / "delivery" / name
            self.assertEqual((folder / "final.mp4").read_bytes(), b"already generated video")
            self.assertIn("我还有一件事。", (folder / "episode.srt").read_text())
            self.assertEqual(report["cases"][name]["subtitle_timing"], "script")
        with tarfile.open(report["archive"]) as archive:
            names = archive.getnames()
        self.assertIn("delivery/ep01/episode.srt", names)
        self.assertIn("delivery/logs/service-vllm.log", names)
        self.assertFalse(any("/runs/" in name or name.endswith(".tar.gz") or "request.json" in name for name in names))

    def test_missing_final_is_reported_and_does_not_export_stale_video(self):
        collect_delivery(self.root)
        (self.root / "runs/ep01/delivery/version-001/final.mp4").unlink()
        report = collect_delivery(self.root)
        self.assertEqual(report["status"], "incomplete")
        self.assertEqual(report["cases"]["ep01"]["status"], "failed")
        self.assertEqual(report["cases"]["ep02"]["status"], "complete")
        self.assertFalse((self.root / "delivery/ep01/final.mp4").exists())
        with tarfile.open(report["archive"]) as archive:
            self.assertNotIn("delivery/ep01/final.mp4", archive.getnames())

    def test_saved_asr_with_zero_length_word_is_recovered_during_collection(self):
        write_json(self.root / "runs/ep02/film/version-001/audio/worker-result.json", {
            "status": "complete", "segments": [
                {"start": 0.1, "end": 1.8, "text": "我还有一件事", "words": [
                    {"start": 1.0, "end": 1.0, "word": "叔"}]}]})
        report = collect_delivery(self.root)
        self.assertEqual(report["cases"]["ep02"]["asr_status"], "complete")
        self.assertEqual(report["cases"]["ep02"]["subtitle_timing"], "asr")
        state = json.loads((self.root / "runs/ep02/state.json").read_text())
        self.assertEqual(len(state["audio"]["transcript"]["timing_warnings"]), 1)
        self.assertIn("00:00:00,100 --> 00:00:01,800", (self.root / "delivery/ep02/episode.srt").read_text())

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


if __name__ == "__main__":
    unittest.main()
