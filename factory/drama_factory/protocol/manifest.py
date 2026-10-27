"""Episode Production Package manifest (epp/1): schema plus the semantic checks run before import."""
from collections import Counter
from dataclasses import dataclass
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from . import h3

Id = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")]
Route = Literal["vdn", "fast_ref", "native_ref"]
ROUTES: tuple[str, ...] = ("vdn", "fast_ref", "native_ref")  # escalation ladder, cheapest first
Mode = Literal["t2va", "fl2va", "ref2va"]
MediaKind = Literal["image", "video", "audio"]
AssetRole = Literal["face_front", "face_profile", "full_body", "costume", "expression", "voice",
                    "scene", "prop", "keyframe", "motion", "style"]
RepairAction = Literal["retry_seed", "revise_prompt", "add_references", "switch_route", "upgrade_mode",
                       "propose_split", "escalate"]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Asset(Strict):
    asset_id: Id
    path: str
    kind: MediaKind
    role: AssetRole
    owner: Id | None = None
    locked: bool = True  # approved in the studio; only locked assets may be added by automatic repair
    note: str = ""


class Character(Strict):
    character_id: Id
    name: str
    appearance: str
    costume: str = ""
    voice: str = ""
    identity_assets: list[Id] = []
    voice_asset: Id | None = None


class Scene(Strict):
    scene_id: Id
    description: str
    assets: list[Id] = []


class DialogueLine(Strict):
    speaker: Id
    language: str
    text: str


class ShotPrompt(Strict):
    description: str
    action: str = ""
    camera: str = ""
    constraints: list[str] = []
    soundscape: str = ""
    music: str = ""


class ShotReview(Strict):
    l3: bool | None = None
    human_final_check: bool | None = None
    min_identity_similarity: float | None = None
    max_dialogue_cer: float | None = None


class Shot(Strict):
    shot_id: Id
    order: int
    duration_s: float = Field(gt=0, le=30)
    frames: int | None = None
    aspect_ratio: Literal["9:16", "16:9", "1:1"] = "9:16"
    mode: Mode
    characters: list[Id] = []
    scene_id: Id | None = None
    importance: Literal["normal", "key"] = "normal"
    motion: Literal["static", "low", "medium", "high"] = "low"
    interaction: bool = False
    dialogue: list[DialogueLine] = []
    dialogue_precision: Literal["none", "loose", "exact"] = "loose"
    prompt: ShotPrompt
    first_frame: Id | None = None
    last_frame: Id | None = None
    references: list[Id] = []
    props: list[Id] = []
    route: Route | Literal["auto"] = "auto"
    priority: int = 0
    depends_on: list[Id] = []
    review: ShotReview = Field(default_factory=ShotReview)
    max_creative_retries: int | None = Field(default=None, ge=0)


class Distribution(Strict):
    regions: list[str] = Field(min_length=1)
    platforms: list[str] = []


class ReviewPolicy(Strict):
    l1: bool = True
    asr_for_dialogue: bool = True
    identity_check: bool = True
    l3: Literal["never", "key_shots", "uncertain", "always"] = "key_shots"
    l3_sample_rate: float = Field(default=0.0, ge=0, le=1)
    human_final_check: Literal["none", "key_shots", "all"] = "key_shots"
    candidate_selection: Literal["first_pass", "best_score"] = "first_pass"
    duration_tolerance_s: float = 0.35
    max_black_ratio: float = 0.05
    max_freeze_s: float = 1.0
    min_identity_similarity: float = 0.55
    max_dialogue_cer: float = 0.15
    tolerate: list[str] = []


class Budget(Strict):
    max_rounds: int = Field(ge=1, le=10)
    candidates_per_round: int = Field(default=1, ge=1, le=8)
    max_takes: int = Field(ge=1, le=40)
    max_technical_attempts: int = Field(default=3, ge=1, le=10)


class RetryPolicy(Strict):
    simple: Budget = Field(default_factory=lambda: Budget(max_rounds=2, candidates_per_round=2, max_takes=4))
    normal: Budget = Field(default_factory=lambda: Budget(max_rounds=3, max_takes=4))
    hard: Budget = Field(default_factory=lambda: Budget(max_rounds=4, max_takes=5))


class AutomationPolicy(Strict):
    director: Literal["rules", "llm"] = "rules"
    allowed_actions: list[RepairAction] = ["retry_seed", "revise_prompt", "add_references", "switch_route",
                                           "upgrade_mode", "propose_split", "escalate"]
    editable_prompt_fields: list[Literal["action", "camera", "constraints"]] = ["action", "camera", "constraints"]
    max_route: Route = "native_ref"


