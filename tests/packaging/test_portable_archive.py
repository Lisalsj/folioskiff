from __future__ import annotations

import os
import stat
import zipfile
from pathlib import Path

import pytest


def _source_tree(tmp_path: Path) -> Path:
    root = tmp_path / "paper-reader"
    (root / "_internal" / "paper_reader" / "static").mkdir(parents=True)
    (root / "paper-reader.exe").write_bytes(b"MZ-frozen-entry")
    (root / "LICENSE").write_text("Apache-2.0\n", encoding="utf-8")
    (root / "THIRD_PARTY_NOTICES.md").write_text("notices\n", encoding="utf-8")
    (root / "release-manifest.json").write_text("{}\n", encoding="utf-8")
    (root / "_internal" / "runtime.dll").write_bytes(b"MZ-runtime")
    (root / "_internal" / "paper_reader" / "static" / "index.html").write_text(
        "<!doctype html>",
        encoding="utf-8",
    )
    return root


def test_portable_archive_is_rooted_sorted_and_reproducible(tmp_path: Path) -> None:
    from scripts.package_release import create_portable_archive, verify_portable_archive

    source = _source_tree(tmp_path / "source")
    first = tmp_path / "first.zip"
    second = tmp_path / "second.zip"

    first_report = create_portable_archive(source, first)
    for path in source.rglob("*"):
        os.utime(path, (1_800_000_000, 1_800_000_000))
    second_report = create_portable_archive(source, second)

    assert first.read_bytes() == second.read_bytes()
    assert first_report == second_report
    assert first_report.file_count == 6
    assert first_report.uncompressed_size_bytes == sum(
        path.stat().st_size for path in source.rglob("*") if path.is_file()
    )

    verified = verify_portable_archive(first, source_root=source)
    assert verified == first_report
    with zipfile.ZipFile(first) as archive:
        infos = archive.infolist()
    names = [item.filename for item in infos]
    assert names == sorted(names, key=str.casefold)
    assert len(names) == len({name.casefold() for name in names})
    assert all(name.startswith("paper-reader/") for name in names)
    assert {name.split("/", 1)[0] for name in names} == {"paper-reader"}
    assert all(item.date_time == (1980, 1, 1, 0, 0, 0) for item in infos)
    assert all(not stat.S_ISLNK(item.external_attr >> 16) for item in infos)


@pytest.mark.parametrize(
    "member",
    [
        "/absolute.txt",
        "C:/absolute.txt",
        "paper-reader/../escape.txt",
        "paper-reader\\backslash.txt",
        "other-root/file.txt",
        "paper-reader/e\u0301.txt",
    ],
)
def test_portable_verifier_rejects_unsafe_member_names(
    tmp_path: Path,
    member: str,
) -> None:
    from scripts.package_release import PortableArchiveError, verify_portable_archive

    archive = tmp_path / "unsafe.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr(member, b"unsafe")
    if "\\" in member:
        normalized = member.replace("\\", "/").encode("utf-8")
        archive.write_bytes(archive.read_bytes().replace(normalized, member.encode("utf-8")))

    with pytest.raises(PortableArchiveError, match="portable_member_name"):
        verify_portable_archive(archive)


def test_portable_verifier_rejects_case_collisions_and_links(tmp_path: Path) -> None:
    from scripts.package_release import PortableArchiveError, verify_portable_archive

    collision = tmp_path / "collision.zip"
    with zipfile.ZipFile(collision, "w") as bundle:
        bundle.writestr("paper-reader/File.txt", b"first")
        bundle.writestr("paper-reader/file.txt", b"second")
    with pytest.raises(PortableArchiveError, match="portable_member_collision"):
        verify_portable_archive(collision)

    linked = tmp_path / "linked.zip"
    info = zipfile.ZipInfo("paper-reader/linked.txt")
    info.create_system = 3
    info.external_attr = (stat.S_IFLNK | 0o777) << 16
    with zipfile.ZipFile(linked, "w") as bundle:
        bundle.writestr(info, b"outside.txt")
    with pytest.raises(PortableArchiveError, match="portable_link_member"):
        verify_portable_archive(linked)


