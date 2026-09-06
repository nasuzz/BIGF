from __future__ import annotations

import json
import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Protocol

from .models import (
    Capability,
    Evidence,
    Operator,
    ProductType,
    RetrievalBatch,
    RetrievalStep,
    StepOutcome,
    StepResult,
)
from .ontology import canonical_region


CREDIT_ORDER = {
    rating: index
    for index, rating in enumerate(
        [
            "AAA",
            "AA+",
            "AA",
            "AA-",
            "A+",
            "A",
            "A-",
            "BBB+",
            "BBB",
            "BBB-",
            "BB+",
            "BB",
            "BB-",
            "B+",
            "B",
            "B-",
            "CCC",
            "CC",
            "C",
            "D",
        ]
    )
}


@dataclass
class RetrievalContext:
    prior_results: dict[str, StepResult] = field(default_factory=dict)

    def evidence_for(self, step_id: str) -> list[Evidence]:
        result = self.prior_results.get(step_id)
        return result.evidence if result else []


class Retriever(Protocol):
    def retrieve(
        self, step: RetrievalStep, context: RetrievalContext
    ) -> list[Evidence] | RetrievalBatch: ...


class RetrieverRegistry:
    def __init__(self, snapshot_id: str | None = None) -> None:
        self._retrievers: dict[Capability, Retriever] = {}
        self.snapshot_id = snapshot_id

    def register(
        self,
        capability: Capability,
        retriever: Retriever,
        *,
        replace: bool = False,
    ) -> None:
        if capability in self._retrievers and not replace:
            raise ValueError(
                f"retriever already registered for capability: {capability.value}"
            )
        self._retrievers[capability] = retriever

    def get(self, capability: Capability) -> Retriever | None:
        return self._retrievers.get(capability)

    def capabilities(self) -> list[Capability]:
        return list(self._retrievers)


class InMemoryStructuredRetriever:
    """Test/local adapter over canonical records. A's DB adapter should implement the same protocol."""

    def __init__(self, records: list[dict]) -> None:
        self.records = [dict(record) for record in records]

    def retrieve(self, step: RetrievalStep, context: RetrievalContext) -> RetrievalBatch:
        product_types = set(step.query.get("product_types") or [])
        records = [
            record
            for record in self.records
            if not product_types or str(record.get("product_type")) in product_types
        ]
        identifiers = {_normalize(item) for item in step.query.get("identifiers") or []}
        if identifiers:
            records = [
                record
                for record in records
                if identifiers
                & {
                    _normalize(record.get(key))
                    for key in ("product_id", "ticker", "isin", "identifier")
                    if record.get(key) is not None
                }
            ]
        mentions = [_normalize(item) for item in step.query.get("product_mentions") or []]
        if mentions:
            records = [
                record
                for record in records
                if any(
                    mention in _normalize(record.get("name") or "")
                    or mention in _normalize(record.get("short_name") or "")
                    for mention in mentions
                )
            ]
        if step.depends_on:
            candidate_sets = [
                {item.product_id for item in context.evidence_for(dependency) if item.product_id}
                for dependency in step.depends_on
            ]
            candidate_ids = set.intersection(*candidate_sets) if candidate_sets else set()
            if not candidate_ids:
                upstream_complete = all(
                    context.prior_results[dependency].coverage_complete is True
                    for dependency in step.depends_on
                    if dependency in context.prior_results
                )
                return RetrievalBatch(
                    evidence=[],
                    coverage_complete=upstream_complete,
                    coverage_note="선행 검색에서 전달된 상품 후보가 없습니다.",
                    total_hits=0,
                )
            records = [
                record
                for record in records
                if str(record.get("product_id") or record.get("id")) in candidate_ids
            ]
        coverage_by_field: dict[str, dict[str, int]] = {}
        for spec in step.query.get("filters") or []:
            coverage_by_field[spec["field"]] = _field_coverage(records, spec["field"])
            records = [record for record in records if _matches(record, spec)]

        sorts = step.query.get("sorts") or []
        for sort in reversed(sorts):
            coverage_by_field[sort["field"]] = _field_coverage(records, sort["field"])
            present = [item for item in records if not _is_missing(item.get(sort["field"]))]
            missing = [item for item in records if _is_missing(item.get(sort["field"]))]
            present.sort(key=lambda item: str(item.get("product_id") or item.get("id") or ""))
            present.sort(
                key=lambda item: _sort_key(item.get(sort["field"])),
                reverse=sort["direction"] == "desc",
            )
            records = present + missing
        coverage_complete = all(
            counts["missing"] == 0 for counts in coverage_by_field.values()
        )

        limit = min(step.top_k, int(step.query.get("limit") or step.top_k))
        total_hits = len(records)
        evidence: list[Evidence] = []
        for rank, record in enumerate(records[:limit], start=1):
            product_id = str(record.get("product_id") or record.get("id") or f"row-{rank}")
            record_id = str(record.get("record_id") or f"{product_id}:{rank}")
            product_type = _product_type(record.get("product_type"))
            name = str(record.get("name") or product_id)
            evidence.append(
                Evidence(
                    evidence_id=f"structured:{product_type or 'unknown'}:{record_id}",
                    capability=Capability.STRUCTURED_SEARCH,
                    source_id=str(record.get("source_id") or "canonical_products"),
                    content=f"{name}: {json.dumps(record, ensure_ascii=False, default=str)}",
                    score=1.0 - (rank - 1) * 0.001,
                    record_id=record_id,
                    product_id=product_id,
                    product_type=product_type,
                    as_of_date=_optional_str(record.get("as_of_date")),
                    source_ref=_optional_str(record.get("source_ref")),
                    structured=record,
                    provenance=dict(record.get("provenance") or {}),
                )
            )
        incomplete_fields = [
            f"{name} {counts['missing']}/{counts['population']}건 결측"
            for name, counts in coverage_by_field.items()
            if counts["missing"]
        ]
        return RetrievalBatch(
            evidence=evidence,
            coverage_complete=coverage_complete,
            coverage_note=(
                "; ".join(incomplete_fields)
                if incomplete_fields
                else "질문에 사용된 구조화 필드의 모집단 커버리지가 완전합니다."
            ),
            coverage_by_field=coverage_by_field,
            total_hits=total_hits,
            truncated=total_hits > limit,
        )


