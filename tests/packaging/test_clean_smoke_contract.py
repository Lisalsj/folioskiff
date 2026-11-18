from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).parents[2]
SCRIPT = ROOT / "packaging" / "windows" / "clean-smoke.ps1"
SANDBOX = ROOT / "packaging" / "windows" / "paper-reader-smoke.wsb"
UPGRADE_SANDBOX = ROOT / "packaging" / "windows" / "paper-reader-upgrade-smoke.wsb"


def _source() -> str:
    return SCRIPT.read_text(encoding="utf-8")


def test_clean_smoke_accepts_only_release_identity_inputs_and_fails_closed() -> None:
    source = _source()
    match = re.search(r"(?ms)^param\(\n(?P<body>.*?)^\)\n", source)
    assert match is not None
    param_block = match.group("body")

    assert "Set-StrictMode -Version Latest" in source
    assert '$ErrorActionPreference = "Stop"' in source
    assert "[string]$ReleaseDirectory" in param_block
    assert "[string]$ExpectedManifestSha256" in param_block
    assert "[ValidateRange(8000, 40000)]" in param_block
    assert "[int]$StartupBudgetMs = 8000" in param_block
    assert re.findall(r"\[string\]\$([A-Za-z][A-Za-z0-9]+)", param_block) == [
        "ReleaseDirectory",
        "ExpectedManifestSha256",
        "PreviousReleaseDirectory",
        "ExpectedPreviousManifestSha256",
    ]
    assert "^[0-9a-f]{64}$" in source
    assert "release_members" in source
    assert "release_manifest_hash" in source
    assert "upgrade_identity_pair" in source
    assert "upgrade_release_collision" in source
    assert "upgrade_version_order" in source
    assert "exit 1" in source


def test_release_and_sboms_are_verified_without_python_or_node() -> None:
    source = _source()

    assert "SHA256SUMS.txt" in source
    assert "release-manifest.json" in source
    assert "paper-reader-release-manifest-v1" in source
    assert "$manifest.openapi_version -ne $manifest.version" in source
    assert "5c69bd4bfe08e2f3403cf15875c0b71d7df4ec6d4ea4ea7b8dffec7c33c1df8a" in source
    assert "CycloneDX" in source
    assert '"1.6"' in source
    assert "Get-FileHash" in source
    assert "10" in source
    assert "Get-Command" in source
    for name in ("python.exe", "node.exe", "git.exe"):
        assert name in source
    assert "System.IO.Compression" in source
    assert "ZipArchive" in source


def test_historical_upgrade_release_does_not_inherit_current_frozen_contract() -> None:
    source = _source()
    validator_start = source.index("function Test-ReleaseDirectory")
    validator_end = source.index("function Expand-SafePortableArchive", validator_start)
    validator = source[validator_start:validator_end]

    assert "[switch]$RequireCurrentContract" in validator
    assert "$manifest.openapi_sha256 -cnotmatch '^[0-9a-f]{64}$'" in validator
    assert (
        "$manifest.database_schema_revision -cnotmatch '^[0-9]{4}_[a-z0-9_]+$'"
        in validator
    )
    assert "$RequireCurrentContract -and" in validator

    current_call = re.search(
        r"(?m)^\s*\$release = Test-ReleaseDirectory .+$", source
    )
    previous_call = re.search(
        r"(?m)^\s*\$previousRelease = Test-ReleaseDirectory .+$", source
    )
    assert current_call is not None
    assert previous_call is not None
    assert "-RequireCurrentContract" in current_call.group(0)
    assert "-RequireCurrentContract" not in previous_call.group(0)


def test_runtime_smoke_covers_isolation_activation_and_crash_recovery() -> None:
    source = _source()

    for token in (
        '"研究验证 含空格"',
        "PAPER_READER_STATE_ROOT",
        "PAPER_READER_CACHE_ROOT",
        "PAPER_READER_LIBRARY_ROOT",
        '"--no-browser"',
        '"--ready-file"',
        '"--shutdown-after"',
        "http://127.0.0.1:",
        "Set-ReadOnlyTree",
        "Assert-NoForbiddenDescendants",
        "second_instance_activated",
        "crash_lock_recovered",
        "program_bytes_unchanged",
        '$health.api_version -ne "v1"',
        "$health.openapi_sha256 -ne "
        '"5c69bd4bfe08e2f3403cf15875c0b71d7df4ec6d4ea4ea7b8dffec7c33c1df8a"',
        '$health.database_schema_revision -ne "0038_remove_legacy_conversations"',
        '$health.plugin_protocol_version -ne "1.0"',
        "runtime_contracts_verified",
        "[System.Diagnostics.Stopwatch]",
        'Fail-CleanSmoke "startup_budget"',
        "portable_startup_ms",
        "startup_budget_ms",
        "startup_within_budget",
        "$PSCommandPath",
        "harness_sha256",
    ):
        assert token in source
    assert source.count("harness_sha256 = $harnessSha256") == 3
    harness_identity = source.index("$harnessSha256 = if (")
    main_execution = source.index("\ntry {\n", harness_identity)
    assert harness_identity < main_execution
    activation = source.index("function Invoke-AppActivationSmoke")
    stopwatch = source.index("$startup = [System.Diagnostics.Stopwatch]::StartNew()", activation)
    process_start = source.index("$primary = Start-NativeProcess", activation)
    assert stopwatch < process_start
    assert "-StartupBudgetMs $StartupBudgetMs" in source
    assert "$script:observedStartupMs = $startupMs" in source
    assert "startup_budget_ms = $StartupBudgetMs" in source
    assert "failure_stage = $stage" in source
    assert "portable_startup_ms = $observedStartupMs" in source


