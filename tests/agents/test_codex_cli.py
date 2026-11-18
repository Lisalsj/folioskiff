from __future__ import annotations

import json
import stat
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

import pytest

from paper_reader.agents.codex_cli import CodexCliBackend
from paper_reader.agents.contracts import (
    AgentBackendError,
    AgentErrorCode,
    AgentFragment,
    AgentLocation,
    AgentProbeCode,
    AgentRequest,
    AgentTaskKind,
    EvidenceAttachment,
    ReasoningEffort,
    ServiceTier,
)
from paper_reader.agents.process_policy import SecureProcessPolicy, SecureProcessResult

FIXTURES = Path(__file__).parent / "fixtures" / "codex"


@dataclass(slots=True)
class ProcessFixture:
    exit_code: int = 0
    stdout: str = ""
    stderr: str = ""
    output_text: str | None = None
    error: AgentBackendError | None = None


@dataclass(frozen=True, slots=True)
class ProcessCall:
    policy: SecureProcessPolicy
    stdin: str


class FakeProcessRunner:
    def __init__(self, fixtures: list[ProcessFixture]) -> None:
        self.fixtures = fixtures
        self.calls: list[ProcessCall] = []

    async def __call__(
        self,
        policy: SecureProcessPolicy,
        *,
        stdin: str,
        cancelled: Callable[[], bool],
        on_stdout_line: Callable[[str], Awaitable[None]] | None = None,
    ) -> SecureProcessResult:
        self.calls.append(ProcessCall(policy=policy, stdin=stdin))
        fixture = self.fixtures.pop(0)
        if fixture.error is not None:
            raise fixture.error
        if cancelled():
            raise AgentBackendError(AgentErrorCode.CANCELLED, "cancelled", retryable=True)
        if fixture.output_text is not None:
            output_path = _option_path(policy.arguments, "-o")
            output_path.write_text(fixture.output_text, encoding="utf-8")
        if on_stdout_line is not None:
            for line in fixture.stdout.splitlines():
                await on_stdout_line(line)
        return SecureProcessResult(
            exit_code=fixture.exit_code,
            stdout=fixture.stdout,
            stderr=fixture.stderr,
        )


def _backend(
    tmp_path: Path,
    runner: FakeProcessRunner,
) -> CodexCliBackend:
    executable = tmp_path / "codex.exe"
    executable.write_bytes(b"fixture executable")
    return CodexCliBackend(
        cache_root=tmp_path / "cache",
        executable=executable,
        process_runner=runner,
        environment={
            "PATH": str(tmp_path),
            "USERPROFILE": str(tmp_path / "profile"),
            "OPENAI_API_KEY": "must-not-be-inherited",
        },
    )


def _request(
    *,
    model_id: str | None = "chosen-model",
    external_research: bool = False,
) -> AgentRequest:
    return AgentRequest(
        task_id="job-codex-1",
        task_kind=AgentTaskKind.REPORT,
        prompt="Analyze only the delimited evidence.",
        output_schema={
            "type": "object",
            "properties": {"summary": {"type": "string"}},
            "required": ["summary"],
            "additionalProperties": False,
        },
        model_id=model_id,
        reasoning_effort=ReasoningEffort.HIGH,
        service_tier=ServiceTier.FLEX,
        external_research=external_research,
        attachments=(
            EvidenceAttachment(
                name="evidence.json",
                media_type="application/json",
                content=b'{"paper":"fixture"}',
            ),
        ),
        timeout_seconds=120,
    )