class InMemoryIdentityRetriever:
    """Local exact/alias resolver. Production uses A's identity index via DataGateway."""

    def __init__(self, records: list[dict], coverage_complete: bool = True) -> None:
        self.records = [dict(record) for record in records]
        self.coverage_complete = coverage_complete

    def retrieve(self, step: RetrievalStep, context: RetrievalContext) -> RetrievalBatch:
        del context
        allowed_types = set(step.query.get("product_types") or [])
        records = [
            record
            for record in self.records
            if not allowed_types or str(record.get("product_type")) in allowed_types
        ]
        identifiers = [_normalize(item) for item in step.query.get("identifiers") or []]
        mentions = [_normalize(item) for item in step.query.get("product_mentions") or []]
        terms = [*identifiers, *mentions]
        matches: dict[str, dict] = {}
        term_match_counts: dict[str, int] = {}
        for term in terms:
            term_matches: list[dict] = []
            for record in records:
                exact_values = {
                    _normalize(value)
                    for key in ("product_id", "ticker", "ric", "isin", "identifier")
                    for value in [record.get(key)]
                    if value is not None
                }
                aliases = {
                    _normalize(value)
                    for value in [
                        record.get("name"),
                        record.get("short_name"),
                        *(record.get("aliases") or []),
                    ]
                    if value is not None
                }
                matched = term in exact_values if term in identifiers else any(
                    term == alias or term in alias for alias in aliases
                )
                if matched:
                    term_matches.append(record)
                    key = str(record.get("product_id") or record.get("id"))
                    matches[key] = record
            term_match_counts[term] = len(term_matches)

        evidence: list[Evidence] = []
        for rank, (key, record) in enumerate(sorted(matches.items()), start=1):
            product_type = _product_type(record.get("product_type"))
            evidence.append(
                Evidence(
                    evidence_id=f"identity:{product_type or 'unknown'}:{key}",
                    capability=Capability.IDENTITY_SEARCH,
                    source_id=str(record.get("source_id") or "canonical_identity"),
                    content=str(record.get("name") or key),
                    score=1.0,
                    record_id=_optional_str(record.get("record_id")),
                    product_id=key,
                    product_type=product_type,
                    as_of_date=_optional_str(record.get("as_of_date")),
                    source_ref=_optional_str(record.get("source_ref")),
                    structured={
                        "name": record.get("name"),
                        "matched_terms": [
                            term
                            for term in terms
                            if _record_matches_identity(record, term, term in identifiers)
                        ],
                    },
                    provenance=dict(record.get("provenance") or {}),
                )
            )
        ambiguous = any(count > 1 for count in term_match_counts.values())
        unresolved = [term for term, count in term_match_counts.items() if count == 0]
        return RetrievalBatch(
            evidence=evidence[: step.top_k],
            coverage_complete=self.coverage_complete,
            coverage_note=(
                f"미해결 식별 표현: {', '.join(unresolved)}" if unresolved else "식별 인덱스를 조회했습니다."
            ),
            total_hits=len(evidence),
            truncated=len(evidence) > step.top_k,
            ambiguous=ambiguous,
        )


