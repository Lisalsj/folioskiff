"""Optional multilingual E5 embeddings and sqlite-vec derived index."""

from __future__ import annotations

import hashlib
import importlib
import importlib.util
import json
import math
import re
import sqlite3
import subprocess
import sys
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from functools import cache
from pathlib import Path, PurePosixPath
from typing import Any, Protocol, cast
from urllib.parse import urlsplit

from sqlalchemy.orm import Session, sessionmaker

from paper_reader.discovery.models import WorkRecord

_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_REVISION_PATTERN = re.compile(r"^[0-9a-f]{40}$")
_BUNDLE_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9.-]{0,119}$")
_MODEL_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$")


class EmbeddingManifestError(ValueError):
    """The packaged embedding manifest is unsafe or internally inconsistent."""


@dataclass(frozen=True, slots=True)
class ModelAsset:
    relative_path: str
    size_bytes: int
    sha256: str
    url: str = ""


@dataclass(frozen=True, slots=True)
class EmbeddingModelManifest:
    model_id: str
    revision: str
    dimension: int
    pooling: str
    normalized: bool
    license_id: str
    source_url: str
    assets: tuple[ModelAsset, ...]
    bundle_id: str = ""
    display_name: str = ""
    purpose: str = ""
    version: str = ""
    license_url: str = ""
    copyright_notice: str = ""

    @property
    def download_size_bytes(self) -> int:
        return sum(asset.size_bytes for asset in self.assets)

    def asset(self, relative_path: str) -> ModelAsset:
        for asset in self.assets:
            if asset.relative_path == relative_path:
                return asset
        raise KeyError(relative_path)

    def snapshot_path(self, model_root: Path) -> Path:
        return Path(model_root) / self.model_id.replace("/", "--") / self.revision


