"""Codex CLI backend with fail-closed inherited-tool isolation."""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import stat
import subprocess
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any, Protocol, cast

from paper_reader.agents.contracts import (
    AgentBackendError,
    AgentCapability,
    AgentErrorCode,
    AgentFragment,
    AgentLocation,
    AgentModel,
    AgentProbeCode,
    AgentRequest,
    AgentResult,
    AgentUsage,
    CancellationCheck,
    FragmentCallback,
    ModelCatalog,
    ReasoningEffort,
    ServiceTier,
    is_visual_attachment,
)
from paper_reader.agents.process_policy import (
    SecureProcessPolicy,
    SecureProcessResult,
    build_allowlisted_environment,
    cleanup_task_directory,
    create_task_directory,
    redact_diagnostic,
    run_secure_process,
)

_MINIMUM_CODEX_VERSION = (0, 100, 0)
_VERSION = re.compile(r"\bcodex-cli\s+(\d+)\.(\d+)\.(\d+)(?:[-+][^\s]+)?\b")
_CONFIGURATION_ERROR = re.compile(
    r"(?:config(?:uration)?(?:\.toml)?|toml).*?"
    r"(?:error|invalid|parse|unknown|variant|expected)",
    re.IGNORECASE | re.DOTALL,
)
_MODEL_COMMAND = re.compile(r"(?m)^\s*models(?:\s|$)")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_MAX_MCP_SERVERS = 100
_MAX_FINAL_OUTPUT_BYTES = 10 * 1024 * 1024
_DISABLED_FEATURES = (
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
)


def _usable_codex_path(value: str | os.PathLike[str] | None) -> Path | None:
    if value is None:
        return None
    candidate = Path(value)
    if candidate.suffix.casefold() in {".ps1", ".cmd", ".bat"} or not candidate.is_file():
        return None
    return candidate


def _bundled_codex_paths(environment: Mapping[str, str]) -> tuple[Path, ...]:
    local_app_data = environment.get("LOCALAPPDATA")
    if not local_app_data:
        return ()
    root = Path(local_app_data) / "OpenAI" / "Codex" / "bin"
    candidates = tuple(
        candidate
        for candidate in root.glob("*/codex.exe")
        if candidate.is_file()
    )
    return tuple(sorted(candidates, key=lambda path: path.stat().st_mtime_ns, reverse=True))


def _chatgpt_process_running() -> bool:
    """Return only a boolean; process paths and command lines stay private."""

    if os.name != "nt":
        return False
    try:
        completed = subprocess.run(
            ["tasklist", "/fo", "csv", "/nh"],
            capture_output=True,
            check=False,
            timeout=3,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError):
        return False
    output = completed.stdout
    if isinstance(output, bytes):
        text_output = output.decode(errors="ignore")
    elif isinstance(output, str):
        text_output = output
    else:
        text_output = ""
    return any(
        line.strip().casefold().startswith('"chatgpt.exe"')
        for line in text_output.splitlines()
    )


class ProcessRunner(Protocol):
    async def __call__(
        self,
        policy: SecureProcessPolicy,
        *,
        stdin: str,
        cancelled: CancellationCheck,
        on_stdout_line: Callable[[str], Awaitable[None]] | None = None,
    ) -> SecureProcessResult: ...


