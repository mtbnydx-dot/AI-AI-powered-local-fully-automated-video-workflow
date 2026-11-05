"""Scene-sized writing calls for a configurable long-form adaptation."""

from __future__ import annotations

import asyncio
import math
import re
from datetime import datetime, timezone
from pathlib import Path

from .export import export_season
from .checks import check_episode
from .llm import GLMClient, write_json
from .pipeline import compact_episode, merge_episode_repair, prompt, read_json
from .long_checks import check_long_plan, check_long_season, check_scene


async def collect(tasks):
    """Let independent calls save their work even when another call fails."""
    results = await asyncio.gather(*tasks, return_exceptions=True)
    errors = [result for result in results if isinstance(result, BaseException)]
    if errors:
        raise RuntimeError("; ".join(str(error) for error in errors))
    return results


def story_evidence(episode: dict) -> dict:
    """A season reviewer needs every beat, but not repeated English art direction."""
    fields = ("id", "scene_id", "location_id", "characters", "prop_ids", "duration",
              "beat_zh", "action_end_zh", "dialogue")
    return {**{key: value for key, value in episode.items() if key != "shots"},
            "shots": [{key: shot[key] for key in fields if key in shot} for shot in episode["shots"]]}


async def run_long(source_path: Path, out: Path, client: GLMClient, *,
                   episode_count: int = 8, episode_minutes: float = 5, repair_rounds: int = 2,
                   production_config: Path | None = None, notes_path: Path | None = None,
                   delivery: Path | None = None, stop_after: str | None = None,
                   review_reasoning_effort: str | None = None) -> dict:
    if episode_count < 1:
        raise ValueError("episode_count must be positive")
    if not math.isfinite(episode_minutes) or episode_minutes <= 0:
        raise ValueError("episode_minutes must be positive and finite")
    review_reasoning_effort = review_reasoning_effort or client.reasoning_effort
    out.mkdir(parents=True, exist_ok=True)
    delivery = delivery or out / "delivery"
    brief = {
        "fresh_creation": "Create a new long-form adaptation directly from the original novel. No old season plan or shot text is supplied or to be reused.",
        "episode_count": episode_count, "requested_episode_minutes": episode_minutes,
        "scene_count_policy": "Choose scenes from source-supported changes in physical location or continuous time block.",
        "shot_count_policy": "Choose shot counts from dramatic progression and motivated camera coverage.",
        "preferred_duration_seconds": 5, "maximum_duration_seconds": 10,
        "maximum_requested_duration_seconds": 9.4,
        "duration_rule": "Choose each duration from speech, action and reaction. About five seconds is a preference, not a fixed duration. Requests up to 9.4 seconds keep H3 frame rounding within ten seconds at 24fps.",
        "target_episode_duration_seconds": episode_minutes * 60,
        "target_season_duration_seconds": episode_count * episode_minutes * 60,
        "runtime_policy": "The requested episode runtime is a target; source-supported events and natural pacing determine the actual runtime, scene count and shot count.",
        "format": "Naturalistic vertical drama with the source's setting and editor-approved style; portrait 576x1024, 24fps",
        "language": "Chinese script and exact Mandarin dialogue; English production, audio and image-slot descriptions",
        "scope": "Text and planned image slots only. No images/audio/video generation.",
        "dramaturgy": "Every scene advances a specific conflict, discovery, choice or consequence. No repeated recaps, padded silent holds, or extending durations to reach the running time.",
        "action_rule": "One complete small action or complete speaking turn per shot. Never split a reach, walk, handover or other physical action across shots. A later settled response may be a new shot.",
        "source_rule": "Preserve the complete source causal chain and ending; expand source-supported choices and follow explicit adaptation decisions in editor notes.",
    }
    state_file = out / "run.json"
    state = {"source": str(source_path.resolve()), "model": client.model,
             "endpoint": client.endpoint, "mode": "long_form", "brief": brief,
             "planning_effort": client.reasoning_effort, "writing_effort": client.reasoning_effort,
             "review_effort": review_reasoning_effort,
             "delivery": str(delivery.resolve()), "status": "running",
             "started_at": datetime.now(timezone.utc).isoformat()}
    if state_file.exists():
        previous = read_json(state_file)
        if previous.get("source") != state["source"] or previous.get("brief") != brief:
            raise ValueError("Use a new output directory for a different source or duration plan")
        state["started_at"] = previous["started_at"]
    write_json(state_file, state)
    original = source_path.read_text(encoding="utf-8")
    source = re.sub(r"\.mw-parser-output[^{}]*\{[^{}]*\}", "", original)
    (out / "source.cleaned.txt").write_text(source, encoding="utf-8")
    notes = notes_path.read_text(encoding="utf-8") if notes_path else ""
    common = {"brief": brief, "source_text": source, "editor_notes": notes}
    write_json(out / "creative_brief.json", brief)

    def pause(stage):
        if stop_after != stage:
            return False
        state.update(status=f"{stage}_complete", stage=stage)
        write_json(state_file, state)
        client.event("stage_complete", stage=stage)
        return True

    async def checked(name, template, payload, destination, validate, *, use_cache=True):
        if use_cache and destination.exists():
            cached = read_json(destination)
            if not any(issue["severity"] == "error" for issue in validate(cached)):
                return cached
        feedback = []
        candidate = None
        for attempt in range(3):
            data = {**payload}
            if feedback:
                data.update(previous_invalid_result=candidate, required_corrections=feedback)
            candidate = await client.complete(f"{name}_v{attempt}", prompt(template), data)
            issues = validate(candidate)
            write_json(out / "checks" / f"{name}_v{attempt}.json", issues)
            feedback = [issue for issue in issues if issue["severity"] == "error"]
            if not feedback:
                write_json(destination, candidate)
                return candidate
            write_json(out / "rejected" / f"{name}_v{attempt}.json", candidate)
        raise ValueError(f"{name} still has {len(feedback)} concrete structural errors")

    async def reviewed(name, template, payload, destination):
        input_path = out / "review_inputs" / destination.name
        if destination.exists() and input_path.exists() and read_json(input_path) == payload:
            return read_json(destination)
        review = await client.complete(name, prompt(template), payload,
                                       temperature=0.3, reasoning_effort=review_reasoning_effort)
        write_json(destination, review)
        write_json(input_path, payload)
        return review

    try:
        season_path = out / "season.json"
        if season_path.exists():
            season = read_json(season_path)
        else:
            season = await client.complete("long_season", prompt("long_season_plan_zh"), common)
            write_json(season_path, season)
        plans = season["episodes"]
        if [item["id"] for item in plans] != [f"ep{i:02}" for i in range(1, episode_count + 1)]:
            raise ValueError("Season must contain exactly the requested ordered episode IDs")
        if pause("season"):
            return state

        async def plan_episode(plan):
            def validate(candidate):
                issues = check_long_plan(candidate, season, plan)
                for field in ("title", "synopsis", "start_state", "end_state"):
                    if not isinstance(candidate.get(field), str) or not candidate[field].strip():
                        issues.append({"severity": "error", "code": "missing_episode_field",
                                       "episode_id": plan["id"], "shot_id": "",
                                       "message": f"Episode plan requires nonempty {field}.",
                                       "suggested_fix": f"Supply {field} before generating scenes."})
                return issues
            return await checked(f"{plan['id']}_plan", "long_episode_plan_zh",
                                 {**common, "season": season, "episode_plan": plan},
                                 out / "plans" / f"{plan['id']}.json",
                                 validate)

        detailed_plans = await collect([plan_episode(plan) for plan in plans])
        if pause("plans"):
            return state

        async def write_scene(plan, scene_plan):
            return await checked(f"{plan['id']}_{scene_plan['id']}", "long_scene_write_zh",
                                 {**common, "season": season, "episode_plan": plan,
                                  "scene_plan": scene_plan},
                                 out / "scenes" / plan["id"] / f"{scene_plan['id']}.json",
                                 lambda candidate: check_scene(candidate, scene_plan, plan, season))

        pending = [write_scene(plan, scene_plan) for plan in detailed_plans
                   if not (out / "drafts" / f"{plan['id']}.json").exists()
                   for scene_plan in plan["scenes"]]
        await collect(pending)
        episodes = []
        for plan in detailed_plans:
            destination = out / "drafts" / f"{plan['id']}.json"
            if destination.exists():
                episode = read_json(destination)
            else:
                episode = {key: plan[key] for key in ("id", "title", "synopsis", "start_state", "end_state")}
                episode["shots"] = [shot for scene_plan in plan["scenes"]
                                    for shot in read_json(out / "scenes" / plan["id"] / f"{scene_plan['id']}.json")["shots"]]
                write_json(destination, episode)
            episodes.append(episode)
        write_json(out / "checks" / "assembled.json", check_long_season(episodes, season))
        if pause("drafts"):
            return state

        async def repair(episode, issues, label):
            plan = next(item for item in detailed_plans if item["id"] == episode["id"])

            def validate(patch):
                try:
                    candidate = merge_episode_repair(episode, patch)
                except (KeyError, TypeError, ValueError) as exc:
                    return [{"severity": "error", "code": "invalid_repair", "episode_id": episode["id"],
                             "shot_id": "", "message": str(exc),
                             "suggested_fix": "Return complete replacements for existing shot IDs without changing their order."}]
                result = []
                for scene_plan in plan["scenes"]:
                    scene = {"episode_id": episode["id"], "scene_id": scene_plan["id"],
                             "shots": candidate["shots"][scene_plan["first_shot_number"] - 1:scene_plan["last_shot_number"]]}
                    result.extend(check_scene(scene, scene_plan, plan, season))
                return result

            patch = await checked(f"{episode['id']}_{label}", "long_shot_repair_zh",
                                  {**common, "season": season, "episode": episode,
                                   "episode_plan": plan, "issues": issues, "adjacent_episode_plans": plans},
                                  out / "patches" / f"{episode['id']}_{label}.json", validate,
                                  use_cache=False)
            repaired = merge_episode_repair(episode, patch)
            write_json(out / "drafts" / f"{episode['id']}.before_{label}.json", episode)
            write_json(out / "drafts" / f"{episode['id']}.json", repaired)
            return repaired

        async def review_episode(episode):
            plan = next(item for item in detailed_plans if item["id"] == episode["id"])
            for cycle in range(repair_rounds + 1):
                review_path = out / "reviews" / f"{episode['id']}_{cycle}.json"
                review = await reviewed(f"{episode['id']}_review_{cycle}", "long_episode_review_zh",
                                        {**common, "season": season, "episode_plan": plan,
                                         "episode": compact_episode(episode)}, review_path)
                issues = [{**i, "episode_id": episode["id"]} for i in check_episode(
                    episode, season, maximum_duration_seconds=10, maximum_dialogue_characters=None)
                          if not (i.get("code") == "shot_count" and i["severity"] == "warning")]
                issues.extend({**issue, "episode_id": episode["id"]} for issue in review.get("issues", []))
                errors = [issue for issue in issues if issue["severity"] == "error"]
                if not errors or cycle == repair_rounds:
                    return episode, {"episode_id": episode["id"], "review": review, "issues": issues}
                episode = await repair(episode, errors, f"local_{cycle}")

        local_results = await collect([review_episode(episode) for episode in episodes])
        episodes = [result[0] for result in local_results]
        local_reviews = [result[1] for result in local_results]
        write_json(out / "episode_reviews.json", local_reviews)
        season_review = {}
        for cycle in range(repair_rounds + 1):
            review_path = out / "reviews" / f"season_{cycle}.json"
            season_review = await reviewed(f"season_review_{cycle}", "long_season_review_zh",
                                           {**common, "season": season,
                                            "episodes": [story_evidence(episode) for episode in episodes]}, review_path)
            errors = [issue for issue in season_review.get("issues", []) if issue["severity"] == "error"]
            if not errors or cycle == repair_rounds:
                break
            async def repair_global(episode):
                relevant = [issue for issue in errors if issue.get("episode_id") in ("", episode["id"])]
                if not relevant:
                    return episode
                return await repair(episode, relevant, f"global_{cycle}")
            previous_episodes = {episode["id"]: episode for episode in episodes}
            episodes = await collect([repair_global(episode) for episode in episodes])
            changed_reviews = await collect([review_episode(episode) for episode in episodes
                                            if episode != previous_episodes[episode["id"]]])
            revised_episodes = {episode["id"]: episode for episode, _ in changed_reviews}
            revised_reviews = {review["episode_id"]: review for _, review in changed_reviews}
            episodes = [revised_episodes.get(episode["id"], episode) for episode in episodes]
            local_reviews = [revised_reviews.get(review["episode_id"], review) for review in local_reviews]
            write_json(out / "episode_reviews.json", local_reviews)

        checks = check_long_season(episodes, season)
        quality = {"status": "text_complete_images_pending", "structural_issues": checks,
                   "episode_reviews": local_reviews, "model_review": season_review,
                   "open_errors": [i for i in checks + season_review.get("issues", [])
                                   + [i for review in local_reviews for i in review["issues"]]
                                   if i["severity"] == "error"],
                   "scope": "Actual text/package checks only; no images, audio or video generated."}
        write_json(out / "quality.json", quality)
        if pause("review"):
            return state
        scene_plans = {scene["id"]: scene for plan in detailed_plans for scene in plan["scenes"]}
        for episode in episodes:
            for shot in episode["shots"]:
                scene = scene_plans[shot["scene_id"]]
                if scene.get("continuity_en"):
                    shot["continuity_en"] = scene["continuity_en"]
                if "anchor_shot_number" in scene:
                    shot["scene_anchor_shot_id"] = f"{episode['id']}_s{scene['anchor_shot_number']:03}"
        summary = export_season(season, episodes, delivery, production_config or Path("configs/b200.yaml"))
        write_json(delivery / "quality.json", quality)
        if quality["open_errors"]:
            summary["status"] = "needs_text_revision_images_pending"
            write_json(delivery / "overview.json", summary)
        state.update(status="needs_text_revision" if quality["open_errors"] else "text_ready",
                     finished_at=datetime.now(timezone.utc).isoformat(), summary=summary)
        write_json(state_file, state)
        client.event("export_finished", status=state["status"], output=str(delivery))
        return state
    except Exception as exc:
        state.update(status="failed", error=str(exc).replace(client.key, "[REDACTED]"))
        write_json(state_file, state)
        raise
