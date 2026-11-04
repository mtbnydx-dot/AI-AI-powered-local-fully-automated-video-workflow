import asyncio
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from imagepipe.models import ImageConfig
from imagepipe.pipeline import run as run_images
from imagepipe.plan import compile_plan
from scriptpipe.batch import export_batch
from scriptpipe.export import export_season
from videopipe.inputs import load_package


def season_fixture():
    season = {
        "title": "连续剧测试", "logline": "同一人物跨集进入新场景。", "style_en": "Naturalistic drama.",
        "characters": [{
            "id": character, "name": name, "appearance_en": "An adult with short black hair.",
            "voice_en": "A calm Mandarin voice.",
            "wardrobes": [{"id": "work", "description_en": "A navy cotton shirt and black trousers."}],
        } for character, name in [("hero", "主角"), ("later_guest", "后续人物")]],
        "locations": [{"id": location, "name": name, "description_en": description} for location, name, description in [
            ("office", "办公室", "An empty office with a desk beside the west window."),
            ("garden", "花园", "An empty garden with a stone path leading north."),
        ]],
        "props": [], "episodes": [],
    }
    episodes = []
    for number in range(1, 4):
        episode_id = f"ep{number:02}"
        episode = {"id": episode_id, "title": f"第{number}集", "synopsis": "主角观察环境。",
                   "start_state": "来到现场", "end_state": "看清环境"}
        season["episodes"].append({**episode, "summary": episode["synopsis"]})
        episode["shots"] = [{
            "id": f"{episode_id}_s001", "scene_id": f"{episode_id}_scene",
            "location_id": "garden" if number == 3 else "office", "time_of_day": "day",
            "characters": [{"id": character, "wardrobe_id": "work"}
                           for character in (["hero", "later_guest"] if number == 3 else ["hero"])],
            "prop_ids": [], "duration": 5.875, "dialogue": [],
            "beat_zh": "观察环境", "action_start_zh": "抬头", "action_end_zh": "目光停住",
            "action_complete": True, "cut_reason_zh": "完成观察",
            "visual_en": "The adult looks around and settles their gaze.", "camera_en": "Locked wide shot.",
            "soundscape_en": "Quiet ambient sound.", "first_frame_prompt_en": "The adult stands still before looking up.",
            "acceptance_en": ["The adult completes the glance."],
        }]
        episodes.append(episode)
    return season, episodes


class ScriptBatchTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.canonical = self.root / "full-season"
        config_path = self.root / "production.yaml"
        config_path.write_text("generator: cpu\n", encoding="utf-8")
        self.season, episodes = season_fixture()
        export_season(self.season, episodes, self.canonical, config_path)
        env_path = self.root / ".env"
        env_path.write_text("SCRIPT_BATCH_TEST_KEY=local-test-key\n", encoding="utf-8")
        self.image_config = ImageConfig.model_validate({
            "api": {"base_url": "http://127.0.0.1:1", "env_file": str(env_path),
                    "key_name": "SCRIPT_BATCH_TEST_KEY", "request_timeout_seconds": 1},
            "scheduler": {"max_attempts": 1},
        })

    def supply_images_and_export(self, episode_ids, output):
        plan = compile_plan(self.canonical, self.image_config, episode_ids)
        for task in plan.tasks:
            target = Path(task.target_path)
            if not target.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                Image.new("RGB", (18, 32), (task.seed % 255, 80, 120)).save(target)
        with contextlib.redirect_stdout(io.StringIO()):
            report = asyncio.run(run_images(plan, self.image_config, self.root / f"images-{episode_ids[0]}"))
        self.assertTrue(report["success"])
        self.assertEqual(report["generated"], 0)
        self.assertEqual(report["reused"], len(plan.tasks))
        result = export_batch(self.canonical, output, episode_ids, [task.asset_id for task in plan.tasks])
        return plan, result

    def test_first_batch_is_portable_and_preserves_canonical_ids_seeds_and_images(self):
        output = self.root / "delivery-ep01-ep02"
        plan, summary = self.supply_images_and_export(["ep01", "ep02"], output)
        self.assertEqual([episode["id"] for episode in summary["episodes"]], ["ep01", "ep02"])
        self.assertTrue(summary["render_ready"])
        self.assertFalse((output / "episodes/ep03").exists())
        manifest = json.loads((output / "image_slots.json").read_text(encoding="utf-8"))
        self.assertEqual({slot["asset_id"] for slot in manifest["shared_assets"]}, {
            "identity-hero", "character-hero-work", "location-office",
        })
        self.assertFalse((self.canonical / "assets/review/characters/identity-later_guest.png").exists())
        self.assertEqual((output / "season.json").read_bytes(), (self.canonical / "season.json").read_bytes())
        self.assertEqual(len(json.loads((output / "season.json").read_text())["episodes"]), 3)
        full_plan = compile_plan(self.canonical, self.image_config)
        full_tasks = {task.asset_id: task for task in full_plan.tasks}
        for task in plan.tasks:
            self.assertEqual(task.seed, full_tasks[task.asset_id].seed)
            self.assertEqual(task.prompt, full_tasks[task.asset_id].prompt)
        for episode in summary["episodes"]:
            relative = Path(episode["package"])
            self.assertEqual((output / relative).read_bytes(), (self.canonical / relative).read_bytes())
            package = load_package(output / relative)
            for shot in package.shots:
                for asset in shot.asset_bindings:
                    self.assertTrue(asset.path.is_relative_to(output))
                    self.assertEqual(asset.path.read_bytes(), (self.canonical / asset.path.relative_to(output)).read_bytes())
        for slot in manifest["shared_assets"] + manifest["first_frames"]:
            self.assertEqual((output / slot["path"]).read_bytes(), (self.canonical / slot["path"]).read_bytes())

    def test_later_batch_reuses_the_same_identity_and_keeps_global_episode_numbering(self):
        early = self.root / "delivery-ep01-ep02"
        early_plan, _ = self.supply_images_and_export(["ep01", "ep02"], early)
        identity = self.canonical / "assets/review/characters/identity-hero.png"
        original_bytes, original_mtime = identity.read_bytes(), identity.stat().st_mtime_ns
        later = self.root / "delivery-ep03"
        later_plan, summary = self.supply_images_and_export(["ep03"], later)
        self.assertEqual(identity.stat().st_mtime_ns, original_mtime)
        self.assertEqual(identity.read_bytes(), original_bytes)
        self.assertEqual((later / identity.relative_to(self.canonical)).read_bytes(), original_bytes)
        early_identity = next(task for task in early_plan.tasks if task.asset_id == "identity-hero")
        later_identity = next(task for task in later_plan.tasks if task.asset_id == "identity-hero")
        self.assertEqual(early_identity, later_identity)
        state = json.loads((self.root / "images-ep03/state.json").read_text())
        self.assertTrue(state["tasks"]["identity-hero"]["reused"])
        self.assertEqual(state["tasks"]["identity-hero"]["attempts"], 0)
        self.assertEqual([episode["id"] for episode in summary["episodes"]], ["ep03"])
        package = load_package(later / summary["episodes"][0]["package"])
        self.assertEqual([(shot.id, shot.seed) for shot in package.shots], [("ep03_s001", 3001)])
        self.assertEqual((later / "season.json").read_bytes(), (early / "season.json").read_bytes())
        self.assertFalse((later / "episodes/ep01").exists())


if __name__ == "__main__":
    unittest.main()
