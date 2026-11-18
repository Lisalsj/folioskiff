"""Multilingual FTS5 indexing with deterministic BM25 ranking."""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from typing import Literal

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session, sessionmaker

from paper_reader.discovery.models import WorkRecord

TOKENIZER_VERSION = "multilingual-v1"
TITLE_WEIGHT = 5.0
ABSTRACT_WEIGHT = 1.0
AUTHOR_WEIGHT = 2.0


@dataclass(frozen=True, slots=True)
class LexicalHit:
    work_id: str
    lexical_score: float
    raw_bm25: float
    matched_fields: tuple[str, ...]


def _is_cjk(character: str) -> bool:
    codepoint = ord(character)
    return (
        0x3400 <= codepoint <= 0x4DBF
        or 0x4E00 <= codepoint <= 0x9FFF
        or 0xF900 <= codepoint <= 0xFAFF
        or 0x20000 <= codepoint <= 0x2FA1F
    )


def multilingual_tokens(value: str) -> tuple[str, ...]:
    """Return stable words plus overlapping CJK unigrams and bigrams."""

    normalized = unicodedata.normalize("NFKC", value).casefold()
    tokens: list[str] = []
    latin_run: list[str] = []
    cjk_run: list[str] = []

    def flush_latin() -> None:
        if latin_run:
            tokens.append("".join(latin_run))
            latin_run.clear()

    def flush_cjk() -> None:
        if not cjk_run:
            return
        tokens.extend(cjk_run)
        tokens.extend(
            cjk_run[index] + cjk_run[index + 1]
            for index in range(len(cjk_run) - 1)
        )
        cjk_run.clear()

    for character in normalized:
        if _is_cjk(character):
            flush_latin()
            cjk_run.append(character)
            continue
        flush_cjk()
        if unicodedata.category(character)[0] in {"L", "N"}:
            latin_run.append(character)
        else:
            flush_latin()
    flush_latin()
    flush_cjk()
    return tuple(tokens)


def _index_text(value: str | None) -> str:
    return " ".join(multilingual_tokens(value or ""))


def _match_expression(
    value: str,
    *,
    operator: Literal["AND", "OR"] = "OR",
    prefix: bool = False,
) -> tuple[str, tuple[str, ...]]:
    tokens = tuple(dict.fromkeys(multilingual_tokens(value)))
    terms = (
        f'"{token}"*'
        if prefix and token.isascii() and token.isalnum()
        else f'"{token}"'
        for token in tokens
    )
    return f" {operator} ".join(terms), tokens


def compile_match_expression(
    value: str,
    *,
    operator: Literal["AND", "OR"] = "OR",
    prefix: bool = False,
) -> str:
    """Compile user text into a quoted FTS5 expression without raw operators."""

    expression, _ = _match_expression(value, operator=operator, prefix=prefix)
    return expression


class LexicalIndex:
    """Owns the derived FTS5 index; canonical Work rows remain authoritative."""

    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self._sessions = sessions

    def rebuild(self) -> int:
        with self._sessions() as session:
            works = list(
                session.scalars(
                    select(WorkRecord)
                    .where(WorkRecord.status == "active")
                    .order_by(WorkRecord.id)
                )
            )
            session.execute(text("DELETE FROM work_search"))
            for work in works:
                self._insert(session, work)
            session.commit()
            return len(works)

    def ensure_current(self) -> bool:
        """Repair interrupted derived indexing when the row counts diverge."""

        with self._sessions() as session:
            indexed = int(session.scalar(text("SELECT count(*) FROM work_search")) or 0)
            active = int(
                session.scalar(
                    select(func.count())
                    .select_from(WorkRecord)
                    .where(WorkRecord.status == "active")
                )
                or 0
            )
        if indexed == active:
            return False
        self.rebuild()
        return True

    def sync_work(self, work_id: str) -> None:
        with self._sessions() as session:
            session.execute(
                text("DELETE FROM work_search WHERE work_id = :work_id"),
                {"work_id": work_id},
            )
            work = session.get(WorkRecord, work_id)
            if work is not None and work.status == "active":
                self._insert(session, work)
            session.commit()

    def search(self, query: str, *, limit: int = 50) -> list[LexicalHit]:
        if not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")
        expression, query_tokens = _match_expression(query)
        if not expression:
            return []

        with self._sessions() as session:
            rows = session.execute(
                text(
                    "SELECT work_id, title, abstract, authors, "
                    "bm25(work_search, 0.0, :title_weight, :abstract_weight, "
                    ":author_weight) AS bm25_value "
                    "FROM work_search WHERE work_search MATCH :query "
                    "ORDER BY bm25_value ASC, work_id ASC LIMIT :limit"
                ),
                {
                    "query": expression,
                    "limit": limit,
                    "title_weight": TITLE_WEIGHT,
                    "abstract_weight": ABSTRACT_WEIGHT,
                    "author_weight": AUTHOR_WEIGHT,
                },
            ).mappings().all()

        strengths = [max(0.0, -float(row["bm25_value"])) for row in rows]
        strongest = max(strengths, default=0.0)
        query_set = {token for token in query_tokens if len(token) > 1}
        if not query_set:
            query_set = set(query_tokens)
        hits: list[LexicalHit] = []
        for position, (row, strength) in enumerate(zip(rows, strengths, strict=True), start=1):
            score = (
                (100.0 * strength / strongest)
                if strongest > 0
                else (100.0 / position)
            )
            matched_fields = tuple(
                field
                for field in ("title", "abstract", "authors")
                if query_set.intersection(str(row[field]).split())
            )
            hits.append(
                LexicalHit(
                    work_id=str(row["work_id"]),
                    lexical_score=round(score, 6),
                    raw_bm25=float(row["bm25_value"]),
                    matched_fields=matched_fields,
                )
            )
        return hits

    def identity_candidates(
        self,
        *,
        title: str,
        authors: tuple[str, ...] | list[str],
        limit: int = 256,
    ) -> list[str]:
        """Return a ranked, bounded candidate set for canonical identity matching."""

        if not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")
        title_expression, _ = _match_expression(title, prefix=True)
        author_expression, _ = _match_expression(" ".join(authors), prefix=True)
        clauses: list[str] = []
        if title_expression:
            clauses.append(f"title : ({title_expression})")
        if author_expression:
            clauses.append(f"authors : ({author_expression})")
        if not clauses:
            return []

        with self._sessions() as session:
            return [
                str(work_id)
                for work_id in session.scalars(
                    text(
                        "SELECT work_id FROM work_search "
                        "WHERE work_search MATCH :query "
                        "ORDER BY bm25(work_search, 0.0, :title_weight, 0.0, "
                        ":author_weight) ASC, work_id ASC LIMIT :limit"
                    ),
                    {
                        "query": " OR ".join(clauses),
                        "limit": limit,
                        "title_weight": TITLE_WEIGHT,
                        "author_weight": AUTHOR_WEIGHT,
                    },
                )
            ]

    @staticmethod
    def _insert(session: Session, work: WorkRecord) -> None:
        session.execute(
            text(
                "INSERT INTO work_search(work_id, title, abstract, authors) "
                "VALUES (:work_id, :title, :abstract, :authors)"
            ),
            {
                "work_id": work.id,
                "title": _index_text(work.canonical_title),
                "abstract": _index_text(work.abstract),
                "authors": _index_text(" ".join(work.authors)),
            },
        )