@dataclass(frozen=True)
class SearchDocument:
    document_id: str
    text: str
    source_id: str
    product_id: str | None = None
    product_type: ProductType | None = None
    as_of_date: str | None = None
    source_ref: str | None = None
    metadata: dict = field(default_factory=dict)


class BM25KeywordRetriever:
    """Dependency-free BM25 baseline for current strategy/name text."""

    def __init__(self, documents: list[SearchDocument], k1: float = 1.5, b: float = 0.75) -> None:
        self.documents = documents
        self.k1 = k1
        self.b = b
        self.tokens = [_tokenize(document.text) for document in documents]
        self.lengths = [len(tokens) for tokens in self.tokens]
        self.avg_length = sum(self.lengths) / len(self.lengths) if self.lengths else 0.0
        self.document_frequency: Counter[str] = Counter()
        for tokens in self.tokens:
            self.document_frequency.update(set(tokens))

    def retrieve(self, step: RetrievalStep, context: RetrievalContext) -> RetrievalBatch:
        del context
        query_text = " ".join(
            [
                str(step.query.get("question") or ""),
                " ".join(step.query.get("entities") or []),
                " ".join(step.query.get("themes") or []),
            ]
        )
        query_tokens = _tokenize_query(query_text)
        allowed_types = set(step.query.get("product_types") or [])
        scored: list[tuple[float, int]] = []
        for index, (document, tokens) in enumerate(zip(self.documents, self.tokens, strict=True)):
            if allowed_types and document.product_type and str(document.product_type) not in allowed_types:
                continue
            if not _passes_hard_text_constraints(document, step.query):
                continue
            score = self._score(query_tokens, tokens, self.lengths[index])
            if score > 0:
                scored.append((score, index))
        scored.sort(key=lambda item: (-item[0], self.documents[item[1]].document_id))

        result: list[Evidence] = []
        for score, index in scored[: step.top_k]:
            document = self.documents[index]
            result.append(
                Evidence(
                    evidence_id=f"keyword:{document.document_id}",
                    capability=Capability.KEYWORD_SEARCH,
                    source_id=document.source_id,
                    content=_excerpt(document.text, query_tokens),
                    score=score,
                    product_id=document.product_id,
                    product_type=document.product_type,
                    as_of_date=document.as_of_date,
                    source_ref=document.source_ref,
                    structured=dict(document.metadata),
                )
            )
        total_hits = len(scored)
        truncated = total_hits > step.top_k
        downstream_sensitive = bool(step.query.get("filters") or step.query.get("sorts"))
        return RetrievalBatch(
            evidence=result,
            coverage_complete=not (truncated and downstream_sensitive),
            coverage_note=(
                f"키워드 후보 {total_hits}건 중 {step.top_k}건만 전달되어 후속 조건의 완전성을 확정할 수 없습니다."
                if truncated and downstream_sensitive
                else f"키워드 후보 {total_hits}건을 평가했습니다."
            ),
            total_hits=total_hits,
            truncated=truncated,
        )

    def _score(self, query: list[str], document: list[str], length: int) -> float:
        if not document or not query:
            return 0.0
        frequencies = Counter(document)
        total = 0.0
        count = len(self.documents)
        for token in query:
            frequency = frequencies.get(token, 0)
            if not frequency:
                continue
            document_frequency = self.document_frequency.get(token, 0)
            inverse_frequency = math.log(
                1.0 + (count - document_frequency + 0.5) / (document_frequency + 0.5)
            )
            normalizer = frequency + self.k1 * (
                1.0 - self.b + self.b * length / max(self.avg_length, 1.0)
            )
            total += inverse_frequency * frequency * (self.k1 + 1.0) / normalizer
        return total


