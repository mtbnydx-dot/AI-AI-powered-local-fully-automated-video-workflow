"""Seal (studio side) and verify (site side) packages; result bundles reuse the same sealing format."""
import hashlib
import json
import os
import shutil
import stat
import tarfile
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from pydantic import ValidationError

from . import keys
from .manifest import Issue, Manifest, RouteCaps, license_issues, safe_relpath, validate

MANIFEST, CHECKSUMS, SIGNATURE = "manifest.json", "checksums.sha256", "signature.ed25519"
MEDIA_SUFFIXES = {".png": "image", ".jpg": "image", ".jpeg": "image", ".webp": "image",
                  ".mp4": "video", ".mov": "video", ".webm": "video",
                  ".wav": "audio", ".mp3": "audio", ".flac": "audio", ".m4a": "audio"}
TEXT_SUFFIXES = {".json", ".txt", ".md", ".csv"}
MAX_FILE_BYTES = 4 * 1024**3


class PackageError(Exception):
    def __init__(self, issues: list[Issue]):
        self.issues = issues
        super().__init__("\n".join(map(str, issues)))


@dataclass
class VerifiedPackage:
    root: Path  # the private staged copy that was verified; import from here, never from the inbox
    manifest: Manifest
    digest: str  # sha256 of checksums.sha256: the package's immutable identity
    key_id: str
    files: dict[str, str]  # relative path -> sha256
    warnings: list[Issue] = field(default_factory=list)
    workdir: Path | None = None  # staging directory to remove with discard()


def discard(verified: VerifiedPackage) -> None:
    if verified.workdir is not None:
        shutil.rmtree(verified.workdir, ignore_errors=True)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sniff(path: Path) -> str | None:
    """Media kind from magic bytes, so a renamed script cannot pass as an image."""
    with path.open("rb") as handle:
        head = handle.read(16)
    if head.startswith(b"\x89PNG\r\n\x1a\n") or head.startswith(b"\xff\xd8\xff"):
        return "image"
    if head[:4] == b"RIFF":
        return {b"WEBP": "image", b"WAVE": "audio"}.get(head[8:12])
    if head.startswith((b"fLaC", b"ID3")) or head[:2] in (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"):
        return "audio"
    if head[4:8] == b"ftyp":
        return "audio" if head[8:11] == b"M4A" else "video"
    if head.startswith(b"\x1a\x45\xdf\xa3"):
        return "video"
    return None


def walk(root: Path) -> tuple[dict[str, Path], list[Issue]]:
    found, issues = {}, []
    for directory, dirnames, filenames in os.walk(root, followlinks=False):
        base = Path(directory)
        for name in list(dirnames):
            if (base / name).is_symlink():
                issues.append(Issue("error", "SYMLINK", (base / name).relative_to(root).as_posix(), "不允许符号链接"))
                dirnames.remove(name)
        for name in filenames:
            path = base / name
            rel = path.relative_to(root).as_posix()
            if path.is_symlink():
                issues.append(Issue("error", "SYMLINK", rel, "不允许符号链接"))
            else:
                found[rel] = path
    return dict(sorted(found.items())), issues


def seal(root: Path, private_key) -> str:
    """Write checksums.sha256 and a detached signature over it. Returns the bundle digest."""
    files, issues = walk(root)
    if issues:
        raise PackageError(issues)
    lines = [f"{sha256_file(path)}  {rel}" for rel, path in files.items() if rel not in (CHECKSUMS, SIGNATURE)]
    data = ("\n".join(lines) + "\n").encode()
    (root / CHECKSUMS).write_bytes(data)
    (root / SIGNATURE).write_text(json.dumps(keys.sign(private_key, data), indent=2) + "\n", encoding="utf-8")
    return hashlib.sha256(data).hexdigest()


def to_tar(root: Path, out: Path) -> Path:
    """Uncompressed tar for offline transfer; media is already compressed."""
    out.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(out, "w") as tar:
        tar.add(root, arcname=root.name)
    return out


def unpack_into(archive: Path, target: Path) -> Path:
    with tarfile.open(archive) as tar:
        for member in tar.getmembers():
            if not (member.isfile() or member.isdir()):
                raise PackageError([Issue("error", "FORBIDDEN_FILE", member.name, "压缩包只能包含普通文件和目录")])
            if not safe_relpath(member.name.rstrip("/")):
                raise PackageError([Issue("error", "BAD_PATH", member.name, "压缩包路径不安全")])
        if hasattr(tarfile, "data_filter"):
            tar.extractall(target, filter="data")
        else:  # Python < 3.11.4; members were vetted above
            tar.extractall(target)
    entries = list(target.iterdir())
    return entries[0] if len(entries) == 1 and entries[0].is_dir() else target


def copy_regular(src: Path, dest: Path) -> None:
    """Copy one regular file without following links; refuse a file swapped between lstat and open."""
    before = os.lstat(src)
    if not stat.S_ISREG(before.st_mode):
        raise PackageError([Issue("error", "FORBIDDEN_FILE", src.name, "只允许普通文件")])
    fd = os.open(src, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0))
    try:
        after = os.fstat(fd)
        if (after.st_dev, after.st_ino) != (before.st_dev, before.st_ino):
            raise PackageError([Issue("error", "FILE_CHANGED", src.name, "文件在导入过程中被替换")])
        dest.parent.mkdir(parents=True, exist_ok=True)
        with open(fd, "rb", closefd=False) as handle, open(dest, "xb") as out:
            shutil.copyfileobj(handle, out, 1 << 20)
    finally:
        os.close(fd)


