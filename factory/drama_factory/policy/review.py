"""Review reports (L0-L3), machine-check rules and the per-take verdict.

Reviewers measure and describe. Thresholds from the package policy are applied here, centrally,
so a take is judged the same way whichever node or workstation reviewed it.
"""
import hashlib
from typing import Literal

from pydantic import BaseModel, Field

from ..protocol import h3
from ..protocol.manifest import ReviewPolicy, Shot
from . import taxonomy

Level = Literal["L0", "L1", "L2.asr", "L2.identity", "L3"]
LEVEL_POOLS = {"L1": "review.l1", "L2.asr": "review.asr", "L2.identity": "review.identity", "L3": "review.l3"}
VISUAL = frozenset({"L1", "L2.identity"})  # doubts a frames-only L3 can settle; dialogue doubt needs a human


def _num(value) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class Finding(BaseModel):
    code: str
    severity: taxonomy.Severity | None = None  # None -> taxonomy default
    t_start: float | None = None
    t_end: float | None = None
    message: str = ""
    suggestion: str = ""
    evidence: list[str] = []
    level: str = ""


class ReviewReport(BaseModel):
    level: Level
    reviewer: str
    verdict: Literal["pass", "fail", "uncertain"]
    confidence: float | None = Field(default=None, ge=0, le=1)
    score: float | None = Field(default=None, ge=0, le=1)
    findings: list[Finding] = []
    metrics: dict = {}


class Outcome(BaseModel):
    status: Literal["pass", "fail", "need_l3"]
    score: float = 0.0
    findings: list[Finding] = []
    needs_human: bool = False  # passed, but some reviewer could not decide


def severity(finding: Finding, policy: ReviewPolicy) -> str:
    if finding.code in policy.tolerate:
        return "minor"
    return finding.severity or taxonomy.default_severity(finding.code)


def l0_report(probe: dict, shot: Shot, expect: dict, policy: ReviewPolicy) -> ReviewReport:
    """Judge ffprobe/ffmpeg measurements taken on the node right after generation."""
    findings: list[Finding] = []

    def add(code: str, message: str, span=(None, None)) -> None:
        findings.append(Finding(code=code, message=message, t_start=span[0], t_end=span[1]))

    if not probe.get("decodable"):
        add("CORRUPT_FILE", f"视频无法完整解码 {probe.get('error', '')}".strip())
    else:
        size = (probe.get("width"), probe.get("height"))
        if size != (expect["width"], expect["height"]):
            add("WRONG_RESOLUTION", f"分辨率 {size[0]}x{size[1]}，请求的是 {expect['width']}x{expect['height']}")
        fps = probe.get("fps") or 0.0
        if abs(fps - h3.FPS) > 0.01:
            add("WRONG_FPS", f"帧率 {fps:g}，应为 {h3.FPS}")
        duration, expected = probe.get("duration_s") or 0.0, expect["frames"] / h3.FPS
        if abs(duration - expected) > policy.duration_tolerance_s:
            add("DURATION_MISMATCH", f"时长 {duration:.2f}s，应为 {expected:.2f}s")
        if duration and probe.get("black_s", 0.0) / duration > policy.max_black_ratio:
            add("BLACK_FRAMES", f"黑场共 {probe['black_s']:.2f}s", _first(probe.get("black_ranges")))
        if shot.motion != "static" and probe.get("longest_freeze_s", 0.0) > policy.max_freeze_s:
            add("FROZEN_FRAMES", f"画面冻结 {probe['longest_freeze_s']:.2f}s", _first(probe.get("freeze_ranges")))
        if (shot.dialogue or shot.prompt.soundscape.strip()) and not probe.get("has_audio"):
            add("AUDIO_MISSING", "缺少音轨")
        if probe.get("has_audio"):
            peak = probe.get("max_volume_db")
            if peak is not None and peak >= -0.1:
                add("AUDIO_CLIPPING", f"音频削波（峰值 {peak:.1f} dB）")
            if shot.dialogue and duration and probe.get("silence_s", 0.0) / duration > 0.9:
                add("DIALOGUE_MISSING", "有台词的镜头几乎全程静音")
    failing = any(severity(f, policy) != "minor" for f in findings)
    return ReviewReport(level="L0", reviewer="probe", verdict="fail" if failing else "pass",
                        findings=findings, metrics=probe)


