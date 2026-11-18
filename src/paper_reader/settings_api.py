"""Read-only runtime capability settings that never expose credential values."""

from __future__ import annotations

from contextlib import suppress

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field, SecretStr

from paper_reader.agents.contracts import (
    AgentBackendError,
    AgentModel,
    AgentProbeCode,
    ModelCatalog,
)
from paper_reader.agents.diagnostics import agent_recommended_action
from paper_reader.agents.registry import AgentBackendRegistry
from paper_reader.credentials import (
    ENRICHER_CREDENTIAL_SPECS,
    OLLAMA_CLOUD_CREDENTIAL,
    SOURCE_CREDENTIAL_NAMES,
    CredentialStoreError,
    EnricherCredentialSpec,
    resolve_source_credentials,
)
from paper_reader.discovery.connectors import ConnectorRegistry, SourceConnector
from paper_reader.discovery.source_registry import build_source_registry
from paper_reader.recommendation.enrichers.registry import EnricherRegistry, build_enricher_registry
from paper_reader.recommendation.enrichment import MetadataEnricher
from paper_reader.security import require_local_mutation, require_local_session

router = APIRouter(
    prefix="/api/v1/settings",
    tags=["settings"],
    dependencies=[Depends(require_local_session)],
)


class SourceCapabilityResponse(BaseModel):
    connector_id: str
    display_name: str
    credential_requirement: str
    supports_exact_updated_date: bool
    enabled: bool
    disabled_reason: str | None
    credential_configured: bool
    setup_url: str | None
    protocol_version: str


class SourceCredentialUpdate(BaseModel):
    value: SecretStr = Field(min_length=1, max_length=4096)


class EmbeddingModelResponse(BaseModel):
    model_id: str
    revision: str
    dimension: int
    download_size_bytes: int
    license_id: str
    source_url: str


class RetrievalCapabilityResponse(BaseModel):
    lexical_enabled: bool
    semantic_enabled: bool
    model_state: str
    degradation_reasons: list[str]
    model: EmbeddingModelResponse


class EnricherCredentialResponse(BaseModel):
    credential_id: str
    label: str
    required: bool
    secret: bool
    configured: bool


class EnricherCapabilityResponse(BaseModel):
    enricher_id: str
    display_name: str
    version: str
    enabled: bool
    disabled_reason: str | None
    setup_url: str
    default_requests_per_second: float | None
    max_concurrency: int
    credentials: list[EnricherCredentialResponse]


class AgentModelResponse(BaseModel):
    model_id: str
    display_name: str
    supports_vision: bool | None
    context_window: int | None


class AgentCapabilityResponse(BaseModel):
    backend_id: str
    display_name: str
    location: str
    version: str | None = None
    probe_code: AgentProbeCode
    recommended_action: str | None
    installed: bool
    authenticated: bool
    available: bool
    supports_schema: bool
    supports_streaming: bool
    supports_vision: bool
    supports_external_research: bool
    credential_required: bool
    credential_configured: bool
    supports_manual_model_id: bool
    supports_default_model: bool
    models: list[AgentModelResponse]
    degradation_reasons: list[str]
    desktop_app_running: bool | None = None


class AgentCredentialStatusResponse(BaseModel):
    backend_id: str
    configured: bool


def _capability(item: SourceConnector) -> SourceCapabilityResponse:
    return SourceCapabilityResponse(
        connector_id=item.capabilities.connector_id,
        display_name=item.capabilities.display_name,
        credential_requirement=item.capabilities.credential_requirement,
        supports_exact_updated_date=item.capabilities.supports_exact_updated_date,
        enabled=item.capabilities.enabled,
        disabled_reason=item.capabilities.disabled_reason,
        credential_configured=item.capabilities.credential_configured,
        setup_url=item.capabilities.setup_url,
        protocol_version=item.capabilities.protocol_version,
    )