def stage(source: Path, staging: Path) -> tuple[Path, Path]:
    """Private copy of a package taken before anything is hashed or parsed.

    Checking files where they sit would let whoever can write the inbox (exchange gateway, removable
    media, a share) swap content between verification and import. Returns (workdir, package root).
    """
    staging.mkdir(parents=True, exist_ok=True)
    workdir = Path(tempfile.mkdtemp(prefix="epp-", dir=staging))
    try:
        if source.is_file():
            return workdir, unpack_into(source, workdir)
        files, issues = walk(source)
        if issues:
            raise PackageError(issues)
        root = workdir / (source.name or "package")
        root.mkdir()
        for rel, path in files.items():
            copy_regular(path, root / rel)
        return workdir, root
    except BaseException:
        shutil.rmtree(workdir, ignore_errors=True)
        raise


def parse_checksums(data: bytes) -> dict[str, str]:
    listed = {}
    for number, line in enumerate(data.decode("utf-8").splitlines(), 1):
        digest, sep, rel = line.partition("  ")
        if not sep or len(digest) != 64 or not safe_relpath(rel) or rel in listed:
            raise PackageError([Issue("error", "CHECKSUMS", f"{CHECKSUMS}:{number}", "校验文件格式错误")])
        listed[rel] = digest.lower()
    return listed


