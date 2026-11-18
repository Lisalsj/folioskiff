from __future__ import annotations

import json
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast

import httpx
import pytest

if TYPE_CHECKING:
    from paper_reader.reader import (
        OcrModelBundle,
        OcrModelInstaller,
        OcrModelRole,
        OcrService,
    )


def tiny_bundle(payloads: dict[str, bytes] | None = None) -> OcrModelBundle:
    from paper_reader.reader import OcrModelBundle, OcrModelFile

    values = payloads or {
        "det.onnx": b"detector",
        "cls.onnx": b"classifier",
        "rec.onnx": b"recognizer",
    }
    roles: tuple[OcrModelRole, ...] = ("det", "cls", "rec")
    files = tuple(
        OcrModelFile(
            role=role,
            filename=name,
            url=f"https://models.example.test/fixed/{name}",
            size=len(values[name]),
            sha256=sha256(values[name]).hexdigest(),
        )
        for role, name in zip(roles, values, strict=True)
    )
    return OcrModelBundle(
        bundle_id="fixture-ppocr",
        display_name="Fixture Chinese and English OCR",
        version="fixture-v1",
        source_revision="f" * 40,
        source_url="https://models.example.test/source",
        license_name="Apache-2.0",
        license_url="https://models.example.test/license",
        copyright_notice="Fixture model copyright notice",
        files=files,
    )


def _rapidocr_paths(tmp_path: Path) -> dict[OcrModelRole, Path]:
    return {
        "det": tmp_path / "det.onnx",
        "cls": tmp_path / "cls.onnx",
        "rec": tmp_path / "rec.onnx",
    }


def install_tiny_bundle(installer: Any, bundle: Any, payloads: dict[str, bytes]) -> None:
    paths = installer.model_paths(bundle)
    for model in bundle.files:
        destination = paths[model.role]
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(payloads[model.filename])


def test_packaged_rapidocr_manifest_is_pinned_licensed_and_auditable() -> None:
    from paper_reader.reader import load_ocr_manifest

    bundle = load_ocr_manifest()

    assert bundle.bundle_id == "rapidocr-ppocrv5-ch-en-mobile"
    assert bundle.version == "PP-OCRv5-mobile-b8ff564a"
    assert bundle.source_revision == "b8ff564a23de421e7144385bfe120fe2bb869932"
    assert bundle.license_name == "Apache-2.0"
    assert "Baidu" in bundle.copyright_notice
    assert bundle.total_size == 22_469_390
    assert tuple(model.role for model in bundle.files) == ("det", "cls", "rec")
    assert {model.sha256 for model in bundle.files} == {
        "4d97c44a20d30a81aad087d6a396b08f786c4635742afc391f6621f5c6ae78ae",
        "54379ae5174d026780215fc748a7f31910dee36818e63d49e17dc598ecc82df7",
        "5825fc7ebf84ae7a412be049820b4d86d77620f204a041697b0494669b1742c5",
    }
    for model in bundle.files:
        assert model.url.startswith(
            f"https://www.modelscope.cn/models/RapidAI/RapidOCR/resolve/{bundle.source_revision}/"
        )
        assert Path(model.filename).name == model.filename
        assert model.size > 0


@pytest.mark.parametrize(
    ("field", "value", "message"),
    (
        ("filename", "../escape.onnx", "filename"),
        ("url", "http://models.example.test/model.onnx", "HTTPS"),
        ("sha256", "not-a-hash", "SHA-256"),
    ),
)
def test_manifest_rejects_path_traversal_insecure_urls_and_invalid_hashes(
    tmp_path: Path,
    field: str,
    value: str,
    message: str,
) -> None:
    from paper_reader.reader import OcrManifestError, load_ocr_manifest

    model = {
        "role": "det",
        "filename": "det.onnx",
        "url": "https://models.example.test/det.onnx",
        "size": 3,
        "sha256": sha256(b"det").hexdigest(),
    }
    model[field] = value
    manifest = {
        "bundle_id": "fixture",
        "display_name": "Fixture",
        "version": "v1",
        "source_revision": "f" * 40,
        "source_url": "https://models.example.test/source",
        "license": {
            "name": "Apache-2.0",
            "url": "https://models.example.test/license",
            "copyright_notice": "Fixture",
        },
        "files": [
            model,
            {
                "role": "cls",
                "filename": "cls.onnx",
                "url": "https://models.example.test/cls.onnx",
                "size": 3,
                "sha256": sha256(b"cls").hexdigest(),
            },
            {
                "role": "rec",
                "filename": "rec.onnx",
                "url": "https://models.example.test/rec.onnx",
                "size": 3,
                "sha256": sha256(b"rec").hexdigest(),
            },
        ],
    }
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(OcrManifestError, match=message):
        load_ocr_manifest(path)
    assert not (tmp_path.parent / "escape.onnx").exists()


def test_capability_separates_runtime_models_and_hash_integrity(tmp_path: Path) -> None:
    from paper_reader.reader import OcrModelInstaller, inspect_ocr_capability

    payloads = {
        "det.onnx": b"detector",
        "cls.onnx": b"classifier",
        "rec.onnx": b"recognizer",
    }
    bundle = tiny_bundle(payloads)
    installer = OcrModelInstaller(models_root=tmp_path / "models")

    unavailable = inspect_ocr_capability(
        bundle=bundle,
        installer=installer,
        runtime_probe=lambda: False,
    )
    assert unavailable.available is False
    assert unavailable.runtime_available is False
    assert unavailable.models_installed is False
    assert unavailable.degradation_reasons == (
        "runtime_unavailable",
        "models_missing",
    )
    assert unavailable.total_size == sum(map(len, payloads.values()))
    assert all(model.installed is False for model in unavailable.files)
    assert all(model.integrity == "missing" for model in unavailable.files)

    install_tiny_bundle(installer, bundle, payloads)
    ready = inspect_ocr_capability(
        bundle=bundle,
        installer=installer,
        runtime_probe=lambda: True,
    )
    assert ready.available is True
    assert ready.models_installed is True
    assert ready.degradation_reasons == ()
    assert all(model.installed is True for model in ready.files)
    assert all(model.integrity == "verified" for model in ready.files)

    installer.model_paths(bundle)["rec"].write_bytes(b"tampered")
    corrupt = inspect_ocr_capability(
        bundle=bundle,
        installer=installer,
        runtime_probe=lambda: True,
    )
    assert corrupt.available is False
    assert corrupt.degradation_reasons == ("model_hash_mismatch",)
    assert corrupt.files[-1].integrity == "hash_mismatch"


def test_runtime_probe_requires_a_successful_isolated_native_import() -> None:
    import subprocess

    from paper_reader.reader import probe_ocr_runtime

    calls: list[tuple[list[str], dict[str, Any]]] = []

    def installed(_: str) -> object:
        return object()

    def failed_runner(command: list[str], **options: Any) -> Any:
        calls.append((command, options))
        return SimpleNamespace(returncode=1)

    assert (
        probe_ocr_runtime(
            finder=installed,
            runner=failed_runner,
            executable="fixture-python",
        )
        is False
    )
    assert calls[0][0][0] == "fixture-python"
    assert "onnxruntime" in calls[0][0][2]
    assert "rapidocr" in calls[0][0][2]
    assert calls[0][1]["timeout"] == 15
    assert calls[0][1]["capture_output"] is True

    assert (
        probe_ocr_runtime(
            finder=installed,
            runner=lambda *_args, **_kwargs: SimpleNamespace(returncode=0),
            executable="fixture-python",
        )
        is True
    )

    not_called = False

    def unexpected_runner(*_args: Any, **_kwargs: Any) -> Any:
        nonlocal not_called
        not_called = True
        return SimpleNamespace(returncode=0)

    assert (
        probe_ocr_runtime(
            finder=lambda name: None if name == "onnxruntime" else object(),
            runner=unexpected_runner,
            executable="fixture-python",
        )
        is False
    )
    assert not_called is False

    def timed_out(*_args: Any, **_kwargs: Any) -> Any:
        raise subprocess.TimeoutExpired("fixture-python", 15)

    assert (
        probe_ocr_runtime(
            finder=installed,
            runner=timed_out,
            executable="fixture-python",
        )
        is False
    )


