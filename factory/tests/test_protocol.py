import pytest
from pydantic import ValidationError

from conftest import edit_manifest, shot
from drama_factory.cluster.config import DEFAULT_ROUTES
from drama_factory.protocol import h3
from drama_factory.protocol.manifest import Manifest, license_issues, validate


def codes(issues, level="error"):
    return {issue.code for issue in issues if issue.level == level}


def test_h3_frame_alignment():
    assert [h3.align_frames(s) for s in (4, 5, 14.375, 15)] == [90, 124, 345, 362]
    assert h3.valid_frames(345) and not h3.valid_frames(346) and not h3.valid_frames(5)


def test_render_prompt_matches_existing_project_sections(manifest):
    text = h3.render_prompt(next(s for s in manifest.shots if s.shot_id == "S003").prompt)
    assert text.startswith("integrated_multimodal_description: ")
    assert "\n\noverall_soundscape: Glassware" in text and "Static two-shot, eye level." in text
    assert h3.dialogue_tags(text) == [("English", "My card is frozen again. Can you get this one?")]


def test_demo_manifest_is_clean(manifest):
    issues = validate(manifest, routes=DEFAULT_ROUTES)
    assert not codes(issues) and not codes(issues, "warning")


def test_unknown_fields_are_rejected(package_dir):
    with pytest.raises(ValidationError):
        edit_manifest(package_dir, lambda d: shot(d, "S001").update(shell="rm -rf /"))


@pytest.mark.parametrize("mutate, code", [
    (lambda d: shot(d, "S003").update(references=["lin_front"] * 10), "REF_LIMIT"),
    (lambda d: shot(d, "S003")["prompt"].update(camera="Match <Picture 4>."), "UNKNOWN_REF_TOKEN"),
    (lambda d: shot(d, "S003")["dialogue"][0].update(text="Different words."), "DIALOGUE_TAGS"),
    (lambda d: shot(d, "S002").update(first_frame=None), "MODE_INPUTS"),
    (lambda d: shot(d, "S001").update(references=["lin_front"]), "MODE_INPUTS"),
    (lambda d: shot(d, "S003")["dialogue"][0].update(speaker="lin_qiao_missing"), "SPEAKER_NOT_IN_SHOT"),
    (lambda d: shot(d, "S001").update(depends_on=["S005"]), "DEPENDENCY_CYCLE"),
    (lambda d: shot(d, "S001").update(frames=100), "BAD_FRAMES"),
    (lambda d: d["assets"][0].update(path="../escape.png"), "BAD_PATH"),
    (lambda d: shot(d, "S004").update(duration_s=20, frames=481), "TOO_LONG"),
    (lambda d: shot(d, "S002").update(route="fast_ref"), "ROUTE_MODE"),
])
def test_semantic_errors(package_dir, mutate, code):
    if code == "DEPENDENCY_CYCLE":
        edit_manifest(package_dir, lambda d: shot(d, "S005").update(depends_on=["S001"]))
    manifest = edit_manifest(package_dir, mutate)
    assert code in codes(validate(manifest, routes=DEFAULT_ROUTES))


def test_unused_reference_is_a_warning(package_dir):
    manifest = edit_manifest(package_dir, lambda d: shot(d, "S003")["references"].append("card_prop"))
    issues = validate(manifest)
    assert not codes(issues) and "UNUSED_REFERENCE" in codes(issues, "warning")


def test_file_kinds_are_cross_checked(manifest):
    kinds = {a.path: a.kind for a in manifest.assets}
    kinds["characters/lin_qiao/voice.wav"] = "image"
    del kinds["props/card.png"]
    assert {"KIND_MISMATCH", "MISSING_FILE"} <= codes(validate(manifest, kinds))


def test_license_regions(manifest):
    restricted = ["US", "EU", "UK", "KR"]
    assert not license_issues(manifest, restricted, [])
    global_release = manifest.model_copy(update={"distribution": manifest.distribution.model_copy(
        update={"regions": ["CN", "Global"]})})
    assert license_issues(global_release, restricted, [])[0].code == "LICENSE_REGION"
    us_release = manifest.model_copy(update={"distribution": manifest.distribution.model_copy(update={"regions": ["us"]})})
    assert license_issues(us_release, restricted, [])
    assert not license_issues(us_release, restricted, ["US"])


def test_schema_export_is_json_schema():
    schema = Manifest.model_json_schema()
    assert schema["title"] == "Manifest" and "shots" in schema["required"]