@pytest.mark.asyncio
async def test_probe_reports_supported_authenticated_codex_without_model_call(
    tmp_path: Path,
) -> None:
    runner = FakeProcessRunner(
        [
            ProcessFixture(stdout="codex-cli 0.115.0\n"),
            ProcessFixture(stderr="Logged in using ChatGPT\n"),
        ]
    )
    backend = _backend(tmp_path, runner)

    capability = await backend.probe()

    assert capability.backend_id == "codex_cli"
    assert capability.location is AgentLocation.CLOUD
    assert capability.installed is True
    assert capability.authenticated is True
    assert capability.available is True
    assert capability.probe_code is AgentProbeCode.READY
    assert capability.supports_schema is True
    assert capability.supports_streaming is True
    assert capability.supports_vision is True
    assert capability.supports_external_research is True
    assert backend.cli_version == "0.115.0"
    assert len(runner.calls) == 2
    assert runner.calls[0].stdin == ""
    assert runner.calls[1].stdin == ""
    for call in runner.calls:
        _assert_safe_global_overrides(call.policy.arguments)
        assert "OPENAI_API_KEY" not in call.policy.environment


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("version", "login", "expected_reason"),
    [
        ("codex-cli 0.80.0", "Logged in using ChatGPT", "codex_cli_too_old"),
        ("unexpected version", "Logged in using ChatGPT", "codex_version_unrecognized"),
        ("codex-cli 0.115.0", "Not logged in", "codex_not_authenticated"),
    ],
)
async def test_probe_degrades_for_unsupported_version_or_missing_login(
    tmp_path: Path,
    version: str,
    login: str,
    expected_reason: str,
) -> None:
    runner = FakeProcessRunner(
        [ProcessFixture(stdout=version), ProcessFixture(exit_code=1, stdout=login)]
    )
    backend = _backend(tmp_path, runner)

    capability = await backend.probe()

    assert capability.available is False
    assert expected_reason in capability.degradation_reasons


def test_missing_codex_is_truthfully_unavailable(tmp_path: Path) -> None:
    backend = CodexCliBackend(
        cache_root=tmp_path / "cache",
        executable=None,
        process_runner=FakeProcessRunner([]),
        executable_locator=lambda _: None,
        environment={"LOCALAPPDATA": str(tmp_path / "localappdata")},
    )

    capability = backend.capability

    assert capability.installed is False
    assert capability.authenticated is False
    assert capability.available is False
    assert capability.probe_code is AgentProbeCode.NOT_INSTALLED
    assert capability.degradation_reasons == ("codex_cli_not_installed",)


@pytest.mark.asyncio
async def test_codex_invalid_config_is_not_reported_as_logged_out(tmp_path: Path) -> None:
    runner = FakeProcessRunner(
        [
            ProcessFixture(stdout="codex-cli 0.115.0"),
            ProcessFixture(
                stderr="C:\\Users\\private\\.codex\\config.toml:3:26: "
                "unknown variant 'max'",
                exit_code=1,
            ),
        ]
    )
    backend = _backend(tmp_path, runner)

    capability = await backend.probe()

    assert capability.probe_code is AgentProbeCode.CONFIGURATION_INVALID
    assert capability.authenticated is False
    assert capability.available is False
    assert "codex_configuration_invalid" in capability.degradation_reasons
    assert "codex_not_authenticated" not in capability.degradation_reasons


@pytest.mark.asyncio
async def test_codex_probe_timeout_has_a_stable_code(tmp_path: Path) -> None:
    runner = FakeProcessRunner(
        [
            ProcessFixture(
                error=AgentBackendError(
                    AgentErrorCode.TIMEOUT,
                    "private timeout diagnostic",
                    retryable=True,
                )
            ),
            ProcessFixture(stderr="Logged in using ChatGPT"),
        ]
    )
    backend = _backend(tmp_path, runner)

    capability = await backend.probe()

    assert capability.probe_code is AgentProbeCode.PROBE_TIMEOUT
    assert capability.available is False


@pytest.mark.asyncio
async def test_model_discovery_falls_back_without_hardcoded_model_names(
    tmp_path: Path,
) -> None:
    runner = FakeProcessRunner([ProcessFixture(stdout="Commands:\n  config\n")])
    backend = _backend(tmp_path, runner)

    catalog = await backend.discover_models()

    assert catalog.models == ()
    assert catalog.supports_manual_model_id is True
    assert catalog.supports_default_model is True
    assert catalog.degradation_reasons == ("codex_model_discovery_unavailable",)
    assert "debug" in runner.calls[0].policy.arguments
    assert "--help" in runner.calls[0].policy.arguments


