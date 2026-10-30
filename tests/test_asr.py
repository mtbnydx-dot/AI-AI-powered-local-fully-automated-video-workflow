import json
import os
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from videopipe.backends.asr import transcribe, _transcribe_local


class ASRTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.video = self.root / "movie.mp4"
        self.video.write_bytes(b"test fixture")
        self.config = SimpleNamespace(enabled=True, model="/models/whisper", gpu=7,
                                      compute_type="int8_float16", language="zh", timeout=1)

    def tearDown(self):
        self.temp.cleanup()

    def test_disabled_asr_does_not_spawn_or_require_dependency(self):
        self.config.enabled = False
        with patch("videopipe.backends.asr.subprocess.run") as run:
            result = transcribe(self.video, self.config, self.root / "asr")
        self.assertEqual(result["status"], "disabled")
        run.assert_not_called()

    def test_child_gets_only_selected_gpu_and_returns_timestamped_segments(self):
        original_visibility = os.environ.get("CUDA_VISIBLE_DEVICES")

        def run(command, **kwargs):
            self.assertEqual(kwargs["env"]["CUDA_VISIBLE_DEVICES"], "7")
            self.assertEqual(kwargs["env"]["HF_HUB_OFFLINE"], "1")
            self.assertEqual(kwargs["timeout"], 1)
            self.assertIn("videopipe.backends.asr", command)
            self.assertEqual(command[command.index("--language") + 1], "zh")
            output = Path(command[command.index("--output") + 1])
            output.write_text(json.dumps({"status": "complete", "segments": [
                {"start": 0.2, "end": 1.2, "text": "你好"}]}))
            return subprocess.CompletedProcess(command, 0, "", "")

        with patch("videopipe.backends.asr.subprocess.run", side_effect=run):
            result = transcribe(self.video, self.config, self.root / "asr")
        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["segments"][0]["start"], 0.2)
        self.assertEqual(os.environ.get("CUDA_VISIBLE_DEVICES"), original_visibility)

    def test_timeout_and_process_failure_are_nonblocking_and_clear_stale_result(self):
        output = self.root / "asr"
        output.mkdir()
        for failure in [subprocess.TimeoutExpired(["python"], 1),
                        subprocess.CompletedProcess([], 1, "", "missing faster_whisper")]:
            (output / "worker-result.json").write_text('{"status":"complete","segments":[]}')
            with self.subTest(failure=failure):
                options = {"side_effect": failure} if isinstance(failure, Exception) else {"return_value": failure}
                with patch("videopipe.backends.asr.subprocess.run", **options):
                    result = transcribe(self.video, self.config, output)
                self.assertEqual(result["status"], "unavailable")
                self.assertEqual(result["segments"], [])
                self.assertIn("error", result)
                self.assertFalse((output / "worker-result.json").exists())

    def test_malformed_timestamps_are_not_a_successful_transcript(self):
        def run(command, **kwargs):
            Path(command[command.index("--output") + 1]).write_text(json.dumps({
                "status": "complete", "segments": [{"start": 3, "end": 1, "text": "bad"}]}))
            return subprocess.CompletedProcess(command, 0, "", "")

        with patch("videopipe.backends.asr.subprocess.run", side_effect=run):
            result = transcribe(self.video, self.config, self.root / "asr")
        self.assertEqual(result["status"], "unavailable")

    def test_worker_uses_local_single_input_and_observed_words(self):
        word = SimpleNamespace(start=0.2, end=0.8, word="Hello", probability=0.9)
        segment = SimpleNamespace(start=0.1, end=1.0, text="Hello", words=[word])
        info = SimpleNamespace(language="en", language_probability=0.99)
        calls = {}

        class Model:
            def __init__(self, name, **kwargs):
                calls["init"] = kwargs

            def transcribe(self, path, **kwargs):
                calls["transcribe"] = kwargs
                return iter([segment]), info

        with patch.dict("sys.modules", {"faster_whisper": SimpleNamespace(WhisperModel=Model)}):
            result = _transcribe_local(self.video, "/local/model", "int8_float16", "en")
        self.assertTrue(calls["init"]["local_files_only"])
        self.assertEqual(calls["init"]["device_index"], 0)
        self.assertEqual(calls["init"]["num_workers"], 1)
        self.assertEqual(calls["transcribe"]["beam_size"], 1)
        self.assertTrue(calls["transcribe"]["word_timestamps"])
        self.assertEqual(result["segments"][0]["words"][0]["start"], 0.2)


if __name__ == "__main__":
    unittest.main()