class StaticRetriever:
    """Small test double for holdings/relations/documents/events adapters."""

    def __init__(
        self,
        evidence_by_capability: dict[Capability, list[Evidence]],
        coverage_complete: bool | None = True,
        coverage_note: str = "",
        total_hits: int | None = None,
        truncated: bool = False,
        ambiguous: bool = False,
    ) -> None:
        self.evidence_by_capability = evidence_by_capability
        self.coverage_complete = coverage_complete
        self.coverage_note = coverage_note
        self.total_hits = total_hits
        self.truncated = truncated
        self.ambiguous = ambiguous

    def retrieve(self, step: RetrievalStep, context: RetrievalContext) -> RetrievalBatch:
        del context
        return RetrievalBatch(
            evidence=list(self.evidence_by_capability.get(step.capability, []))[: step.top_k],
            coverage_complete=self.coverage_complete,
            coverage_note=self.coverage_note,
            total_hits=self.total_hits,
            truncated=self.truncated,
            ambiguous=self.ambiguous,
        )


def _matches(record: dict, spec: dict) -> bool:
    field = spec["field"]
    operator = spec["operator"]
    expected = spec.get("value")
    actual = record.get(field)
    if actual is None:
        return False
    if field == "investment_region":
        actual_region = canonical_region(str(actual))
        expected_values = (
            list(expected)
            if operator == Operator.IN.value and isinstance(expected, (list, tuple, set))
            else [expected]
        )
        expected_regions = {
            resolved
            for value in expected_values
            if (resolved := canonical_region(str(value))) is not None
        }
        if actual_region is not None and expected_regions:
            if operator in {
                Operator.EQ.value,
                Operator.CONTAINS.value,
                Operator.IN.value,
            }:
                return actual_region in expected_regions
            if operator == Operator.NE.value:
                return actual_region not in expected_regions
    if operator == Operator.EQ.value:
        return _normalize(actual) == _normalize(expected)
    if operator == Operator.NE.value:
        return _normalize(actual) != _normalize(expected)
    if operator == Operator.CONTAINS.value:
        return _normalize(expected) in _normalize(actual)
    if operator == Operator.IN.value:
        return _normalize(actual) in {_normalize(item) for item in expected}
    if operator in {Operator.GT.value, Operator.GTE.value, Operator.LT.value, Operator.LTE.value}:
        try:
            left, right = float(actual), float(expected)
        except (TypeError, ValueError):
            return False
        return {
            Operator.GT.value: left > right,
            Operator.GTE.value: left >= right,
            Operator.LT.value: left < right,
            Operator.LTE.value: left <= right,
        }[operator]
    if operator in {Operator.CREDIT_AT_LEAST.value, Operator.CREDIT_AT_MOST.value}:
        actual_rank = CREDIT_ORDER.get(_canonical_credit_rating(actual))
        expected_rank = CREDIT_ORDER.get(_canonical_credit_rating(expected))
        if actual_rank is None or expected_rank is None:
            return False
        if operator == Operator.CREDIT_AT_LEAST.value:
            return actual_rank <= expected_rank
        return actual_rank >= expected_rank
    return False


def _record_matches_identity(record: dict, term: str, identifier: bool) -> bool:
    if identifier:
        values = {
            _normalize(record.get(key))
            for key in ("product_id", "ticker", "ric", "isin", "identifier")
            if record.get(key) is not None
        }
        return term in values
    aliases = {
        _normalize(value)
        for value in [
            record.get("name"),
            record.get("short_name"),
            *(record.get("aliases") or []),
        ]
        if value is not None
    }
    return any(term == alias or term in alias for alias in aliases)