@pytest.mark.asyncio
async def test_model_discovery_parses_supported_dynamic_catalog(tmp_path: Path) -> None:
    runner = FakeProcessRunner(
        [
            ProcessFixture(stdout="Commands:\n  models  Inspect model catalog\n"),
            ProcessFixture(
                stdout=json.dumps(
                    {
                        "models": [
                            {"id": "z-model", "display_name": "Z model"},
                            {
                                "id": "a-model",
                                "display_name": "A model",
                                "supports_vision": True,
                                "context_window": 200000,
                            },
                        ]
                    }
                )
            ),
        ]
    )
    backend = _backend(tmp_path, runner)

    catalog = await backend.discover_models()

    assert [item.model_id for item in catalog.models] == ["a-model", "z-model"]
    assert catalog.models[0].supports_vision is True
    assert catalog.models[0].context_window == 200000
    assert catalog.supports_manual_model_id is True
    assert catalog.supports_default_model is True


@pytest.mark.asyncio
async def test_generation_disables_inherited_tools_and_parses_jsonl(
    tmp_path: Path,
) -> None:
    success = (FIXTURES / "success.jsonl").read_text(encoding="utf-8")
    runner = FakeProcessRunner(
        [
            ProcessFixture(
                stdout=json.dumps(
                    [
                        {"name": "alpha.server", "enabled": True},
                        {"name": "space server", "enabled": True},
                        {"name": 'quote"server', "enabled": True},
                    ]
                )
            ),
            ProcessFixture(
                stdout=success,
                output_text='{"summary":"fixture"}',
            ),
        ]
    )
    backend = _backend(tmp_path, runner)
    fragments: list[AgentFragment] = []

    result = await backend.generate(
        _request(),
        on_fragment=_append(fragments),
        cancelled=lambda: False,
    )

    assert result.structured_output == {"summary": "fixture"}
    assert result.resolved_model_id == "chosen-model"
    assert result.usage.input_tokens == 120
    assert result.usage.output_tokens == 30
    assert fragments == [
        AgentFragment(index=0, kind="text", text='{"summary":"fixture"}')
    ]
    assert len(runner.calls) == 2
    mcp_call, exec_call = runner.calls
    assert tuple(mcp_call.policy.arguments[-3:]) == ("mcp", "list", "--json")
    arguments = exec_call.policy.arguments
    _assert_safe_global_overrides(arguments)
    assert "exec" in arguments
    assert "--json" in arguments
    assert "--output-schema" in arguments
    assert "--sandbox" in arguments
    assert arguments[arguments.index("--sandbox") + 1] == "read-only"
    assert "--ephemeral" in arguments
    assert "--skip-git-repo-check" in arguments
    assert "-C" in arguments
    assert "--model" in arguments
    assert arguments[arguments.index("--model") + 1] == "chosen-model"
    assert "--search" not in arguments
    assert 'web_search="disabled"' in arguments
    assert 'mcp_servers."alpha.server".enabled=false' in arguments
    assert 'mcp_servers."space server".enabled=false' in arguments
    assert 'mcp_servers."quote\\"server".enabled=false' in arguments
    assert exec_call.stdin == "Analyze only the delimited evidence."
    assert "OPENAI_API_KEY" not in exec_call.policy.environment

    schema_path = _option_path(arguments, "--output-schema")
    output_path = _option_path(arguments, "-o")
    evidence_path = exec_call.policy.working_directory / "evidence.json"
    assert not schema_path.exists()
    assert not output_path.exists()
    assert not evidence_path.exists()
    assert not exec_call.policy.working_directory.exists()