@pytest.mark.asyncio
async def test_model_installer_resumes_partial_download_and_verifies_hash(
    tmp_path: Path,
) -> None:
    from paper_reader.reader import OcrModelInstaller

    payload = b"abcdef"
    bundle = tiny_bundle({"det.onnx": payload, "cls.onnx": b"c", "rec.onnx": b"r"})
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        name = request.url.path.rsplit("/", 1)[-1]
        values = {"det.onnx": payload, "cls.onnx": b"c", "rec.onnx": b"r"}
        body = values[name]
        range_header = request.headers.get("Range")
        if name == "det.onnx":
            assert range_header == "bytes=3-"
            return httpx.Response(
                206,
                headers={"Content-Range": "bytes 3-5/6"},
                content=body[3:],
            )
        assert range_header is None
        return httpx.Response(200, content=body)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        installer = OcrModelInstaller(
            models_root=tmp_path / "models",
            client=client,
        )
        partial = installer.model_paths(bundle)["det"].with_suffix(".onnx.part")
        partial.parent.mkdir(parents=True, exist_ok=True)
        partial.write_bytes(payload[:3])
        progress: list[tuple[int, int]] = []

        await installer.install(
            bundle,
            cancelled=lambda: False,
            progress=lambda received, total: progress.append((received, total)),
        )

    assert len(requests) == 3
    assert progress[-1] == (bundle.total_size, bundle.total_size)
    assert partial.exists() is False
    for model in bundle.files:
        path = installer.model_paths(bundle)[model.role]
        assert (
            path.read_bytes()
            == {
                "det.onnx": payload,
                "cls.onnx": b"c",
                "rec.onnx": b"r",
            }[model.filename]
        )


class InterruptingStream(httpx.AsyncByteStream):
    def __init__(self, prefix: bytes) -> None:
        self.prefix = prefix

    async def __aiter__(self):  # type: ignore[no-untyped-def]
        yield self.prefix
        raise httpx.ReadError("connection reset")

    async def aclose(self) -> None:
        return None


@pytest.mark.asyncio
async def test_model_installer_preserves_interrupted_partial_then_resumes(
    tmp_path: Path,
) -> None:
    from paper_reader.reader import OcrModelDownloadError, OcrModelInstaller

    payload = b"resume-after-interruption"
    values = {"det.onnx": payload, "cls.onnx": b"c", "rec.onnx": b"r"}
    bundle = tiny_bundle(values)
    interrupted = True

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal interrupted
        name = request.url.path.rsplit("/", 1)[-1]
        body = values[name]
        if name == "det.onnx" and interrupted:
            interrupted = False
            return httpx.Response(200, stream=InterruptingStream(body[:7]))
        start = int(request.headers.get("Range", "bytes=0-")[6:-1])
        if start:
            return httpx.Response(
                206,
                headers={"Content-Range": f"bytes {start}-{len(body) - 1}/{len(body)}"},
                content=body[start:],
            )
        return httpx.Response(200, content=body)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        installer = OcrModelInstaller(tmp_path / "models", client=client)
        with pytest.raises(OcrModelDownloadError) as captured:
            await installer.install(bundle, cancelled=lambda: False)
        assert captured.value.code == "network"
        partial = installer.model_paths(bundle)["det"].with_suffix(".onnx.part")
        assert partial.read_bytes() == payload[:7]

        await installer.install(bundle, cancelled=lambda: False)

    assert partial.exists() is False
    assert installer.model_paths(bundle)["det"].read_bytes() == payload


@pytest.mark.asyncio
async def test_model_installer_rejects_hash_mismatch_and_honors_cancel(
    tmp_path: Path,
) -> None:
    from paper_reader.reader import OcrModelDownloadError, OcrModelInstaller

    values = {"det.onnx": b"detector", "cls.onnx": b"classifier", "rec.onnx": b"rec"}
    bundle = tiny_bundle(values)

    async def bad_handler(request: httpx.Request) -> httpx.Response:
        name = request.url.path.rsplit("/", 1)[-1]
        body = b"x" * len(values[name])
        return httpx.Response(200, content=body)

    async with httpx.AsyncClient(transport=httpx.MockTransport(bad_handler)) as client:
        installer = OcrModelInstaller(tmp_path / "bad-models", client=client)
        with pytest.raises(OcrModelDownloadError) as captured:
            await installer.install(bundle, cancelled=lambda: False)
        assert captured.value.code == "hash_mismatch"
        assert installer.model_paths(bundle)["det"].exists() is False
        assert installer.model_paths(bundle)["det"].with_suffix(".onnx.part").exists() is False

    cancelled = False

    async def good_handler(request: httpx.Request) -> httpx.Response:
        name = request.url.path.rsplit("/", 1)[-1]
        return httpx.Response(200, content=values[name])

    async with httpx.AsyncClient(transport=httpx.MockTransport(good_handler)) as client:
        installer = OcrModelInstaller(tmp_path / "cancel-models", client=client)

        def progress(_: int, __: int) -> None:
            nonlocal cancelled
            cancelled = True

        with pytest.raises(OcrModelDownloadError) as captured:
            await installer.install(
                bundle,
                cancelled=lambda: cancelled,
                progress=progress,
            )
        assert captured.value.code == "cancelled"


def test_rapidocr_backend_is_lazy_configures_local_models_and_parses_lines(
    tmp_path: Path,
) -> None:
    from paper_reader.reader import RapidOcrBackend

    paths = _rapidocr_paths(tmp_path)
    for path in paths.values():
        path.write_bytes(b"model")
    loaded: list[dict[str, Any]] = []
    calls: list[bytes] = []

    class FakeRapidOCR:
        def __init__(self, *, params: dict[str, Any]) -> None:
            loaded.append(params)

        def __call__(self, image: bytes):  # type: ignore[no-untyped-def]
            calls.append(image)
            return SimpleNamespace(
                boxes=(
                    ((10.0, 20.0), (110.0, 20.0), (110.0, 50.0), (10.0, 50.0)),
                    ((12.0, 60.0), (160.0, 60.0), (160.0, 90.0), (12.0, 90.0)),
                ),
                txts=("中文 English", "agent"),
                scores=(0.93, 0.81),
            )

    module = SimpleNamespace(RapidOCR=FakeRapidOCR, __version__="3.8.1")
    module_calls = 0

    def load_module() -> Any:
        nonlocal module_calls
        module_calls += 1
        return module

    backend = RapidOcrBackend(model_paths=paths, module_loader=load_module)
    assert module_calls == 0

    lines = backend.recognize(b"\x89PNG fixture")
    repeated = backend.recognize(b"\x89PNG second")

    assert module_calls == 1
    assert backend.version == "rapidocr-3.8.1"
    assert [line.text for line in lines] == ["中文 English", "agent"]
    assert [line.confidence for line in lines] == [0.93, 0.81]
    assert lines[0].polygon[0] == (10.0, 20.0)
    assert len(repeated) == 2
    assert calls == [b"\x89PNG fixture", b"\x89PNG second"]
    params = loaded[0]
    assert params["Global.log_level"] == "critical"
    assert params["Det.model_path"] == str(paths["det"])
    assert params["Cls.model_path"] == str(paths["cls"])
    assert params["Rec.model_path"] == str(paths["rec"])
    assert params["Det.ocr_version"] == "PP-OCRv5"
    assert params["Rec.lang_type"] == "ch"


@pytest.mark.parametrize(
    "result",
    (
        SimpleNamespace(boxes=None, txts=None, scores=None),
        SimpleNamespace(boxes=(((0, 0), (1, 0), (1, 1), (0, 1)),), txts=(), scores=()),
        SimpleNamespace(
            boxes=(((0, 0), (1, 0), (1, 1), (0, 1)),),
            txts=("text",),
            scores=(1.5,),
        ),
    ),
)
def test_rapidocr_backend_handles_empty_and_rejects_malformed_output(
    tmp_path: Path,
    result: Any,
) -> None:
    from paper_reader.reader import OcrError, RapidOcrBackend

    paths = _rapidocr_paths(tmp_path)

    class FakeRapidOCR:
        def __init__(self, **_: object) -> None:
            pass

        def __call__(self, _: bytes) -> Any:
            return result

    backend = RapidOcrBackend(
        model_paths=paths,
        module_loader=lambda: SimpleNamespace(RapidOCR=FakeRapidOCR, __version__="test"),
    )

    if result.boxes is None:
        assert backend.recognize(b"image") == ()
    else:
        with pytest.raises(OcrError, match="invalid output") as captured:
            backend.recognize(b"image")
        assert captured.value.code == "invalid_output"


@dataclass(frozen=True, slots=True)
class OcrContext:
    sessions: Any
    store: Any