class CodexCliBackend:
    """Stable `codex exec` adapter; never uses the experimental app server."""

    def __init__(
        self,
        *,
        cache_root: Path,
        executable: Path | None = None,
        process_runner: ProcessRunner = run_secure_process,
        environment: Mapping[str, str] | None = None,
        executable_locator: Callable[[str], str | None] = shutil.which,
        desktop_process_detector: Callable[[], bool] | None = None,
    ) -> None:
        self._cache_root = Path(cache_root)
        self._cache_root.mkdir(parents=True, exist_ok=True)
        explicit_candidate = _usable_codex_path(executable)
        if explicit_candidate is not None:
            # Tests and callers that pin a CLI path expect that executable to
            # be authoritative. Discovery is used only when no usable path was
            # explicitly supplied.
            candidates: list[Path | None] = [explicit_candidate]
        else:
            discovery_environment = os.environ if environment is None else environment
            candidates = [
                _usable_codex_path(executable_locator("codex")),
                _usable_codex_path(executable_locator("codex.exe")),
                *_bundled_codex_paths(discovery_environment),
            ]
        self._candidates = tuple(dict.fromkeys(path for path in candidates if path is not None))
        self._executable = self._candidates[0] if self._candidates else None
        self._legacy_hooks_config = executable is not None
        self._desktop_process_detector = desktop_process_detector or _chatgpt_process_running
        self._process_runner = process_runner
        self._environment = build_allowlisted_environment(
            os.environ if environment is None else environment
        )
        self._probe_directory = self._cache_root / "agent-probes"
        self._probe_directory.mkdir(parents=True, exist_ok=True)
        self._cli_version: str | None = None
        self._capability = self._new_capability(
            probe_code=(
                AgentProbeCode.UNAVAILABLE
                if self._executable is not None
                else AgentProbeCode.NOT_INSTALLED
            ),
            installed=self._executable is not None,
            authenticated=False,
            available=False,
            degradation_reasons=(
                ("codex_capability_not_probed",)
                if self._executable is not None
                else ("codex_cli_not_installed",)
            ),
            desktop_app_running=None,
        )

    @property
    def capability(self) -> AgentCapability:
        return self._capability

    @property
    def cli_version(self) -> str | None:
        return self._cli_version

    async def probe(self) -> AgentCapability:
        desktop_running = await asyncio.to_thread(self._desktop_process_detector)
        self._capability = replace(self._capability, desktop_app_running=desktop_running)
        if self._executable is None:
            return self._capability

        first_failure: tuple[Path, AgentCapability, str | None] | None = None
        for candidate in self._candidates:
            self._executable = candidate
            capability = await self._probe_candidate()
            if capability.available:
                return capability
            if first_failure is None:
                first_failure = (candidate, capability, self._cli_version)
        if first_failure is not None:
            self._executable, self._capability, self._cli_version = first_failure
        return self._capability

    async def _probe_candidate(self) -> AgentCapability:
        self._cli_version = None

        version_result: SecureProcessResult | None = None
        login_result: SecureProcessResult | None = None
        version_error: AgentBackendError | None = None
        login_error: AgentBackendError | None = None
        try:
            version_result = await self._run(
                (*self._safe_arguments(legacy_hooks=self._legacy_hooks_config), "--version"),
                working_directory=self._probe_directory,
                timeout_seconds=15,
            )
        except AgentBackendError as error:
            version_error = error
        try:
            login_result = await self._run(
                (*self._safe_arguments(legacy_hooks=self._legacy_hooks_config), "login", "status"),
                working_directory=self._probe_directory,
                timeout_seconds=15,
            )
        except AgentBackendError as error:
            login_error = error

        version_supported = False
        version_recognized = False
        version_too_old = False
        if version_result is not None and version_result.exit_code == 0:
            matched = _VERSION.search(f"{version_result.stdout}\n{version_result.stderr}")
            if matched is not None:
                version_recognized = True
                parts = tuple(int(value) for value in matched.groups())
                self._cli_version = ".".join(matched.groups())
                version_supported = parts >= _MINIMUM_CODEX_VERSION
                version_too_old = not version_supported

        login_text = (
            f"{login_result.stdout}\n{login_result.stderr}".casefold()
            if login_result is not None
            else ""
        )
        authenticated = bool(
            login_result is not None
            and login_result.exit_code == 0
            and "logged in" in login_text
            and "not logged in" not in login_text
        )
        logged_out = any(
            marker in login_text
            for marker in ("not logged in", "logged out", "authentication required")
        )
        configuration_invalid = bool(
            login_result is not None and _CONFIGURATION_ERROR.search(login_text)
        )

        probe_code: AgentProbeCode
        reasons: tuple[str, ...]
        if version_too_old:
            probe_code = AgentProbeCode.VERSION_UNSUPPORTED
            reasons = ("codex_cli_too_old",)
        elif any(
            error is not None and error.code is AgentErrorCode.TIMEOUT
            for error in (version_error, login_error)
        ):
            probe_code = AgentProbeCode.PROBE_TIMEOUT
            reasons = ("codex_probe_timeout",)
        elif configuration_invalid:
            probe_code = AgentProbeCode.CONFIGURATION_INVALID
            reasons = ("codex_configuration_invalid",)
        elif version_error is not None or version_result is None:
            probe_code = AgentProbeCode.UNAVAILABLE
            reasons = ("codex_version_probe_failed",)
        elif not version_recognized:
            probe_code = AgentProbeCode.UNAVAILABLE
            reasons = ("codex_version_unrecognized",)
        elif authenticated and version_supported:
            probe_code = AgentProbeCode.READY
            reasons = ()
        elif logged_out or (
            login_error is not None
            and login_error.code is AgentErrorCode.AUTHENTICATION
        ):
            probe_code = AgentProbeCode.NOT_AUTHENTICATED
            reasons = ("codex_not_authenticated",)
        else:
            probe_code = AgentProbeCode.UNAVAILABLE
            reasons = ("codex_login_probe_failed",)
        self._capability = self._new_capability(
            probe_code=probe_code,
            installed=True,
            authenticated=authenticated,
            available=probe_code is AgentProbeCode.READY,
            degradation_reasons=reasons,
            desktop_app_running=self._capability.desktop_app_running,
        )
        return self._capability

    async def discover_models(self) -> ModelCatalog:
        if self._executable is None:
            return self._fallback_catalog("codex_cli_not_installed")
        try:
            help_result = await self._run(
                (*self._safe_arguments(legacy_hooks=self._legacy_hooks_config), "debug", "--help"),
                working_directory=self._probe_directory,
                timeout_seconds=20,
            )
            if help_result.exit_code != 0 or not _MODEL_COMMAND.search(help_result.stdout):
                return self._fallback_catalog("codex_model_discovery_unavailable")
            catalog_result = await self._run(
                (
                    *self._safe_arguments(legacy_hooks=self._legacy_hooks_config),
                    "debug",
                    "models",
                    "--json",
                ),
                working_directory=self._probe_directory,
                timeout_seconds=30,
            )
            if catalog_result.exit_code != 0:
                return self._fallback_catalog("codex_model_discovery_failed")
            models = self._parse_model_catalog(catalog_result.stdout)
            return ModelCatalog(
                models=models,
                supports_manual_model_id=True,
                supports_default_model=True,
            )
        except (AgentBackendError, ValueError, json.JSONDecodeError):
            return self._fallback_catalog("codex_model_discovery_failed")

    async def generate(
        self,
        request: AgentRequest,
        *,
        on_fragment: FragmentCallback,
        cancelled: CancellationCheck,
    ) -> AgentResult:
        if self._executable is None:
            raise AgentBackendError(
                AgentErrorCode.UNAVAILABLE,
                "Codex CLI is not installed",
            )
        task_directory = create_task_directory(self._cache_root, request.task_id)
        cleanup_task_directory(self._cache_root, task_directory)
        task_directory = create_task_directory(self._cache_root, request.task_id)
        try:
            schema_path = task_directory / "output-schema.json"
            output_path = task_directory / "output.json"
            schema_path.write_text(
                json.dumps(request.output_schema, ensure_ascii=False, separators=(",", ":")),
                encoding="utf-8",
            )
            _mark_read_only(schema_path)
            visual_paths: list[Path] = []
            for attachment in request.attachments:
                attachment_path = task_directory / attachment.name
                attachment_path.write_bytes(attachment.content)
                _mark_read_only(attachment_path)
                if is_visual_attachment(attachment):
                    visual_paths.append(attachment_path)

            mcp_names = await self._mcp_server_names(
                task_directory,
                cancelled=cancelled,
            )
            arguments = list(
                self._safe_arguments(
                    reasoning_effort=request.reasoning_effort,
                    service_tier=request.service_tier,
                    external_research=request.external_research,
                    legacy_hooks=self._legacy_hooks_config,
                )
            )
            for name in mcp_names:
                # A plugin inventory may include virtual servers absent from the
                # config. A bare enabled=false creates a transport-less entry.
                if self._legacy_hooks_config:
                    arguments.extend(("-c", f"mcp_servers.{_toml_key(name)}.enabled=false"))
                else:
                    arguments.extend((
                        "-c",
                        f"mcp_servers.{_toml_key(name)}="
                        '{enabled=false,command="disabled-paper-reader-mcp",args=[]}',
                    ))
            arguments.extend(
                (
                    "exec",
                    "--json",
                    "--output-schema",
                    str(schema_path),
                    "-o",
                    str(output_path),
                    "--sandbox",
                    "read-only",
                    "--ephemeral",
                    "--skip-git-repo-check",
                    "-C",
                    str(task_directory),
                )
            )
            if request.model_id is not None:
                arguments.extend(("--model", request.model_id))
            for visual_path in visual_paths:
                arguments.extend(("--image", str(visual_path)))
            arguments.append("-")

            parser = _CodexEventParser(on_fragment)
            result = await self._run(
                tuple(arguments),
                working_directory=task_directory,
                timeout_seconds=request.timeout_seconds,
                output_limit_bytes=16 * 1024 * 1024,
                stdin=request.prompt,
                cancelled=cancelled,
                on_stdout_line=parser.accept,
            )
            if result.exit_code != 0:
                raise AgentBackendError(
                    AgentErrorCode.PROCESS,
                    "Codex exited before producing a valid report",
                    retryable=True,
                    diagnostic=redact_diagnostic(result.stderr or result.stdout),
                )
            raw_output, structured_output = _read_final_output(output_path)
            return AgentResult(
                raw_output=raw_output,
                structured_output=structured_output,
                resolved_model_id=parser.resolved_model_id or request.model_id,
                usage=parser.usage,
            )
        finally:
            cleanup_task_directory(self._cache_root, task_directory)

    async def _mcp_server_names(
        self,
        working_directory: Path,
        *,
        cancelled: CancellationCheck,
    ) -> tuple[str, ...]:
        result = await self._run(
            (
                *self._safe_arguments(legacy_hooks=self._legacy_hooks_config),
                "mcp",
                "list",
                "--json",
            ),
            working_directory=working_directory,
            timeout_seconds=20,
            cancelled=cancelled,
        )
        if result.exit_code != 0:
            raise AgentBackendError(
                AgentErrorCode.PROTOCOL,
                "Codex MCP inventory failed; generation was blocked",
                diagnostic=redact_diagnostic(result.stderr),
            )
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError as error:
            raise AgentBackendError(
                AgentErrorCode.PROTOCOL,
                "Codex MCP inventory was not valid JSON; generation was blocked",
            ) from error
        if not isinstance(payload, list) or len(payload) > _MAX_MCP_SERVERS:
            raise AgentBackendError(
                AgentErrorCode.PROTOCOL,
                "Codex MCP inventory had an invalid shape; generation was blocked",
            )
        names: list[str] = []
        for item in payload:
            if not isinstance(item, dict) or not isinstance(item.get("name"), str):
                raise AgentBackendError(
                    AgentErrorCode.PROTOCOL,
                    "Codex MCP inventory omitted a server name; generation was blocked",
                )
            name = cast(str, item["name"])
            if not name.strip() or len(name) > 200 or _CONTROL.search(name):
                raise AgentBackendError(
                    AgentErrorCode.PROTOCOL,
                    "Codex MCP inventory contained an unsafe server name",
                )
            names.append(name)
        return tuple(dict.fromkeys(names))

    async def _run(
        self,
        arguments: tuple[str, ...],
        *,
        working_directory: Path,
        timeout_seconds: float,
        output_limit_bytes: int = 1024 * 1024,
        stdin: str = "",
        cancelled: CancellationCheck = lambda: False,
        on_stdout_line: Callable[[str], Awaitable[None]] | None = None,
    ) -> SecureProcessResult:
        if self._executable is None:  # pragma: no cover - guarded by callers
            raise AgentBackendError(AgentErrorCode.UNAVAILABLE, "Codex CLI is not installed")
        policy = SecureProcessPolicy(
            executable=self._executable,
            arguments=arguments,
            environment=self._environment,
            working_directory=working_directory,
            timeout_seconds=timeout_seconds,
            output_limit_bytes=output_limit_bytes,
        )
        return await self._process_runner(
            policy,
            stdin=stdin,
            cancelled=cancelled,
            on_stdout_line=on_stdout_line,
        )

    @staticmethod
    def _safe_arguments(
        *,
        reasoning_effort: ReasoningEffort = ReasoningEffort.HIGH,
        service_tier: ServiceTier = ServiceTier.FLEX,
        external_research: bool = False,
        legacy_hooks: bool = False,
    ) -> tuple[str, ...]:
        arguments: list[str] = [
            "-c",
            f'model_reasoning_effort="{reasoning_effort.value}"',
            "-c",
            f'service_tier="{service_tier.value}"',
            "-c",
            'approval_policy="never"',
            "-c",
            f'web_search="{"live" if external_research else "disabled"}"',
            "-c",
            "hooks=[]" if legacy_hooks else "hooks={}",
            "-c",
            "notify=[]",
            "-c",
            "project_doc_max_bytes=0",
            "-c",
            'shell_environment_policy.inherit="none"',
            "-a",
            "never",
        ]
        for feature in _DISABLED_FEATURES:
            arguments.extend(("--disable", feature))
        if external_research:
            arguments.append("--search")
        else:
            for feature in ("search_tool", "web_search_request", "web_search_cached"):
                arguments.extend(("--disable", feature))
        return tuple(arguments)

    @staticmethod
    def _parse_model_catalog(value: str) -> tuple[AgentModel, ...]:
        payload: Any = json.loads(value)
        if isinstance(payload, dict):
            payload = payload.get("models")
        if not isinstance(payload, list) or len(payload) > 500:
            raise ValueError("Codex model catalog has an invalid shape")
        models: list[AgentModel] = []
        for item in payload:
            if not isinstance(item, dict):
                raise ValueError("Codex model catalog item is invalid")
            model_id = item.get("id") or item.get("model") or item.get("slug")
            if not isinstance(model_id, str):
                raise ValueError("Codex model catalog item has no ID")
            display_name = item.get("display_name") or item.get("name") or model_id
            context_window = item.get("context_window")
            models.append(
                AgentModel(
                    model_id=model_id,
                    display_name=display_name if isinstance(display_name, str) else model_id,
                    supports_vision=(
                        item.get("supports_vision")
                        if isinstance(item.get("supports_vision"), bool)
                        else None
                    ),
                    context_window=(context_window if isinstance(context_window, int) else None),
                )
            )
        return tuple(models)

    @staticmethod
    def _fallback_catalog(reason: str) -> ModelCatalog:
        return ModelCatalog(
            supports_manual_model_id=True,
            supports_default_model=True,
            degradation_reasons=(reason,),
        )

    @staticmethod
    def _new_capability(
        *,
        probe_code: AgentProbeCode,
        installed: bool,
        authenticated: bool,
        available: bool,
        degradation_reasons: tuple[str, ...],
        desktop_app_running: bool | None = None,
    ) -> AgentCapability:
        return AgentCapability(
            backend_id="codex_cli",
            display_name="Codex CLI",
            location=AgentLocation.CLOUD,
            probe_code=probe_code,
            installed=installed,
            authenticated=authenticated,
            available=available,
            supports_schema=True,
            supports_streaming=True,
            supports_vision=True,
            supports_external_research=True,
            degradation_reasons=degradation_reasons,
            desktop_app_running=desktop_app_running,
        )


