import io
import tarfile

import pytest

from conftest import edit_manifest
from drama_factory.protocol import keys
from drama_factory.protocol.package import PackageError, discard, seal, sha256_file, to_tar, verify


def issue_codes(exc: PackageError) -> set[str]:
    return {issue.code for issue in exc.issues}


def test_seal_and_verify_directory_and_tar(tmp_path, package_dir, studio):
    private, trusted = studio
    digest = seal(package_dir, private)
    verified = verify(package_dir, trusted, staging=tmp_path / "staging")
    assert verified.digest == digest and verified.manifest.package_id == "DEMO_EP01_V1"
    assert "manifest.json" in verified.files and len(verified.files) == 10
    archive = to_tar(package_dir, tmp_path / "out" / "pkg.tar")
    assert verify(archive, trusted, staging=tmp_path / "staging").digest == digest
    discard(verified)


def test_verification_works_on_a_private_snapshot(tmp_path, package_dir, studio):
    private, trusted = studio
    seal(package_dir, private)
    original = (package_dir / "manifest.json").read_bytes()
    verified = verify(package_dir, trusted, staging=tmp_path / "staging")
    assert verified.root != package_dir and verified.workdir.parent == tmp_path / "staging"
    # Whoever can write the inbox swaps content after the check: the import copy is unaffected.
    (package_dir / "manifest.json").write_text("{}", encoding="utf-8")
    (package_dir / "props" / "card.png").write_bytes(b"\x89PNG\r\n\x1a\nswapped")
    assert (verified.root / "manifest.json").read_bytes() == original
    assert sha256_file(verified.root / "props" / "card.png") == verified.files["props/card.png"]
    discard(verified)
    assert not verified.workdir.exists()


def test_tampered_file_and_smuggled_file_are_rejected(package_dir, studio):
    private, trusted = studio
    seal(package_dir, private)
    (package_dir / "props" / "card.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"tampered")
    (package_dir / "extra.json").write_text("{}", encoding="utf-8")
    with pytest.raises(PackageError) as caught:
        verify(package_dir, trusted)
    assert {"CHECKSUM", "UNLISTED_FILE"} <= issue_codes(caught.value)


def test_untrusted_signer_is_rejected(tmp_path, package_dir, studio):
    keys.generate(tmp_path / "rogue")
    seal(package_dir, keys.load_private(tmp_path / "rogue.key"))
    with pytest.raises(PackageError) as caught:
        verify(package_dir, studio[1])
    assert issue_codes(caught.value) == {"SIGNATURE"}


def test_scripts_and_disguised_files_are_rejected(package_dir, studio):
    private, trusted = studio
    (package_dir / "setup.sh").write_text("#!/bin/sh\nrm -rf /\n", encoding="utf-8")
    (package_dir / "props" / "card.png").write_text("#!/bin/sh\necho not an image\n", encoding="utf-8")
    seal(package_dir, private)  # even a validly signed package may not carry executables
    with pytest.raises(PackageError) as caught:
        verify(package_dir, trusted)
    assert {"FORBIDDEN_FILE", "FILE_TYPE"} <= issue_codes(caught.value)


def test_semantic_errors_block_import(package_dir, studio):
    private, trusted = studio
    edit_manifest(package_dir, lambda d: d["distribution"].update(regions=["CN", "US"]))
    seal(package_dir, private)
    with pytest.raises(PackageError) as caught:
        verify(package_dir, trusted, restricted_regions=["US", "EU", "UK", "KR"])
    assert "LICENSE_REGION" in issue_codes(caught.value)


def test_tar_with_path_traversal_is_rejected(tmp_path, studio):
    archive = tmp_path / "evil.tar"
    with tarfile.open(archive, "w") as tar:
        data = b"x"
        info = tarfile.TarInfo("../../outside.txt")
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))
    with pytest.raises(PackageError) as caught:
        verify(archive, studio[1], staging=tmp_path / "staging")
    assert issue_codes(caught.value) == {"BAD_PATH"}
    assert not (tmp_path / "outside.txt").exists()