def _refresh_registry(request: Request, connector_id: str) -> SourceCapabilityResponse:
    rebuilt = build_source_registry(
        client=request.app.state.http_client,
        credentials=resolve_source_credentials(request.app.state.credential_store),
    )
    current = request.app.state.connector_registry
    if isinstance(current, ConnectorRegistry):
        current.replace_builtins(rebuilt.all())
    else:  # pragma: no cover - application lifespan installs the concrete registry
        current = rebuilt
        request.app.state.connector_registry = current
    _refresh_plugin_adapters(request)
    connector = current.get(connector_id)
    if connector is None:  # pragma: no cover - built-in map and registry are defined together
        raise RuntimeError("Configured source is missing from the built-in registry")
    return _capability(connector)


def _enricher_capability(
    item: MetadataEnricher,
    *,
    request: Request,
) -> EnricherCapabilityResponse:
    runtime_credentials = resolve_source_credentials(request.app.state.credential_store)
    credential_items = [
        EnricherCredentialResponse(
            credential_id=spec.credential_id,
            label=spec.label,
            required=spec.required,
            secret=spec.secret,
            configured=getattr(runtime_credentials, spec.attribute) is not None,
        )
        for spec in ENRICHER_CREDENTIAL_SPECS
        if spec.enricher_id == item.capabilities.enricher_id
    ]
    capability = item.capabilities
    return EnricherCapabilityResponse(
        enricher_id=capability.enricher_id,
        display_name=capability.display_name,
        version=capability.version,
        enabled=capability.enabled,
        disabled_reason=capability.disabled_reason,
        setup_url=capability.setup_url,
        default_requests_per_second=capability.default_requests_per_second,
        max_concurrency=capability.max_concurrency,
        credentials=credential_items,
    )


def _refresh_enricher_registry(
    request: Request,
    enricher_id: str,
) -> EnricherCapabilityResponse:
    rebuilt = build_enricher_registry(
        client=request.app.state.http_client,
        credentials=resolve_source_credentials(request.app.state.credential_store),
    )
    current = request.app.state.enricher_registry
    if isinstance(current, EnricherRegistry):
        current.replace_builtins(rebuilt.all())
    else:  # pragma: no cover - application lifespan installs the concrete registry
        current = rebuilt
        request.app.state.enricher_registry = current
    _refresh_plugin_adapters(request)
    enricher = current.get(enricher_id)
    if enricher is None:  # pragma: no cover - built-in registry and specs are paired
        raise RuntimeError("Configured metadata enricher is missing from the registry")
    return _enricher_capability(enricher, request=request)


def _ollama_cloud_credential_configured(request: Request) -> bool:
    try:
        value = request.app.state.credential_store.get(OLLAMA_CLOUD_CREDENTIAL)
    except CredentialStoreError:
        return False
    return bool(value and value.strip())


def _refresh_agent_registry(request: Request) -> None:
    factory = getattr(request.app.state, "agent_registry_factory", None)
    if factory is not None:
        key = None
        with suppress(CredentialStoreError):
            key = request.app.state.credential_store.get(OLLAMA_CLOUD_CREDENTIAL)
        rebuilt = factory(key.strip() if key and key.strip() else None)
        current = request.app.state.agent_registry
        if isinstance(current, AgentBackendRegistry):
            current.replace_builtins(rebuilt.all())
        else:  # pragma: no cover - application lifespan installs the concrete registry
            request.app.state.agent_registry = rebuilt
        _refresh_plugin_adapters(request)


def _refresh_plugin_adapters(request: Request) -> None:
    callback = getattr(request.app.state, "plugin_adapter_refresh", None)
    if callable(callback):
        callback()


def _agent_models(models: tuple[AgentModel, ...]) -> list[AgentModelResponse]:
    return [
        AgentModelResponse(
            model_id=item.model_id,
            display_name=item.display_name,
            supports_vision=item.supports_vision,
            context_window=item.context_window,
        )
        for item in models
    ]


def _safe_agent_version(backend: object) -> str | None:
    value = getattr(backend, "cli_version", None)
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    if not cleaned or len(cleaned) > 80 or not cleaned.isprintable():
        return None
    return cleaned


def _credential_name(connector_id: str) -> str:
    credential_name = SOURCE_CREDENTIAL_NAMES.get(connector_id)
    if credential_name is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Source does not accept a user credential",
        )
    return credential_name