@pytest.mark.asyncio
async def test_generation_writes_bounded_read_only_evidence_before_execution(
    tmp_path: Path,
) -> None:
    observations: dict[str, object] = {}

    class InspectingRunner(FakeProcessRunner):
        async def __call__(self, *args: object, **kwargs: object) -> SecureProcessResult:
            policy = args[0]
            assert isinstance(policy, SecureProcessPolicy)
            if "exec" in policy.arguments:
                evidence = policy.working_directory / "evidence.json"
                schema = _option_path(policy.arguments, "--output-schema")
                observations["evidence"] = evidence.read_bytes()
                observations["schema"] = json.loads(schema.read_text(encoding="utf-8"))
                observations["evidence_mode"] = stat.S_IMODE(evidence.stat().st_mode)
                observations["schema_mode"] = stat.S_IMODE(schema.stat().st_mode)
            return await super().__call__(*args, **kwargs)  # type: ignore[arg-type]

    runner = InspectingRunner(
        [
            ProcessFixture(stdout="[]"),
            ProcessFixture(
                stdout=(FIXTURES / "success.jsonl").read_text(encoding="utf-8"),
                output_text='{"summary":"fixture"}',
            ),
        ]
    )
    backend = _backend(tmp_path, runner)

    await backend.generate(
        _request(model_id=None),
        on_fragment=_append([]),
        cancelled=lambda: False,
    )

    assert observations["evidence"] == b'{"paper":"fixture"}'
    assert observations["schema"] == _request().output_schema
    assert int(observations["evidence_mode"]) & stat.S_IWRITE == 0
    assert int(observations["schema_mode"]) & stat.S_IWRITE == 0
    exec_arguments = runner.calls[1].policy.arguments
    assert "--model" not in exec_arguments


@pytest.mark.asyncio
async def test_external_research_is_explicit_and_does_not_restore_other_tools(
    tmp_path: Path,
) -> None:
    runner = FakeProcessRunner(
        [
            ProcessFixture(stdout="[]"),
            ProcessFixture(
                stdout=(FIXTURES / "success.jsonl").read_text(encoding="utf-8"),
                output_text='{"summary":"fixture"}',
            ),
        ]
    )
    backend = _backend(tmp_path, runner)

    await backend.generate(
        _request(external_research=True),
        on_fragment=_append([]),
        cancelled=lambda: False,
    )

    arguments = runner.calls[1].policy.arguments
    assert "--search" in arguments
    assert 'web_search="live"' in arguments
    for feature in ("shell_tool", "multi_agent", "plugins", "apps"):
        assert _disabled(arguments, feature)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mcp_stdout",
    ["not-json", '{"name":"not-a-list"}', '[{"enabled":true}]'],
)
async def test_generation_fails_closed_when_mcp_inventory_is_invalid(
    tmp_path: Path,
    mcp_stdout: str,
) -> None:
    runner = FakeProcessRunner([ProcessFixture(stdout=mcp_stdout)])
    backend = _backend(tmp_path, runner)

    with pytest.raises(AgentBackendError) as captured:
        await backend.generate(
            _request(),
            on_fragment=_append([]),
            cancelled=lambda: False,
        )

    assert captured.value.code is AgentErrorCode.PROTOCOL
    assert len(runner.calls) == 1
    assert not (tmp_path / "cache" / "agent-jobs" / "job-codex-1").exists()


@pytest.mark.asyncio
async def test_generation_rejects_malformed_jsonl_and_invalid_final_json(
    tmp_path: Path,
) -> None:
    malformed_runner = FakeProcessRunner(
        [
            ProcessFixture(stdout="[]"),
            ProcessFixture(
                stdout=(FIXTURES / "malformed.jsonl").read_text(encoding="utf-8"),
                output_text='{"summary":"fixture"}',
            ),
        ]
    )
    backend = _backend(tmp_path, malformed_runner)

    with pytest.raises(AgentBackendError) as malformed:
        await backend.generate(
            _request(),
            on_fragment=_append([]),
            cancelled=lambda: False,
        )
    assert malformed.value.code is AgentErrorCode.PROTOCOL

    invalid_runner = FakeProcessRunner(
        [
            ProcessFixture(stdout="[]"),
            ProcessFixture(
                stdout='{"type":"turn.completed","usage":{}}',
                output_text="not-json",
            ),
        ]
    )
    backend = _backend(tmp_path, invalid_runner)
    with pytest.raises(AgentBackendError) as invalid:
        await backend.generate(
            _request(),
            on_fragment=_append([]),
            cancelled=lambda: False,
        )
    assert invalid.value.code is AgentErrorCode.INVALID_OUTPUT


