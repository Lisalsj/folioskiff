from __future__ import annotations

import base64
import json
from collections.abc import Awaitable, Callable
from hashlib import sha256
from pathlib import Path

import fitz  # type: ignore[import-untyped]
import httpx
import pytest

from paper_reader.agents.codex_cli import CodexCliBackend
from paper_reader.agents.contracts import (
    AgentBackendError,
    AgentErrorCode,
    AgentFragment,
    AgentRequest,
    AgentTaskKind,
    EvidenceAttachment,
    ReasoningEffort,
    ServiceTier,
)
from paper_reader.agents.evidence import (
    EvidenceBundle,
    EvidenceCoverage,
    EvidenceDocument,
    EvidenceLevel,
    EvidencePage,
    EvidenceWork,
)
from paper_reader.agents.ollama import OllamaBackend
from paper_reader.agents.process_policy import SecureProcessPolicy, SecureProcessResult
from paper_reader.agents.vision import (
    VisualEvidenceError,
    VisualEvidencePolicy,
    render_visual_evidence,
    select_visual_pages,
)


@pytest.mark.parametrize(
    "reference",
    (
        "See Figure 2 for the architecture.",
        "See Table 3 for the complete comparison.",
        "See Equation (4) for the objective.",
        "结果见公式 5。",
    ),
)
def test_references_select_only_the_target_and_bounded_adjacent_context(
    reference: str,
) -> None:
    bundle = _bundle(
        "a" * 64,
        tuple(
            reference if page == 4 else f"Unrelated prose on page {page}." for page in range(1, 9)
        ),
    )

    selected = select_visual_pages(
        bundle,
        supports_vision=True,
        text_extraction_sufficient=False,
    )

    assert [item.page_number for item in selected] == [3, 4, 5]
    assert [item.primary for item in selected] == [False, True, False]
    assert {item.page_number for item in selected}.isdisjoint({1, 2, 6, 7, 8})


def test_no_visual_plan_when_text_is_sufficient_model_lacks_vision_or_policy_disables() -> None:
    bundle = _bundle("b" * 64, ("See Figure 1 for the result.",))

    assert (
        select_visual_pages(
            bundle,
            supports_vision=True,
            text_extraction_sufficient=True,
        )
        == ()
    )

    complete_table_text = (
        "Table 1 contains complete extracted rows and values. "
        + "method accuracy latency memory; baseline 0.81 12 4; proposed 0.92 10 3. " * 8
    )
    complete_bundle = _bundle("c" * 64, (complete_table_text,))
    assert select_visual_pages(complete_bundle, supports_vision=True) == ()
    assert (
        select_visual_pages(
            bundle,
            supports_vision=False,
            text_extraction_sufficient=False,
        )
        == ()
    )
    assert (
        select_visual_pages(
            bundle,
            supports_vision=True,
            text_extraction_sufficient=False,
            policy=VisualEvidencePolicy(enabled=False),
        )
        == ()
    )


def test_rendering_is_hash_bound_deterministic_bounded_and_in_memory(tmp_path: Path) -> None:
    pdf_path = tmp_path / "paper.pdf"
    _write_visual_pdf(pdf_path, page_count=8)
    document_hash = sha256(pdf_path.read_bytes()).hexdigest()
    bundle = _bundle(
        document_hash,
        tuple(f"See Figure {page} for the visual result." for page in range(1, 9)),
    )
    policy = VisualEvidencePolicy(
        adjacent_pages=0,
        max_pages=6,
        max_dimension_pixels=512,
        max_total_pixels=6 * 512 * 512,
        max_page_bytes=512 * 1024,
        max_total_bytes=3 * 1024 * 1024,
    )

    first = render_visual_evidence(
        pdf_path,
        bundle,
        supports_vision=True,
        policy=policy,
    )
    repeated = render_visual_evidence(
        pdf_path,
        bundle,
        supports_vision=True,
        policy=policy,
    )

    assert len(first.pages) == 6
    assert [item.page_number for item in first.pages] == [1, 2, 3, 4, 5, 6]
    assert first == repeated
    assert first.document_content_sha256 == document_hash
    assert first.total_pixels <= policy.max_total_pixels
    assert first.total_bytes <= policy.max_total_bytes
    assert list(tmp_path.iterdir()) == [pdf_path]
    for item in first.pages:
        assert item.document_content_sha256 == document_hash
        assert item.image_sha256 == sha256(item.attachment.content).hexdigest()
        assert item.attachment.name == f"visual-{document_hash[:16]}-p{item.page_number:04d}.png"
        assert item.attachment.media_type == "image/png"
        assert item.attachment.content.startswith(b"\x89PNG\r\n\x1a\n")
        assert max(item.width_pixels, item.height_pixels) <= policy.max_dimension_pixels
        assert item.width_pixels * item.height_pixels <= policy.max_total_pixels
        assert len(item.attachment.content) <= policy.max_page_bytes

    corrupt_path = tmp_path / "corrupt.pdf"
    corrupt_path.write_bytes(pdf_path.read_bytes() + b"corruption")
    with pytest.raises(VisualEvidenceError, match="hash"):
        render_visual_evidence(
            corrupt_path,
            bundle,
            supports_vision=True,
            policy=policy,
        )