def load_embedding_manifest(path: Path | None = None) -> EmbeddingModelManifest:
    """Load the pinned E5 manifest from package data or an explicit test path."""

    if path is None:
        from paper_reader.assets.manifest import load_packaged_model_manifests

        common = load_packaged_model_manifests()[0]
        runtime = common.runtime
        return EmbeddingModelManifest(
            model_id=cast(str, runtime["model_id"]),
            revision=common.revision,
            dimension=cast(int, runtime["dimension"]),
            pooling=cast(str, runtime["pooling"]),
            normalized=cast(bool, runtime["normalized"]),
            license_id=common.license.name,
            source_url=common.source_url,
            assets=tuple(
                ModelAsset(
                    relative_path=item.relative_path,
                    size_bytes=item.size_bytes,
                    sha256=item.sha256,
                    url=item.url,
                )
                for item in common.files
            ),
            bundle_id=common.bundle_id,
            display_name=common.display_name,
            purpose=common.purpose,
            version=common.version,
            license_url=common.license.url,
            copyright_notice=common.license.copyright_notice,
        )

    source = Path(path)
    try:
        raw = cast(Any, json.loads(source.read_text(encoding="utf-8")))
    except (OSError, UnicodeError, ValueError) as error:
        raise EmbeddingManifestError("embedding manifest cannot be read as JSON") from error
    required = {
        "bundle_id",
        "display_name",
        "purpose",
        "version",
        "model_id",
        "revision",
        "dimension",
        "pooling",
        "normalized",
        "license",
        "source_url",
        "assets",
    }
    if not isinstance(raw, dict) or set(raw) != required:
        raise EmbeddingManifestError("embedding manifest fields are incomplete or unknown")

    bundle_id = _manifest_text(raw, "bundle_id", maximum=120)
    if _BUNDLE_ID_PATTERN.fullmatch(bundle_id) is None:
        raise EmbeddingManifestError("embedding manifest bundle_id is unsafe")
    model_id = _manifest_text(raw, "model_id", maximum=160)
    if _MODEL_ID_PATTERN.fullmatch(model_id) is None:
        raise EmbeddingManifestError("embedding manifest model_id is unsafe")
    revision = _manifest_text(raw, "revision", maximum=40).casefold()
    if _REVISION_PATTERN.fullmatch(revision) is None:
        raise EmbeddingManifestError("embedding manifest revision must be a Git hash")
    dimension = raw.get("dimension")
    if isinstance(dimension, bool) or not isinstance(dimension, int) or not 1 <= dimension <= 4096:
        raise EmbeddingManifestError("embedding manifest dimension is invalid")
    pooling = _manifest_text(raw, "pooling", maximum=20)
    if pooling not in {"mean", "cls"}:
        raise EmbeddingManifestError("embedding manifest pooling is invalid")
    normalized = raw.get("normalized")
    if not isinstance(normalized, bool):
        raise EmbeddingManifestError("embedding manifest normalized flag is invalid")
    source_url = _manifest_https_url(
        _manifest_text(raw, "source_url", maximum=2048),
        field="source URL",
    )
    source_parts = urlsplit(source_url)
    expected_source_path = f"/{model_id}/tree/{revision}"
    if source_parts.hostname != "huggingface.co" or source_parts.path != expected_source_path:
        raise EmbeddingManifestError("embedding manifest source URL is not revision pinned")

    license_value = raw.get("license")
    if not isinstance(license_value, dict) or set(license_value) != {
        "id",
        "url",
        "copyright_notice",
    }:
        raise EmbeddingManifestError("embedding manifest license is incomplete or unknown")
    license_id = _manifest_text(license_value, "id", maximum=120)
    license_url = _manifest_https_url(
        _manifest_text(license_value, "url", maximum=2048),
        field="license URL",
    )
    copyright_notice = _manifest_text(license_value, "copyright_notice", maximum=1000)

    assets_value = raw.get("assets")
    if not isinstance(assets_value, list) or not 1 <= len(assets_value) <= 64:
        raise EmbeddingManifestError("embedding manifest assets are invalid")
    assets: list[ModelAsset] = []
    seen_paths: set[str] = set()
    for value in assets_value:
        if not isinstance(value, dict) or set(value) != {
            "relative_path",
            "url",
            "size_bytes",
            "sha256",
        }:
            raise EmbeddingManifestError(
                "embedding manifest asset fields are incomplete or unknown"
            )
        relative_path = _manifest_relative_path(_manifest_text(value, "relative_path", maximum=240))
        folded_path = relative_path.casefold()
        if folded_path in seen_paths:
            raise EmbeddingManifestError("embedding manifest asset paths must be unique")
        seen_paths.add(folded_path)
        asset_url = _manifest_https_url(
            _manifest_text(value, "url", maximum=2048),
            field="asset URL",
        )
        expected_path = f"/{model_id}/resolve/{revision}/{relative_path}"
        asset_parts = urlsplit(asset_url)
        if (
            asset_parts.hostname != source_parts.hostname
            or asset_parts.path != expected_path
            or asset_parts.query
        ):
            raise EmbeddingManifestError("embedding manifest asset URL is not revision pinned")
        size = value.get("size_bytes")
        if isinstance(size, bool) or not isinstance(size, int) or not 1 <= size <= 2**31:
            raise EmbeddingManifestError("embedding manifest asset size is invalid")
        digest = _manifest_text(value, "sha256", maximum=64).casefold()
        if _SHA256_PATTERN.fullmatch(digest) is None:
            raise EmbeddingManifestError("embedding manifest asset SHA-256 is invalid")
        assets.append(
            ModelAsset(
                relative_path=relative_path,
                size_bytes=size,
                sha256=digest,
                url=asset_url,
            )
        )

    return EmbeddingModelManifest(
        model_id=model_id,
        revision=revision,
        dimension=dimension,
        pooling=pooling,
        normalized=normalized,
        license_id=license_id,
        source_url=source_url,
        assets=tuple(assets),
        bundle_id=bundle_id,
        display_name=_manifest_text(raw, "display_name", maximum=240),
        purpose=_manifest_text(raw, "purpose", maximum=1000),
        version=_manifest_text(raw, "version", maximum=120),
        license_url=license_url,
        copyright_notice=copyright_notice,
    )


def _manifest_text(value: dict[str, Any], key: str, *, maximum: int) -> str:
    item = value.get(key)
    if not isinstance(item, str) or not item.strip() or len(item) > maximum:
        raise EmbeddingManifestError(f"embedding manifest {key} is invalid")
    return item.strip()


def _manifest_https_url(value: str, *, field: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
    ):
        raise EmbeddingManifestError(f"embedding manifest {field} must be HTTPS")
    return value


def _manifest_relative_path(value: str) -> str:
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or "\\" in value
        or not path.parts
        or path.as_posix() != value
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise EmbeddingManifestError("embedding manifest asset path is unsafe")
    return path.as_posix()


DEFAULT_E5_MANIFEST = load_embedding_manifest()


@dataclass(frozen=True, slots=True)
class VectorCapability:
    enabled: bool
    fastembed_available: bool
    sqlite_vec_available: bool
    model_state: str
    degradation_reasons: tuple[str, ...]
    manifest: EmbeddingModelManifest


@cache
def module_is_usable(module_name: str) -> bool:
    if importlib.util.find_spec(module_name) is None:
        return False
    try:
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "import importlib,sys; importlib.import_module(sys.argv[1])",
                module_name,
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def _module_is_installed(module_name: str) -> bool:
    return importlib.util.find_spec(module_name) is not None