def _enricher_credential_spec(
    enricher_id: str,
    credential_id: str,
) -> EnricherCredentialSpec:
    for spec in ENRICHER_CREDENTIAL_SPECS:
        if spec.enricher_id == enricher_id and spec.credential_id == credential_id:
            return spec
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail="Metadata enricher does not accept this credential",
    )


def _validate_enricher_credential(spec: EnricherCredentialSpec, value: str) -> None:
    if spec.credential_id != "contact_email":
        return
    local, separator, domain = value.partition("@")
    if not separator or not local or "." not in domain or domain.startswith("."):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Crossref contact email must be valid",
        )


@router.get("/sources", response_model=list[SourceCapabilityResponse])
def source_capabilities(request: Request) -> list[SourceCapabilityResponse]:
    return [_capability(item) for item in request.app.state.connector_registry.all()]


@router.get("/retrieval", response_model=RetrievalCapabilityResponse)
def retrieval_capability(request: Request) -> RetrievalCapabilityResponse:
    capability = request.app.state.vector_capability
    manifest = capability.manifest
    return RetrievalCapabilityResponse(
        lexical_enabled=True,
        semantic_enabled=capability.enabled,
        model_state=capability.model_state,
        degradation_reasons=list(capability.degradation_reasons),
        model=EmbeddingModelResponse(
            model_id=manifest.model_id,
            revision=manifest.revision,
            dimension=manifest.dimension,
            download_size_bytes=manifest.download_size_bytes,
            license_id=manifest.license_id,
            source_url=manifest.source_url,
        ),
    )


@router.get("/enrichers", response_model=list[EnricherCapabilityResponse])
def enricher_capabilities(request: Request) -> list[EnricherCapabilityResponse]:
    return [
        _enricher_capability(item, request=request)
        for item in request.app.state.enricher_registry.all()
    ]


@router.get(
    "/agents",
    response_model=list[AgentCapabilityResponse],
)
async def agent_capabilities(request: Request) -> list[AgentCapabilityResponse]:
    responses: list[AgentCapabilityResponse] = []
    cloud_credential = _ollama_cloud_credential_configured(request)
    for backend in request.app.state.agent_registry.all():
        probe = getattr(backend, "probe", None)
        if callable(probe):
            with suppress(AgentBackendError):
                await probe()
        capability = backend.capability
        cached_catalog = getattr(backend, "cached_model_catalog", None)
        if isinstance(cached_catalog, ModelCatalog):
            catalog = cached_catalog
            catalog_reasons = catalog.degradation_reasons
        else:
            try:
                catalog = await backend.discover_models()
                catalog_reasons = catalog.degradation_reasons
            except AgentBackendError as error:
                catalog = ModelCatalog()
                catalog_reasons = (f"model_discovery_{error.code.value}",)
        reasons = list(
            dict.fromkeys((*capability.degradation_reasons, *catalog_reasons))
        )
        responses.append(
            AgentCapabilityResponse(
                backend_id=capability.backend_id,
                display_name=capability.display_name,
                location=capability.location.value,
                version=_safe_agent_version(backend),
                probe_code=capability.probe_code,
                recommended_action=agent_recommended_action(capability.probe_code),
                installed=capability.installed,
                authenticated=capability.authenticated,
                available=capability.available,
                supports_schema=capability.supports_schema,
                supports_streaming=capability.supports_streaming,
                supports_vision=capability.supports_vision,
                supports_external_research=capability.supports_external_research,
                credential_required=capability.backend_id == "ollama_cloud",
                credential_configured=(
                    cloud_credential if capability.backend_id == "ollama_cloud" else False
                ),
                supports_manual_model_id=catalog.supports_manual_model_id,
                supports_default_model=catalog.supports_default_model,
                models=_agent_models(catalog.models),
                degradation_reasons=reasons,
                desktop_app_running=capability.desktop_app_running,
            )
        )
    return responses


