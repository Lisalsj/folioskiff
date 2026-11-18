from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

import pytest


def tiny_manifest(payload: bytes):  # type: ignore[no-untyped-def]
    from paper_reader.recommendation.semantic import EmbeddingModelManifest, ModelAsset

    return EmbeddingModelManifest(
        model_id="fixture/e5",
        revision="fixture-revision",
        dimension=2,
        pooling="mean",
        normalized=True,
        license_id="MIT",
        source_url="https://example.invalid/fixture",
        assets=(
            ModelAsset(
                relative_path="onnx/model.onnx",
                size_bytes=len(payload),
                sha256=hashlib.sha256(payload).hexdigest(),
            ),
        ),
    )


def test_e5_manifest_is_revision_size_and_hash_pinned(tmp_path: Path) -> None:
    from paper_reader.recommendation.semantic import DEFAULT_E5_MANIFEST

    manifest = DEFAULT_E5_MANIFEST

    assert manifest.model_id == "intfloat/multilingual-e5-small"
    assert manifest.revision == "0e60b8d9d2166d80387f86e3b48ec9ced55f4d15"
    assert manifest.dimension == 384
    assert manifest.license_id == "MIT"
    assert manifest.download_size_bytes == 492_421_556
    assert manifest.asset("onnx/model.onnx").sha256 == (
        "ca456c06b3a9505ddfd9131408916dd79290368331e7d76bb621f1cba6bc8665"
    )
    assert manifest.snapshot_path(tmp_path) == (
        tmp_path
        / "intfloat--multilingual-e5-small"
        / "0e60b8d9d2166d80387f86e3b48ec9ced55f4d15"
    )


def test_capability_probe_distinguishes_missing_invalid_and_ready_assets(tmp_path: Path) -> None:
    from paper_reader.recommendation.semantic import probe_vector_capability

    payload = b"verified ONNX fixture"
    manifest = tiny_manifest(payload)
    snapshot = manifest.snapshot_path(tmp_path)
    model_file = snapshot / "onnx" / "model.onnx"

    missing = probe_vector_capability(
        tmp_path,
        manifest=manifest,
        module_available=lambda _: True,
    )
    assert missing.enabled is False
    assert missing.model_state == "missing"
    assert missing.degradation_reasons == ("embedding_model_not_installed",)

    model_file.parent.mkdir(parents=True)
    model_file.write_bytes(b"tampered")
    invalid = probe_vector_capability(
        tmp_path,
        manifest=manifest,
        module_available=lambda _: True,
    )
    assert invalid.model_state == "invalid"
    assert invalid.degradation_reasons == ("embedding_model_hash_mismatch",)

    model_file.write_bytes(payload)
    unavailable = probe_vector_capability(
        tmp_path,
        manifest=manifest,
        module_available=lambda _: False,
    )
    assert unavailable.model_state == "ready"
    assert unavailable.degradation_reasons == (
        "fastembed_unavailable",
        "sqlite_vec_unavailable",
    )

    ready = probe_vector_capability(
        tmp_path,
        manifest=manifest,
        module_available=lambda _: True,
    )
    assert ready.enabled is True
    assert ready.degradation_reasons == ()