def verify_model_asset(path: Path, asset: ModelAsset) -> bool:
    if not path.is_file() or path.stat().st_size != asset.size_bytes:
        return False
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest() == asset.sha256


def probe_vector_capability(
    model_root: Path,
    *,
    manifest: EmbeddingModelManifest = DEFAULT_E5_MANIFEST,
    module_available: Callable[[str], bool] = module_is_usable,
) -> VectorCapability:
    snapshot = manifest.snapshot_path(model_root)
    asset_paths = tuple(snapshot / asset.relative_path for asset in manifest.assets)
    if not all(path.is_file() for path in asset_paths):
        model_state = "missing"
    elif all(
        verify_model_asset(path, asset)
        for path, asset in zip(asset_paths, manifest.assets, strict=True)
    ):
        model_state = "ready"
    else:
        model_state = "invalid"

    if model_state == "ready" or module_available is not module_is_usable:
        fastembed_available = module_available("fastembed")
        sqlite_vec_available = module_available("sqlite_vec")
    else:
        # Native imports are only needed after a verified model is ready. Avoid paying
        # startup cost or risking a broken DLL while semantic search is already disabled.
        fastembed_available = _module_is_installed("fastembed")
        sqlite_vec_available = _module_is_installed("sqlite_vec")
    reasons: list[str] = []
    if not fastembed_available:
        reasons.append("fastembed_unavailable")
    if not sqlite_vec_available:
        reasons.append("sqlite_vec_unavailable")
    if model_state == "missing":
        reasons.append("embedding_model_not_installed")
    elif model_state == "invalid":
        reasons.append("embedding_model_hash_mismatch")
    return VectorCapability(
        enabled=not reasons,
        fastembed_available=fastembed_available,
        sqlite_vec_available=sqlite_vec_available,
        model_state=model_state,
        degradation_reasons=tuple(reasons),
        manifest=manifest,
    )


class EmbeddingModel(Protocol):
    def embed(self, documents: Iterable[str]) -> Iterable[Any]: ...


class FastEmbedBackend:
    """Lazy FastEmbed adapter that applies the prefixes required by E5."""

    def __init__(
        self,
        *,
        manifest: EmbeddingModelManifest = DEFAULT_E5_MANIFEST,
        model_root: Path | None = None,
        model_factory: Callable[[], EmbeddingModel] | None = None,
    ) -> None:
        self.manifest = manifest
        self._model_root = model_root
        self._model_factory = model_factory
        self._model: EmbeddingModel | None = None

    def embed_query(self, query: str) -> tuple[float, ...]:
        values = self._embed((f"query: {query.strip()}",))
        return values[0]

    def embed_passages(self, passages: Sequence[str]) -> tuple[tuple[float, ...], ...]:
        return self._embed(tuple(f"passage: {passage.strip()}" for passage in passages))

    def _embed(self, texts: Sequence[str]) -> tuple[tuple[float, ...], ...]:
        if not texts:
            return ()
        vectors = tuple(self._coerce(vector) for vector in self._get_model().embed(texts))
        if len(vectors) != len(texts):
            raise RuntimeError("Embedding backend returned a different number of vectors")
        return vectors

    def _coerce(self, vector: Any) -> tuple[float, ...]:
        values = tuple(float(value) for value in vector)
        if len(values) != self.manifest.dimension:
            raise RuntimeError(
                f"Embedding dimension {len(values)} does not match {self.manifest.dimension}"
            )
        if not all(math.isfinite(value) for value in values):
            raise RuntimeError("Embedding contains a non-finite value")
        return values

    def _get_model(self) -> EmbeddingModel:
        if self._model is None:
            self._model = (
                self._model_factory() if self._model_factory is not None else self._load_model()
            )
        return self._model

    def _load_model(self) -> EmbeddingModel:
        if self._model_root is None:
            raise RuntimeError("model_root is required for the FastEmbed runtime")
        fastembed = importlib.import_module("fastembed")
        descriptions = importlib.import_module("fastembed.common.model_description")
        text_embedding = fastembed.TextEmbedding
        supported = {
            str(item["model"])
            for item in cast(list[dict[str, Any]], text_embedding.list_supported_models())
        }
        if self.manifest.model_id not in supported:
            text_embedding.add_custom_model(
                model=self.manifest.model_id,
                pooling=descriptions.PoolingType.MEAN,
                normalization=self.manifest.normalized,
                sources=descriptions.ModelSource(hf=self.manifest.model_id),
                dim=self.manifest.dimension,
                model_file="onnx/model.onnx",
            )
        snapshot = self.manifest.snapshot_path(self._model_root)
        return cast(
            EmbeddingModel,
            text_embedding(
                model_name=self.manifest.model_id,
                specific_model_path=str(snapshot),
                cache_dir=str(self._model_root),
            ),
        )