def test_rendering_honours_cancellation_without_leaving_artifacts(tmp_path: Path) -> None:
    pdf_path = tmp_path / "paper.pdf"
    _write_visual_pdf(pdf_path, page_count=2)
    bundle = _bundle(
        sha256(pdf_path.read_bytes()).hexdigest(),
        ("See Figure 1.", "See Table 2."),
    )

    with pytest.raises(AgentBackendError) as captured:
        render_visual_evidence(
            pdf_path,
            bundle,
            supports_vision=True,
            text_extraction_sufficient=False,
            cancelled=lambda: True,
        )

    assert captured.value.code is AgentErrorCode.CANCELLED
    assert list(tmp_path.iterdir()) == [pdf_path]


@pytest.mark.asyncio
async def test_codex_uses_image_argument_only_for_visual_attachments(tmp_path: Path) -> None:
    calls: list[SecureProcessPolicy] = []

    async def runner(
        policy: SecureProcessPolicy,
        *,
        stdin: str,
        cancelled: Callable[[], bool],
        on_stdout_line: Callable[[str], Awaitable[None]] | None = None,
    ) -> SecureProcessResult:
        calls.append(policy)
        if "mcp" in policy.arguments:
            return SecureProcessResult(exit_code=0, stdout="[]", stderr="")
        output = Path(policy.arguments[policy.arguments.index("-o") + 1])
        output.write_text('{"summary":"ok"}', encoding="utf-8")
        if on_stdout_line is not None:
            await on_stdout_line('{"type":"turn.completed","usage":{}}')
        return SecureProcessResult(exit_code=0, stdout="", stderr="")

    executable = tmp_path / "codex.exe"
    executable.write_bytes(b"fixture")
    backend = CodexCliBackend(
        cache_root=tmp_path / "cache",
        executable=executable,
        process_runner=runner,
        environment={"PATH": str(tmp_path)},
    )
    image = EvidenceAttachment(
        "visual-a-p0001.png",
        "image/png",
        b"\x89PNG\r\n\x1a\nfixture",
    )
    data = EvidenceAttachment("evidence.json", "application/json", b"{}")

    await backend.generate(
        _agent_request(attachments=(data, image)),
        on_fragment=_append([]),
        cancelled=lambda: False,
    )

    arguments = calls[-1].arguments
    assert arguments.count("--image") == 1
    image_path = Path(arguments[arguments.index("--image") + 1])
    assert image_path.name == image.name
    assert "evidence.json" not in [
        Path(arguments[index + 1]).name
        for index, value in enumerate(arguments[:-1])
        if value == "--image"
    ]
    assert not calls[-1].working_directory.exists()


