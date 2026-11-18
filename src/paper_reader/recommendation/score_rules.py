"""Versioned, deterministic user configuration for objective score dimensions."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from types import MappingProxyType

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session, sessionmaker

from paper_reader.discovery.models import (
    ScoreRuleSelectionRecord,
    ScoreRuleVersionRecord,
)
from paper_reader.jobs.models import utc_now
from paper_reader.recommendation.quality import QUALITY_RULE_VERSION, QUALITY_WEIGHTS

DEFAULT_QUALITY_RULE_ID = "quality-default-v1"


@dataclass(frozen=True, slots=True)
class QualityDimensionSpec:
    key: str
    label: str
    description: str


QUALITY_DIMENSION_SPECS: tuple[QualityDimensionSpec, ...] = (
    QualityDimensionSpec(
        "citation_performance",
        "归一化引用表现",
        "按论文年份与研究领域校准，缺失时使用中性先验。",
    ),
    QualityDimensionSpec(
        "venue_rating",
        "发表场所与评级",
        "分别使用 CCF 或用户合法导入的 JCR、CAS、新锐证据。",
    ),
    QualityDimensionSpec(
        "author_impact",
        "作者公开学术影响",
        "只使用来源明确且可核验的公开作者指标。",
    ),
    QualityDimensionSpec(
        "institution_impact",
        "机构与实验室影响",
        "依据规范机构映射和版本化规则，不凭名称猜测。",
    ),
    QualityDimensionSpec(
        "code_data_impact",
        "代码与数据影响",
        "只有论文明确关联的代码、模型或数据产物才能计分。",
    ),
    QualityDimensionSpec(
        "research_transparency",
        "研究透明度",
        "检查代码、数据、注册和可复现实验信息。",
    ),
    QualityDimensionSpec(
        "publication_maturity",
        "发表成熟度",
        "区分预印本、会议、期刊和已核验版本状态。",
    ),
)


@dataclass(frozen=True, slots=True)
class ScoreRuleDimensionInput:
    key: str
    enabled: bool
    weight: float


@dataclass(frozen=True, slots=True)
class ScoreRuleDimension:
    key: str
    label: str
    description: str
    enabled: bool
    weight: float


@dataclass(frozen=True, slots=True)
class ScoreRuleVersion:
    id: str
    rule_version: str
    revision: int
    name: str
    builtin: bool
    active: bool
    dimensions: tuple[ScoreRuleDimension, ...]
    rule_sha256: str
    parent_version_id: str | None
    created_at: datetime | None

    @property
    def weights(self) -> Mapping[str, float]:
        return MappingProxyType(
            {
                dimension.key: dimension.weight if dimension.enabled else 0.0
                for dimension in self.dimensions
            }
        )


def default_quality_dimensions() -> tuple[ScoreRuleDimension, ...]:
    return tuple(
        ScoreRuleDimension(
            key=spec.key,
            label=spec.label,
            description=spec.description,
            enabled=True,
            weight=float(QUALITY_WEIGHTS[spec.key]),
        )
        for spec in QUALITY_DIMENSION_SPECS
    )


def validate_quality_dimensions(
    dimensions: Sequence[ScoreRuleDimensionInput],
) -> tuple[ScoreRuleDimensionInput, ...]:
    expected = tuple(spec.key for spec in QUALITY_DIMENSION_SPECS)
    by_key: dict[str, ScoreRuleDimensionInput] = {}
    for dimension in dimensions:
        key = dimension.key.strip()
        if key not in expected:
            raise ValueError(f"unknown quality dimension: {key}")
        if key in by_key:
            raise ValueError(f"quality dimensions must contain exactly one {key}")
        if not math.isfinite(dimension.weight) or not 0 <= dimension.weight <= 1:
            raise ValueError("dimension weights must be between 0 and 1")
        if not dimension.enabled and dimension.weight != 0:
            raise ValueError("disabled dimensions must have zero weight")
        if dimension.enabled and dimension.weight <= 0:
            raise ValueError("enabled dimensions must have positive weight")
        by_key[key] = ScoreRuleDimensionInput(
            key=key,
            enabled=bool(dimension.enabled),
            weight=float(dimension.weight),
        )
    if set(by_key) != set(expected):
        raise ValueError("quality dimensions must contain exactly the seven known dimensions")
    total = sum(dimension.weight for dimension in by_key.values())
    if not math.isclose(total, 1.0, rel_tol=0, abs_tol=1e-9):
        raise ValueError("enabled dimension weights must sum to 1")
    return tuple(by_key[key] for key in expected)


class ScoreRuleRepository:
    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self._sessions = sessions

    def get_active_quality_rule(self) -> ScoreRuleVersion:
        with self._sessions() as session:
            selection = session.get(ScoreRuleSelectionRecord, "quality")
            if selection is None:
                return self._default_rule()
            record = session.get(ScoreRuleVersionRecord, selection.version_id)
            if record is None:  # pragma: no cover - protected by foreign keys
                raise RuntimeError("Active score rule version is missing")
            return self._domain(record, active=True)

    def quality_weights(self) -> Mapping[str, float]:
        return self.get_active_quality_rule().weights

    def save_quality_rule(
        self,
        *,
        name: str,
        dimensions: Sequence[ScoreRuleDimensionInput],
    ) -> ScoreRuleVersion:
        normalized_name = " ".join(name.split())
        if not normalized_name or len(normalized_name) > 80:
            raise ValueError("score rule name must contain 1-80 characters")
        normalized = validate_quality_dimensions(dimensions)
        serialized_dimensions = [
            {
                "key": dimension.key,
                "enabled": dimension.enabled,
                "weight": dimension.weight,
            }
            for dimension in normalized
        ]
        fingerprint = self._sha256(
            {
                "schema": "quality-rule-v1",
                "name": normalized_name,
                "dimensions": serialized_dimensions,
            }
        )
        now = utc_now()
        with self._sessions() as session:
            record = session.scalar(
                select(ScoreRuleVersionRecord).where(
                    ScoreRuleVersionRecord.rule_kind == "quality",
                    ScoreRuleVersionRecord.rule_sha256 == fingerprint,
                )
            )
            selection = session.get(ScoreRuleSelectionRecord, "quality")
            if record is None:
                next_revision = (
                    session.scalar(
                        select(func.max(ScoreRuleVersionRecord.revision)).where(
                            ScoreRuleVersionRecord.rule_kind == "quality"
                        )
                    )
                    or 0
                ) + 1
                record = ScoreRuleVersionRecord(
                    rule_kind="quality",
                    revision=next_revision,
                    name=normalized_name,
                    dimensions=serialized_dimensions,
                    rule_sha256=fingerprint,
                    parent_version_id=(selection.version_id if selection is not None else None),
                    created_at=now,
                )
                session.add(record)
                session.flush()
            if selection is None:
                selection = ScoreRuleSelectionRecord(
                    rule_kind="quality",
                    version_id=record.id,
                    selected_at=now,
                )
                session.add(selection)
            else:
                selection.version_id = record.id
                selection.selected_at = now
            session.commit()
            return self._domain(record, active=True)

    def reset_quality_rule(self) -> ScoreRuleVersion:
        with self._sessions() as session:
            session.execute(
                delete(ScoreRuleSelectionRecord).where(
                    ScoreRuleSelectionRecord.rule_kind == "quality"
                )
            )
            session.commit()
        return self._default_rule()

    @staticmethod
    def _sha256(payload: dict[str, object]) -> str:
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @staticmethod
    def _default_rule() -> ScoreRuleVersion:
        return ScoreRuleVersion(
            id=DEFAULT_QUALITY_RULE_ID,
            rule_version=QUALITY_RULE_VERSION,
            revision=0,
            name="默认客观质量规则",
            builtin=True,
            active=True,
            dimensions=default_quality_dimensions(),
            rule_sha256=hashlib.sha256(b"quality-default-v1").hexdigest(),
            parent_version_id=None,
            created_at=None,
        )

    @classmethod
    def _domain(
        cls,
        record: ScoreRuleVersionRecord,
        *,
        active: bool,
    ) -> ScoreRuleVersion:
        specs = {spec.key: spec for spec in QUALITY_DIMENSION_SPECS}
        inputs = validate_quality_dimensions(
            tuple(
                ScoreRuleDimensionInput(
                    key=str(item["key"]),
                    enabled=bool(item["enabled"]),
                    weight=float(item["weight"]),
                )
                for item in record.dimensions
            )
        )
        created_at = record.created_at
        if created_at.tzinfo is None or created_at.utcoffset() is None:
            created_at = created_at.replace(tzinfo=UTC)
        else:
            created_at = created_at.astimezone(UTC)
        return ScoreRuleVersion(
            id=record.id,
            rule_version=f"{QUALITY_RULE_VERSION}:{record.rule_sha256[:12]}",
            revision=record.revision,
            name=record.name,
            builtin=False,
            active=active,
            dimensions=tuple(
                ScoreRuleDimension(
                    key=value.key,
                    label=specs[value.key].label,
                    description=specs[value.key].description,
                    enabled=value.enabled,
                    weight=value.weight,
                )
                for value in inputs
            ),
            rule_sha256=record.rule_sha256,
            parent_version_id=record.parent_version_id,
            created_at=created_at,
        )