def test_installer_lifecycle_and_offline_degradation_are_ephemeral() -> None:
    source = _source()

    for token in (
        "New-NetFirewallRule",
        "Remove-NetFirewallRule",
        "-RemoteAddress Any",
        "Get-NetFirewallAddressFilter",
        "firewall_requires_administrator",
        "firewall_service_unavailable",
        "firewall_permission_denied",
        "firewall_invalid_argument",
        "-Protocol Any",
        "-RemoteAddress",
        '"/VERYSILENT"',
        '"/NORESTART"',
        '"/NOCLOSEAPPLICATIONS"',
        '"/NOFORCECLOSEAPPLICATIONS"',
        "DisplayVersion",
        "unins000.exe",
        "installer_installed",
        "installer_reinstalled",
        "installer_upgraded",
        "installer_uninstalled",
        "user_data_preserved",
        "previous_release_manifest_sha256",
        "upgrade_verified",
        "offline_startup",
        "outbound_firewall_blocked",
    ):
        assert token in source
    assert "installer_upgraded = $true" not in source
    lifecycle_start = source.index("function Invoke-InstallerLifecycle")
    lifecycle_end = source.index("function Remove-OwnedScratch", lifecycle_start)
    lifecycle = source[lifecycle_start:lifecycle_end]
    base_install = lifecycle.index("-FilePath $BaseInstaller")
    current_install = lifecycle.index("-FilePath $Installer")
    assert base_install < current_install
    assert lifecycle.count("Wait-AppReady") == 2
    assert "USERPROFILE" not in source
    assert "[Environment]::GetFolderPath" not in source
    assert "Documents" not in source
    assert "-RemotePort" not in source
    assert "0.0.0.0-126.255.255.255" not in source
    assert "128.0.0.0-255.255.255.255" not in source
    assert "firewall_skipped" not in source
    assert "diagnostic_skip" not in source


def test_script_has_no_external_network_or_upload_surface() -> None:
    source = _source()

    assert "Get-ChildItem Env:" not in source
    assert '[System.Environment]::GetEnvironmentVariables("Process")' in source
    assert "Invoke-WebRequest" not in source
    assert "Start-BitsTransfer" not in source
    assert "gh " not in source
    assert "curl" not in source.casefold()
    urls = re.findall(r"https?://[^\s'\"`]+", source)
    assert urls
    assert all(url.startswith("http://127.0.0.1:") for url in urls)
    assert "paper-reader-clean-smoke-evidence-v1" in source
    assert "ConvertTo-Json" in source
    assert "paper-reader-clean-smoke-evidence.json" in source


def test_windows_sandbox_is_offline_read_only_and_runs_the_same_harness() -> None:
    source = SANDBOX.read_text(encoding="utf-8")
    root = ET.fromstring(source)

    assert root.tag == "Configuration"
    assert root.findtext("Networking") == "Disable"
    assert root.findtext("VGpu") == "Disable"
    assert root.findtext("ClipboardRedirection") == "Disable"
    mapped = root.findall("MappedFolders/MappedFolder")
    assert len(mapped) == 2
    assert mapped[0].findtext("HostFolder") == r"C:\paper-reader-sandbox"
    assert mapped[0].findtext("SandboxFolder") == r"C:\paper-reader-input"
    assert mapped[0].findtext("ReadOnly") == "true"
    assert mapped[1].findtext("HostFolder") == r"C:\paper-reader-sandbox-output"
    assert mapped[1].findtext("SandboxFolder") == r"C:\paper-reader-output"
    assert mapped[1].findtext("ReadOnly") == "false"
    command = root.findtext("LogonCommand/Command") or ""
    assert "clean-smoke.ps1" in command
    assert "RUNNER_TEMP='C:\\paper-reader-output'" in command
    assert "-ReleaseDirectory C:\\paper-reader-input\\release" in command
    assert "-ExpectedManifestSha256" in command
    assert "REPLACE_WITH_64_LOWERCASE_SHA256" in command


def test_upgrade_sandbox_requires_two_verified_release_identities() -> None:
    source = UPGRADE_SANDBOX.read_text(encoding="utf-8")
    root = ET.fromstring(source)

    assert root.findtext("Networking") == "Disable"
    mapped = root.findall("MappedFolders/MappedFolder")
    assert len(mapped) == 2
    assert mapped[0].findtext("ReadOnly") == "true"
    assert mapped[1].findtext("ReadOnly") == "false"
    command = root.findtext("LogonCommand/Command") or ""
    assert "-ReleaseDirectory C:\\paper-reader-input\\release" in command
    assert "-PreviousReleaseDirectory C:\\paper-reader-input\\previous-release" in command
    assert "-ExpectedManifestSha256 REPLACE_WITH_CURRENT_64_LOWERCASE_SHA256" in command
    assert ("-ExpectedPreviousManifestSha256 REPLACE_WITH_PREVIOUS_64_LOWERCASE_SHA256") in command
