import asyncio
import copy
import json
import tempfile
import unittest
from pathlib import Path

from imagepipe.models import ImageConfig
from imagepipe.plan import compile_plan
from scriptpipe.checks import check_episode
from scriptpipe.long_checks import check_long_plan, check_long_season, check_scene
from scriptpipe.long_pipeline import run_long
from videopipe.models import PipelineConfig


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
    return {"title": "测试剧集", "style_en": "Naturalistic drama.", "characters": [],
            "locations": [{"id": "room", "name": "房间", "description_en": "A room in daylight."}], "props": [],
            "episodes": [{"id": episode["id"], "title": "测试"} for episode in episodes]}


class ScriptClient:
    """Supply model responses while running the actual planning/review/repair flow."""

    key = "test-key"
    model = "deepseek-v4.1-flash"
    endpoint = "https://example.invalid"
    reasoning_effort = "high"

    def __init__(self, scene_shot_counts=(2, 3)):
        self.calls = []
        self.scene_shot_counts = scene_shot_counts

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
                shots = [shot for shot in episode_fixture("ep01", self.scene_shot_counts)["shots"]
                         if shot["scene_id"] == scene["id"]]
                for index, shot in enumerate(shots, 1):
                    shot["id"] = f"{scene['id']}_s{index:03}"
                return {"episode_id": "ep01", "scene_id": scene["id"],
                        "anchor_shot_id": shots[-1]["id"], "shots": shots}
        if name == "ep01_review_0":
            return {"issues": [{"severity": "error", "code": "story_clarity", "message": "Clarify the first beat."}]}
        if name == "ep01_local_0_v0":
            shot = {**payload["episode"]["shots"][0], "beat_zh": "新的线索显现", "duration": 2.5}
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
                self.assertNotIn("preferred_duration_seconds", brief)
                self.assertEqual(brief["maximum_requested_duration_seconds"], 9.4)
                self.assertEqual(brief["maximum_duration_seconds"], 10)
                for key in ("scenes_per_episode", "shots_per_scene", "shots_per_episode", "duration_per_shot"):
                    self.assertNotIn(key, brief)
                self.assertEqual(result["review_effort"], "high")

    def test_plan_ranges_are_estimates_independent_of_beat_count(self):
        episode = episode_fixture("ep01")
        season = season_fixture([episode])
        plan = plan_fixture(episode)
        self.assertEqual(check_long_plan(plan, season, season["episodes"][0]), [])
        plan["scenes"][1]["last_shot_number"] = 4
        self.assertEqual(check_long_plan(plan, season, season["episodes"][0]), [])
        for field, value, expected_code in [
            ("first_shot_number", 2, "scene_shot_range"),
            ("last_shot_number", 2, "scene_shot_range"),
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
        config = PipelineConfig(comfyui={"maximum_duration_seconds": 6})
        self.assertIn("h3_duration_limit", [issue["code"] for issue in check_episode(episode, season, config=config)])
        config.comfyui.maximum_duration_seconds = 12
        episode["shots"][0]["duration"] = 11.5
        self.assertEqual(check_scene(scene, plan["scenes"][0], plan, season, config=config), [])

    def test_dialogue_and_ongoing_actions_have_no_creative_quotas(self):
        episode = episode_fixture("ep01", (1,))
        season = season_fixture([episode])
        season["characters"] = [{"id": name, "wardrobes": [{"id": "outfit"}]} for name in ("speaker", "listener")]
        shot = episode["shots"][0]
        shot.update(duration=2, characters=[{"id": name, "wardrobe_id": "outfit"} for name in ("speaker", "listener")], dialogue=[{
            "speaker_id": "speaker", "voiceover": False, "delivery_en": "Natural measured Mandarin.",
            "text_zh": "我们已经核对了所有资料，明天上午一起去办公室确认最后的合同。",
        }, {"speaker_id": "listener", "voiceover": False, "delivery_en": "A quick reply.", "text_zh": "OK, 2 > 1."}],
            acceptance_en=[], action_complete=False, action_end_zh="手伸到一半", visual_en='The sign reads “合同”.')
        self.assertEqual(check_long_season([episode], season), [])
        self.assertEqual(check_episode(episode, season), [])
        shot["dialogue"][1]["speaker_id"] = "missing"
        self.assertIn("unknown_speaker", [issue["code"] for issue in check_episode(episode, season)])

    def test_variable_scene_counts_survive_assembly_repair_export_and_image_planning(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "novel.txt"
            source.write_text("测试原文", encoding="utf-8")
            client = ScriptClient(scene_shot_counts=(4, 1))
            result = asyncio.run(run_long(source, root / "run", client, episode_count=1))
            self.assertEqual(result["status"], "text_ready")
            episode = json.loads((root / "run/drafts/ep01.json").read_text())
            self.assertEqual([shot["id"] for shot in episode["shots"]], [f"ep01_s{i:03}" for i in range(1, 6)])
            self.assertEqual(episode["shots"][0]["duration"], 2.5)
            self.assertEqual([shot["scene_anchor_shot_id"] for shot in episode["shots"]], ["ep01_s004"] * 4 + ["ep01_s005"])
            plan = json.loads((root / "run/assembled_plans/ep01.json").read_text())
            self.assertEqual([(s["first_shot_number"], s["last_shot_number"]) for s in plan["scenes"]], [(1, 4), (5, 5)])
            image_plan = compile_plan(root / "run/delivery", ImageConfig())
            frames = [task for task in image_plan.tasks if task.asset_id.startswith("first-frame-")]
            self.assertEqual(len(frames), 5)
            anchor = "first-frame-ep01-ep01_s004"
            self.assertIn(anchor, next(task for task in frames if task.asset_id.endswith("s001")).reference_ids)
            self.assertNotIn(anchor, next(task for task in frames if task.asset_id == anchor).reference_ids)

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
