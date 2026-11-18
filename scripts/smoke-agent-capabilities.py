"""Read-only local Agent capability smoke check; never invokes a model."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

import httpx

from paper_reader.agents.codex_cli import CodexCliBackend
from paper_reader.agents.ollama import OllamaBackend
from paper_reader.credentials import KeyringCredentialStore, resolve_ollama_cloud_api_key
from paper_reader.settings import AppPaths


async def inspect_codex(cache_root: Path) -> dict[str, object]:
    backend = CodexCliBackend(cache_root=cache_root)
    capability = await backend.probe()
    catalog = await backend.discover_models()
    return {
        "backend_id": capability.backend_id,
        "installed": capability.installed,
        "authenticated": capability.authenticated,
        "available": capability.available,
        "version": backend.cli_version,
        "models": [model.model_id for model in catalog.models],
        "supports_manual_model_id": catalog.supports_manual_model_id,
        "supports_default_model": catalog.supports_default_model,
        "degradation_reasons": [
            *capability.degradation_reasons,
            *catalog.degradation_reasons,
        ],
    }


async def inspect_ollama(backend: OllamaBackend) -> dict[str, object]:
    capability = await backend.probe()
    catalog = backend.cached_model_catalog
    return {
        "backend_id": capability.backend_id,
        "installed": capability.installed,
        "authenticated": capability.authenticated,
        "available": capability.available,
        "models": [model.model_id for model in catalog.models] if catalog else [],
        "supports_manual_model_id": (
            catalog.supports_manual_model_id if catalog else True
        ),
        "degradation_reasons": list(
            dict.fromkeys(
                (
                    *capability.degradation_reasons,
                    *(catalog.degradation_reasons if catalog else ()),
                )
            )
        ),
    }


async def inspect_all(cache_root: Path) -> dict[str, object]:
    cloud_key = resolve_ollama_cloud_api_key(KeyringCredentialStore())
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(20, connect=5),
        follow_redirects=False,
        trust_env=False,
    ) as client:
        codex = await inspect_codex(cache_root)
        local = await inspect_ollama(OllamaBackend.local(client=client))
        cloud = await inspect_ollama(
            OllamaBackend.cloud(client=client, api_key=cloud_key)
        )
    return {
        "codex_cli": codex,
        "ollama_local": local,
        "ollama_cloud": cloud,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Inspect local Agent capabilities without invoking a model."
    )
    parser.add_argument(
        "--cache-root",
        type=Path,
        default=AppPaths.default().cache_root,
        help="Directory for bounded capability-probe state.",
    )
    arguments = parser.parse_args()
    result = asyncio.run(inspect_all(arguments.cache_root))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
