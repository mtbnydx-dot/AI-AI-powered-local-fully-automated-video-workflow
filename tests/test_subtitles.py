import json
from pathlib import Path
import tempfile
import unittest

from videopipe.models import Shot
from videopipe.subtitles import create_subtitles, extract_dialogue


class SubtitleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_original_chinese_and_punctuation_are_kept_with_observed_word_times(self):
        shot = Shot(id="s1", duration=4, prompt="She says <d>[Chinese] 等等，我来了！</d>")
        transcript = {"status": "complete", "segments": [{
            "start": 0.5, "end": 2, "text": "等等我来了", "words": [
                {"start": 0.6, "end": 0.9, "word": "等等", "probability": 0.9},
                {"start": 1.1, "end": 1.8, "word": "我来了", "probability": 0.95},
            ],
        }]}
        result = create_subtitles([shot], transcript, self.root)
        self.assertEqual(result["status"], "complete")
        data = Path(result["path"]).read_text(encoding="utf-8-sig")
        self.assertIn("00:00:00,600 --> 00:00:01,800", data)
        self.assertIn("等等，我来了！", data)
        self.assertNotIn("[Chinese]", data)
        self.assertEqual(shot.prompt, "She says <d>[Chinese] 等等，我来了！</d>")

    def test_segment_timestamps_can_match_an_entire_line_in_its_own_shot(self):
        shots = [Shot(id="s1", duration=3, prompt="Silence."),
                 Shot(id="s2", duration=3, prompt="<d>[English] Please, wait.</d>")]
        result = create_subtitles(shots, {"status": "complete", "segments": [
            {"start": 0.2, "end": 1.0, "text": "Please wait"},
            {"start": 3.4, "end": 4.2, "text": "Please wait"},
        ]}, self.root)
        self.assertEqual(result["matched"][0]["start"], 3.4)
        self.assertEqual(result["matched"][0]["text"], "Please, wait.")

    def test_missing_dialogue_does_not_get_fabricated_timestamps(self):
        shot = Shot(id="s1", duration=4, prompt="<d>[English] Wait.</d> <d>[English] Come back.</d>")
        result = create_subtitles([shot], {"status": "complete", "segments": [
            {"start": 0.5, "end": 1.0, "text": "Wait"},
        ]}, self.root)
        self.assertEqual(result["status"], "partial")
        self.assertEqual(len(result["unmatched"]), 1)
        self.assertNotIn("Come back", Path(result["path"]).read_text(encoding="utf-8-sig"))
        self.assertNotIn("start", result["unmatched"][0])

    def test_repeated_line_uses_observed_occurrences_in_order(self):
        shot = Shot(id="s1", duration=4, prompt="<d>[English] Wait.</d> <d>[English] Wait.</d>")
        result = create_subtitles([shot], {"status": "complete", "segments": [
            {"start": 0.5, "end": 1.0, "text": "Wait"},
            {"start": 2.0, "end": 2.5, "text": "Wait"},
        ]}, self.root)
        self.assertEqual(result["status"], "complete")
        self.assertEqual([line["start"] for line in result["matched"]], [0.5, 2.0])

    def test_partial_segment_without_word_times_is_not_split(self):
        shot = Shot(id="s1", duration=4, prompt="<d>[English] Wait.</d>")
        result = create_subtitles([shot], {"status": "complete", "segments": [
            {"start": 0.5, "end": 2.0, "text": "Wait. Come back."},
        ]}, self.root)
        self.assertEqual(result["status"], "unaligned")

    def test_word_boundaries_do_not_change_the_original_words(self):
        result = create_subtitles([Shot(id="s1", duration=3, prompt="<d>[English] A part.</d>")],
                                  {"status": "complete", "segments": [
                                      {"start": 0.5, "end": 1.0, "text": "Apart"}]}, self.root)
        self.assertEqual(result["status"], "unaligned")

    def test_missing_asr_removes_stale_srt_and_does_not_infer_timing(self):
        (self.root / "episode.srt").write_text("old output")
        shot = Shot(id="s1", prompt="<d>[English] Hello!</d>")
        result = create_subtitles([shot], {"status": "unavailable"}, self.root)
        self.assertEqual(result["status"], "unavailable")
        self.assertFalse((self.root / "episode.srt").exists())
        self.assertEqual(json.loads((self.root / "subtitles.json").read_text())["path"], None)

    def test_cross_shot_intervals_are_not_subtitle_times(self):
        shot = Shot(id="s1", duration=2, prompt="<d>[English] Wait.</d>")
        result = create_subtitles([shot], {"status": "complete", "segments": [
            {"start": 1.5, "end": 2.2, "text": "Wait"}]}, self.root)
        self.assertEqual(result["status"], "unaligned")

    def test_scene_descriptions_are_not_dialogue_and_continuity_tags_are_not_spoken(self):
        shots = [Shot(id="s1", prompt="A sign reads Hello. The man is quiet."),
                 Shot(id="s2", prompt="<d>[English] I will<cutoff></d>")]
        self.assertEqual([line["text"] for line in extract_dialogue(shots)], ["I will"])
        self.assertEqual(create_subtitles(shots[:1], {"status": "complete"}, self.root)["status"],
                         "no_dialogue")


if __name__ == "__main__":
    unittest.main()