class Manifest(Strict):
    schema_version: Literal["epp/1"] = "epp/1"
    package_id: Id
    project_id: Id
    episode_id: Id
    version: int = Field(ge=1)
    title: str = ""
    created_by: str
    created_at: str
    distribution: Distribution
    negative_prompt: str = ""
    characters: list[Character] = []
    scenes: list[Scene] = []
    assets: list[Asset] = []
    shots: list[Shot] = Field(min_length=1)
    review_policy: ReviewPolicy = Field(default_factory=ReviewPolicy)
    retry_policy: RetryPolicy = Field(default_factory=RetryPolicy)
    automation: AutomationPolicy = Field(default_factory=AutomationPolicy)


class RouteCaps(Strict):
    """What a generation pool can do; comes from site config, not from packages."""
    modes: list[Mode]
    max_frames: int = 345


@dataclass(frozen=True)
class Issue:
    level: Literal["error", "warning"]
    code: str
    where: str
    message: str

    def __str__(self) -> str:
        return f"[{self.level}] {self.code} @ {self.where}: {self.message}"


def safe_relpath(path: str) -> bool:
    if not path or "\\" in path or path.startswith("/") or ":" in path:
        return False
    return all(part not in ("", ".", "..") for part in path.split("/"))


def frames_of(shot: Shot) -> int:
    return shot.frames or h3.align_frames(shot.duration_s)