def test_module_capability_requires_a_successful_native_import(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from paper_reader.recommendation import semantic

    class FailedProbe:
        returncode = -1

    monkeypatch.setattr(semantic.importlib.util, "find_spec", lambda _: object())
    monkeypatch.setattr(semantic.subprocess, "run", lambda *args, **kwargs: FailedProbe())
    semantic.module_is_usable.cache_clear()

    assert semantic.module_is_usable("fastembed") is False
    semantic.module_is_usable.cache_clear()


class RecordingModel:
    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []

    def embed(self, documents):  # type: ignore[no-untyped-def]
        values = tuple(documents)
        self.calls.append(values)
        return iter([1.0, 0.0] for _ in values)


def test_fastembed_adapter_applies_e5_query_and_passage_prefixes() -> None:
    from paper_reader.recommendation.semantic import FastEmbedBackend

    model = RecordingModel()
    backend = FastEmbedBackend(
        manifest=tiny_manifest(b"model"),
        model_factory=lambda: model,
    )

    assert backend.embed_query("癌症影像") == (1.0, 0.0)
    assert backend.embed_passages(("Paper title\nAbstract",)) == ((1.0, 0.0),)
    assert model.calls == [
        ("query: 癌症影像",),
        ("passage: Paper title\nAbstract",),
    ]


@dataclass
class FakeVectorStore:
    hashes: dict[str, str] = field(default_factory=dict)
    vectors: dict[str, tuple[float, ...]] = field(default_factory=dict)
    deleted: list[str] = field(default_factory=list)

    def content_hash(self, work_id: str) -> str | None:
        return self.hashes.get(work_id)

    def upsert(
        self,
        *,
        work_id: str,
        model_id: str,
        model_revision: str,
        content_sha256: str,
        vector: tuple[float, ...],
    ) -> None:
        self.hashes[work_id] = content_sha256
        self.vectors[work_id] = vector

    def delete(self, work_id: str) -> None:
        self.deleted.append(work_id)
        self.hashes.pop(work_id, None)
        self.vectors.pop(work_id, None)

    def search(self, vector: tuple[float, ...], *, limit: int):  # type: ignore[no-untyped-def]
        from paper_reader.recommendation.semantic import StoredVectorHit

        return [StoredVectorHit(work_id="work-near", distance=0.18)][:limit]


def test_semantic_index_hashes_content_avoids_duplicate_work_and_maps_cosine_score(
    tmp_path: Path,
) -> None:
    from paper_reader.database import create_session_factory, create_sqlite_engine
    from paper_reader.discovery.identity import IdentityRepository, SourcePaper
    from paper_reader.migrations import upgrade_database
    from paper_reader.recommendation.semantic import FastEmbedBackend, SemanticIndex

    database = tmp_path / "semantic.sqlite3"
    upgrade_database(database)
    engine = create_sqlite_engine(database)
    sessions = create_session_factory(engine)
    model = RecordingModel()
    backend = FastEmbedBackend(
        manifest=tiny_manifest(b"model"),
        model_factory=lambda: model,
    )
    store = FakeVectorStore()
    semantic = SemanticIndex(sessions, backend=backend, store=store)
    try:
        work = IdentityRepository(sessions).ingest(
            SourcePaper(
                connector_id="arxiv",
                external_id="2603.00001",
                title="Multilingual diffusion retrieval",
                authors=("A. Example",),
                abstract="Semantic recommendation across languages.",
            )
        ).work

        assert semantic.sync_work(work.id) is True
        assert semantic.sync_work(work.id) is False
        assert len(model.calls) == 1

        hit = semantic.search("扩散模型")[0]
        assert hit.work_id == "work-near"
        assert hit.distance == 0.18
        assert hit.semantic_score == 82.0
        assert model.calls[-1] == ("query: 扩散模型",)
    finally:
        engine.dispose()


def test_embedding_record_lookup_uses_static_parameterized_json(tmp_path: Path) -> None:
    from paper_reader.recommendation.semantic import _fetch_embedding_records

    database = tmp_path / "records.sqlite3"
    malicious_work_id = "work'); DROP TABLE embedding_records; --"
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE embedding_records (id INTEGER PRIMARY KEY, work_id TEXT NOT NULL)"
        )
        connection.executemany(
            "INSERT INTO embedding_records(id, work_id) VALUES (?, ?)",
            ((1, malicious_work_id), (2, "ordinary-work")),
        )

        assert _fetch_embedding_records(connection, (1, 2)) == [
            (1, malicious_work_id),
            (2, "ordinary-work"),
        ]
        assert connection.execute(
            "SELECT COUNT(*) FROM embedding_records"
        ).fetchone() == (2,)


@pytest.mark.models
def test_sqlite_vec_store_contract_when_optional_runtime_is_installed(tmp_path: Path) -> None:
    pytest.importorskip("sqlite_vec")
    from paper_reader.recommendation.semantic import SqliteVecStore

    store = SqliteVecStore(tmp_path / "vectors.sqlite3", dimension=2)
    store.upsert(
        work_id="near",
        model_id="fixture/e5",
        model_revision="1",
        content_sha256="1" * 64,
        vector=(1.0, 0.0),
    )
    store.upsert(
        work_id="far",
        model_id="fixture/e5",
        model_revision="1",
        content_sha256="2" * 64,
        vector=(0.0, 1.0),
    )

    assert store.content_hash("near") == "1" * 64
    assert store.distance("near", (1.0, 0.0)) == pytest.approx(0.0)
    assert store.distance("missing", (1.0, 0.0)) is None
    assert [hit.work_id for hit in store.search((1.0, 0.0), limit=2)] == ["near", "far"]
    store.upsert(
        work_id="near",
        model_id="fixture/e5",
        model_revision="2",
        content_sha256="3" * 64,
        vector=(-1.0, 0.0),
    )
    assert store.content_hash("near") == "3" * 64
    assert [hit.work_id for hit in store.search((1.0, 0.0), limit=2)] == ["far", "near"]
    store.delete("near")
    assert [hit.work_id for hit in store.search((1.0, 0.0), limit=2)] == ["far"]
