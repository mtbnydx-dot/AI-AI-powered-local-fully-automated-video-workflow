import asyncio
import copy
import tempfile
import unittest
from pathlib import Path

from scriptpipe.checks import check_episode, spoken_length
from scriptpipe.long_checks import check_long_plan, check_long_season, check_scene
from scriptpipe.long_pipeline import run_long


def episode_fixture(episode_id, scene_shot_counts=(2, 3)):
    shots = []
    for scene_number, count in enumerate(scene_shot_counts, 1):
        for _ in range(count):
            number = len(shots) + 1
            shots.append({
                "id": f"{episode_id}_s{number:03}", "scene_id": f"{episode_id}_scene{scene_number:02}",
                "location_id": "room", "time_of_day": "day", "duration": (4, 6.5, 9.4)[(number - 1) % 3],
                "characters": [], "prop_ids": [], "dialogue": [],
                "visual_en": "An empty room.", "camera_en": "Locked wide shot.",
                "soundscape_en": "Quiet room tone.", "first_frame_prompt_en": "An empty room in daylight.",
                "acceptance_en": ["The room remains empty."], "beat_zh": "光线落入室内",
                "action_start_zh": "光线移动", "action_end_zh": "光线停住", "cut_reason_zh": "转入下一时刻",
                "action_complete": True,
            })
    return {
        "id": episode_id, "title": "测试", "synopsis": "发现线索", "start_state": "等待", "end_state": "知情",
        "shots": shots,
    }


def plan_fixture(episode):
    scenes = []
    for number, shot in enumerate(episode["shots"], 1):
        if not scenes or scenes[-1]["id"] != shot["scene_id"]:
            scenes.append({"id": shot["scene_id"], "location_id": "room", "characters": [], "prop_ids": [],
                           "first_shot_number": number, "anchor_shot_number": number, "beats": []})
        scenes[-1]["last_shot_number"] = number
        scenes[-1]["beats"].append(shot["beat_zh"])
    return {**{key: value for key, value in episode.items() if key != "shots"}, "scenes": scenes}


def season_fixture(episodes):
    return {"characters": [], "locations": [{"id": "room"}], "props": [],
            "episodes": [{"id": episode["id"]} for episode in episodes]}


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
            return season_fixture([{"id": f"ep{index:02}"} for index in range(1, payload["brief"]["episode_count"] + 1)])
        if name == "ep01_plan_v0":
            return plan_fixture(episode_fixture("ep01"))
        for scene in plan_fixture(episode_fixture("ep01"))["scenes"]:
            if name == f"ep01_{scene['id']}_v0":
                return {"episode_id": "ep01", "scene_id": scene["id"],
                        "shots": episode_fixture("ep01")["shots"][scene["first_shot_number"] - 1:scene["last_shot_number"]]}
        if name == "ep01_review_0":
            return {"issues": [{"severity": "error", "code": "story_clarity", "message": "Clarify the first beat."}]}
        if name == "ep01_local_0_v0":
            shot = {**episode_fixture("ep01")["shots"][0], "beat_zh": "新的线索显现"}
            return {"id": "ep01", "shots": [shot]}
        if name in ("ep01_review_1", "season_review_0"):
            return {"issues": []}
        raise AssertionError(f"Unexpected model call: {name}")


