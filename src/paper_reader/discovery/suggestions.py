"""Agent-extensible keyword suggestions with a deterministic, offline fallback."""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from paper_reader.discovery.keywords import (
    ConceptGroup,
    KeywordRepository,
    KeywordSetDefinition,
    KeywordVersion,
)


class CorpusKeyword(BaseModel):
    model_config = ConfigDict(frozen=True)

    term: str = Field(min_length=1, max_length=200)
    translation: str | None = Field(default=None, min_length=1, max_length=200)
    weight: float = Field(default=1, ge=0)


@dataclass(frozen=True, slots=True)
class KeywordSuggestionContext:
    research_intent: str
    target_count: int
    parent_definition: KeywordSetDefinition | None
    corpus_terms: tuple[CorpusKeyword, ...]
    preference_terms: tuple[CorpusKeyword, ...]


class KeywordSuggestionBackend(Protocol):
    @property
    def backend_id(self) -> str: ...

    async def suggest(self, context: KeywordSuggestionContext) -> KeywordSetDefinition: ...


class ManualKeywordsRequiredError(ValueError):
    pass


class KeywordSuggestionGenerationError(RuntimeError):
    """A model-backed keyword request failed without a safe local substitute."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        backend_id: str | None = None,
        requested_model_id: str | None = None,
        diagnostic: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.backend_id = backend_id
        self.requested_model_id = requested_model_id
        self.diagnostic = diagnostic


@dataclass(frozen=True, slots=True)
class ControlledConcept:
    key: str
    label: str
    triggers: tuple[str, ...]
    terms: tuple[str, ...]


CONTROLLED_CONCEPTS: tuple[ControlledConcept, ...] = (
    ControlledConcept(
        key="diffusion",
        label="扩散模型",
        triggers=("diffusion", "扩散模型", "扩散生成"),
        terms=(
            "diffusion model",
            "扩散模型",
            "denoising diffusion",
            "去噪扩散",
            "score-based model",
            "基于评分的模型",
            "latent diffusion",
            "潜空间扩散",
        ),
    ),
    ControlledConcept(
        key="agent",
        label="智能体",
        triggers=("agent", "智能体", "智能代理"),
        terms=(
            "AI agent",
            "人工智能智能体",
            "autonomous agent",
            "自主智能体",
            "tool-using agent",
            "工具调用智能体",
            "multi-agent system",
            "多智能体系统",
        ),
    ),
    ControlledConcept(
        key="cancer",
        label="癌症研究",
        triggers=("cancer", "oncology", "tumor", "癌症", "肿瘤"),
        terms=(
            "cancer",
            "癌症",
            "oncology",
            "肿瘤学",
            "tumor",
            "肿瘤",
        ),
    ),
    ControlledConcept(
        key="medical_imaging",
        label="医学影像",
        triggers=("medical imaging", "MRI", "CT", "医学影像", "磁共振"),
        terms=(
            "medical imaging",
            "医学影像",
            "magnetic resonance imaging",
            "磁共振成像",
            "computed tomography",
            "计算机断层扫描",
        ),
    ),
    ControlledConcept(
        key="large_language_model",
        label="大语言模型",
        triggers=("large language model", "LLM", "大语言模型", "大模型"),
        terms=(
            "large language model",
            "大语言模型",
            "foundation model",
            "基础模型",
            "instruction tuning",
            "指令微调",
        ),
    ),
    ControlledConcept(
        key="retrieval_augmented_generation",
        label="检索增强生成",
        triggers=("RAG", "retrieval augmented", "检索增强"),
        terms=(
            "retrieval-augmented generation",
            "检索增强生成",
            "dense retrieval",
            "稠密检索",
            "grounded generation",
            "有据生成",
        ),
    ),
)

RELATED_CONCEPTS: dict[str, tuple[ControlledConcept, ...]] = {
    "diffusion": (
        ControlledConcept(
            key="generative_model",
            label="生成模型",
            triggers=(),
            terms=(
                "generative model",
                "生成模型",
                "image synthesis",
                "图像生成",
                "conditional generation",
                "条件生成",
            ),
        ),
        ControlledConcept(
            key="diffusion_evaluation",
            label="扩散模型评估",
            triggers=(),
            terms=(
                "sample quality",
                "样本质量",
                "generation fidelity",
                "生成保真度",
                "sampling efficiency",
                "采样效率",
            ),
        ),
    ),
    "agent": (
        ControlledConcept(
            key="agent_reasoning",
            label="智能体推理",
            triggers=(),
            terms=(
                "planning and reasoning",
                "规划与推理",
                "tool learning",
                "工具学习",
                "agent memory",
                "智能体记忆",
            ),
        ),
    ),
    "cancer": (
        ControlledConcept(
            key="precision_oncology",
            label="精准肿瘤学",
            triggers=(),
            terms=(
                "precision oncology",
                "精准肿瘤学",
                "cancer diagnosis",
                "癌症诊断",
                "treatment response",
                "治疗反应",
            ),
        ),
    ),
}


def _normalize(value: str) -> str:
    return unicodedata.normalize("NFKC", value).casefold()


def _term_count(definition: KeywordSetDefinition) -> int:
    return sum(
        len(group.terms)
        for section in (definition.required, definition.boost, definition.excluded)
        for group in section
    )


def _is_bilingual(definition: KeywordSetDefinition) -> bool:
    terms = [
        term
        for section in (definition.required, definition.boost, definition.excluded)
        for group in section
        for term in group.terms
    ]
    has_latin = any(any("a" <= char <= "z" for char in _normalize(term)) for term in terms)
    has_cjk = any(any("\u4e00" <= char <= "\u9fff" for char in term) for term in terms)
    return has_latin and has_cjk


def _take_concepts(
    concepts: list[tuple[str, ControlledConcept]],
    target_count: int,
) -> KeywordSetDefinition:
    required: list[ConceptGroup] = []
    boost: list[ConceptGroup] = []
    remaining = target_count
    for section, concept in concepts:
        if remaining < 2:
            break
        take = min(len(concept.terms), remaining)
        if take % 2:
            take -= 1
        if take < 2:
            continue
        group = ConceptGroup(
            key=concept.key,
            label=concept.label,
            terms=concept.terms[:take],
        )
        (required if section == "required" else boost).append(group)
        remaining -= take
    return KeywordSetDefinition(required=tuple(required), boost=tuple(boost))


def _corpus_definition(
    corpus_terms: tuple[CorpusKeyword, ...],
    target_count: int,
) -> KeywordSetDefinition | None:
    terms: list[str] = []
    for candidate in sorted(corpus_terms, key=lambda item: (-item.weight, item.term)):
        if candidate.translation is None:
            continue
        terms.extend((candidate.term.strip(), candidate.translation.strip()))
        if len(terms) >= target_count:
            break
    terms = terms[:target_count]
    if len(terms) < 12:
        return None
    return KeywordSetDefinition(
        required=(
            ConceptGroup(
                key="local_corpus",
                label="本地语料关联词",
                terms=tuple(terms),
            ),
        )
    )


def _ordered_preference_terms(
    preference_terms: tuple[CorpusKeyword, ...],
) -> tuple[str, ...]:
    terms: list[str] = []
    seen: set[str] = set()
    for candidate in sorted(
        preference_terms,
        key=lambda item: (-item.weight, _normalize(item.term)),
    ):
        for raw in (candidate.term, candidate.translation):
            if raw is None:
                continue
            term = " ".join(raw.split())
            identity = _normalize(term)
            if identity and identity not in seen:
                terms.append(term)
                seen.add(identity)
    return tuple(terms)


def local_fallback(
    *,
    research_intent: str,
    target_count: int,
    corpus_terms: tuple[CorpusKeyword, ...],
    preference_terms: tuple[CorpusKeyword, ...],
) -> KeywordSetDefinition:
    normalized_intent = _normalize(research_intent)
    matched = [
        concept
        for concept in CONTROLLED_CONCEPTS
        if any(_normalize(trigger) in normalized_intent for trigger in concept.triggers)
    ]
    if not matched:
        corpus_definition = _corpus_definition(
            (*corpus_terms, *preference_terms),
            target_count,
        )
        if corpus_definition is None:
            raise ManualKeywordsRequiredError(
                "manual keyword configuration is required because no safe "
                "suggestion evidence exists"
            )
        return corpus_definition

    concepts: list[tuple[str, ControlledConcept]] = [("required", item) for item in matched]
    known_keys = {item.key for item in matched}
    for item in matched:
        for related in RELATED_CONCEPTS.get(item.key, ()):
            if related.key not in known_keys:
                concepts.append(("boost", related))
                known_keys.add(related.key)
    preference_candidates = _ordered_preference_terms(preference_terms)
    reserved_count = min(len(preference_candidates), max(0, target_count - 12))
    definition = _take_concepts(concepts, target_count - reserved_count)
    existing_terms = {
        _normalize(term)
        for section in (definition.required, definition.boost, definition.excluded)
        for group in section
        for term in group.terms
    }
    preference_budget = target_count - _term_count(definition)
    selected_preferences = tuple(
        term
        for term in preference_candidates
        if _normalize(term) not in existing_terms
    )[:preference_budget]
    if selected_preferences:
        definition = KeywordSetDefinition(
            required=definition.required,
            boost=(
                *definition.boost,
                ConceptGroup(
                    key="preference_context",
                    label="阅读偏好关联词",
                    terms=selected_preferences,
                ),
            ),
            excluded=definition.excluded,
        )
    if _term_count(definition) < 12:
        raise ManualKeywordsRequiredError(
            "manual keyword configuration is required because fewer than 12 supported terms exist"
        )
    return definition


def merge_definitions(
    parent: KeywordSetDefinition,
    proposal: KeywordSetDefinition,
) -> KeywordSetDefinition:
    sections: dict[str, list[ConceptGroup]] = {
        "required": list(parent.required),
        "boost": list(parent.boost),
        "excluded": list(parent.excluded),
    }
    positions = {
        group.key: (section, index)
        for section, groups in sections.items()
        for index, group in enumerate(groups)
    }
    for section in ("required", "boost", "excluded"):
        for group in getattr(proposal, section):
            existing = positions.get(group.key)
            if existing is None:
                positions[group.key] = (section, len(sections[section]))
                sections[section].append(group)
                continue
            existing_section, index = existing
            current = sections[existing_section][index]
            merged_terms = list(current.terms)
            known = {_normalize(term) for term in merged_terms}
            for term in group.terms:
                if _normalize(term) not in known:
                    merged_terms.append(term)
                    known.add(_normalize(term))
            sections[existing_section][index] = ConceptGroup(
                key=current.key,
                label=current.label,
                terms=tuple(merged_terms),
            )
    return KeywordSetDefinition(
        required=tuple(sections["required"]),
        boost=tuple(sections["boost"]),
        excluded=tuple(sections["excluded"]),
    )


class KeywordSuggestionService:
    def __init__(
        self,
        *,
        repository: KeywordRepository,
        backend: KeywordSuggestionBackend | None = None,
        allow_local_fallback: bool = True,
    ) -> None:
        self._repository = repository
        self._backend = backend
        self._allow_local_fallback = allow_local_fallback

    async def suggest(
        self,
        *,
        research_intent: str,
        target_count: int = 18,
        parent_version_id: str | None = None,
        corpus_terms: tuple[CorpusKeyword, ...] = (),
        preference_terms: tuple[CorpusKeyword, ...] = (),
        preference_context_enabled: bool = False,
        preference_context_sha256: str | None = None,
    ) -> KeywordVersion:
        if not 12 <= target_count <= 24:
            raise ValueError("target_count must be between 12 and 24")
        parent = self._repository.get(parent_version_id) if parent_version_id else None
        context = KeywordSuggestionContext(
            research_intent=research_intent,
            target_count=target_count,
            parent_definition=parent.definition if parent else None,
            corpus_terms=corpus_terms,
            preference_terms=preference_terms,
        )
        definition: KeywordSetDefinition | None = None
        fallback_reason: str | None = None
        if self._backend is not None:
            try:
                candidate = await self._backend.suggest(context)
                if parent is None and not (
                    12 <= _term_count(candidate) <= 24 and _is_bilingual(candidate)
                ):
                    raise ValueError("Agent suggestion must contain 12-24 bilingual terms")
                definition = candidate
            except KeywordSuggestionGenerationError:
                raise
            except Exception as error:
                fallback_reason = type(error).__name__
                if not self._allow_local_fallback:
                    code = (
                        "invalid_output"
                        if isinstance(error, ValueError)
                        else "generation_failed"
                    )
                    message = (
                        str(error)
                        if isinstance(error, ValueError)
                        else "模型没有返回可用的关键词建议"
                    )
                    raise KeywordSuggestionGenerationError(
                        code,
                        message,
                        backend_id=getattr(self._backend, "backend_id", None),
                        diagnostic=fallback_reason,
                    ) from error

        if self._backend is None and not self._allow_local_fallback:
            raise KeywordSuggestionGenerationError(
                "agent_not_configured",
                "关键词生成尚未配置可用的 Agent 后端",
            )

        mode = "agent" if definition is not None else "local_fallback"
        if definition is None:
            definition = local_fallback(
                research_intent=research_intent,
                target_count=24 if parent is not None else target_count,
                corpus_terms=corpus_terms,
                preference_terms=preference_terms,
            )
        if parent is not None:
            definition = merge_definitions(parent.definition, definition)

        backend_id = (
            self._backend.backend_id
            if mode == "agent" and self._backend is not None
            else None
        )
        generator_info: dict[str, object] = {
            "mode": mode,
            "backend_id": backend_id,
            "target_count": target_count,
            "intent_used_for_filtering": False,
            "preference_context": {
                "enabled": preference_context_enabled,
                "sha256": (
                    preference_context_sha256
                    if preference_context_enabled
                    else None
                ),
                "term_count": (
                    len(preference_terms) if preference_context_enabled else 0
                ),
            },
        }
        if fallback_reason is not None:
            generator_info["fallback_reason"] = fallback_reason
        if mode == "agent" and self._backend is not None:
            extra_info = getattr(self._backend, "invocation_info", {})
            if isinstance(extra_info, dict):
                generator_info.update(extra_info)
        return self._repository.create_version(
            research_intent=research_intent,
            definition=definition,
            parent_version_id=parent_version_id,
            generator_info=generator_info,
        )
