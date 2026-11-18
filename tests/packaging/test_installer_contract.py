from __future__ import annotations

import os
from dataclasses import fields
from pathlib import Path

import pytest

from paper_reader import __version__


def test_same_version_installer_smoke_is_not_mislabeled_as_an_upgrade() -> None:
    from scripts.package_release import InstallerSmokeReport

    names = {field.name for field in fields(InstallerSmokeReport)}
    assert "reinstall_exit_code" in names
    assert "upgrade_exit_code" not in names


def test_release_packaging_environment_drops_credentials_and_tool_injection(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from scripts.package_release import _sanitized_packaging_environment

    monkeypatch.setenv("OPENAI_API_KEY", "do-not-package")
    monkeypatch.setenv("IEEE_XPLORE_API_KEY", "do-not-package")
    monkeypatch.setenv("PAPER_READER_SIGN_COMMAND", "do-not-run-in-task-7")
    monkeypatch.setenv("PYTHONPATH", str(tmp_path / "foreign-python"))
    monkeypatch.setenv("ISCC", str(tmp_path / "foreign-compiler"))

    environment = _sanitized_packaging_environment()

    assert environment["PYTHONPATH"] == ""
    assert environment["PYTHONNOUSERSITE"] == "1"
    assert "OPENAI_API_KEY" not in environment
    assert "IEEE_XPLORE_API_KEY" not in environment
    assert "PAPER_READER_SIGN_COMMAND" not in environment
    assert "ISCC" not in environment


def test_iscc_must_be_explicit_or_from_a_fixed_install_location(tmp_path: Path) -> None:
    from scripts.package_release import ReleasePackageError, resolve_iscc

    relative = Path("untrusted/iscc.exe")
    with pytest.raises(ReleasePackageError, match="inno_compiler_path_unsafe"):
        resolve_iscc(relative)

    wrong_name = tmp_path / "compiler.exe"
    wrong_name.write_bytes(b"not iscc")
    with pytest.raises(ReleasePackageError, match="inno_compiler_path_unsafe"):
        resolve_iscc(wrong_name)

    explicit = tmp_path / "ISCC.exe"
    explicit.write_bytes(b"compiler")
    assert resolve_iscc(explicit) == explicit.absolute()


def test_installer_command_is_argument_safe_and_version_bound(tmp_path: Path) -> None:
    from scripts.package_release import installer_command

    compiler = (tmp_path / "tool path" / "ISCC.exe").absolute()
    source = (tmp_path / "source with 空格" / "paper-reader").absolute()
    output = (tmp_path / "output with 空格").absolute()
    script = (tmp_path / "paper-reader.iss").absolute()

    command = installer_command(
        iscc=compiler,
        script=script,
        distribution=source,
        output_dir=output,
        version="0.6.0",
    )

    assert command[0] == str(compiler)
    assert command[-1] == str(script)
    assert f"/DSourceDir={source}" in command
    assert f"/DOutputDir={output}" in command
    assert "/DAppVersion=0.6.0" in command
    assert all('"' not in argument for argument in command)


def test_silent_installer_arguments_are_explicit_and_non_destructive(tmp_path: Path) -> None:
    from scripts.package_release import silent_installer_arguments

    install = (tmp_path / "安装 path" / "paper-reader").absolute()
    arguments = silent_installer_arguments(
        install_root=install,
        group_name="paper-reader-smoke-a1b2c3",
    )

    assert arguments == (
        "/VERYSILENT",
        "/SUPPRESSMSGBOXES",
        "/NORESTART",
        "/NOCLOSEAPPLICATIONS",
        "/NOFORCECLOSEAPPLICATIONS",
        "/SP-",
        f"/DIR={install}",
        "/GROUP=paper-reader-smoke-a1b2c3",
        "/MERGETASKS=!desktopicon",
    )
    assert all('"' not in argument for argument in arguments)


def test_installer_exit_codes_fail_closed() -> None:
    from scripts.package_release import ReleasePackageError, require_successful_exit

    require_successful_exit(0, operation="inno_compile")
    for return_code in (1, 2, 3, 4, 5, 8, 17, -1, 0xDEAD):
        with pytest.raises(ReleasePackageError, match="inno_compile_failed"):
            require_successful_exit(return_code, operation="inno_compile")


def test_atomic_release_promotion_rolls_back_every_output(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from scripts import package_release

    portable = tmp_path / "paper-reader-portable.zip"
    installer = tmp_path / "paper-reader-setup.exe"
    portable.write_bytes(b"old-portable")
    installer.write_bytes(b"old-installer")
    candidate_portable = tmp_path / ".new-portable.zip"
    candidate_installer = tmp_path / ".new-installer.exe"
    candidate_portable.write_bytes(b"new-portable")
    candidate_installer.write_bytes(b"new-installer")
    real_replace = package_release.os.replace
    injected = False

    def fail_second_candidate(source: object, destination: object) -> None:
        nonlocal injected
        if Path(source) == candidate_installer and not injected:
            injected = True
            raise OSError("injected second promotion failure")
        real_replace(source, destination)

    monkeypatch.setattr(package_release.os, "replace", fail_second_candidate)

    with pytest.raises(OSError, match="injected second promotion failure"):
        package_release._atomic_publish_set(
            (
                (candidate_portable, portable),
                (candidate_installer, installer),
            )
        )

    assert portable.read_bytes() == b"old-portable"
    assert installer.read_bytes() == b"old-installer"
    assert candidate_portable.read_bytes() == b"new-portable"
    assert candidate_installer.read_bytes() == b"new-installer"
    assert not tuple(tmp_path.glob(".*.previous-*"))


def test_installed_tree_must_preserve_every_distribution_byte(tmp_path: Path) -> None:
    from scripts.package_release import ReleasePackageError, verify_installed_tree

    source = tmp_path / "source" / "paper-reader"
    installed = tmp_path / "installed"
    for root in (source, installed):
        (root / "_internal").mkdir(parents=True)
        (root / "paper-reader.exe").write_bytes(b"entry")
        (root / "_internal" / "runtime.dll").write_bytes(b"runtime")
    (installed / "unins000.exe").write_bytes(b"uninstaller")
    (installed / "unins000.dat").write_bytes(b"uninstall-log")

    verify_installed_tree(installed, source_root=source)

    (installed / "_internal" / "runtime.dll").write_bytes(b"tampered")
    with pytest.raises(ReleasePackageError, match="installed_tree_mismatch"):
        verify_installed_tree(installed, source_root=source)


@pytest.mark.skipif(
    os.environ.get("PAPER_READER_INSTALLER_PATH") is None,
    reason="set PAPER_READER_INSTALLER_PATH for the real silent installer smoke",
)
def test_real_installer_has_expected_pe_identity() -> None:
    from scripts.package_release import inspect_installer_pe

    report = inspect_installer_pe(Path(os.environ["PAPER_READER_INSTALLER_PATH"]))
    assert report.product_name == "页舟 · FolioSkiff"
    assert report.version == __version__
    assert report.publisher == "FolioSkiff contributors"


@pytest.mark.skipif(
    os.environ.get("PAPER_READER_INSTALLER_PATH") is None
    or os.environ.get("PAPER_READER_DISTRIBUTION_PATH") is None,
    reason="set installer and distribution paths for the real lifecycle smoke",
)
def test_real_silent_installer_lifecycle(tmp_path: Path) -> None:
    from scripts.package_release import smoke_installer_lifecycle

    report = smoke_installer_lifecycle(
        installer=Path(os.environ["PAPER_READER_INSTALLER_PATH"]),
        distribution=Path(os.environ["PAPER_READER_DISTRIBUTION_PATH"]),
        work_root=tmp_path / "Windows 安装验证 含空格",
    )

    assert report.install_exit_code == 0
    assert report.portable_activation_exit_code == 0
    assert report.running_block_exit_code != 0
    assert report.reinstall_exit_code == 0
    assert report.downgrade_block_exit_code != 0
    assert report.uninstall_exit_code == 0
    assert report.program_removed
    assert report.user_data_preserved