@pytest.fixture
def ocr_context(tmp_path: Path) -> OcrContext:
    from paper_reader.database import Base, create_session_factory, create_sqlite_engine
    from paper_reader.discovery import models as discovery_models
    from paper_reader.jobs import models as job_models
    from paper_reader.reader import models as reader_models
    from paper_reader.reader.storage import ContentStore

    _ = (
        discovery_models.WorkRecord,
        job_models.JobRecord,
        reader_models.LibraryEntryRecord,
        reader_models.DocumentPageTextRecord,
    )
    engine = create_sqlite_engine(tmp_path / "ocr.sqlite3")
    Base.metadata.create_all(engine)
    return OcrContext(
        sessions=create_session_factory(engine),
        store=ContentStore(tmp_path / "library"),
    )


def add_mixed_document(
    context: OcrContext,
    pdf_path: Path,
    *,
    label: str,
) -> tuple[str, str]:
    from datetime import UTC, datetime

    from paper_reader.discovery.identity import IdentityRepository, SourcePaper
    from paper_reader.discovery.models import DocumentVersionRecord
    from paper_reader.reader.models import DocumentPageTextRecord, LibraryEntryRecord

    now = datetime(2026, 7, 13, 10, 0, tzinfo=UTC)
    stored = context.store.ingest(
        (pdf_path.read_bytes(),),
        media_type="application/pdf",
        max_bytes=10 * 1024 * 1024,
    )
    ingested = IdentityRepository(context.sessions).ingest(
        SourcePaper(
            connector_id="fixture",
            external_id=f"ocr-{label}",
            title=f"OCR fixture {label}",
            authors=("Alice",),
        )
    )
    with context.sessions() as session:
        document = DocumentVersionRecord(
            work_id=ingested.work.id,
            source_record_id=ingested.source_record.id,
            content_sha256=stored.content_sha256,
            access_status="user_provided",
            media_type="application/pdf",
            completeness=100,
            source_updated_at=now,
            extraction_status="complete",
            relative_path=stored.relative_path,
            byte_size=stored.byte_size,
            original_filename=f"{label}.pdf",
            acquisition_kind="manual_import",
            last_accessed_at=now,
            created_at=now,
        )
        session.add(document)
        session.flush()
        session.add(
            LibraryEntryRecord(
                work_id=ingested.work.id,
                added_via="pdf",
                created_at=now,
                updated_at=now,
            )
        )
        session.add_all(
            (
                DocumentPageTextRecord(
                    document_version_id=document.id,
                    page_number=1,
                    page_width=420,
                    page_height=595,
                    native_text="Native evidence page\n",
                    native_blocks=[
                        {
                            "x0": 48.0,
                            "y0": 60.0,
                            "x1": 190.0,
                            "y1": 75.0,
                            "text": "Native evidence page\n",
                            "block_number": 0,
                        }
                    ],
                    native_character_count=18,
                    likely_scanned=False,
                    ocr_state="not_needed",
                    ocr_text=None,
                    ocr_blocks=[],
                    ocr_confidence=None,
                    extractor_version="fixture-native-v1",
                    created_at=now,
                    updated_at=now,
                ),
                DocumentPageTextRecord(
                    document_version_id=document.id,
                    page_number=2,
                    page_width=420,
                    page_height=595,
                    native_text="",
                    native_blocks=[],
                    native_character_count=0,
                    likely_scanned=True,
                    ocr_state="pending",
                    ocr_text=None,
                    ocr_blocks=[],
                    ocr_confidence=None,
                    extractor_version="fixture-native-v1",
                    created_at=now,
                    updated_at=now,
                ),
            )
        )
        session.commit()
        return ingested.work.id, document.id


class RecordingBackend:
    version = "fixture-ocr-engine-v1"

    def __init__(self, outputs: list[Any]) -> None:
        self.outputs = outputs
        self.calls: list[bytes] = []

    def recognize(self, image: bytes) -> Any:
        self.calls.append(image)
        value = self.outputs[min(len(self.calls) - 1, len(self.outputs) - 1)]
        if isinstance(value, Exception):
            raise value
        return value


def build_ready_service(
    context: OcrContext,
    tmp_path: Path,
    backend: RecordingBackend,
    **service_options: Any,
) -> tuple[OcrService, OcrModelBundle, OcrModelInstaller]:
    from paper_reader.jobs.repository import JobRepository
    from paper_reader.reader import OcrModelInstaller, OcrService

    payloads = {
        "det.onnx": b"detector",
        "cls.onnx": b"classifier",
        "rec.onnx": b"recognizer",
    }
    bundle = tiny_bundle(payloads)
    installer = OcrModelInstaller(models_root=tmp_path / "models")
    install_tiny_bundle(installer, bundle, payloads)
    service = OcrService(
        sessions=context.sessions,
        jobs=JobRepository(context.sessions),
        store=context.store,
        bundle=bundle,
        installer=installer,
        backend=backend,
        runtime_probe=lambda: True,
        **service_options,
    )
    return service, bundle, installer


def native_page_snapshot(context: OcrContext, document_id: str) -> tuple[Any, ...]:
    from sqlalchemy import select

    from paper_reader.reader.models import DocumentPageTextRecord

    with context.sessions() as session:
        rows = session.scalars(
            select(DocumentPageTextRecord)
            .where(DocumentPageTextRecord.document_version_id == document_id)
            .order_by(DocumentPageTextRecord.page_number)
        )
        return tuple(
            (
                row.page_number,
                row.page_width,
                row.page_height,
                row.native_text,
                row.native_blocks,
                row.native_character_count,
                row.likely_scanned,
                row.extractor_version,
            )
            for row in rows
        )


def test_ocr_only_processes_scanned_pages_and_preserves_native_evidence(
    ocr_context: OcrContext,
    tmp_path: Path,
) -> None:
    from paper_reader.reader import OcrLine
    from tests.reader.pdf_fixtures import write_mixed_pdf

    pdf = write_mixed_pdf(tmp_path / "mixed-ocr.pdf")
    work_id, document_id = add_mixed_document(
        ocr_context,
        pdf,
        label="mixed",
    )
    backend = RecordingBackend(
        [
            (
                OcrLine(
                    text="癌症 diffusion",
                    confidence=0.96,
                    polygon=((0, 0), (840, 0), (840, 100), (0, 100)),
                ),
                OcrLine(
                    text="agent",
                    confidence=0.72,
                    polygon=((84, 200), (420, 200), (420, 300), (84, 300)),
                ),
            )
        ]
    )
    service, bundle, _ = build_ready_service(ocr_context, tmp_path, backend)
    before = native_page_snapshot(ocr_context, document_id)

    result = service.recognize_scanned_pages(
        document_id,
        cancelled=lambda: False,
    )

    assert result.document_version_id == document_id
    assert result.page_count == 1
    assert result.line_count == 2
    assert result.aggregate_confidence == pytest.approx(0.885, abs=0.001)
    assert result.engine_version == backend.version
    assert result.model_bundle_id == bundle.bundle_id
    assert len(backend.calls) == 1
    assert backend.calls[0].startswith(b"\x89PNG")
    assert native_page_snapshot(ocr_context, document_id) == before

    from paper_reader.reader.models import DocumentPageTextRecord

    with ocr_context.sessions() as session:
        native = session.get(DocumentPageTextRecord, (document_id, 1))
        scanned = session.get(DocumentPageTextRecord, (document_id, 2))
        assert native is not None
        assert scanned is not None
        assert native.ocr_state == "not_needed"
        assert native.ocr_text is None
        assert scanned.ocr_state == "complete"
        assert scanned.ocr_text == "癌症 diffusion\nagent\n"
        assert scanned.ocr_confidence == pytest.approx(0.885, abs=0.001)
        assert scanned.ocr_engine_version == backend.version
        assert scanned.ocr_model_bundle_id == bundle.bundle_id
        assert scanned.ocr_blocks == [
            {
                "x0": 0.0,
                "y0": 0.0,
                "x1": 420.0,
                "y1": 50.0,
                "text": "癌症 diffusion",
                "block_number": 0,
                "confidence": 0.96,
            },
            {
                "x0": 42.0,
                "y0": 100.0,
                "x1": 210.0,
                "y1": 150.0,
                "text": "agent",
                "block_number": 1,
                "confidence": 0.72,
            },
        ]


