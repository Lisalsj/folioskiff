from __future__ import annotations

import hashlib
import importlib
import json
import sqlite3
from datetime import UTC, datetime, timedelta
from io import BytesIO
from pathlib import Path
from uuid import uuid4

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory

ROOT = Path(__file__).parents[2]
EXPECTED_DATABASE_SCHEMA_SHA256 = "4d2261f6d12a971a0acf495585f6302d349fa71cdcd028fb1929ac6c7a4579a4"
EXPECTED_OPENAPI_SHA256 = "5c69bd4bfe08e2f3403cf15875c0b71d7df4ec6d4ea4ea7b8dffec7c33c1df8a"
EXPECTED_PLUGIN_PROTOCOL_SHA256 = "520cdcc36b7e5bcede23a9b6ae891ec9e7896ca41020f628fae76b757973bae5"


def _canonical_sha256(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _split_table_clauses(sql: str) -> tuple[str, list[str], str]:
    start = sql.find("(")
    end = sql.rfind(")")
    if start < 0 or end <= start:
        return sql, [], ""
    clauses: list[str] = []
    current: list[str] = []
    depth = 0
    quote: str | None = None
    index = start + 1
    while index < end:
        character = sql[index]
        if quote is not None:
            current.append(character)
            if character == quote:
                if index + 1 < end and sql[index + 1] == quote:
                    index += 1
                    current.append(sql[index])
                else:
                    quote = None
        elif character in {"'", '"', "`"}:
            quote = character
            current.append(character)
        elif character == "[":
            quote = "]"
            current.append(character)
        elif character == "(":
            depth += 1
            current.append(character)
        elif character == ")":
            depth -= 1
            current.append(character)
        elif character == "," and depth == 0:
            clauses.append(" ".join("".join(current).split()))
            current = []
        else:
            current.append(character)
        index += 1
    clauses.append(" ".join("".join(current).split()))
    return " ".join(sql[:start].split()), clauses, " ".join(sql[end + 1 :].split())


def _canonical_schema_sql(object_type: str, sql: str | None) -> str:
    normalized = " ".join((sql or "").split())
    if object_type != "table" or not normalized:
        return normalized
    prefix, clauses, suffix = _split_table_clauses(normalized)
    if not clauses:
        return normalized
    table_constraint_prefixes = (
        "CHECK ",
        "CONSTRAINT ",
        "FOREIGN KEY",
        "PRIMARY KEY",
        "UNIQUE ",
    )
    columns = [
        clause for clause in clauses if not clause.upper().startswith(table_constraint_prefixes)
    ]
    constraints = sorted(
        (clause for clause in clauses if clause.upper().startswith(table_constraint_prefixes)),
        key=str.casefold,
    )
    ending = f" {suffix}" if suffix else ""
    return f"{prefix} ({', '.join([*columns, *constraints])}){ending}"


def _database_schema_inventory(database: Path) -> dict[str, object]:
    connection = sqlite3.connect(database)
    try:
        revision_row = connection.execute("SELECT version_num FROM alembic_version").fetchone()
        assert revision_row is not None
        rows = connection.execute(
            "SELECT type, name, tbl_name, sql FROM sqlite_master "
            "WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name"
        ).fetchall()
        objects = [
            {
                "type": object_type,
                "name": name,
                "table": table,
                "sql": _canonical_schema_sql(str(object_type), sql),
            }
            for object_type, name, table, sql in rows
        ]
        tables: dict[str, object] = {}
        for _, name, _, _ in rows:
            if not any(item[0] == "table" and item[1] == name for item in rows):
                continue
            quoted_table = _quote_identifier(str(name))
            indexes = []
            for index_row in connection.execute(f"PRAGMA index_list({quoted_table})"):
                index_name = str(index_row[1])
                quoted_index = _quote_identifier(index_name)
                indexes.append(
                    {
                        "identity": list(index_row[1:]),
                        "columns": [
                            list(item)
                            for item in connection.execute(f"PRAGMA index_xinfo({quoted_index})")
                        ],
                    }
                )
            tables[str(name)] = {
                "columns": [
                    list(item) for item in connection.execute(f"PRAGMA table_xinfo({quoted_table})")
                ],
                "foreign_keys": sorted(
                    (
                        list(item[1:])
                        for item in connection.execute(f"PRAGMA foreign_key_list({quoted_table})")
                    ),
                    key=lambda item: json.dumps(item, separators=(",", ":")),
                ),
                "indexes": sorted(indexes, key=lambda item: str(item["identity"][0]).casefold()),
            }
        return {
            "revision": str(revision_row[0]),
            "objects": objects,
            "tables": tables,
        }
    finally:
        connection.close()


def _revision_chain() -> tuple[str, ...]:
    import paper_reader.migrations

    configuration = Config()
    configuration.set_main_option(
        "script_location",
        str(Path(paper_reader.migrations.__file__).parent),
    )
    revisions = ScriptDirectory.from_config(configuration).walk_revisions()
    return tuple(revision.revision for revision in reversed(tuple(revisions)))


def test_database_head_inventory_and_revision_chain_match_frozen_contract(
    tmp_path: Path,
) -> None:
    from paper_reader.migrations import migration_head_revision, upgrade_database
    from paper_reader.migrations.versions import (
        FROZEN_DATABASE_HEAD,
        FROZEN_DATABASE_REVISION_CHAIN,
        FROZEN_DATABASE_SCHEMA_SHA256,
        OLDEST_SUPPORTED_DATABASE_REVISION,
    )

    database = tmp_path / "frozen-v1.sqlite3"
    upgrade_database(database)
    inventory = _database_schema_inventory(database)

    assert FROZEN_DATABASE_HEAD == "0038_remove_legacy_conversations"
    assert OLDEST_SUPPORTED_DATABASE_REVISION == "0001_foundation"
    assert migration_head_revision() == FROZEN_DATABASE_HEAD
    assert _revision_chain() == FROZEN_DATABASE_REVISION_CHAIN
    assert FROZEN_DATABASE_REVISION_CHAIN[0] == OLDEST_SUPPORTED_DATABASE_REVISION
    assert FROZEN_DATABASE_REVISION_CHAIN[-1] == FROZEN_DATABASE_HEAD
    assert FROZEN_DATABASE_REVISION_CHAIN[-5:] == (
        "0034_agent_language_preferences",
        "0035_assistant_core",
        "0036_translations",
        "0037_related_resources",
        "0038_remove_legacy_conversations",
    )
    assert FROZEN_DATABASE_SCHEMA_SHA256 == EXPECTED_DATABASE_SCHEMA_SHA256
    assert _canonical_sha256(inventory) == FROZEN_DATABASE_SCHEMA_SHA256


def test_openapi_v1_document_matches_frozen_semantic_contract() -> None:
    from paper_reader import __version__
    from paper_reader.openapi import (
        API_CONTRACT_VERSION,
        OPENAPI_CONTRACT_SHA256,
        load_openapi_document,
        openapi_contract_sha256,
    )

    document = load_openapi_document(ROOT / "openapi" / "openapi.json")
    paths = document.get("paths")

    assert API_CONTRACT_VERSION == "v1"
    assert OPENAPI_CONTRACT_SHA256 == EXPECTED_OPENAPI_SHA256
    assert openapi_contract_sha256(document) == OPENAPI_CONTRACT_SHA256
    assert document["info"]["version"] == __version__
    assert isinstance(paths, dict)
    assert paths
    assert all(str(path).startswith("/api/v1/") for path in paths)
    assert {
        "/api/v1/onboarding",
        "/api/v1/assistant/works/{work_id}",
        "/api/v1/translations",
        "/api/v1/related-resources/works/{work_id}/draft",
        "/api/v1/conversations/works/{work_id}",
    }.issubset(paths)


def test_openapi_loader_rejects_duplicate_json_facts(tmp_path: Path) -> None:
    from paper_reader.openapi import load_openapi_document

    duplicate = tmp_path / "duplicate-openapi.json"
    duplicate.write_bytes(b'{"info":{"version":"0.6.0"},"paths":{},"paths":{"/api/v1/health":{}}}')

    with pytest.raises(ValueError, match="duplicate"):
        load_openapi_document(duplicate)


@pytest.mark.asyncio
async def test_plugin_protocol_1x_contract_runs_old_fixture_and_rejects_unknown_major(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from paper_reader.plugins.contracts import (
        PLUGIN_PROTOCOL_MAJOR,
        PLUGIN_PROTOCOL_VERSION,
        PluginManifest,
        ProtocolRange,
        is_protocol_compatible,
    )
    from paper_reader.plugins.sdk import (
        PLUGIN_PROTOCOL_CONTRACT_SHA256,
        JsonlCodec,
        PluginRequest,
        PluginResponse,
        plugin_protocol_contract_sha256,
    )
    from paper_reader.plugins.worker import run_application_once

    monkeypatch.syspath_prepend(str(ROOT / "examples" / "plugins" / "fixture-source" / "src"))
    fixture = importlib.import_module("paper_reader_fixture_source")
    request = PluginRequest(
        protocol_version="1.0",
        request_id=str(uuid4()),
        capability="fixture.source",
        method="probe",
        payload={},
        deadline=datetime.now(UTC) + timedelta(seconds=30),
    )
    codec = JsonlCodec()
    output = BytesIO()

    exit_code = await run_application_once(
        fixture.plugin,
        input_stream=BytesIO(codec.encode(request)),
        output_stream=output,
    )
    frames = [codec.decode_frame(line) for line in output.getvalue().splitlines(keepends=True)]

    assert PLUGIN_PROTOCOL_VERSION == "1.0"
    assert PLUGIN_PROTOCOL_MAJOR == 1
    assert PLUGIN_PROTOCOL_CONTRACT_SHA256 == EXPECTED_PLUGIN_PROTOCOL_SHA256
    assert plugin_protocol_contract_sha256() == PLUGIN_PROTOCOL_CONTRACT_SHA256
    assert is_protocol_compatible(
        ProtocolRange(minimum="1.0", maximum="1.0"),
        host_version=PLUGIN_PROTOCOL_VERSION,
    )
    assert not is_protocol_compatible(
        ProtocolRange(minimum="2.0", maximum="2.9"),
        host_version=PLUGIN_PROTOCOL_VERSION,
    )
    assert not is_protocol_compatible(
        ProtocolRange(minimum="1.0", maximum="1.9"),
        host_version="2.0",
    )
    assert PluginManifest.model_validate(fixture.plugin.manifest).protocol.maximum == "1.0"
    assert exit_code == 0
    assert len(frames) == 1
    assert isinstance(frames[0], PluginResponse)
    assert frames[0].ok is True


def test_release_layout_and_builder_fail_closed_over_frozen_contracts(tmp_path: Path) -> None:
    from paper_reader.migrations.versions import FROZEN_DATABASE_HEAD
    from paper_reader.openapi import API_CONTRACT_VERSION, OPENAPI_CONTRACT_SHA256
    from paper_reader.packaging.resources import load_release_layout
    from paper_reader.plugins.contracts import PLUGIN_PROTOCOL_VERSION
    from scripts.build_release_metadata import ReleaseMetadataError, build_release_metadata

    layout = load_release_layout(ROOT / "packaging" / "release-layout.json")

    assert layout.api_version == API_CONTRACT_VERSION
    assert layout.database_schema_revision == FROZEN_DATABASE_HEAD
    assert layout.openapi_sha256 == OPENAPI_CONTRACT_SHA256
    assert layout.plugin_protocol_version == PLUGIN_PROTOCOL_VERSION

    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text("# Changelog\n", encoding="utf-8")
    with pytest.raises(ReleaseMetadataError) as failure:
        build_release_metadata(
            release_dir=tmp_path / "missing-release",
            version="0.6.0",
            git_commit="a" * 40,
            database_revision="9999_future_schema",
            artifact_roles={},
            changelog_path=changelog,
            changelog_summary="Contract failure fixture.",
            migration_notes="No migration is executed.",
            signed=False,
        )

    assert failure.value.code == "database_revision_contract"
