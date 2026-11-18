from __future__ import annotations

import re
from pathlib import Path
from uuid import uuid4

import pytest

ROOT = Path(__file__).parents[2]
SCRIPT = ROOT / "packaging/windows/paper-reader.iss"
APP_ID = "{381B261E-B659-4B56-855E-ADDE80CC5BD2}"
RUN_MARKER = r"Local\paper-reader-installer-running-v1"


def _source() -> str:
    return SCRIPT.read_text(encoding="utf-8")


def _directives(source: str) -> dict[str, str]:
    setup = source.split("[Setup]", 1)[1].split("\n[", 1)[0]
    result: dict[str, str] = {}
    for raw in setup.splitlines():
        line = raw.strip()
        if line and not line.startswith(";") and "=" in line:
            key, value = line.split("=", 1)
            result[key.casefold()] = value.strip()
    return result


def test_inno_setup_is_fixed_per_user_windows_11_x64() -> None:
    source = _source()
    directives = _directives(source)

    assert directives["appid"] == "{{381B261E-B659-4B56-855E-ADDE80CC5BD2}"
    assert directives["appname"] == "页舟 · FolioSkiff"
    assert directives["appversion"] == "{#AppVersion}"
    assert directives["minversion"] == "10.0.22000"
    assert directives["architecturesallowed"] == "x64compatible"
    assert directives["architecturesinstallin64bitmode"] == "x64compatible"
    assert directives["privilegesrequired"] == "lowest"
    assert "privilegesrequiredoverridesallowed" not in directives
    assert directives["defaultdirname"] == r"{localappdata}\Programs\paper-reader"
    assert directives["defaultgroupname"] == "FolioSkiff"
    assert directives["allownoicons"] == "no"
    assert directives["usepreviousappdir"] == "yes"
    assert directives["uninstalllogmode"] == "append"
    assert directives["createuninstallregkey"] == "yes"
    assert directives["appmutex"] == RUN_MARKER


def test_inno_setup_never_forces_or_restarts_a_running_reader() -> None:
    source = _source()
    directives = _directives(source)

    assert directives["closeapplications"] == "no"
    assert directives["allowcancelduringinstall"] == "no"
    assert directives["restartapplications"] == "no"
    assert directives["restartifneededbyrun"] == "no"
    assert "restartreplace" not in source.casefold()
    assert "forcecloseapplications" not in source.casefold()
    assert "CheckForMutexes" in source
    assert RUN_MARKER in source
    assert "function PrepareToInstall(var NeedsRestart: Boolean): String" in source
    assert "FolioSkiff is running" in source


def test_inno_file_and_shortcut_surface_is_closed() -> None:
    source = _source()
    folded = source.casefold()

    assert "[Files]".casefold() in folded
    assert 'Source: "{#SourceDir}\\*"' in source
    assert 'DestDir: "{app}"' in source
    assert "recursesubdirs" in source
    assert "createallsubdirs" in source
    assert "[Registry]".casefold() not in folded
    assert "[Dirs]".casefold() not in folded
    assert "[UninstallDelete]".casefold() not in folded
    assert 'Name: "{group}\\FolioSkiff"' in source
    assert 'Name: "{autodesktop}\\FolioSkiff"' in source
    assert "Tasks: desktopicon" in source
    assert re.search(r"Name: desktopicon;.*Flags: unchecked", source)
    assert 'Filename: "{app}\\paper-reader.exe"' in source
    assert 'Parameters: "serve"' in source
    assert "postinstall" in source
    assert "skipifsilent" in source
    assert "unchecked" in source

    forbidden = (
        r"CurrentVersion\Run",
        "schtasks",
        "New-NetFirewallRule",
        "netsh firewall",
        "ChangesEnvironment=yes",
        "ChangesAssociations=yes",
        "regserver",
        "sharedfile",
        "runascurrentuser",
    )
    assert all(item.casefold() not in folded for item in forbidden)


def test_inno_blocks_downgrades_and_requires_binary_version_match() -> None:
    source = _source()

    assert "GetFileVersionString" in source
    assert 'AppVersion + ".0"' in source
    assert "DisplayVersion" in source
    assert "StrToVersion" in source
    assert "ComparePackedVersion" in source
    assert "installed version is newer" in source.casefold()
    assert "PrepareToInstall" in source
    assert "protect the local database" in source


def test_uninstall_cleanup_is_opt_in_confirmed_and_default_roots_only() -> None:
    source = _source()

    assert "UninstallSilent" in source
    assert "CleanupLocalData := False" in source
    assert "CleanupResearchData := False" in source
    assert "models, cache, and logs" in source
    assert "paper library and backups" in source
    assert "DELETE PAPER DATA" in source
    assert "CleanupConfirmation.Text" in source
    assert "{localappdata}\\paper-reader\\models" in source
    assert "{localappdata}\\paper-reader\\logs" in source
    assert "{userdocs}\\paper-reader\\objects" in source
    assert "{userdocs}\\paper-reader\\backups" in source
    assert "FILE_ATTRIBUTE_REPARSE_POINT" in source
    assert "TreeIsSafe" in source
    assert "DelTree" in source
    assert "CurUninstallStepChanged" in source
    assert "usPostUninstall" in source
    assert "PAPER_READER_STATE_ROOT" not in source
    assert "PAPER_READER_LIBRARY_ROOT" not in source
    assert "CreateCustomForm(ScaleX(520), ScaleY(280), False, True)" in source
    assert "CleanupForm.ShowModal()" in source


def test_app_id_and_running_marker_are_shared_with_python_contract() -> None:
    from paper_reader.packaging.windows import INSTALLER_APP_ID, INSTALLER_RUN_MARKER

    assert INSTALLER_APP_ID == APP_ID
    assert INSTALLER_RUN_MARKER == RUN_MARKER


def test_installer_running_marker_is_held_only_inside_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from paper_reader.packaging import windows
    from paper_reader.packaging.windows import (
        InstallerRunMarker,
        is_installer_run_marker_present,
    )

    # Exercise the real Windows handle lifecycle without sharing a marker
    # with an application that the developer has already started.
    monkeypatch.setattr(windows, "INSTALLER_RUN_MARKER", rf"Local\paper-reader-test-{uuid4().hex}")
    assert not is_installer_run_marker_present()
    with InstallerRunMarker():
        assert is_installer_run_marker_present()
    assert not is_installer_run_marker_present()