def test_ocr_service_uses_isolated_document_processor_without_loading_parent_backend(
    ocr_context: OcrContext,
    tmp_path: Path,
) -> None:
    from paper_reader.reader import (
        IsolatedOcrDocument,
        IsolatedOcrPage,
        OcrLine,
    )
    from tests.reader.pdf_fixtures import write_mixed_pdf

    pdf = write_mixed_pdf(tmp_path / "isolated-ocr.pdf")
    _, document_id = add_mixed_document(
        ocr_context,
        pdf,
        label="isolated-ocr",
    )
    backend = RecordingBackend([RuntimeError("parent backend must remain unloaded")])

    class RecordingProcessor:
        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []

        def process(self, **kwargs: Any) -> IsolatedOcrDocument:
            self.calls.append(kwargs)
            page = kwargs["pages"][0]
            kwargs["progress"](1, 1)
            return IsolatedOcrDocument(
                pages=(
                    IsolatedOcrPage(
                        page_number=page.page_number,
                        rendered_width=840,
                        rendered_height=1190,
                        lines=(
                            OcrLine(
                                text="isolated evidence",
                                confidence=0.91,
                                polygon=((0, 0), (840, 0), (840, 100), (0, 100)),
                            ),
                        ),
                    ),
                ),
                engine_version="rapidocr-isolated-v1",
            )

    processor = RecordingProcessor()
    service, bundle, _ = build_ready_service(
        ocr_context,
        tmp_path,
        backend,
        document_processor=processor,
    )
    progress: list[tuple[int, int]] = []

    result = service.recognize_scanned_pages(
        document_id,
        cancelled=lambda: False,
        progress=lambda completed, total: progress.append((completed, total)),
    )

    assert result.engine_version == "rapidocr-isolated-v1"
    assert result.model_bundle_id == bundle.bundle_id
    assert result.pages[0].text == "isolated evidence\n"
    assert backend.calls == []
    assert len(processor.calls) == 1
    assert progress == [(1, 1)]


def test_ocr_zero_result_is_complete_auditable_and_effective(
    ocr_context: OcrContext,
    tmp_path: Path,
) -> None:
    from paper_reader.jobs.repository import JobRepository
    from paper_reader.reader.extraction import DocumentExtractionService, PdfTextExtractor
    from paper_reader.reader.models import DocumentPageTextRecord
    from tests.reader.pdf_fixtures import write_mixed_pdf

    pdf = write_mixed_pdf(tmp_path / "empty-ocr.pdf")
    work_id, document_id = add_mixed_document(
        ocr_context,
        pdf,
        label="empty",
    )
    backend = RecordingBackend([()])
    service, bundle, _ = build_ready_service(ocr_context, tmp_path, backend)

    result = service.recognize_scanned_pages(
        document_id,
        cancelled=lambda: False,
    )

    assert result.line_count == 0
    assert result.aggregate_confidence == 0
    with ocr_context.sessions() as session:
        page = session.get(DocumentPageTextRecord, (document_id, 2))
        assert page is not None
        assert page.ocr_state == "complete"
        assert page.ocr_text == ""
        assert page.ocr_blocks == []
        assert page.ocr_confidence == 0
        assert page.ocr_model_bundle_id == bundle.bundle_id

    pages = DocumentExtractionService(
        sessions=ocr_context.sessions,
        jobs=JobRepository(ocr_context.sessions),
        store=ocr_context.store,
        extractor=PdfTextExtractor(),
    ).list_pages(work_id=work_id, document_version_id=document_id)
    assert pages[1].text_source == "ocr"
    assert pages[1].text == ""
    assert pages[1].ocr_engine_version == backend.version
    assert pages[1].ocr_model_bundle_id == bundle.bundle_id


@pytest.mark.parametrize("mode", ("cancel", "backend_failure"))
def test_ocr_cancel_or_backend_failure_never_publishes_partial_pages(
    ocr_context: OcrContext,
    tmp_path: Path,
    mode: str,
) -> None:
    from paper_reader.reader import OcrError, OcrLine
    from paper_reader.reader.models import DocumentPageTextRecord
    from tests.reader.pdf_fixtures import write_mixed_pdf

    pdf = write_mixed_pdf(tmp_path / f"atomic-{mode}.pdf")
    _, document_id = add_mixed_document(
        ocr_context,
        pdf,
        label=f"atomic-{mode}",
    )
    with ocr_context.sessions() as session:
        first = session.get(DocumentPageTextRecord, (document_id, 1))
        assert first is not None
        first.likely_scanned = True
        first.ocr_state = "pending"
        session.commit()

    first_output = (
        OcrLine(
            text="first",
            confidence=0.9,
            polygon=((0, 0), (100, 0), (100, 20), (0, 20)),
        ),
    )
    second_output: Any = (
        OcrError("runtime_failure", "OCR backend failed", retryable=True)
        if mode == "backend_failure"
        else first_output
    )
    backend = RecordingBackend([first_output, second_output])
    service, _, _ = build_ready_service(ocr_context, tmp_path, backend)

    def cancelled() -> bool:
        return mode == "cancel" and len(backend.calls) >= 1

    with pytest.raises(OcrError) as captured:
        service.recognize_scanned_pages(
            document_id,
            cancelled=cancelled,
        )
    assert captured.value.code == ("cancelled" if mode == "cancel" else "runtime_failure")

    with ocr_context.sessions() as session:
        pages = (
            session.get(DocumentPageTextRecord, (document_id, 1)),
            session.get(DocumentPageTextRecord, (document_id, 2)),
        )
        assert all(page is not None for page in pages)
        assert [page.ocr_text for page in pages if page is not None] == [None, None]
        assert [page.ocr_state for page in pages if page is not None] == [
            "pending",
            "pending",
        ]


@pytest.mark.parametrize(
    ("service_options", "outputs", "scan_first_page", "expected_code"),
    (
        ({"max_scanned_pages": 1}, [()], True, "too_many_pages"),
        ({"max_image_bytes": 1}, [()], False, "image_too_large"),
        (
            {"max_lines_per_page": 1},
            [
                (
                    ("first", 0.9, ((0, 0), (100, 0), (100, 20), (0, 20))),
                    ("second", 0.8, ((0, 20), (100, 20), (100, 40), (0, 40))),
                )
            ],
            False,
            "too_many_lines",
        ),
        (
            {"max_characters_per_page": 4},
            [(("secret overflow", 0.9, ((0, 0), (100, 0), (100, 20), (0, 20))),)],
            False,
            "too_much_text",
        ),
    ),
)
def test_ocr_enforces_page_resource_limits_without_partial_evidence(
    ocr_context: OcrContext,
    tmp_path: Path,
    service_options: dict[str, int],
    outputs: list[tuple[tuple[str, float, tuple[tuple[int, int], ...]], ...]],
    scan_first_page: bool,
    expected_code: str,
) -> None:
    from paper_reader.reader import OcrError, OcrLine
    from paper_reader.reader.models import DocumentPageTextRecord
    from tests.reader.pdf_fixtures import write_mixed_pdf

    pdf = write_mixed_pdf(tmp_path / f"limit-{expected_code}.pdf")
    _, document_id = add_mixed_document(
        ocr_context,
        pdf,
        label=f"limit-{expected_code}",
    )
    if scan_first_page:
        with ocr_context.sessions() as session:
            first = session.get(DocumentPageTextRecord, (document_id, 1))
            assert first is not None
            first.likely_scanned = True
            first.ocr_state = "pending"
            session.commit()
    backend_outputs = [
        tuple(
            OcrLine(text=text, confidence=confidence, polygon=polygon)
            for text, confidence, polygon in output
        )
        for output in outputs
    ]
    backend = RecordingBackend(backend_outputs)
    service, _, _ = build_ready_service(
        ocr_context,
        tmp_path,
        backend,
        **service_options,
    )

    with pytest.raises(OcrError) as captured:
        service.recognize_scanned_pages(document_id, cancelled=lambda: False)

    assert captured.value.code == expected_code
    assert str(pdf) not in str(captured.value)
    assert "secret overflow" not in str(captured.value)
    with ocr_context.sessions() as session:
        pages = tuple(
            session.get(DocumentPageTextRecord, (document_id, page_number))
            for page_number in (1, 2)
        )
        assert all(page is not None for page in pages)
        assert all(page.ocr_text is None for page in pages if page is not None)