@dataclass(frozen=True, slots=True)
class StoredVectorHit:
    work_id: str
    distance: float


def _fetch_embedding_records(
    connection: sqlite3.Connection,
    identifiers: tuple[int, ...],
) -> list[tuple[int, str]]:
    if not identifiers or any(
        type(identifier) is not int or identifier < 1 for identifier in identifiers
    ):
        raise ValueError("embedding identifiers must be positive integers")
    encoded = json.dumps(identifiers, separators=(",", ":"))
    rows = connection.execute(
        "SELECT id, work_id FROM embedding_records "
        "WHERE id IN (SELECT CAST(value AS INTEGER) FROM json_each(?)) "
        "ORDER BY id",
        (encoded,),
    ).fetchall()
    return [(int(row[0]), str(row[1])) for row in rows]


class VectorStore(Protocol):
    def content_hash(self, work_id: str) -> str | None: ...

    def upsert(
        self,
        *,
        work_id: str,
        model_id: str,
        model_revision: str,
        content_sha256: str,
        vector: tuple[float, ...],
    ) -> None: ...

    def delete(self, work_id: str) -> None: ...

    def distance(self, work_id: str, vector: tuple[float, ...]) -> float | None: ...

    def search(self, vector: tuple[float, ...], *, limit: int) -> list[StoredVectorHit]: ...


class SqliteVecStore:
    """Rebuildable sqlite-vec cache kept outside the canonical application database."""

    def __init__(self, database: Path, *, dimension: int) -> None:
        if dimension < 1:
            raise ValueError("dimension must be positive")
        self._database = Path(database)
        self._dimension = dimension
        self._database.parent.mkdir(parents=True, exist_ok=True)
        with self._connect():
            pass

    def content_hash(self, work_id: str) -> str | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT content_sha256 FROM embedding_records WHERE work_id = ?",
                (work_id,),
            ).fetchone()
        return str(row[0]) if row is not None else None

    def upsert(
        self,
        *,
        work_id: str,
        model_id: str,
        model_revision: str,
        content_sha256: str,
        vector: tuple[float, ...],
    ) -> None:
        self._validate_vector(vector)
        if not re.fullmatch(r"[0-9a-f]{64}", content_sha256):
            raise ValueError("content_sha256 must be lowercase SHA-256")
        with self._connect() as connection:
            row = connection.execute(
                "SELECT id FROM embedding_records WHERE work_id = ?",
                (work_id,),
            ).fetchone()
            if row is None:
                cursor = connection.execute(
                    "INSERT INTO embedding_records "
                    "(work_id, model_id, model_revision, content_sha256) VALUES (?, ?, ?, ?)",
                    (work_id, model_id, model_revision, content_sha256),
                )
                if cursor.lastrowid is None:  # pragma: no cover - SQLite invariant
                    raise RuntimeError("SQLite did not return an embedding record ID")
                embedding_id = cursor.lastrowid
            else:
                embedding_id = int(row[0])
                connection.execute(
                    "UPDATE embedding_records SET model_id = ?, model_revision = ?, "
                    "content_sha256 = ? WHERE id = ?",
                    (model_id, model_revision, content_sha256, embedding_id),
                )
                connection.execute(
                    "DELETE FROM work_embeddings WHERE embedding_id = ?",
                    (embedding_id,),
                )
            connection.execute(
                "INSERT INTO work_embeddings(embedding_id, embedding) VALUES (?, ?)",
                (embedding_id, self._serialize(vector)),
            )

    def delete(self, work_id: str) -> None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT id FROM embedding_records WHERE work_id = ?",
                (work_id,),
            ).fetchone()
            if row is None:
                return
            embedding_id = int(row[0])
            connection.execute(
                "DELETE FROM work_embeddings WHERE embedding_id = ?",
                (embedding_id,),
            )
            connection.execute("DELETE FROM embedding_records WHERE id = ?", (embedding_id,))

    def distance(self, work_id: str, vector: tuple[float, ...]) -> float | None:
        self._validate_vector(vector)
        with self._connect() as connection:
            record = connection.execute(
                "SELECT id FROM embedding_records WHERE work_id = ?",
                (work_id,),
            ).fetchone()
            if record is None:
                return None
            row = connection.execute(
                "SELECT vec_distance_cosine(embedding, ?) FROM work_embeddings "
                "WHERE embedding_id = ?",
                (self._serialize(vector), int(record[0])),
            ).fetchone()
        return float(row[0]) if row is not None else None

    def search(self, vector: tuple[float, ...], *, limit: int) -> list[StoredVectorHit]:
        self._validate_vector(vector)
        if not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")
        with self._connect() as connection:
            matches = connection.execute(
                "SELECT embedding_id, distance FROM work_embeddings "
                "WHERE embedding MATCH ? AND k = ? ORDER BY distance",
                (self._serialize(vector), limit),
            ).fetchall()
            matches.sort(key=lambda row: (float(row[1]), int(row[0])))
            if not matches:
                return []
            identifiers = tuple(int(row[0]) for row in matches)
            records = _fetch_embedding_records(connection, identifiers)
        work_ids = {int(row[0]): str(row[1]) for row in records}
        return [
            StoredVectorHit(work_id=work_ids[int(row[0])], distance=float(row[1]))
            for row in matches
            if int(row[0]) in work_ids
        ]

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._database)
        try:
            connection.enable_load_extension(True)
            sqlite_vec = importlib.import_module("sqlite_vec")
            sqlite_vec.load(connection)
        finally:
            connection.enable_load_extension(False)
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA busy_timeout = 5000")
        connection.execute(
            "CREATE TABLE IF NOT EXISTS embedding_records ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, work_id TEXT NOT NULL UNIQUE, "
            "model_id TEXT NOT NULL, model_revision TEXT NOT NULL, "
            "content_sha256 TEXT NOT NULL)"
        )
        connection.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS work_embeddings USING vec0("
            "embedding_id INTEGER PRIMARY KEY, "
            f"embedding FLOAT[{self._dimension}] distance_metric=cosine)"
        )
        return connection

    def _serialize(self, vector: tuple[float, ...]) -> bytes:
        sqlite_vec = importlib.import_module("sqlite_vec")
        return cast(bytes, sqlite_vec.serialize_float32(list(vector)))

    def _validate_vector(self, vector: tuple[float, ...]) -> None:
        if len(vector) != self._dimension:
            raise ValueError(f"vector must contain {self._dimension} values")
        if not all(math.isfinite(value) for value in vector):
            raise ValueError("vector contains a non-finite value")