def _first(ranges) -> tuple:
    return tuple(ranges[0]) if ranges else (None, None)


def required_levels(shot: Shot, policy: ReviewPolicy) -> list[str]:
    """Reviews that run in parallel after L0 passes. L3 is decided afterwards."""
    levels = ["L1"] if policy.l1 else []
    if policy.asr_for_dialogue and shot.dialogue and shot.dialogue_precision != "none":
        levels.append("L2.asr")
    if policy.identity_check and shot.characters:
        levels.append("L2.identity")
    return levels


def wants_l3(shot: Shot, policy: ReviewPolicy, take_id: str, uncertain: bool) -> bool:
    if shot.review.l3 is not None:
        return shot.review.l3
    if policy.l3 == "never":
        return False
    if policy.l3 == "always" or uncertain:
        return True
    if policy.l3 == "key_shots" and shot.importance == "key":
        return True
    sample = int(hashlib.sha256(take_id.encode()).hexdigest()[:8], 16) / 0xFFFFFFFF
    return sample < policy.l3_sample_rate


def metric_findings(report: ReviewReport, shot: Shot, policy: ReviewPolicy, names: dict[str, str]) -> list[Finding]:
    """Turn measured metrics (dialogue CER, identity similarity) into findings using package thresholds."""
    found = []
    cer = _num(report.metrics.get("cer"))
    if report.level == "L2.asr" and cer is not None:
        limit = shot.review.max_dialogue_cer if shot.review.max_dialogue_cer is not None else policy.max_dialogue_cer
        if cer > limit and not any(f.code == "DIALOGUE_MISMATCH" for f in report.findings):
            heard = str(report.metrics.get("transcript", ""))[:120]
            found.append(Finding(code="DIALOGUE_MISMATCH", message=f"台词字错率 {cer:.0%}（上限 {limit:.0%}），识别为：{heard}"))
    identity = report.metrics.get("identity")
    if report.level == "L2.identity" and isinstance(identity, dict):
        limit = shot.review.min_identity_similarity
        limit = policy.min_identity_similarity if limit is None else limit
        for character_id in shot.characters:  # every scripted character, not just the ones reported
            name, value = names.get(character_id, character_id), _num(identity.get(character_id))
            if value is None:
                found.append(Finding(code="WRONG_CHARACTER", message=f"未检测到 {name}（无有效相似度）"))
            elif value < limit:
                found.append(Finding(code="IDENTITY_DRIFT", message=f"{name} 身份相似度 {value:.2f}，低于 {limit:.2f}"))
    return found


def unmeasured(report: ReviewReport) -> bool:
    """A measuring reviewer that returns no measurement must not count as a pass."""
    if report.level == "L2.asr":
        return _num(report.metrics.get("cer")) is None
    if report.level == "L2.identity":
        return not isinstance(report.metrics.get("identity"), dict)
    return False


def verdict(reports: list[ReviewReport], shot: Shot, policy: ReviewPolicy, take_id: str,
            names: dict[str, str] | None = None) -> Outcome:
    findings, failing, uncertain_levels, scores = [], [], set(), []
    for report in reports:
        report_failing = 0
        for finding in [*report.findings, *metric_findings(report, shot, policy, names or {})]:
            item = finding.model_copy(update={"level": report.level, "severity": severity(finding, policy)})
            findings.append(item)
            if item.severity != "minor":
                failing.append(item)
                report_failing += 1
        # A "fail" without a concrete, locatable finding is treated as indecision, never as evidence.
        if report.verdict == "uncertain" or (report.verdict == "fail" and not report_failing) or unmeasured(report):
            uncertain_levels.add(report.level)
        if report.score is not None:
            scores.append(report.score)
    if failing:
        return Outcome(status="fail", findings=findings)
    has_l3 = any(report.level == "L3" for report in reports)
    visual_doubt = bool(uncertain_levels & VISUAL)
    if not has_l3 and wants_l3(shot, policy, take_id, visual_doubt):
        return Outcome(status="need_l3", findings=findings)
    needs_human = ("L3" in uncertain_levels or bool(uncertain_levels - VISUAL - {"L3"})
                   or (visual_doubt and not has_l3))
    score = (sum(scores) / len(scores) if scores else 1.0) - 0.02 * len(findings)
    return Outcome(status="pass", score=round(max(score, 0.0), 4), findings=findings, needs_human=needs_human)