def validate(manifest: Manifest, file_kinds: dict[str, str] | None = None,
             routes: dict[str, RouteCaps] | None = None) -> list[Issue]:
    """Cross-reference checks the schema cannot express. file_kinds maps package paths to sniffed media kind."""
    issues: list[Issue] = []

    def err(code: str, where: str, message: str) -> None:
        issues.append(Issue("error", code, where, message))

    for label, ids in (("asset", [a.asset_id for a in manifest.assets]),
                       ("character", [c.character_id for c in manifest.characters]),
                       ("scene", [s.scene_id for s in manifest.scenes]),
                       ("shot", [s.shot_id for s in manifest.shots])):
        duplicates = sorted(i for i, n in Counter(ids).items() if n > 1)
        if duplicates:
            err("DUPLICATE_ID", label, f"重复的 {label} ID：{', '.join(duplicates)}")
    orders = Counter(s.order for s in manifest.shots)
    if any(n > 1 for n in orders.values()):
        err("DUPLICATE_ORDER", "shots", "镜头 order 不能重复")

    assets = {a.asset_id: a for a in manifest.assets}
    for asset in manifest.assets:
        where = f"assets[{asset.asset_id}]"
        if not safe_relpath(asset.path):
            err("BAD_PATH", where, f"路径必须是包内相对路径：{asset.path}")
        elif file_kinds is not None:
            actual = file_kinds.get(asset.path)
            if actual is None:
                err("MISSING_FILE", where, f"包内不存在 {asset.path}")
            elif actual != asset.kind:
                err("KIND_MISMATCH", where, f"{asset.path} 内容是 {actual}，声明为 {asset.kind}")

    def need(where: str, asset_id: str, kind: str | None = None) -> str | None:
        asset = assets.get(asset_id)
        if asset is None:
            err("UNKNOWN_ASSET", where, f"未定义素材 {asset_id}")
            return None
        if kind and asset.kind != kind:
            err("KIND_MISMATCH", where, f"{asset_id} 需要 {kind}，实际是 {asset.kind}")
        return asset.kind

    for character in manifest.characters:
        where = f"characters[{character.character_id}]"
        for asset_id in character.identity_assets:
            need(f"{where}.identity_assets", asset_id, "image")
        if character.voice_asset:
            need(f"{where}.voice_asset", character.voice_asset, "audio")
    for scene in manifest.scenes:
        for asset_id in scene.assets:
            need(f"scenes[{scene.scene_id}].assets", asset_id)

    characters = {c.character_id for c in manifest.characters}
    scenes = {s.scene_id for s in manifest.scenes}
    shots = {s.shot_id: s for s in manifest.shots}
    for shot in manifest.shots:
        where = f"shots[{shot.shot_id}]"
        for character_id in shot.characters:
            if character_id not in characters:
                err("UNKNOWN_CHARACTER", where, f"未定义角色 {character_id}")
        if shot.scene_id and shot.scene_id not in scenes:
            err("UNKNOWN_SCENE", where, f"未定义场景 {shot.scene_id}")
        for line in shot.dialogue:
            if line.speaker not in shot.characters:
                err("SPEAKER_NOT_IN_SHOT", where, f"说话人 {line.speaker} 不在本镜头角色列表中")
        if shot.frames is not None:
            if not h3.valid_frames(shot.frames):
                err("BAD_FRAMES", where, f"H3 帧数须为 17n+5（如 73、121、193），当前 {shot.frames}")
            elif abs(shot.frames / h3.FPS - shot.duration_s) > h3.FRAME_STEP / h3.FPS:
                err("FRAMES_DURATION_MISMATCH", where, f"{shot.frames} 帧与时长 {shot.duration_s}s 不符")

        if shot.mode == "t2va" and (shot.first_frame or shot.last_frame or shot.references):
            err("MODE_INPUTS", where, "T2VA 不接受首尾帧或参考素材")
        if shot.mode == "fl2va":
            if not shot.first_frame:
                err("MODE_INPUTS", where, "FL2VA 需要首帧")
            if shot.references:
                err("MODE_INPUTS", where, "FL2VA 不接受参考素材；需要角色参考请改用 ref2va")
        if shot.mode == "ref2va":
            if not shot.references:
                err("MODE_INPUTS", where, "Ref2VA 至少需要一个参考素材")
            if shot.first_frame or shot.last_frame:
                err("MODE_INPUTS", where, "Ref2VA 不使用首尾帧；把构图图作为 <Picture n> 参考")
        for field in ("first_frame", "last_frame"):
            if getattr(shot, field):
                need(f"{where}.{field}", getattr(shot, field), "image")
        for asset_id in shot.props:
            need(f"{where}.props", asset_id)

        kinds = [kind for asset_id in shot.references if (kind := need(f"{where}.references", asset_id))]
        counts = Counter(kinds)
        for kind in ("image", "video", "audio"):
            if counts[kind] > h3.REF_LIMITS[kind]:
                err("REF_LIMIT", where, f"{kind} 参考最多 {h3.REF_LIMITS[kind]} 个，当前 {counts[kind]} 个")
        if len(kinds) > h3.REF_LIMITS["total"]:
            err("REF_LIMIT", where, f"混合参考最多 {h3.REF_LIMITS['total']} 个，当前 {len(kinds)} 个")

        texts = h3.prompt_texts(shot.prompt)
        used = set().union(*(h3.ref_tokens(text) for text in texts))
        available = set(h3.ref_slots(kinds))
        for kind, number in sorted(used - available):
            err("UNKNOWN_REF_TOKEN", where, f"提示词引用了 <{kind} {number}>，参考列表里没有对应素材")
        for kind, number in sorted(available - used):
            issues.append(Issue("warning", "UNUSED_REFERENCE", where, f"<{kind} {number}> 未在提示词中说明用途"))
        tags = [tag for text in texts for tag in h3.dialogue_tags(text)]
        declared = [(line.language.strip(), h3.normalize(line.text)) for line in shot.dialogue]
        if tags != declared:
            err("DIALOGUE_TAGS", where, "提示词中的 <d>[语言] 台词</d> 必须与 dialogue 列表逐句、按顺序一致")

        if routes is not None:
            frames = frames_of(shot)
            able = [r for r, caps in routes.items() if shot.mode in caps.modes]
            if not able:
                err("NO_ROUTE", where, f"站点没有支持 {shot.mode} 的生成池")
            elif frames > max(routes[r].max_frames for r in able):
                err("TOO_LONG", where, f"{frames} 帧超过站点 {shot.mode} 上限；请拆成更短的镜头")
            if shot.route != "auto" and (shot.route not in routes or shot.mode not in routes[shot.route].modes):
                err("ROUTE_MODE", where, f"生成池 {shot.route} 不支持 {shot.mode}")

        for dependency in shot.depends_on:
            if dependency not in shots or dependency == shot.shot_id:
                err("UNKNOWN_DEPENDENCY", where, f"依赖的镜头 {dependency} 不存在")

    visiting: set[str] = set()
    done: set[str] = set()

    def visit(shot_id: str) -> bool:
        if shot_id in done or shot_id not in shots:
            return False
        if shot_id in visiting:
            return True
        visiting.add(shot_id)
        cyclic = any(visit(d) for d in shots[shot_id].depends_on)
        visiting.discard(shot_id)
        done.add(shot_id)
        return cyclic

    if any(visit(shot_id) for shot_id in shots):
        err("DEPENDENCY_CYCLE", "shots", "镜头依赖存在环")
    return issues


def license_issues(manifest: Manifest, restricted: list[str], authorized: list[str]) -> list[Issue]:
    """H3 community license excludes some regions; publishing there needs a separate, recorded grant."""
    open_restricted = {r.upper() for r in restricted} - {r.upper() for r in authorized}
    regions = {r.upper() for r in manifest.distribution.regions}
    if regions & {"GLOBAL", "WORLDWIDE", "ALL"}:
        regions |= open_restricted
    blocked = sorted(regions & open_restricted)
    if not blocked:
        return []
    return [Issue("error", "LICENSE_REGION", "distribution.regions",
                  f"H3 社区许可不覆盖 {', '.join(blocked)}；需另行取得授权并登记到站点配置 license.authorized_regions")]
