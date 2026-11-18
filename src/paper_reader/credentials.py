"""Credential-vault boundary for source API keys."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import keyring

from paper_reader.settings import SourceCredentials

SERVICE_NAME = "paper-reader"
OLLAMA_CLOUD_CREDENTIAL = "agent.ollama_cloud.api_key"
SOURCE_CREDENTIAL_NAMES = {
    "ieee": "source.ieee.api_key",
    "springer_nature": "source.springer_nature.api_key",
    "openreview": "source.openreview.access_token",
}


@dataclass(frozen=True, slots=True)
class EnricherCredentialSpec:
    enricher_id: str
    credential_id: str
    label: str
    attribute: str
    vault_name: str
    required: bool
    secret: bool


ENRICHER_CREDENTIAL_SPECS = (
    EnricherCredentialSpec(
        enricher_id="openalex",
        credential_id="api_key",
        label="OpenAlex API key",
        attribute="openalex_api_key",
        vault_name="enricher.openalex.api_key",
        required=True,
        secret=True,
    ),
    EnricherCredentialSpec(
        enricher_id="semantic_scholar",
        credential_id="api_key",
        label="Semantic Scholar API key",
        attribute="semantic_scholar_api_key",
        vault_name="enricher.semantic_scholar.api_key",
        required=False,
        secret=True,
    ),
    EnricherCredentialSpec(
        enricher_id="crossref",
        credential_id="contact_email",
        label="Crossref contact email",
        attribute="crossref_contact_email",
        vault_name="enricher.crossref.contact_email",
        required=False,
        secret=False,
    ),
    EnricherCredentialSpec(
        enricher_id="crossref",
        credential_id="plus_api_token",
        label="Crossref Metadata Plus token",
        attribute="crossref_plus_api_token",
        vault_name="enricher.crossref.plus_api_token",
        required=False,
        secret=True,
    ),
)
ENRICHER_CREDENTIAL_NAMES = {
    (spec.enricher_id, spec.credential_id): spec.vault_name
    for spec in ENRICHER_CREDENTIAL_SPECS
}


class CredentialStoreError(RuntimeError):
    pass


class CredentialStore(Protocol):
    def get(self, name: str) -> str | None: ...

    def set(self, name: str, value: str) -> None: ...

    def delete(self, name: str) -> None: ...


class NullCredentialStore:
    """Non-persistent default for embedded/test app factories."""

    def get(self, name: str) -> str | None:
        return None

    def set(self, name: str, value: str) -> None:
        raise CredentialStoreError("Credential storage is unavailable")

    def delete(self, name: str) -> None:
        raise CredentialStoreError("Credential storage is unavailable")


class KeyringCredentialStore:
    """OS-backed implementation; Windows resolves to Credential Manager."""

    def get(self, name: str) -> str | None:
        try:
            value = keyring.get_password(SERVICE_NAME, name)
        except Exception as error:  # backend implementations expose different error subclasses
            raise CredentialStoreError("Credential storage is unavailable") from error
        return _clean(value)

    def set(self, name: str, value: str) -> None:
        cleaned = _clean(value)
        if cleaned is None:
            raise ValueError("Credential value cannot be blank")
        try:
            keyring.set_password(SERVICE_NAME, name, cleaned)
        except Exception as error:
            raise CredentialStoreError("Credential storage is unavailable") from error

    def delete(self, name: str) -> None:
        try:
            existing = keyring.get_password(SERVICE_NAME, name)
            if existing is not None:
                keyring.delete_password(SERVICE_NAME, name)
        except Exception as error:
            raise CredentialStoreError("Credential storage is unavailable") from error


def resolve_ollama_cloud_api_key(store: CredentialStore) -> str | None:
    """Resolve only the application-owned Ollama Cloud vault entry."""
    try:
        return _clean(store.get(OLLAMA_CLOUD_CREDENTIAL))
    except CredentialStoreError:
        return None


def _clean(value: str | None) -> str | None:
    cleaned = value.strip() if value is not None else ""
    return cleaned or None


def resolve_source_credentials(store: CredentialStore) -> SourceCredentials:
    environment = SourceCredentials.from_environment()

    def vault_value(source: str) -> str | None:
        try:
            return _clean(store.get(SOURCE_CREDENTIAL_NAMES[source]))
        except CredentialStoreError:
            return None

    def enricher_value(enricher_id: str, credential_id: str) -> str | None:
        name = ENRICHER_CREDENTIAL_NAMES[(enricher_id, credential_id)]
        try:
            return _clean(store.get(name))
        except CredentialStoreError:
            return None

    return SourceCredentials(
        ieee_api_key=vault_value("ieee") or environment.ieee_api_key,
        springer_nature_api_key=(
            vault_value("springer_nature") or environment.springer_nature_api_key
        ),
        openreview_access_token=(vault_value("openreview") or environment.openreview_access_token),
        openalex_api_key=(
            enricher_value("openalex", "api_key") or environment.openalex_api_key
        ),
        semantic_scholar_api_key=(
            enricher_value("semantic_scholar", "api_key")
            or environment.semantic_scholar_api_key
        ),
        crossref_contact_email=(
            enricher_value("crossref", "contact_email")
            or environment.crossref_contact_email
        ),
        crossref_plus_api_token=(
            enricher_value("crossref", "plus_api_token")
            or environment.crossref_plus_api_token
        ),
    )