class _CodexEventParser:
    def __init__(self, on_fragment: FragmentCallback) -> None:
        self._on_fragment = on_fragment
        self._fragment_index = 0
        self.usage = AgentUsage()
        self.resolved_model_id: str | None = None

    async def accept(self, line: str) -> None:
        if not line.strip():
            return
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as error:
            raise AgentBackendError(
                AgentErrorCode.PROTOCOL,
                "Codex emitted malformed JSONL",
            ) from error
        if not isinstance(payload, dict) or not isinstance(payload.get("type"), str):
            raise AgentBackendError(
                AgentErrorCode.PROTOCOL,
                "Codex emitted an invalid JSONL event",
            )
        event_type = cast(str, payload["type"])
        for key in ("model", "model_id", "resolved_model_id"):
            model_id = payload.get(key)
            if isinstance(model_id, str) and model_id.strip():
                self.resolved_model_id = model_id.strip()
                break
        event_item = payload.get("item")
        if isinstance(event_item, dict):
            for key in ("model", "model_id", "resolved_model_id"):
                model_id = event_item.get(key)
                if isinstance(model_id, str) and model_id.strip():
                    self.resolved_model_id = model_id.strip()
                    break
        if event_type == "item.completed":
            item = payload.get("item")
            if isinstance(item, dict) and item.get("type") == "agent_message":
                text = item.get("text")
                if not isinstance(text, str):
                    raise AgentBackendError(
                        AgentErrorCode.PROTOCOL,
                        "Codex Agent message did not contain text",
                    )
                await self._on_fragment(
                    AgentFragment(index=self._fragment_index, kind="text", text=text)
                )
                self._fragment_index += 1
        elif event_type == "turn.completed":
            usage = payload.get("usage")
            if isinstance(usage, dict):
                self.usage = AgentUsage(
                    input_tokens=_optional_nonnegative_int(usage.get("input_tokens")),
                    output_tokens=_optional_nonnegative_int(usage.get("output_tokens")),
                )


def _optional_nonnegative_int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _read_final_output(path: Path) -> tuple[str, dict[str, Any]]:
    try:
        size = path.stat().st_size
    except OSError as error:
        raise AgentBackendError(
            AgentErrorCode.INVALID_OUTPUT,
            "Codex did not write its structured final output",
        ) from error
    if size > _MAX_FINAL_OUTPUT_BYTES:
        raise AgentBackendError(
            AgentErrorCode.INVALID_OUTPUT,
            "Codex structured final output exceeded the maximum size",
        )
    try:
        raw = path.read_text(encoding="utf-8")
        payload = json.loads(raw)
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise AgentBackendError(
            AgentErrorCode.INVALID_OUTPUT,
            "Codex structured final output was not valid JSON",
        ) from error
    if not isinstance(payload, dict):
        raise AgentBackendError(
            AgentErrorCode.INVALID_OUTPUT,
            "Codex structured final output must be a JSON object",
        )
    return raw, cast(dict[str, Any], payload)


def _mark_read_only(path: Path) -> None:
    path.chmod(stat.S_IREAD)


def _toml_key(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)