@router.put(
    "/agents/ollama-cloud/credential",
    response_model=AgentCredentialStatusResponse,
    dependencies=[Depends(require_local_mutation)],
)
def save_ollama_cloud_credential(
    payload: SourceCredentialUpdate,
    request: Request,
) -> AgentCredentialStatusResponse:
    value = payload.value.get_secret_value().strip()
    if not value:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Credential value cannot be blank",
        )
    try:
        request.app.state.credential_store.set(OLLAMA_CLOUD_CREDENTIAL, value)
    except CredentialStoreError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "credential_store_unavailable",
                "message": "Windows credential storage is unavailable",
            },
        ) from error
    _refresh_agent_registry(request)
    return AgentCredentialStatusResponse(backend_id="ollama_cloud", configured=True)


@router.delete(
    "/agents/ollama-cloud/credential",
    response_model=AgentCredentialStatusResponse,
    dependencies=[Depends(require_local_mutation)],
)
def delete_ollama_cloud_credential(request: Request) -> AgentCredentialStatusResponse:
    try:
        request.app.state.credential_store.delete(OLLAMA_CLOUD_CREDENTIAL)
    except CredentialStoreError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "credential_store_unavailable",
                "message": "Windows credential storage is unavailable",
            },
        ) from error
    _refresh_agent_registry(request)
    return AgentCredentialStatusResponse(backend_id="ollama_cloud", configured=False)


@router.put(
    "/sources/{connector_id}/credential",
    response_model=SourceCapabilityResponse,
    dependencies=[Depends(require_local_mutation)],
)
def save_source_credential(
    connector_id: str,
    payload: SourceCredentialUpdate,
    request: Request,
) -> SourceCapabilityResponse:
    credential_name = _credential_name(connector_id)
    value = payload.value.get_secret_value().strip()
    if not value:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Credential value cannot be blank",
        )
    try:
        request.app.state.credential_store.set(credential_name, value)
    except CredentialStoreError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "credential_store_unavailable",
                "message": "Windows credential storage is unavailable",
            },
        ) from error
    return _refresh_registry(request, connector_id)


@router.delete(
    "/sources/{connector_id}/credential",
    response_model=SourceCapabilityResponse,
    dependencies=[Depends(require_local_mutation)],
)
def delete_source_credential(
    connector_id: str,
    request: Request,
) -> SourceCapabilityResponse:
    credential_name = _credential_name(connector_id)
    try:
        request.app.state.credential_store.delete(credential_name)
    except CredentialStoreError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "credential_store_unavailable",
                "message": "Windows credential storage is unavailable",
            },
        ) from error
    return _refresh_registry(request, connector_id)


@router.put(
    "/enrichers/{enricher_id}/credentials/{credential_id}",
    response_model=EnricherCapabilityResponse,
    dependencies=[Depends(require_local_mutation)],
)
def save_enricher_credential(
    enricher_id: str,
    credential_id: str,
    payload: SourceCredentialUpdate,
    request: Request,
) -> EnricherCapabilityResponse:
    spec = _enricher_credential_spec(enricher_id, credential_id)
    value = payload.value.get_secret_value().strip()
    if not value:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Credential value cannot be blank",
        )
    _validate_enricher_credential(spec, value)
    try:
        request.app.state.credential_store.set(spec.vault_name, value)
    except CredentialStoreError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "credential_store_unavailable",
                "message": "Windows credential storage is unavailable",
            },
        ) from error
    return _refresh_enricher_registry(request, enricher_id)


@router.delete(
    "/enrichers/{enricher_id}/credentials/{credential_id}",
    response_model=EnricherCapabilityResponse,
    dependencies=[Depends(require_local_mutation)],
)
def delete_enricher_credential(
    enricher_id: str,
    credential_id: str,
    request: Request,
) -> EnricherCapabilityResponse:
    spec = _enricher_credential_spec(enricher_id, credential_id)
    try:
        request.app.state.credential_store.delete(spec.vault_name)
    except CredentialStoreError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "credential_store_unavailable",
                "message": "Windows credential storage is unavailable",
            },
        ) from error
    return _refresh_enricher_registry(request, enricher_id)
