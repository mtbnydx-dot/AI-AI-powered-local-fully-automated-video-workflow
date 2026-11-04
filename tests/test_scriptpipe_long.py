import asyncio
import tempfile
import unittest
from pathlib import Path

from scriptpipe.long_checks import check_long_season
from scriptpipe.long_pipeline import run_long


def episode_fixture(episode_id):
    return {
        "id": episode_id, "title": "测试", "synopsis": "发现线索", "start_state": "等待", "end_state": "知情",
        "shots": [{
            "id": f"{episode_id}_s{number:03}", "scene_id": f"{episode_id}_room",
            "location_id": "room", "time_of_day": "day", "duration": 5.875,
            "characters": [], "prop_ids": [], "dialogue": [],
            "visual_en": "An empty room.", "camera_en": "Locked wide shot.",
            "soundscape_en": "Quiet room tone.", "first_frame_prompt_en": "An empty room in daylight.",
            "acceptance_en": ["The room remains empty."], "beat_zh": "光线落入室内",
            "action_start_zh": "光线移动", "action_end_zh": "光线停住", "cut_reason_zh": "转入下一时刻",
            "action_complete": True,
        } for number in range(1, 13)],
    }


class ScriptClient:
    """Supply model responses while running the actual planning/review/repair flow."""

    key = "test-key"
    model = "deepseek-v4.1-flash"
    endpoint = "https://example.invalid"
    reasoning_effort = "high"

    def __init__(self):
        self.calls = []

    def event(self, *args, **kwargs):
        pass

    async def complete(self, name, system, payload, **kwargs):
        self.calls.append((name, kwargs))
        if name == "long_season":
            return {"characters": [], "locations": [{"id": "room"}], "props": [],
                    "episodes": [{"id": f"ep{index:02}"} for index in range(1, payload["brief"]["episode_count"] + 1)]}
        if name == "ep01_plan_v0":
            return {**{key: value for key, value in episode_fixture("ep01").items() if key != "shots"},
                    "scenes": [{"id": "ep01_room", "location_id": "room", "characters": [], "prop_ids": [],
                                "first_shot_number": 1, "last_shot_number": 12, "beats": ["场景变化"] * 12}]}
        if name == "ep01_ep01_room_v0":
            return {"episode_id": "ep01", "scene_id": "ep01_room", "shots": episode_fixture("ep01")["shots"]}
        if name == "ep01_review_0":
            return {"issues": [{"severity": "error", "code": "story_clarity", "message": "Clarify the first beat."}]}
        if name == "ep01_local_0_v0":
            shot = {**episode_fixture("ep01")["shots"][0], "beat_zh": "新的线索显现"}
            return {"id": "ep01", "shots": [shot]}
        if name in ("ep01_review_1", "season_review_0"):
            return {"issues": []}
        raise AssertionError(f"Unexpected model call: {name}")


class LongScriptTests(unittest.TestCase):
    def test_episode_count_and_duration_are_independent_parameters(self):
        for count, minutes, scene_count in [(64, 5, 4), (1, 1, 1), (48, 10, 9)]:
            with self.subTest(count=count, minutes=minutes), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                source = root / "novel.txt"
                source.write_text("测试原文", encoding="utf-8")
                result = asyncio.run(run_long(source, root / "run", ScriptClient(), episode_count=count,
                                             episode_minutes=minutes, stop_after="season"))
                brief = result["brief"]
                self.assertEqual(brief["episode_count"], count)
                self.assertEqual(brief["scenes_per_episode"], scene_count)
                self.assertEqual(brief["episode_duration_seconds"], scene_count * 12 * 5.875)
                self.assertEqual(result["review_effort"], "high")

    def test_long_season_uses_planned_episode_and_shot_counts(self):
        episodes = [episode_fixture(f"ep{number:02}") for number in range(1, 12)]
        season = {"characters": [], "locations": [{"id": "room"}], "props": [],
                  "episodes": [{"id": episode["id"]} for episode in episodes]}
        self.assertEqual(check_long_season(episodes, season, 12), [])
        episodes[0]["shots"].pop()
        self.assertIn("long_episode_shot_count", [issue["code"] for issue in check_long_season(episodes, season, 12)])
        self.assertIn("season_episode_order", [issue["code"] for issue in check_long_season(episodes[:-1], season, 12)])

    def test_all_text_stages_inherit_high_including_repair_and_review(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "novel.txt"
            source.write_text("测试原文", encoding="utf-8")
            client = ScriptClient()
            result = asyncio.run(run_long(source, root / "run", client, episode_count=1,
                                         episode_minutes=1, stop_after="review"))
            self.assertEqual(result["status"], "review_complete")
            self.assertEqual([name for name, _ in client.calls], [
                "long_season", "ep01_plan_v0", "ep01_ep01_room_v0", "ep01_review_0",
                "ep01_local_0_v0", "ep01_review_1", "season_review_0",
            ])
            for name, kwargs in client.calls:
                with self.subTest(stage=name):
                    self.assertEqual(kwargs.get("reasoning_effort", client.reasoning_effort), "high")


if __name__ == "__main__":
    unittest.main()