def verify_bundle(root: Path, trusted: dict) -> tuple[dict[str, Path], dict[str, str], dict[str, str], str, str, list[Issue]]:
    """Signature + checksum + file-type gate shared by packages and result bundles.

    Returns (files, listed sha256 by path, media kind by path, signer key id, digest, issues).
    """
    files, issues = walk(root)
    if CHECKSUMS not in files or SIGNATURE not in files:
        raise PackageError([Issue("error", "UNSIGNED", ".", f"缺少 {CHECKSUMS} 或 {SIGNATURE}")])
    data = files[CHECKSUMS].read_bytes()
    try:
        key_id = keys.verify(trusted, json.loads(files[SIGNATURE].read_text("utf-8")), data)
    except (ValueError, json.JSONDecodeError) as exc:
        raise PackageError([Issue("error", "SIGNATURE", SIGNATURE, str(exc))]) from exc
    listed = parse_checksums(data)
    actual = {rel for rel in files if rel not in (CHECKSUMS, SIGNATURE)}
    issues += [Issue("error", "UNLISTED_FILE", rel, "文件不在 checksums 中（疑似夹带）") for rel in sorted(actual - set(listed))]
    issues += [Issue("error", "MISSING_FILE", rel, "checksums 列出的文件不存在") for rel in sorted(set(listed) - actual)]
    kinds = {}
    for rel in sorted(actual & set(listed)):
        path, suffix = files[rel], PurePosixPath(rel).suffix.lower()
        if suffix not in MEDIA_SUFFIXES and suffix not in TEXT_SUFFIXES:
            issues.append(Issue("error", "FORBIDDEN_FILE", rel, "只允许图片、视频、音频与 JSON/文本；不能携带脚本或可执行文件"))
        elif path.stat().st_size > MAX_FILE_BYTES:
            issues.append(Issue("error", "FILE_TOO_LARGE", rel, "单个文件超过 4 GiB"))
        elif sha256_file(path) != listed[rel]:
            issues.append(Issue("error", "CHECKSUM", rel, "哈希不匹配：文件被修改或损坏"))
        elif suffix in MEDIA_SUFFIXES:
            kind = sniff(path)
            if kind != MEDIA_SUFFIXES[suffix]:
                issues.append(Issue("error", "FILE_TYPE", rel, f"扩展名是 {suffix}，内容识别为 {kind or '未知格式'}"))
            else:
                kinds[rel] = kind
        else:
            try:
                path.read_text("utf-8")
            except UnicodeDecodeError:
                issues.append(Issue("error", "FILE_TYPE", rel, "文本文件必须是 UTF-8"))
    return files, listed, kinds, key_id, hashlib.sha256(data).hexdigest(), issues


def verify(source: Path, trusted: dict, staging: Path | None = None, routes: dict[str, RouteCaps] | None = None,
           restricted_regions: Sequence[str] = (), authorized_regions: Sequence[str] = ()) -> VerifiedPackage:
    """Full gate before import: signature, file integrity, schema, semantics, site capability and license.

    Everything is checked on a private staged copy (see stage()); the caller imports from
    `VerifiedPackage.root` and then calls discard().
    """
    workdir, root = stage(source, staging or Path(tempfile.gettempdir()))
    try:
        files, listed, kinds, key_id, digest, issues = verify_bundle(root, trusted)
        manifest = None
        if MANIFEST not in files:
            issues.append(Issue("error", "NO_MANIFEST", MANIFEST, "缺少 manifest.json"))
        else:
            try:
                manifest = Manifest.model_validate_json(files[MANIFEST].read_bytes())
            except ValidationError as exc:
                issues += [Issue("error", "SCHEMA", ".".join(map(str, e["loc"])) or MANIFEST, e["msg"])
                           for e in exc.errors()]
        if manifest is not None:
            issues += validate(manifest, kinds, routes)
            issues += license_issues(manifest, list(restricted_regions), list(authorized_regions))
        if any(issue.level == "error" for issue in issues):
            raise PackageError(issues)
    except BaseException:
        shutil.rmtree(workdir, ignore_errors=True)
        raise
    return VerifiedPackage(root, manifest, digest, key_id, listed, [i for i in issues if i.level == "warning"], workdir)


def inspect(root: Path, routes: dict[str, RouteCaps] | None = None) -> list[Issue]:
    """Studio-side pre-seal check: schema and semantics without signature."""
    path = root / MANIFEST
    if not path.is_file():
        return [Issue("error", "NO_MANIFEST", MANIFEST, "缺少 manifest.json")]
    try:
        manifest = Manifest.model_validate_json(path.read_bytes())
    except ValidationError as exc:
        return [Issue("error", "SCHEMA", ".".join(map(str, e["loc"])) or MANIFEST, e["msg"]) for e in exc.errors()]
    files, issues = walk(root)
    kinds = {rel: kind for rel, p in files.items() if (kind := sniff(p))}
    return issues + validate(manifest, kinds, routes)