@pytest.mark.asyncio
async def test_ollama_base64_images_exist_only_on_the_relevant_user_message() -> None:
    payloads: list[dict[str, object]] = []
    metadata_payloads: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/show":
            metadata_payloads.append(json.loads(request.content))
            return httpx.Response(200, json={"capabilities": ["completion", "vision"]})
        assert request.url == httpx.URL("http://127.0.0.1:11434/api/chat")
        payloads.append(json.loads(request.content))
        return _stream_response('{"summary":"ok"}')

    image_bytes = b"\x89PNG\r\n\x1a\nbounded-image-bytes"
    request = _agent_request(
        attachments=(
            EvidenceAttachment("evidence.json", "application/json", b"{}"),
            EvidenceAttachment("visual-a-p0001.png", "image/png", image_bytes),
        )
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        backend = OllamaBackend.local(client=client)
        await backend.generate(
            request,
            on_fragment=_append([]),
            cancelled=lambda: False,
        )

    assert metadata_payloads == [{"model": request.model_id}]
    assert len(payloads) == 1
    messages = payloads[0]["messages"]
    assert isinstance(messages, list)
    assert messages == [
        {
            "role": "user",
            "content": request.prompt,
            "images": [base64.b64encode(image_bytes).decode("ascii")],
        }
    ]
    assert "images" not in {key for key in payloads[0] if key != "messages"}


@pytest.mark.asyncio
async def test_ollama_model_discovery_preserves_explicit_vision_capability() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "models": [
                    {
                        "name": "vision-model",
                        "capabilities": ["completion", "vision"],
                    },
                    {
                        "name": "text-model",
                        "capabilities": ["completion"],
                    },
                    {
                        "name": "clip-family-model",
                        "details": {"families": ["mllama", "clip"]},
                    },
                ]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        models = (await OllamaBackend.local(client=client).discover_models()).models

    assert {item.model_id: item.supports_vision for item in models} == {
        "clip-family-model": True,
        "text-model": False,
        "vision-model": True,
    }


def _bundle(document_hash: str, texts: tuple[str, ...]) -> EvidenceBundle:
    pages = tuple(
        EvidencePage(
            evidence_id=f"page:{number}:native",
            page_number=number,
            source="native",
            text=text,
            source_text_sha256=sha256(text.encode()).hexdigest(),
            page_width=420,
            page_height=595,
            confidence=None,
            truncated=False,
        )
        for number, text in enumerate(texts, start=1)
    )
    total = sum(len(text) for text in texts)
    return EvidenceBundle.create(
        work=EvidenceWork(
            work_id="work-visual",
            title="Visual evidence fixture",
            authors=("Researcher",),
            abstract="Fixture abstract.",
            doi=None,
            published_at=None,
        ),
        document=EvidenceDocument(
            document_version_id="document-visual",
            content_sha256=document_hash,
            access_status="user_provided",
            media_type="application/pdf",
            completeness=100,
            extraction_status="complete",
        ),
        evidence_level=EvidenceLevel.FULL_TEXT,
        pages=pages,
        annotations=(),
        coverage=EvidenceCoverage(
            total_pages=len(pages),
            available_pages=len(pages),
            included_pages=len(pages),
            total_characters=total,
            included_characters=total,
            percentage=100,
            truncated=False,
        ),
    )


def _write_visual_pdf(path: Path, *, page_count: int) -> None:
    document = fitz.open()
    try:
        for number in range(1, page_count + 1):
            page = document.new_page(width=420, height=595)
            page.insert_text((36, 48), f"Figure {number}")
            page.draw_rect(fitz.Rect(36, 72, 384, 300), color=(0, 0.3, 0), width=2)
            page.draw_line((50, 280), (350, 100), color=(0.5, 0, 0), width=3)
        document.save(path)
    finally:
        document.close()


def _agent_request(
    *,
    attachments: tuple[EvidenceAttachment, ...],
) -> AgentRequest:
    return AgentRequest(
        task_id="visual-job",
        task_kind=AgentTaskKind.REPORT,
        prompt="Analyze the explicitly supplied visual evidence.",
        output_schema={
            "type": "object",
            "properties": {"summary": {"type": "string"}},
            "required": ["summary"],
            "additionalProperties": False,
        },
        model_id="vision-model",
        reasoning_effort=ReasoningEffort.HIGH,
        service_tier=ServiceTier.FLEX,
        attachments=attachments,
        timeout_seconds=120,
    )


def _stream_response(content: str) -> httpx.Response:
    lines = (
        json.dumps({"message": {"role": "assistant", "content": content}, "done": False}),
        json.dumps(
            {
                "message": {"role": "assistant", "content": ""},
                "done": True,
                "prompt_eval_count": 1,
                "eval_count": 1,
            }
        ),
    )
    return httpx.Response(200, content=("\n".join(lines) + "\n").encode())


def _append(target: list[AgentFragment]) -> Callable[[AgentFragment], Awaitable[None]]:
    async def append(value: AgentFragment) -> None:
        target.append(value)

    return append