def test_ocr_enforces_document_text_and_elapsed_limits_atomically(
    ocr_context: OcrContext,
    tmp_path: Path,
) -> None:
    from paper_reader.reader import OcrError, OcrLine
    from paper_reader.reader.models import DocumentPageTextRecord
    from tests.reader.pdf_fixtures import write_mixed_pdf

    pdf = write_mixed_pdf(tmp_path / "bounded-ocr.pdf")
    _, document_id = add_mixed_document(ocr_context, pdf, label="bounded-ocr")
    with ocr_context.sessions() as session:
        first = session.get(DocumentPageTextRecord, (document_id, 1))
        assert first is not None
        first.likely_scanned = True
        first.ocr_state = "pending"
        session.commit()

    line = OcrLine(
        text="four",
        confidence=0.9,
        polygon=((0, 0), (100, 0), (100, 20), (0, 20)),
    )
    backend = RecordingBackend([(line,), (line,)])
    service, _, _ = build_ready_service(
        ocr_context,
        tmp_path,
        backend,
        max_characters_per_page=10,
        max_characters_total=7,
    )
    with pytest.raises(OcrError) as captured:
        service.recognize_scanned_pages(document_id, cancelled=lambda: False)
    assert captured.value.code == "too_much_text"

    line_limited_service, _, _ = build_ready_service(
        ocr_context,
        tmp_path,
        RecordingBackend([(line,), (line,)]),
        max_lines_per_page=2,
        max_lines_total=1,
    )
    with pytest.raises(OcrError) as captured:
        line_limited_service.recognize_scanned_pages(
            document_id,
            cancelled=lambda: False,
        )
    assert captured.value.code == "too_many_lines"

    ticks = iter((0.0, 0.0, 0.0, 2.0))
    timed_service, _, _ = build_ready_service(
        ocr_context,
        tmp_path,
        RecordingBackend([(line,), (line,)]),
        max_seconds=1.0,
        clock=lambda: next(ticks, 2.0),
    )
    with pytest.raises(OcrError) as captured:
        timed_service.recognize_scanned_pages(document_id, cancelled=lambda: False)
    assert captured.value.code == "time_limit"

    with ocr_context.sessions() as session:
        pages = tuple(
            session.get(DocumentPageTextRecord, (document_id, page_number))
            for page_number in (1, 2)
        )
        assert all(page is not None for page in pages)
        assert all(page.ocr_text is None for page in pages if page is not None)


@pytest.mark.parametrize(
    "option",
    (
        "max_scanned_pages",
        "max_render_pixels",
        "max_image_bytes",
        "max_lines_per_page",
        "max_lines_total",
        "max_characters_per_page",
        "max_characters_total",
        "max_seconds",
    ),
)
def test_ocr_rejects_nonpositive_resource_limits(
    ocr_context: OcrContext,
    tmp_path: Path,
    option: str,
) -> None:
    with pytest.raises(ValueError, match=option):
        build_ready_service(
            ocr_context,
            tmp_path,
            RecordingBackend([()]),
            **{option: 0},
        )


def test_ocr_capability_blocks_recognition_but_not_explicit_install(
    ocr_context: OcrContext,
    tmp_path: Path,
) -> None:
    from paper_reader.jobs.repository import JobRepository
    from paper_reader.reader import OcrError, OcrModelInstaller, OcrService
    from tests.reader.pdf_fixtures import write_mixed_pdf

    pdf = write_mixed_pdf(tmp_path / "capability.pdf")
    work_id, document_id = add_mixed_document(
        ocr_context,
        pdf,
        label="capability",
    )
    payloads = {
        "det.onnx": b"detector",
        "cls.onnx": b"classifier",
        "rec.onnx": b"recognizer",
    }
    bundle = tiny_bundle(payloads)
    installer = OcrModelInstaller(tmp_path / "models")
    backend = RecordingBackend([()])

    missing_models = OcrService(
        sessions=ocr_context.sessions,
        jobs=JobRepository(ocr_context.sessions),
        store=ocr_context.store,
        bundle=bundle,
        installer=installer,
        backend=backend,
        runtime_probe=lambda: True,
    )
    with pytest.raises(OcrError) as captured:
        missing_models.create_recognition_job(
            work_id=work_id,
            document_version_id=document_id,
            idempotency_key="missing-models",
        )
    assert captured.value.code == "models_missing"

    install_tiny_bundle(installer, bundle, payloads)
    missing_runtime = OcrService(
        sessions=ocr_context.sessions,
        jobs=JobRepository(ocr_context.sessions),
        store=ocr_context.store,
        bundle=bundle,
        installer=installer,
        backend=backend,
        runtime_probe=lambda: False,
    )
    with pytest.raises(OcrError) as captured:
        missing_runtime.create_recognition_job(
            work_id=work_id,
            document_version_id=document_id,
            idempotency_key="missing-runtime",
        )
    assert captured.value.code == "runtime_unavailable"
    install_job = missing_runtime.create_install_job(idempotency_key="install-anyway")
    assert install_job.kind == "reader.install_ocr_models"


