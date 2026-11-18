from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest

from tests.reader.pdf_fixtures import write_text_pdf


def _fixture_command(mode: str | None = None) -> tuple[str, ...]:
    fixture = Path(__file__).parent / "fixtures" / "native_protocol_fixture.py"
    command = (sys.executable, str(fixture))
    return command if mode is None else (*command, mode)


def test_native_runner_strips_credentials_and_accepts_only_bounded_result_file(
    tmp_path: Path,
) -> None:
    from paper_reader.reader.native_process import NativeProcessRunner

    source = dict(os.environ)
    source["OPENAI_API_KEY"] = "must-not-reach-native-worker"
    source["PATH"] = r"C:\attacker-bin"
    progress: list[tuple[int, int]] = []
    runner = NativeProcessRunner(
        work_root=tmp_path / "native-jobs",
        command=_fixture_command(),
        environment_source=source,
    )

    result = runner.run(
        operation="probe",
        payload={},
        timeout_seconds=5,
        cancelled=lambda: False,
        progress=lambda completed, total: progress.append((completed, total)),
    )

    assert result == {
        "ok": True,
        "result": {
            "openai_key_present": False,
            "path_contains_attacker": False,
            "operation": "probe",
        },
    }
    assert progress == []
    assert list((tmp_path / "native-jobs").iterdir()) == []


@pytest.mark.parametrize(
    ("timeout_seconds", "cancel_after", "expected_code"),
    ((0.15, None, "time_limit"), (5.0, 0.15, "cancelled")),
)
def test_native_runner_hard_kills_timeout_and_cancelled_workers(
    tmp_path: Path,
    timeout_seconds: float,
    cancel_after: float | None,
    expected_code: str,
) -> None:
    from paper_reader.reader.native_process import NativeProcessError, NativeProcessRunner

    runner = NativeProcessRunner(
        work_root=tmp_path / expected_code,
        command=_fixture_command(),
    )
    started = time.monotonic()

    with pytest.raises(NativeProcessError) as captured:
        runner.run(
            operation="hang",
            payload={},
            timeout_seconds=timeout_seconds,
            cancelled=lambda: (
                cancel_after is not None and time.monotonic() - started >= cancel_after
            ),
        )

    assert captured.value.code == expected_code
    assert time.monotonic() - started < 2
    assert list((tmp_path / expected_code).iterdir()) == []


@pytest.mark.parametrize(
    ("mode", "payload", "cancel_after", "timeout_seconds", "expected_code"),
    (
        ("never-read", {"padding": "x" * 900_000}, None, 0.15, "time_limit"),
        ("never-read", {"padding": "x" * 900_000}, 0.15, 5.0, "cancelled"),
        ("result-then-hang", {}, None, 0.15, "time_limit"),
        ("result-then-hang", {}, 0.15, 5.0, "cancelled"),
    ),
)
def test_native_deadline_and_cancel_cover_request_write_and_post_receipt_exit(
    tmp_path: Path,
    mode: str,
    payload: dict[str, str],
    cancel_after: float | None,
    timeout_seconds: float,
    expected_code: str,
) -> None:
    from paper_reader.reader.native_process import NativeProcessError, NativeProcessRunner

    runner = NativeProcessRunner(
        work_root=tmp_path / f"{mode}-{expected_code}",
        command=_fixture_command(mode),
    )
    started = time.monotonic()

    with pytest.raises(NativeProcessError) as captured:
        runner.run(
            operation="probe",
            payload=payload,
            timeout_seconds=timeout_seconds,
            cancelled=lambda: (
                cancel_after is not None and time.monotonic() - started >= cancel_after
            ),
        )

    assert captured.value.code == expected_code
    assert time.monotonic() - started < 2
    assert list((tmp_path / f"{mode}-{expected_code}").iterdir()) == []


def test_pdf_extraction_runs_through_the_isolated_worker_protocol(tmp_path: Path) -> None:
    from paper_reader.reader.extraction import IsolatedPdfTextExtractor

    document = write_text_pdf(tmp_path / "isolated.pdf", ("Isolated native evidence",))
    extractor = IsolatedPdfTextExtractor(work_root=tmp_path / "native-jobs", max_seconds=10)
    progress: list[tuple[int, int]] = []

    result = extractor.extract(
        document,
        cancelled=lambda: False,
        progress=lambda completed, total: progress.append((completed, total)),
    )

    assert result.page_count == 1
    assert result.pages[0].text == "Isolated native evidence\n"
    assert result.extractor_version.startswith("pymupdf-")
    assert progress == [(1, 1)]


def test_native_status_stream_has_a_cumulative_event_budget(tmp_path: Path) -> None:
    from paper_reader.reader.native_process import NativeProcessError, NativeProcessRunner

    runner = NativeProcessRunner(
        work_root=tmp_path / "status-flood",
        command=_fixture_command("status-flood"),
    )

    with pytest.raises(NativeProcessError) as captured:
        runner.run(
            operation="probe",
            payload={},
            timeout_seconds=5,
            cancelled=lambda: False,
        )

    assert captured.value.code == "protocol_error"
    assert list((tmp_path / "status-flood").iterdir()) == []


@pytest.mark.skipif(os.name != "nt", reason="Windows Job Object contract")
def test_native_worker_job_object_rejects_descendant_processes(tmp_path: Path) -> None:
    from paper_reader.reader.native_process import NativeProcessRunner

    runner = NativeProcessRunner(
        work_root=tmp_path / "job-object",
        command=_fixture_command("spawn-child"),
    )

    result = runner.run(
        operation="probe",
        payload={},
        timeout_seconds=5,
        cancelled=lambda: False,
    )

    assert result["result"]["child_started"] is False
    assert list((tmp_path / "job-object").iterdir()) == []