def test_portable_verifier_rejects_noncanonical_order_and_metadata(tmp_path: Path) -> None:
    from scripts.package_release import PortableArchiveError, verify_portable_archive

    unordered = tmp_path / "unordered.zip"
    with zipfile.ZipFile(unordered, "w") as bundle:
        bundle.writestr("paper-reader/z.txt", b"last")
        bundle.writestr("paper-reader/a.txt", b"first")
    with pytest.raises(PortableArchiveError, match="portable_member_order"):
        verify_portable_archive(unordered)

    cases = (
        ((2026, 1, 2, 3, 4, 6), stat.S_IFREG | 0o444, zipfile.ZIP_DEFLATED),
        ((1980, 1, 1, 0, 0, 0), stat.S_IFREG | 0o644, zipfile.ZIP_DEFLATED),
        ((1980, 1, 1, 0, 0, 0), stat.S_IFREG | 0o444, zipfile.ZIP_STORED),
    )
    for index, (timestamp, mode, compression) in enumerate(cases):
        archive = tmp_path / f"metadata-{index}.zip"
        info = zipfile.ZipInfo("paper-reader/file.txt", date_time=timestamp)
        info.create_system = 3
        info.external_attr = mode << 16
        info.compress_type = compression
        with zipfile.ZipFile(archive, "w") as bundle:
            bundle.writestr(info, b"payload")
        with pytest.raises(PortableArchiveError, match="portable_member_metadata"):
            verify_portable_archive(archive)


def test_portable_verifier_detects_content_drift(tmp_path: Path) -> None:
    from scripts.package_release import (
        PortableArchiveError,
        create_portable_archive,
        verify_portable_archive,
    )

    source = _source_tree(tmp_path / "source")
    archive = tmp_path / "portable.zip"
    create_portable_archive(source, archive)
    (source / "_internal" / "runtime.dll").write_bytes(b"changed")

    with pytest.raises(PortableArchiveError, match="portable_source_mismatch"):
        verify_portable_archive(archive, source_root=source)


def test_portable_creation_preserves_previous_archive_on_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from scripts import package_release

    source = _source_tree(tmp_path / "source")
    destination = tmp_path / "portable.zip"
    destination.write_bytes(b"previous-release")

    def fail_payload(_source: Path, temporary: Path) -> None:
        temporary.write_bytes(b"partial-new-release")
        raise OSError("injected archive write failure")

    monkeypatch.setattr(package_release, "_write_portable_payload", fail_payload)

    with pytest.raises(package_release.PortableArchiveError, match="portable_write_failed"):
        package_release.create_portable_archive(source, destination)

    assert destination.read_bytes() == b"previous-release"
    assert not tuple(tmp_path.glob(".portable.zip.*.tmp"))


def test_release_outputs_reject_reparse_ancestors(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from scripts import package_release

    source = _source_tree(tmp_path / "source")
    monkeypatch.setattr(package_release, "_has_reparse_ancestor", lambda _path: True)

    with pytest.raises(
        package_release.PortableArchiveError,
        match="portable_destination_unsafe",
    ):
        package_release.create_portable_archive(source, tmp_path / "out" / "portable.zip")
    with pytest.raises(package_release.ReleasePackageError, match="release_output_unsafe"):
        package_release._safe_output_directory(tmp_path / "release", source=source)


def test_portable_creation_rejects_linked_source_when_supported(tmp_path: Path) -> None:
    from scripts.package_release import PortableArchiveError, create_portable_archive

    source = _source_tree(tmp_path / "source")
    outside = tmp_path / "outside.txt"
    outside.write_text("private", encoding="utf-8")
    link = source / "_internal" / "linked.txt"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("symlink creation is unavailable on this Windows profile")

    with pytest.raises(PortableArchiveError, match="portable_source_link"):
        create_portable_archive(source, tmp_path / "portable.zip")