@pytest.mark.asyncio
async def test_ocr_install_and_recognition_jobs_are_idempotent_and_resumable(
    ocr_context: OcrContext,
    tmp_path: Path,
) -> None:
    from paper_reader.jobs.repository import JobRepository
    from paper_reader.reader import OcrLine, OcrModelInstaller, OcrService
    from tests.reader.pdf_fixtures import write_mixed_pdf

    pdf = write_mixed_pdf(tmp_path / "jobs.pdf")
    work_id, document_id = add_mixed_document(ocr_context, pdf, label="jobs")
    payloads = {
        "det.onnx": b"detector",
        "cls.onnx": b"classifier",
        "rec.onnx": b"recognizer",
    }
    bundle = tiny_bundle(payloads)

    async def handler(request: httpx.Request) -> httpx.Response:
        name = request.url.path.rsplit("/", 1)[-1]
        return httpx.Response(200, content=payloads[name])

    jobs = JobRepository(ocr_context.sessions)
    backend = RecordingBackend(
        [
            (
                OcrLine(
                    text="job evidence",
                    confidence=0.87,
                    polygon=((0, 0), (400, 0), (400, 50), (0, 50)),
                ),
            )
        ]
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        installer = OcrModelInstaller(tmp_path / "models", client=client)
        service = OcrService(
            sessions=ocr_context.sessions,
            jobs=jobs,
            store=ocr_context.store,
            bundle=bundle,
            installer=installer,
            backend=backend,
            runtime_probe=lambda: True,
        )
        install = service.create_install_job(idempotency_key="install-models")
        duplicate_install = service.create_install_job(idempotency_key="install-models")
        assert duplicate_install.id == install.id
        assert service.resumable_install_job_ids() == (install.id,)

        await service.install_models(install.id)

        installed = jobs.get(install.id)
        assert installed is not None
        assert installed.status == "succeeded"
        assert installed.result == {
            "bundle_id": bundle.bundle_id,
            "version": bundle.version,
            "installed_files": 3,
            "installed_bytes": bundle.total_size,
        }
        recognition = service.create_recognition_job(
            work_id=work_id,
            document_version_id=document_id,
            idempotency_key="ocr-document",
        )
        duplicate_recognition = service.create_recognition_job(
            work_id=work_id,
            document_version_id=document_id,
            idempotency_key="ocr-document",
        )
        assert duplicate_recognition.id == recognition.id
        assert service.resumable_recognition_job_ids() == (recognition.id,)

        await service.execute_recognition(recognition.id)

    finished = jobs.get(recognition.id)
    assert finished is not None
    assert finished.status == "succeeded"
    assert finished.progress == 100
    assert finished.result is not None
    assert finished.result["document_version_id"] == document_id
    assert finished.result["page_count"] == 1
    assert finished.result["line_count"] == 1
    assert service.resumable_recognition_job_ids() == ()
    event_types = [event.event_type for event in jobs.list_events()]
    assert "reader.ocr_models.succeeded" in event_types
    assert "reader.ocr.succeeded" in event_types


def _valid_manifest_payload() -> dict[str, Any]:
    values = {"det": b"det", "cls": b"cls", "rec": b"rec"}
    return {
        "bundle_id": "fixture-bundle",
        "display_name": "Fixture OCR models",
        "version": "fixture-v1",
        "source_revision": "f" * 40,
        "source_url": "https://models.example.test/source",
        "license": {
            "name": "Apache-2.0",
            "url": "https://models.example.test/license",
            "copyright_notice": "Fixture copyright",
        },
        "files": [
            {
                "role": role,
                "filename": f"{role}.onnx",
                "url": f"https://models.example.test/{role}.onnx",
                "size": len(value),
                "sha256": sha256(value).hexdigest(),
            }
            for role, value in values.items()
        ],
    }


def test_manifest_strictly_rejects_malformed_and_ambiguous_bundles(
    tmp_path: Path,
) -> None:
    from paper_reader.reader import OcrManifestError, load_ocr_manifest

    base = _valid_manifest_payload()

    def changed(mutator: Any) -> dict[str, Any]:
        value = cast(dict[str, Any], json.loads(json.dumps(base)))
        mutator(value)
        return value

    cases: tuple[tuple[str, Any, str], ...] = (
        ("invalid-json", "{", "cannot be read"),
        ("non-object", [], "root must be an object"),
        (
            "unknown-root-field",
            changed(lambda value: value.update({"unexpected": True})),
            "fields are incomplete or unknown",
        ),
        (
            "blank-display-name",
            changed(lambda value: value.__setitem__("display_name", "  ")),
            "display_name must be a non-empty string",
        ),
        (
            "unsafe-bundle-id",
            changed(lambda value: value.__setitem__("bundle_id", "../escape")),
            "bundle_id is unsafe",
        ),
        (
            "invalid-revision",
            changed(lambda value: value.__setitem__("source_revision", "latest")),
            "source revision must be a Git hash",
        ),
        (
            "invalid-license",
            changed(lambda value: value.__setitem__("license", [])),
            "license is incomplete or unknown",
        ),
        (
            "wrong-file-count",
            changed(lambda value: value["files"].pop()),
            "must contain det, cls, and rec files",
        ),
        (
            "unknown-file-field",
            changed(lambda value: value["files"][0].update({"extra": "no"})),
            "model fields are incomplete or unknown",
        ),
        (
            "invalid-role",
            changed(lambda value: value["files"][0].__setitem__("role", "seg")),
            "model role is invalid",
        ),
        (
            "non-positive-size",
            changed(lambda value: value["files"][0].__setitem__("size", 0)),
            "model size must be positive",
        ),
        (
            "wrong-role-order",
            changed(lambda value: value["files"].reverse()),
            "model roles must be det, cls, and rec",
        ),
        (
            "duplicate-filename",
            changed(
                lambda value: value["files"][1].__setitem__(
                    "filename", value["files"][0]["filename"]
                )
            ),
            "model filenames must be unique",
        ),
    )

    for name, payload, message in cases:
        path = tmp_path / f"{name}.json"
        if isinstance(payload, str):
            path.write_text(payload, encoding="utf-8")
        else:
            path.write_text(json.dumps(payload), encoding="utf-8")
        with pytest.raises(OcrManifestError, match=message):
            load_ocr_manifest(path)


@pytest.mark.asyncio
async def test_model_installer_handles_cached_files_and_partial_edge_cases(
    tmp_path: Path,
) -> None:
    from paper_reader.reader import OcrModelDownloadError, OcrModelInstaller

    values = {"det.onnx": b"detector", "cls.onnx": b"cls", "rec.onnx": b"rec"}
    bundle = tiny_bundle(values)
    unavailable = OcrModelInstaller(tmp_path / "no-client")
    with pytest.raises(OcrModelDownloadError) as captured:
        await unavailable.install(bundle, cancelled=lambda: False)
    assert captured.value.code == "network"

    requests: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        name = request.url.path.rsplit("/", 1)[-1]
        requests.append(name)
        return httpx.Response(200, content=values[name])

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        cached = OcrModelInstaller(tmp_path / "cached", client=client)
        install_tiny_bundle(cached, bundle, values)
        await cached.install(bundle, cancelled=lambda: False)
        assert requests == []

        complete_partial = OcrModelInstaller(tmp_path / "complete-partial", client=client)
        det_partial = complete_partial.model_paths(bundle)["det"].with_suffix(".onnx.part")
        det_partial.parent.mkdir(parents=True, exist_ok=True)
        det_partial.write_bytes(values["det.onnx"])
        await complete_partial.install(bundle, cancelled=lambda: False)
        assert complete_partial.model_paths(bundle)["det"].read_bytes() == values["det.onnx"]

        oversized_partial = OcrModelInstaller(tmp_path / "oversized", client=client)
        oversized = oversized_partial.model_paths(bundle)["det"].with_suffix(".onnx.part")
        oversized.parent.mkdir(parents=True, exist_ok=True)
        oversized.write_bytes(b"too-long-for-detector")
        await oversized_partial.install(bundle, cancelled=lambda: False)
        assert oversized_partial.model_paths(bundle)["det"].read_bytes() == values["det.onnx"]

        ignored_range = OcrModelInstaller(tmp_path / "ignored-range", client=client)
        ignored = ignored_range.model_paths(bundle)["det"].with_suffix(".onnx.part")
        ignored.parent.mkdir(parents=True, exist_ok=True)
        ignored.write_bytes(values["det.onnx"][:2])
        await ignored_range.install(bundle, cancelled=lambda: False)
        assert ignored_range.model_paths(bundle)["det"].read_bytes() == values["det.onnx"]


@pytest.mark.parametrize(
    ("mode", "expected_code"),
    (
        ("bad-range", "network"),
        ("compressed", "network"),
        ("server-error", "network"),
        ("truncated", "network"),
        ("oversized", "hash_mismatch"),
    ),
)
@pytest.mark.asyncio
async def test_model_installer_rejects_untrusted_or_incomplete_responses(
    tmp_path: Path,
    mode: str,
    expected_code: str,
) -> None:
    from paper_reader.reader import OcrModelDownloadError, OcrModelInstaller

    values = {"det.onnx": b"detector", "cls.onnx": b"cls", "rec.onnx": b"rec"}
    bundle = tiny_bundle(values)

    async def handler(request: httpx.Request) -> httpx.Response:
        name = request.url.path.rsplit("/", 1)[-1]
        body = values[name]
        if name != "det.onnx":
            return httpx.Response(200, content=body)
        if mode == "bad-range":
            return httpx.Response(
                206,
                headers={"Content-Range": "bytes 1-7/8"},
                content=body[2:],
            )
        if mode == "compressed":
            return httpx.Response(
                200,
                headers={"Content-Encoding": "gzip"},
                content=body,
            )
        if mode == "server-error":
            return httpx.Response(503)
        if mode == "truncated":
            return httpx.Response(200, content=body[:-1])
        return httpx.Response(200, content=body + b"x")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        installer = OcrModelInstaller(tmp_path / mode, client=client)
        if mode == "bad-range":
            partial = installer.model_paths(bundle)["det"].with_suffix(".onnx.part")
            partial.parent.mkdir(parents=True, exist_ok=True)
            partial.write_bytes(values["det.onnx"][:2])
        with pytest.raises(OcrModelDownloadError) as captured:
            await installer.install(bundle, cancelled=lambda: False)

    assert captured.value.code == expected_code


@pytest.mark.asyncio
async def test_model_installer_contains_callback_failures(tmp_path: Path) -> None:
    from paper_reader.reader import OcrModelDownloadError, OcrModelInstaller

    values = {"det.onnx": b"detector", "cls.onnx": b"cls", "rec.onnx": b"rec"}
    bundle = tiny_bundle(values)

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=values[request.url.path.rsplit("/", 1)[-1]],
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        installer = OcrModelInstaller(tmp_path / "callbacks", client=client)

        def broken_cancel() -> bool:
            raise RuntimeError("cancel probe failed")

        with pytest.raises(OcrModelDownloadError) as captured:
            await installer.install(bundle, cancelled=broken_cancel)
        assert captured.value.code == "internal_error"

        def broken_progress(_: int, __: int) -> None:
            raise RuntimeError("progress sink failed")

        with pytest.raises(OcrModelDownloadError) as captured:
            await installer.install(
                bundle,
                cancelled=lambda: False,
                progress=broken_progress,
            )
        assert captured.value.code == "internal_error"


def test_rapidocr_backend_contains_runtime_loading_and_execution_failures(
    tmp_path: Path,
) -> None:
    from paper_reader.reader import OcrError, RapidOcrBackend

    paths = _rapidocr_paths(tmp_path)
    with pytest.raises(ValueError, match="requires det, cls, and rec"):
        RapidOcrBackend(model_paths={"det": paths["det"]})

    def unavailable() -> Any:
        raise ImportError("rapidocr missing")

    backend = RapidOcrBackend(model_paths=paths, module_loader=unavailable)
    with pytest.raises(OcrError) as captured:
        backend.recognize(b"image")
    assert captured.value.code == "runtime_unavailable"

    class BrokenConstructor:
        def __init__(self, **_: object) -> None:
            raise RuntimeError("cannot load model")

    backend = RapidOcrBackend(
        model_paths=paths,
        module_loader=lambda: SimpleNamespace(
            RapidOCR=BrokenConstructor,
            __version__="fixture",
        ),
    )
    with pytest.raises(OcrError) as captured:
        backend.recognize(b"image")
    assert captured.value.code == "runtime_failure"

    class BrokenCall:
        def __init__(self, **_: object) -> None:
            pass

        def __call__(self, _: bytes) -> Any:
            raise RuntimeError("recognition crashed")

    backend = RapidOcrBackend(
        model_paths=paths,
        module_loader=lambda: SimpleNamespace(RapidOCR=BrokenCall),
    )
    with pytest.raises(OcrError) as captured:
        backend.recognize(b"image")
    assert captured.value.code == "runtime_failure"


@pytest.mark.parametrize(
    "result",
    (
        SimpleNamespace(boxes=(), txts=None, scores=()),
        SimpleNamespace(boxes=("not-a-box",), txts=("text",), scores=(0.8,)),
        SimpleNamespace(
            boxes=(((0, 0), (1, 0), (1, 1), (0,)),),
            txts=("text",),
            scores=(0.8,),
        ),
        SimpleNamespace(
            boxes=(((0, 0), (1, 0), (1, float("nan")), (0, 1)),),
            txts=("text",),
            scores=(0.8,),
        ),
        SimpleNamespace(
            boxes=(((0, 0), (1, 0), (1, 1), (0, 1)),),
            txts=("text",),
            scores=("not-a-score",),
        ),
    ),
)
def test_rapidocr_backend_rejects_incomplete_or_nonfinite_output(
    tmp_path: Path,
    result: Any,
) -> None:
    from paper_reader.reader import OcrError, RapidOcrBackend

    paths = _rapidocr_paths(tmp_path)

    class FakeRapidOCR:
        def __init__(self, **_: object) -> None:
            pass

        def __call__(self, _: bytes) -> Any:
            return result

    backend = RapidOcrBackend(
        model_paths=paths,
        module_loader=lambda: SimpleNamespace(RapidOCR=FakeRapidOCR),
    )
    with pytest.raises(OcrError) as captured:
        backend.recognize(b"image")
    assert captured.value.code == "invalid_output"


@pytest.mark.asyncio
async def test_ocr_jobs_persist_cancellation_and_typed_failures(
    ocr_context: OcrContext,
    tmp_path: Path,
) -> None:
    from paper_reader.jobs.repository import JobRepository
    from paper_reader.reader import (
        OcrError,
        OcrModelInstaller,
        OcrService,
    )
    from tests.reader.pdf_fixtures import write_mixed_pdf

    pdf = write_mixed_pdf(tmp_path / "job-failures.pdf")
    work_id, document_id = add_mixed_document(
        ocr_context,
        pdf,
        label="job-failures",
    )
    jobs = JobRepository(ocr_context.sessions)
    payloads = {
        "det.onnx": b"detector",
        "cls.onnx": b"classifier",
        "rec.onnx": b"recognizer",
    }
    bundle = tiny_bundle(payloads)
    ready_installer = OcrModelInstaller(tmp_path / "ready-models")
    install_tiny_bundle(ready_installer, bundle, payloads)
    backend = RecordingBackend([OcrError("runtime_failure", "backend failed", retryable=True)])
    service = OcrService(
        sessions=ocr_context.sessions,
        jobs=jobs,
        store=ocr_context.store,
        bundle=bundle,
        installer=ready_installer,
        backend=backend,
        runtime_probe=lambda: True,
    )

    cancelled_install = service.create_install_job(idempotency_key="cancel-install")
    jobs.request_cancel(cancelled_install.id)
    await service.install_models(cancelled_install.id)
    finished = jobs.get(cancelled_install.id)
    assert finished is not None
    assert finished.status == "cancelled"
    assert finished.error_code == "cancelled"

    missing_installer = OcrModelInstaller(tmp_path / "missing-models")
    missing_service = OcrService(
        sessions=ocr_context.sessions,
        jobs=jobs,
        store=ocr_context.store,
        bundle=bundle,
        installer=missing_installer,
        backend=backend,
        runtime_probe=lambda: True,
    )
    failed_install = missing_service.create_install_job(idempotency_key="network-install")
    await missing_service.install_models(failed_install.id)
    finished = jobs.get(failed_install.id)
    assert finished is not None
    assert finished.status == "failed"
    assert finished.error_code == "network"
    assert finished.result == {"retryable": True}

    cancelled_ocr = service.create_recognition_job(
        work_id=work_id,
        document_version_id=document_id,
        idempotency_key="cancel-ocr",
    )
    jobs.request_cancel(cancelled_ocr.id)
    await service.execute_recognition(cancelled_ocr.id)
    finished = jobs.get(cancelled_ocr.id)
    assert finished is not None
    assert finished.status == "cancelled"
    assert finished.error_code == "cancelled"

    failed_ocr = service.create_recognition_job(
        work_id=work_id,
        document_version_id=document_id,
        idempotency_key="failed-ocr",
    )
    await service.execute_recognition(failed_ocr.id)
    finished = jobs.get(failed_ocr.id)
    assert finished is not None
    assert finished.status == "failed"
    assert finished.error_code == "runtime_failure"
    assert finished.result == {"retryable": True}

    event_types = [event.event_type for event in jobs.list_events()]
    assert "reader.ocr_models.cancelled" in event_types
    assert "reader.ocr_models.failed" in event_types
    assert "reader.ocr.cancelled" in event_types
    assert "reader.ocr.failed" in event_types


@pytest.mark.asyncio
async def test_ocr_job_commands_reject_wrong_identity_or_payload(
    ocr_context: OcrContext,
    tmp_path: Path,
) -> None:
    from paper_reader.jobs.repository import JobRepository
    from paper_reader.reader.ocr_contracts import (
        OCR_MODEL_INSTALL_JOB_KIND,
        OCR_RECOGNITION_JOB_KIND,
    )
    from paper_reader.reader.ocr_runtime import OcrModelInstaller
    from paper_reader.reader.ocr_service import OcrService

    jobs = JobRepository(ocr_context.sessions)
    bundle = tiny_bundle()
    installer = OcrModelInstaller(tmp_path / "models")
    service = OcrService(
        sessions=ocr_context.sessions,
        jobs=jobs,
        store=ocr_context.store,
        bundle=bundle,
        installer=installer,
        backend=RecordingBackend([()]),
        runtime_probe=lambda: True,
    )

    with pytest.raises(ValueError, match="1-160"):
        service.create_install_job(idempotency_key=" ")
    with pytest.raises(ValueError, match="1-160"):
        service.create_install_job(idempotency_key="x" * 161)
    with pytest.raises(KeyError):
        await service.install_models("missing-job")

    wrong_kind = jobs.create(
        kind="fixture.other",
        payload={},
        idempotency_key="wrong-kind",
    )
    with pytest.raises(ValueError, match="not the requested OCR command"):
        await service.install_models(wrong_kind.id)

    bad_install = jobs.create(
        kind=OCR_MODEL_INSTALL_JOB_KIND,
        payload={"bundle_id": "other"},
        idempotency_key="bad-install-payload",
    )
    with pytest.raises(ValueError, match="payload is invalid"):
        await service.install_models(bad_install.id)

    bad_recognition = jobs.create(
        kind=OCR_RECOGNITION_JOB_KIND,
        payload={"work_id": 1},
        idempotency_key="bad-recognition-payload",
    )
    with pytest.raises(ValueError, match="payload is invalid"):
        await service.execute_recognition(bad_recognition.id)

    foreign_bundle = jobs.create(
        kind=OCR_RECOGNITION_JOB_KIND,
        payload={
            "work_id": "work",
            "document_version_id": "document",
            "content_sha256": "0" * 64,
            "bundle_id": "other",
            "version": "other",
        },
        idempotency_key="foreign-bundle",
    )
    with pytest.raises(ValueError, match="different model bundle"):
        await service.execute_recognition(foreign_bundle.id)

    collision = jobs.create(
        kind="fixture.other",
        payload={"different": True},
        idempotency_key="command-collision",
    )
    assert collision.kind == "fixture.other"
    with pytest.raises(ValueError, match="different OCR model command"):
        service.create_install_job(idempotency_key="command-collision")


@pytest.mark.parametrize(
    "mode",
    ("not-in-library", "missing-file", "restricted", "unsafe-path", "not-extracted"),
)
def test_ocr_rechecks_document_access_and_local_integrity(
    ocr_context: OcrContext,
    tmp_path: Path,
    mode: str,
) -> None:
    from paper_reader.discovery.models import DocumentVersionRecord
    from paper_reader.reader import OcrError
    from paper_reader.reader.models import LibraryEntryRecord
    from tests.reader.pdf_fixtures import write_mixed_pdf

    pdf = write_mixed_pdf(tmp_path / f"integrity-{mode}.pdf")
    _, document_id = add_mixed_document(
        ocr_context,
        pdf,
        label=f"integrity-{mode}",
    )
    service, _, _ = build_ready_service(
        ocr_context,
        tmp_path / mode,
        RecordingBackend([()]),
    )

    with ocr_context.sessions() as session:
        document = session.get(DocumentVersionRecord, document_id)
        assert document is not None
        if mode == "not-in-library":
            entry = session.get(LibraryEntryRecord, document.work_id)
            assert entry is not None
            session.delete(entry)
        elif mode == "missing-file":
            path = ocr_context.store.resolve_object(
                document.content_sha256,
                document.relative_path,
            )
            path.unlink()
        elif mode == "restricted":
            document.access_status = "restricted"
        elif mode == "unsafe-path":
            document.relative_path = "../escape.pdf"
        else:
            document.extraction_status = "pending"
        session.commit()

    with pytest.raises(OcrError) as captured:
        service.recognize_scanned_pages(document_id, cancelled=lambda: False)
    assert captured.value.code == "document_unavailable"


def test_ocr_rejects_oversized_page_raster_before_allocating_pixels(
    ocr_context: OcrContext,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from paper_reader.reader import OcrError, ocr_service
    from paper_reader.reader.models import DocumentPageTextRecord
    from tests.reader.pdf_fixtures import write_mixed_pdf

    pdf = write_mixed_pdf(tmp_path / "oversized-raster.pdf")
    _, document_id = add_mixed_document(
        ocr_context,
        pdf,
        label="oversized-raster",
    )
    with ocr_context.sessions() as session:
        row = session.get(DocumentPageTextRecord, (document_id, 2))
        assert row is not None
        row.page_width = 5_000
        row.page_height = 3_000
        session.commit()

    class FakePage:
        rect = SimpleNamespace(width=5_000, height=3_000)

        def get_pixmap(self, **_: object) -> Any:
            raise AssertionError("oversized raster must be rejected before rendering")

    class FakePdf:
        needs_pass = False
        is_repaired = False
        page_count = 2

        def __enter__(self) -> FakePdf:
            return self

        def __exit__(self, *_: object) -> None:
            return None

        def load_page(self, index: int) -> FakePage:
            assert index == 1
            return FakePage()

    fake_module = SimpleNamespace(
        open=lambda _: FakePdf(),
        Matrix=lambda *_: object(),
    )
    monkeypatch.setattr(ocr_service, "import_module", lambda _: fake_module)
    service, _, _ = build_ready_service(
        ocr_context,
        tmp_path / "oversized-raster-service",
        RecordingBackend([()]),
    )

    with pytest.raises(OcrError) as captured:
        service.recognize_scanned_pages(document_id, cancelled=lambda: False)
    assert captured.value.code == "page_too_large"


@pytest.mark.parametrize("mutation", ("native-evidence", "access"))
def test_ocr_discards_results_when_document_changes_during_recognition(
    ocr_context: OcrContext,
    tmp_path: Path,
    mutation: str,
) -> None:
    from sqlalchemy import select

    from paper_reader.discovery.models import DocumentVersionRecord
    from paper_reader.reader import OcrError, OcrLine
    from paper_reader.reader.models import DocumentPageTextRecord
    from tests.reader.pdf_fixtures import write_mixed_pdf

    pdf = write_mixed_pdf(tmp_path / f"stale-{mutation}.pdf")
    _, document_id = add_mixed_document(
        ocr_context,
        pdf,
        label=f"stale-{mutation}",
    )

    class MutatingBackend(RecordingBackend):
        def recognize(self, image: bytes):  # type: ignore[no-untyped-def]
            with ocr_context.sessions() as session:
                if mutation == "native-evidence":
                    row = session.scalar(
                        select(DocumentPageTextRecord).where(
                            DocumentPageTextRecord.document_version_id == document_id,
                            DocumentPageTextRecord.page_number == 2,
                        )
                    )
                    assert row is not None
                    row.native_text = "changed concurrently"
                else:
                    document = session.get(DocumentVersionRecord, document_id)
                    assert document is not None
                    document.access_status = "restricted"
                session.commit()
            return super().recognize(image)

    backend = MutatingBackend(
        [
            (
                OcrLine(
                    text="must not persist",
                    confidence=0.9,
                    polygon=((0, 0), (300, 0), (300, 40), (0, 40)),
                ),
            )
        ]
    )
    service, _, _ = build_ready_service(ocr_context, tmp_path / mutation, backend)

    with pytest.raises(OcrError) as captured:
        service.recognize_scanned_pages(document_id, cancelled=lambda: False)
    assert captured.value.code == "stale_pages"

    with ocr_context.sessions() as session:
        row = session.get(DocumentPageTextRecord, (document_id, 2))
        assert row is not None
        assert row.ocr_state == "pending"
        assert row.ocr_text is None


@pytest.mark.parametrize("callback", ("cancel", "progress"))
def test_ocr_contains_host_callback_failures_without_persisting_partial_evidence(
    ocr_context: OcrContext,
    tmp_path: Path,
    callback: str,
) -> None:
    from paper_reader.reader import OcrError, OcrLine
    from paper_reader.reader.models import DocumentPageTextRecord
    from tests.reader.pdf_fixtures import write_mixed_pdf

    pdf = write_mixed_pdf(tmp_path / f"callback-{callback}.pdf")
    _, document_id = add_mixed_document(
        ocr_context,
        pdf,
        label=f"callback-{callback}",
    )
    service, _, _ = build_ready_service(
        ocr_context,
        tmp_path / callback,
        RecordingBackend(
            [
                (
                    OcrLine(
                        text="temporary",
                        confidence=0.9,
                        polygon=((0, 0), (300, 0), (300, 40), (0, 40)),
                    ),
                )
            ]
        ),
    )

    def broken_cancel() -> bool:
        raise RuntimeError("cancel callback failed")

    def broken_progress(_: int, __: int) -> None:
        raise RuntimeError("progress callback failed")

    with pytest.raises(OcrError) as captured:
        service.recognize_scanned_pages(
            document_id,
            cancelled=broken_cancel if callback == "cancel" else lambda: False,
            progress=broken_progress if callback == "progress" else lambda _a, _b: None,
        )
    assert captured.value.code == "internal_error"

    with ocr_context.sessions() as session:
        row = session.get(DocumentPageTextRecord, (document_id, 2))
        assert row is not None
        assert row.ocr_state == "pending"
        assert row.ocr_text is None


@pytest.mark.parametrize(
    "line",
    (
        SimpleNamespace(
            text="bad confidence",
            confidence=float("nan"),
            polygon=((0, 0), (10, 0), (10, 10), (0, 10)),
        ),
        SimpleNamespace(
            text="bad points",
            confidence=0.8,
            polygon=((0, 0), (10, 0), (10, 10)),
        ),
        SimpleNamespace(
            text="degenerate",
            confidence=0.8,
            polygon=((1, 1), (1, 1), (1, 1), (1, 1)),
        ),
    ),
)
def test_ocr_rejects_invalid_normalized_page_evidence(
    ocr_context: OcrContext,
    tmp_path: Path,
    line: Any,
) -> None:
    from paper_reader.reader import OcrError
    from tests.reader.pdf_fixtures import write_mixed_pdf

    pdf = write_mixed_pdf(tmp_path / "invalid-page-evidence.pdf")
    _, document_id = add_mixed_document(
        ocr_context,
        pdf,
        label=sha256(repr(line).encode()).hexdigest()[:8],
    )
    service, _, _ = build_ready_service(
        ocr_context,
        tmp_path / sha256(repr(line).encode()).hexdigest()[:8],
        RecordingBackend([(line,)]),
    )

    with pytest.raises(OcrError) as captured:
        service.recognize_scanned_pages(document_id, cancelled=lambda: False)
    assert captured.value.code == "invalid_output"


@pytest.mark.parametrize(
    "symbol_name",
    (
        "OcrManifestError",
        "OcrError",
        "OcrModelDownloadError",
        "OcrModelFile",
        "OcrModelBundle",
        "OcrModelFileStatus",
        "OcrCapability",
        "OcrLine",
        "OcrPageResult",
        "OcrResult",
        "IsolatedOcrPage",
        "IsolatedOcrDocument",
        "OcrBackend",
        "OcrDocumentProcessor",
        "OcrAssetLifecycle",
        "OcrModelInstaller",
        "RapidOcrBackend",
        "IsolatedOcrDocumentProcessor",
        "OcrService",
    ),
)
def test_ocr_public_class_surface_is_characterized(symbol_name: str) -> None:
    """Freeze every OCR class imported outside the pre-split module."""

    import inspect

    import paper_reader.reader as ocr

    assert inspect.isclass(getattr(ocr, symbol_name))


@pytest.mark.parametrize(
    "symbol_name",
    (
        "load_ocr_manifest",
        "inspect_ocr_capability",
        "probe_ocr_runtime",
    ),
)
def test_ocr_public_function_surface_is_characterized(symbol_name: str) -> None:
    """Freeze every OCR function imported outside the pre-split module."""

    import paper_reader.reader as ocr

    symbol = getattr(ocr, symbol_name)
    assert callable(symbol)
    assert symbol.__name__ == symbol_name