@pytest.mark.asyncio
async def test_nonzero_exit_is_redacted_and_existing_backend_errors_survive(
    tmp_path: Path,
) -> None:
    failed_runner = FakeProcessRunner(
        [
            ProcessFixture(stdout="[]"),
            ProcessFixture(exit_code=2, stderr="request failed with sk-live-secret"),
        ]
    )
    backend = _backend(tmp_path, failed_runner)

    with pytest.raises(AgentBackendError) as failed:
        await backend.generate(
            _request(),
            on_fragment=_append([]),
            cancelled=lambda: False,
        )
    assert failed.value.code is AgentErrorCode.PROCESS
    assert failed.value.diagnostic is not None
    assert "sk-live-secret" not in failed.value.diagnostic

    timeout_runner = FakeProcessRunner(
        [
            ProcessFixture(stdout="[]"),
            ProcessFixture(
                error=AgentBackendError(
                    AgentErrorCode.TIMEOUT,
                    "timed out",
                    retryable=True,
                )
            ),
        ]
    )
    backend = _backend(tmp_path, timeout_runner)
    with pytest.raises(AgentBackendError) as timeout:
        await backend.generate(
            _request(),
            on_fragment=_append([]),
            cancelled=lambda: False,
        )
    assert timeout.value.code is AgentErrorCode.TIMEOUT
    assert timeout.value.retryable is True

    cancelled_runner = FakeProcessRunner(
        [
            ProcessFixture(stdout="[]"),
            ProcessFixture(
                error=AgentBackendError(
                    AgentErrorCode.CANCELLED,
                    "cancelled",
                    retryable=True,
                )
            ),
        ]
    )
    backend = _backend(tmp_path, cancelled_runner)
    with pytest.raises(AgentBackendError) as cancelled:
        await backend.generate(
            _request(),
            on_fragment=_append([]),
            cancelled=lambda: False,
        )
    assert cancelled.value.code is AgentErrorCode.CANCELLED
    assert cancelled.value.retryable is True


def _assert_safe_global_overrides(arguments: tuple[str, ...]) -> None:
    expected_configs = {
        'model_reasoning_effort="high"',
        'service_tier="flex"',
        'approval_policy="never"',
        "hooks=[]",
        "notify=[]",
        "project_doc_max_bytes=0",
    }
    config_values = {
        arguments[index + 1]
        for index, value in enumerate(arguments[:-1])
        if value in {"-c", "--config"}
    }
    assert expected_configs.issubset(config_values)
    assert _option_value(arguments, "-a") == "never"
    for feature in (
        "shell_tool",
        "shell_snapshot",
        "unified_exec",
        "multi_agent",
        "apps",
        "plugins",
        "image_generation",
        "js_repl",
        "codex_hooks",
        "skill_mcp_dependency_install",
        "tool_suggest",
        "tool_call_mcp_elicitation",
    ):
        assert _disabled(arguments, feature), feature


def _disabled(arguments: tuple[str, ...], feature: str) -> bool:
    return any(
        value == "--disable" and index + 1 < len(arguments) and arguments[index + 1] == feature
        for index, value in enumerate(arguments)
    )


def _option_value(arguments: tuple[str, ...], option: str) -> str:
    return arguments[arguments.index(option) + 1]


def _option_path(arguments: tuple[str, ...], option: str) -> Path:
    return Path(_option_value(arguments, option))


def _append(target: list[AgentFragment]) -> Callable[[AgentFragment], Awaitable[None]]:
    async def append(value: AgentFragment) -> None:
        target.append(value)

    return append