class LongScriptTests(unittest.TestCase):
    def test_episode_runtime_is_a_target_without_scene_or_shot_quotas(self):
        for count, minutes in [(64, 5), (1, 1), (48, 10)]:
            with self.subTest(count=count, minutes=minutes), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                source = root / "novel.txt"
                source.write_text("测试原文", encoding="utf-8")
                result = asyncio.run(run_long(source, root / "run", ScriptClient(), episode_count=count,
                                             episode_minutes=minutes, stop_after="season"))
                brief = result["brief"]
                self.assertEqual(brief["episode_count"], count)
                self.assertEqual(brief["target_episode_duration_seconds"], minutes * 60)
                self.assertEqual(brief["target_season_duration_seconds"], count * minutes * 60)
                self.assertEqual(brief["preferred_duration_seconds"], 5)
                self.assertEqual(brief["maximum_requested_duration_seconds"], 9.4)
                self.assertEqual(brief["maximum_duration_seconds"], 10)
                for key in ("scenes_per_episode", "shots_per_scene", "shots_per_episode", "duration_per_shot"):
                    self.assertNotIn(key, brief)
                self.assertEqual(result["review_effort"], "high")

    def test_dynamic_plan_still_requires_consecutive_ranges_and_matching_beats(self):
        episode = episode_fixture("ep01")
        season = season_fixture([episode])
        plan = plan_fixture(episode)
        self.assertEqual(check_long_plan(plan, season, season["episodes"][0]), [])
        for field, value, expected_code in [
            ("first_shot_number", 2, "scene_shot_range"),
            ("last_shot_number", 4, "scene_shot_range"),
            ("beats", [], "scene_beat_count"),
            ("anchor_shot_number", 6, "scene_anchor"),
        ]:
            with self.subTest(field=field):
                changed = copy.deepcopy(plan)
                changed["scenes"][1][field] = value
                self.assertIn(expected_code, [issue["code"] for issue in check_long_plan(changed, season, season["episodes"][0])])

    def test_long_season_allows_different_scene_counts_and_shot_counts(self):
        episodes = [episode_fixture("ep01", (2, 3)), episode_fixture("ep02", (1,)),
                    episode_fixture("ep03", (4, 1, 2))]
        season = season_fixture(episodes)
        self.assertEqual(check_long_season(episodes, season), [])
        episodes[0]["shots"][2]["id"] = "ep01_s004"
        self.assertIn("shot_number", [issue["code"] for issue in check_long_season(episodes, season)])
        self.assertIn("season_episode_order", [issue["code"] for issue in check_long_season(episodes[:-1], season)])

    def test_h3_rounding_limit_applies_to_flexible_shot_durations(self):
        episode = episode_fixture("ep01", (1,))
        season = season_fixture([episode])
        plan = plan_fixture(episode)
        scene = {"episode_id": "ep01", "scene_id": "ep01_scene01", "shots": episode["shots"]}
        episode["shots"][0]["duration"] = 9.4
        self.assertEqual(check_scene(scene, plan["scenes"][0], plan, season), [])
        episode["shots"][0]["duration"] = 9.5
        self.assertIn("h3_duration_limit", [issue["code"] for issue in check_scene(scene, plan["scenes"][0], plan, season)])
        episode["shots"][0]["duration"] = 6
        self.assertIn("h3_duration_limit", [issue["code"] for issue in check_episode(episode, season)])

    def test_long_dialogue_uses_duration_budget_while_short_mode_keeps_its_limit(self):
        episode = episode_fixture("ep01", (1,))
        season = season_fixture([episode])
        season["characters"] = [{"id": "speaker", "wardrobes": [{"id": "outfit"}]}]
        shot = episode["shots"][0]
        shot.update(duration=9.4, characters=[{"id": "speaker", "wardrobe_id": "outfit"}], dialogue=[{
            "speaker_id": "speaker", "voiceover": False, "delivery_en": "Natural measured Mandarin.",
            "text_zh": "我们已经核对了所有资料，明天上午一起去办公室确认最后的合同。",
        }])
        self.assertGreater(spoken_length(shot["dialogue"][0]["text_zh"]), 22)
        self.assertEqual(check_long_season([episode], season), [])
        short_codes = [issue["code"] for issue in check_episode(episode, season)]
        self.assertIn("duration_limit", short_codes)
        self.assertIn("dialogue_length", short_codes)
        shot["duration"] = 4
        self.assertIn("dialogue_timing", [issue["code"] for issue in check_long_season([episode], season)])

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
                "long_season", "ep01_plan_v0", "ep01_ep01_scene01_v0", "ep01_ep01_scene02_v0", "ep01_review_0",
                "ep01_local_0_v0", "ep01_review_1", "season_review_0",
            ])
            for name, kwargs in client.calls:
                with self.subTest(stage=name):
                    self.assertEqual(kwargs.get("reasoning_effort", client.reasoning_effort), "high")


if __name__ == "__main__":
    unittest.main()