def _is_missing(value) -> bool:
    if value is None:
        return True
    if isinstance(value, float) and math.isnan(value):
        return True
    return isinstance(value, str) and not value.strip()


def _field_coverage(records: list[dict], field_name: str) -> dict[str, int]:
    missing = sum(_is_missing(record.get(field_name)) for record in records)
    return {
        "population": len(records),
        "available": len(records) - missing,
        "missing": missing,
    }


def _normalize(value) -> str:
    text = re.sub(r"\s+", "", str(value)).lower()
    if text in {"1", "y", "yes", "true", "판매중", "가능"}:
        return "true"
    if text in {"0", "n", "no", "false", "판매완료", "불가"}:
        return "false"
    if text.endswith("krw"):
        return "krw"
    return text


def _canonical_credit_rating(value) -> str:
    return str(value).upper().strip().replace("0", "")


def _sort_key(value):
    try:
        return (1, float(value))
    except (TypeError, ValueError):
        return (0, str(value))


def _tokenize(text: str) -> list[str]:
    words = re.findall(r"[가-힣]+|[a-zA-Z]+|\d+(?:\.\d+)?", text.lower())
    tokens: list[str] = []
    for word in words:
        tokens.append(word)
        if re.fullmatch(r"[가-힣]+", word) and len(word) >= 4:
            tokens.extend(word[index : index + 2] for index in range(len(word) - 1))
    return tokens


QUERY_STOPWORDS = {
    "etf",
    "etn",
    "상품",
    "펀드",
    "채권",
    "관련",
    "추천",
    "추천해줘",
    "알려줘",
    "찾아줘",
    "설명",
    "설명해줘",
    "전략",
    "위험요인",
    "구조",
    "최근",
}


def _tokenize_query(text: str) -> list[str]:
    words = re.findall(r"[가-힣]+|[a-zA-Z]+|\d+(?:\.\d+)?", text.lower())
    tokens: list[str] = []
    for word in words:
        if word in QUERY_STOPWORDS:
            continue
        tokens.append(word)
        if re.fullmatch(r"[가-힣]+", word) and len(word) >= 4:
            tokens.extend(word[index : index + 2] for index in range(len(word) - 1))
    return tokens


def _passes_hard_text_constraints(document: SearchDocument, query: dict) -> bool:
    searchable_text = " ".join(
        [document.text, *(str(value) for value in document.metadata.values())]
    ).lower()
    compact_text = _normalize(searchable_text)
    identifiers = [_normalize(item) for item in query.get("identifiers") or []]
    if identifiers and not any(item in compact_text for item in identifiers):
        metadata_values = {_normalize(value) for value in document.metadata.values()}
        if not any(item in metadata_values for item in identifiers):
            return False

    mentions = [_normalize(item) for item in query.get("product_mentions") or []]
    if mentions and not any(item in compact_text for item in mentions):
        return False

    entities = [_normalize(item) for item in query.get("text_entities") or []]
    if entities and not all(item in compact_text for item in entities):
        return False

    themes = query.get("themes") or []
    if themes:
        from .ontology import THEME_ALIASES
        from .parser import _contains_alias

        for theme in themes:
            aliases = THEME_ALIASES.get(str(theme), (str(theme),))
            if not any(_contains_alias(searchable_text, alias.lower()) for alias in aliases):
                return False
    return True


def _excerpt(text: str, query_tokens: list[str], limit: int = 500) -> str:
    lowered = text.lower()
    positions = [lowered.find(token) for token in query_tokens if len(token) > 1 and token in lowered]
    start = max(0, min(positions) - 120) if positions else 0
    snippet = text[start : start + limit].strip()
    return ("…" if start else "") + snippet + ("…" if start + limit < len(text) else "")


def _product_type(value) -> ProductType | None:
    try:
        return ProductType(str(value))
    except (ValueError, TypeError):
        return None


def _optional_str(value) -> str | None:
    return None if value is None else str(value)