@dataclass(frozen=True, slots=True)
class SemanticHit:
    work_id: str
    semantic_score: float
    distance: float


class SemanticIndex:
    def __init__(
        self,
        sessions: sessionmaker[Session],
        *,
        backend: FastEmbedBackend,
        store: VectorStore,
    ) -> None:
        self._sessions = sessions
        self._backend = backend
        self._store = store

    def sync_work(self, work_id: str) -> bool:
        with self._sessions() as session:
            work = session.get(WorkRecord, work_id)
            if work is None or work.status != "active":
                self._store.delete(work_id)
                return True
            passage = work.canonical_title.strip()
            if work.abstract:
                passage = f"{passage}\n{work.abstract.strip()}"

        fingerprint = hashlib.sha256(
            (
                f"{self._backend.manifest.model_id}@{self._backend.manifest.revision}\0{passage}"
            ).encode()
        ).hexdigest()
        if self._store.content_hash(work_id) == fingerprint:
            return False
        vector = self._backend.embed_passages((passage,))[0]
        self._store.upsert(
            work_id=work_id,
            model_id=self._backend.manifest.model_id,
            model_revision=self._backend.manifest.revision,
            content_sha256=fingerprint,
            vector=vector,
        )
        return True

    def search(self, query: str, *, limit: int = 50) -> list[SemanticHit]:
        cleaned = " ".join(query.split())
        if not cleaned:
            return []
        vector = self._backend.embed_query(cleaned)
        return [
            SemanticHit(
                work_id=hit.work_id,
                semantic_score=round(max(0.0, min(100.0, (1.0 - hit.distance) * 100)), 6),
                distance=hit.distance,
            )
            for hit in self._store.search(vector, limit=limit)
        ]

    def score_candidates(
        self,
        query: str,
        work_ids: tuple[str, ...],
    ) -> dict[str, SemanticHit]:
        cleaned = " ".join(query.split())
        if not cleaned or not work_ids:
            return {}
        vector = self._backend.embed_query(cleaned)
        results: dict[str, SemanticHit] = {}
        for work_id in work_ids:
            distance = self._store.distance(work_id, vector)
            if distance is None:
                continue
            results[work_id] = SemanticHit(
                work_id=work_id,
                semantic_score=round(max(0.0, min(100.0, (1.0 - distance) * 100)), 6),
                distance=distance,
            )
        return results