@pytest.mark.skipif(os.name != "nt", reason="Windows Job Object contract")
def test_native_worker_job_object_enforces_process_memory_limit(tmp_path: Path) -> None:
    from paper_reader.reader.native_process import NativeProcessError, NativeProcessRunner

    root = tmp_path / "memory-job"
    runner = NativeProcessRunner(
        work_root=root,
        command=_fixture_command("memory-pressure"),
        memory_limit_bytes=128 * 1024 * 1024,
    )

    try:
        result = runner.run(
            operation="probe",
            payload={},
            timeout_seconds=5,
            cancelled=lambda: False,
        )
    except NativeProcessError as error:
        assert error.code == "worker_failed"
    else:
        assert result["result"]["memory_limited"] is True
    assert list(root.iterdir()) == []


def test_hidden_frozen_worker_command_is_not_a_server_command() -> None:
    from paper_reader.cli import build_parser

    args = build_parser().parse_args(["native-worker"])

    assert args.command == "native-worker"


def test_frozen_native_worker_job_allows_no_launcher_or_descendant(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import paper_reader.reader.native_process as native_process

    monkeypatch.setattr(native_process.sys, "frozen", True, raising=False)
    runner = native_process.NativeProcessRunner(work_root=tmp_path / "frozen-job")

    assert runner._active_process_limit == 1


def test_ocr_document_processor_validates_isolated_page_output(tmp_path: Path) -> None:
    from paper_reader.reader.ocr_contracts import _PageSnapshot
    from paper_reader.reader.ocr_runtime import IsolatedOcrDocumentProcessor

    class FixtureRunner:
        def run(self, **kwargs):  # type: ignore[no-untyped-def]
            assert kwargs["operation"] == "ocr_document"
            kwargs["progress"](1, 1)
            return {
                "ok": True,
                "result": {
                    "engine_version": "rapidocr-fixture",
                    "pages": [
                        {
                            "page_number": 1,
                            "rendered_width": 840,
                            "rendered_height": 1190,
                            "lines": [
                                {
                                    "text": "isolated OCR",
                                    "confidence": 0.9,
                                    "polygon": [
                                        [0, 0],
                                        [840, 0],
                                        [840, 100],
                                        [0, 100],
                                    ],
                                }
                            ],
                        }
                    ],
                },
            }

    model_paths = {
        "det": tmp_path / "det.onnx",
        "cls": tmp_path / "cls.onnx",
        "rec": tmp_path / "rec.onnx",
    }
    for path in model_paths.values():
        path.write_bytes(b"fixture")
    processor = IsolatedOcrDocumentProcessor(
        model_paths=model_paths,
        work_root=tmp_path / "ocr-jobs",
        runner=FixtureRunner(),  # type: ignore[arg-type]
    )
    progress: list[tuple[int, int]] = []

    result = processor.process(
        document=tmp_path / "paper.pdf",
        pages=(
            _PageSnapshot(
                page_number=1,
                page_width=420,
                page_height=595,
                native_evidence_hash="a" * 64,
            ),
        ),
        max_render_pixels=25_000_000,
        max_image_bytes=64 * 1024 * 1024,
        max_lines_per_page=10_000,
        max_lines_total=200_000,
        max_characters_per_page=1_000_000,
        max_characters_total=10_000_000,
        timeout_seconds=10,
        cancelled=lambda: False,
        progress=lambda completed, total: progress.append((completed, total)),
    )

    assert result.engine_version == "rapidocr-fixture"
    assert result.pages[0].lines[0].text == "isolated OCR"
    assert progress == [(1, 1)]


def test_isolated_ocr_result_rejects_document_line_amplification() -> None:
    from paper_reader.reader.ocr_contracts import OcrError, _PageSnapshot
    from paper_reader.reader.ocr_runtime import _isolated_ocr_document

    line = {
        "text": "x",
        "confidence": 0.9,
        "polygon": [[0, 0], [100, 0], [100, 20], [0, 20]],
    }
    envelope = {
        "ok": True,
        "result": {
            "engine_version": "rapidocr-fixture",
            "pages": [
                {
                    "page_number": 1,
                    "rendered_width": 840,
                    "rendered_height": 1190,
                    "lines": [line, line],
                }
            ],
        },
    }

    with pytest.raises(OcrError) as captured:
        _isolated_ocr_document(
            envelope,
            pages=(
                _PageSnapshot(
                    page_number=1,
                    page_width=420,
                    page_height=595,
                    native_evidence_hash="a" * 64,
                ),
            ),
            max_render_pixels=25_000_000,
            max_lines_per_page=2,
            max_lines_total=1,
            max_characters_per_page=1_000_000,
            max_characters_total=10_000_000,
        )

    assert captured.value.code == "invalid_output"


@pytest.mark.asyncio
async def test_application_wires_both_pdf_and_ocr_native_calls_to_isolated_workers(
    tmp_path: Path,
) -> None:
    from paper_reader.app import create_app
    from paper_reader.reader import IsolatedOcrDocumentProcessor
    from paper_reader.reader.extraction import IsolatedPdfTextExtractor
    from tests.foundation.test_api import make_paths

    app = create_app(paths=make_paths(tmp_path), bootstrap_token="native-boundary")

    async with app.router.lifespan_context(app):
        extraction = app.state.document_extraction_service
        ocr = app.state.ocr_service
        assert isinstance(extraction._extractor, IsolatedPdfTextExtractor)
        assert isinstance(ocr._document_processor, IsolatedOcrDocumentProcessor)
