"""Local-only management API for installed, isolated plugins."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Literal, Protocol, cast

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field

from paper_reader.plugins.catalog import PluginCatalogRefresh
from paper_reader.plugins.contracts import PermissionKind, PermissionRequest
from paper_reader.plugins.discovery import PluginInstallation, PluginInstallationRegistry
from paper_reader.plugins.repository import PluginHealthRepository
from paper_reader.plugins.sandbox import SandboxLauncher, SandboxProbe
from paper_reader.security import require_local_mutation, require_local_session

router = APIRouter(prefix="/api/v1/plugins", tags=["plugins"])


class _ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PluginPermissionSummary(_ApiModel):
    kind: PermissionKind
    resource: str
    purpose: str
    granted: bool


class PluginCapabilitySummary(_ApiModel):
    capability_id: str
    kind: str
    methods: tuple[str, ...]
    timeout_seconds: int
    ready: bool
    permissions: tuple[PluginPermissionSummary, ...]


class PluginSummary(_ApiModel):
    plugin_id: str
    display_name: str
    distribution_name: str
    distribution_version: str
    license: str
    protocol_minimum: str
    protocol_maximum: str
    compatible: bool
    compatibility_reason: str | None
    enabled: bool
    status: Literal[
        "ready",
        "disabled",
        "incompatible",
        "permissions_required",
        "circuit_open",
        "sandbox_unavailable",
    ]
    health_reason: str | None
    consecutive_failures: int
    circuit_open_reason: str | None
    last_called_at: datetime | None
    sandbox_available: bool
    sandbox_reason: str | None
    capabilities: tuple[PluginCapabilitySummary, ...]


class PluginEnabledRequest(_ApiModel):
    enabled: bool


class PluginPermissionDecisionRequest(_ApiModel):
    kind: PermissionKind
    resource: str = Field(min_length=2, max_length=253)
    decision: Literal["grant", "revoke"]


class PluginRefreshIssueSummary(_ApiModel):
    plugin_id: str | None
    code: str


class PluginRefreshSummary(_ApiModel):
    discovered: int
    registered: int
    issues: tuple[PluginRefreshIssueSummary, ...]


class _PluginCatalogLike(Protocol):
    async def refresh(self) -> PluginCatalogRefresh: ...


@router.get("", dependencies=[Depends(require_local_session)])
def list_plugins(request: Request) -> list[PluginSummary]:
    registry = _registry(request)
    probe = _probe(request)
    return [_summary(item, registry=registry, probe=probe) for item in registry.list_all()]


@router.post(
    "/refresh",
    dependencies=[Depends(require_local_mutation)],
)
async def refresh_plugins(request: Request) -> PluginRefreshSummary:
    refreshed = await _catalog(request).refresh()
    _refresh_adapters(request)
    return PluginRefreshSummary(
        discovered=refreshed.discovered,
        registered=refreshed.registered,
        issues=tuple(
            PluginRefreshIssueSummary(plugin_id=issue.plugin_id, code=issue.code)
            for issue in refreshed.issues
        ),
    )


@router.put(
    "/{plugin_id}/enabled",
    dependencies=[Depends(require_local_mutation)],
)
def set_plugin_enabled(
    plugin_id: str,
    payload: PluginEnabledRequest,
    request: Request,
) -> PluginSummary:
    registry = _registry(request)
    try:
        changed = registry.set_enabled(plugin_id, payload.enabled)
    except KeyError as error:
        raise _not_found() from error
    except ValueError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
    _refresh_adapters(request)
    return _summary(changed, registry=registry, probe=_probe(request))


@router.post(
    "/{plugin_id}/permissions",
    dependencies=[Depends(require_local_mutation)],
)
def decide_plugin_permission(
    plugin_id: str,
    payload: PluginPermissionDecisionRequest,
    request: Request,
) -> PluginSummary:
    registry = _registry(request)
    try:
        installation = registry.require(plugin_id)
    except KeyError as error:
        raise _not_found() from error
    declared = {
        (permission.kind, permission.resource): permission
        for permission in installation.manifest.requested_permissions()
    }
    permission = declared.get((payload.kind, payload.resource.strip().casefold()))
    if permission is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Permission was not declared by the current plugin manifest",
        )
    try:
        registry.record_permission_decision(
            plugin_id,
            PermissionRequest(
                kind=permission.kind,
                resource=permission.resource,
                purpose=permission.purpose,
            ),
            decision=payload.decision,
            decided_at=datetime.now(UTC),
        )
    except ValueError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
    _refresh_adapters(request)
    return _summary(registry.require(plugin_id), registry=registry, probe=_probe(request))


@router.post(
    "/{plugin_id}/reset",
    dependencies=[Depends(require_local_mutation)],
)
def reset_plugin_circuit(plugin_id: str, request: Request) -> PluginSummary:
    try:
        changed = _health(request).reset_circuit(plugin_id, reset_at=datetime.now(UTC))
    except KeyError as error:
        raise _not_found() from error
    _refresh_adapters(request)
    return _summary(changed, registry=_registry(request), probe=_probe(request))


def _summary(
    installation: PluginInstallation,
    *,
    registry: PluginInstallationRegistry,
    probe: SandboxProbe,
) -> PluginSummary:
    granted = {
        (permission.kind, permission.resource)
        for permission in registry.effective_permissions(installation.plugin_id)
    }
    base_ready = (
        installation.compatible
        and installation.enabled
        and installation.circuit_open_reason is None
        and probe.available
    )
    capabilities = tuple(
        PluginCapabilitySummary(
            capability_id=capability.capability_id,
            kind=capability.kind.value,
            methods=capability.methods,
            timeout_seconds=capability.timeout_seconds,
            ready=base_ready
            and all(
                (permission.kind, permission.resource) in granted
                for permission in capability.permissions
            ),
            permissions=tuple(
                PluginPermissionSummary(
                    kind=permission.kind,
                    resource=permission.resource,
                    purpose=permission.purpose,
                    granted=(permission.kind, permission.resource) in granted,
                )
                for permission in capability.permissions
            ),
        )
        for capability in installation.manifest.capabilities
    )
    plugin_status, health_reason = _health_status(
        installation,
        sandbox=probe,
        all_capabilities_ready=all(item.ready for item in capabilities),
    )
    return PluginSummary(
        plugin_id=installation.plugin_id,
        display_name=installation.manifest.display_name,
        distribution_name=installation.distribution_name,
        distribution_version=installation.distribution_version,
        license=installation.manifest.license,
        protocol_minimum=installation.manifest.protocol.minimum,
        protocol_maximum=installation.manifest.protocol.maximum,
        compatible=installation.compatible,
        compatibility_reason=installation.compatibility_reason,
        enabled=installation.enabled,
        status=plugin_status,
        health_reason=health_reason,
        consecutive_failures=installation.consecutive_failures,
        circuit_open_reason=installation.circuit_open_reason,
        last_called_at=installation.last_called_at,
        sandbox_available=probe.available,
        sandbox_reason=probe.code,
        capabilities=capabilities,
    )


def _health_status(
    installation: PluginInstallation,
    *,
    sandbox: SandboxProbe,
    all_capabilities_ready: bool,
) -> tuple[
    Literal[
        "ready",
        "disabled",
        "incompatible",
        "permissions_required",
        "circuit_open",
        "sandbox_unavailable",
    ],
    str | None,
]:
    if not installation.compatible:
        return "incompatible", installation.compatibility_reason or "protocol_incompatible"
    if not installation.enabled:
        return "disabled", "plugin_disabled"
    if installation.circuit_open_reason is not None:
        return "circuit_open", installation.circuit_open_reason
    if not sandbox.available:
        return "sandbox_unavailable", sandbox.code or "sandbox_unavailable"
    if not all_capabilities_ready:
        return "permissions_required", "permissions_required"
    return "ready", None


def _registry(request: Request) -> PluginInstallationRegistry:
    value = getattr(request.app.state, "plugin_installations", None)
    if not isinstance(value, PluginInstallationRegistry):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Plugin registry is unavailable",
        )
    return value


def _health(request: Request) -> PluginHealthRepository:
    value = getattr(request.app.state, "plugin_health", None)
    if not isinstance(value, PluginHealthRepository):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Plugin health service is unavailable",
        )
    return value


def _catalog(request: Request) -> _PluginCatalogLike:
    value: Any = getattr(request.app.state, "plugin_catalog", None)
    if value is None or not callable(getattr(value, "refresh", None)):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Plugin discovery service is unavailable",
        )
    return cast(_PluginCatalogLike, value)


def _probe(request: Request) -> SandboxProbe:
    launcher = getattr(request.app.state, "plugin_sandbox_launcher", None)
    if launcher is None:
        return SandboxProbe(
            available=False,
            code="sandbox_unavailable",
            app_container=False,
            job_object=False,
        )
    typed_launcher: SandboxLauncher = launcher
    return typed_launcher.probe()


def _refresh_adapters(request: Request) -> None:
    callback: Callable[[], None] | None = getattr(
        request.app.state,
        "plugin_adapter_refresh",
        None,
    )
    if callback is not None:
        callback()


def _not_found() -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Plugin not found")
