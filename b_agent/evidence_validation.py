from __future__ import annotations

from datetime import date
from typing import Any

from .models import Capability, Evidence, RetrievalStep
from .ontology import THEME_ALIASES
from .parser import _contains_alias
from .relation_results import relation_result_role, relation_result_values
from .retrievers import _matches, _normalize


PRODUCT_LINKED = {
    Capability.IDENTITY_SEARCH,
    Capability.STRUCTURED_SEARCH,
    Capability.KEYWORD_SEARCH,
    Capability.VECTOR_SEARCH,
    Capability.HOLDING_SEARCH,
}


class EvidenceValidator:
    """Rejects malformed adapter output before it can mark a retrieval step successful."""

    def validate(
        self,
        step: RetrievalStep,
        evidence: list[Evidence],
        prior_results: dict[str, Any] | None = None,
    ) -> tuple[list[Evidence], list[str]]:
        valid: list[Evidence] = []
        errors: list[str] = []
        for index, item in enumerate(evidence):
            reason = self._invalid_reason(step, item, prior_results or {})
            if reason:
                errors.append(f"evidence[{index}]: {reason}")
            else:
                valid.append(item)
        return valid, errors

    @staticmethod
    def _invalid_reason(
        step: RetrievalStep, item: Evidence, prior_results: dict[str, Any]
    ) -> str | None:
        if item.capability != step.capability:
            return "capability가 실행 단계와 다릅니다"
        if not item.evidence_id.strip():
            return "evidence_id가 없습니다"
        if not item.source_id.strip():
            return "source_id가 없습니다"
        if not _nonempty(item.source_ref):
            return "원본 source_ref가 없습니다"
        if not item.content.strip() and not item.structured:
            return "근거 내용이 없습니다"
        if item.capability in PRODUCT_LINKED and not item.product_id:
            return "product_id가 없습니다"
        allowed_types = set(step.query.get("product_types") or [])
        if item.product_type and allowed_types and item.product_type.value not in allowed_types:
            return "요청한 상품군과 다른 근거입니다"
        if item.capability == Capability.STRUCTURED_SEARCH and not item.structured:
            return "구조화 검색 근거에 필드 값이 없습니다"
        if item.capability == Capability.IDENTITY_SEARCH:
            requested = {
                _normalize(value)
                for value in [
                    *(step.query.get("identifiers") or []),
                    *(step.query.get("product_mentions") or []),
                ]
            }
            matched = {
                _normalize(value)
                for value in item.structured.get("matched_terms", [])
            }
            if requested and not requested.intersection(matched):
                return "상품 식별 근거가 요청한 이름·코드와 연결되지 않습니다"
        if item.capability == Capability.STRUCTURED_SEARCH:
            for query_filter in step.query.get("filters") or []:
                if not _matches(item.structured, query_filter):
                    return f"구조화 근거가 필터 '{query_filter.get('field')}'를 만족하지 않습니다"
            candidate_sets = []
            for dependency in step.depends_on:
                result = prior_results.get(dependency)
                ids = {
                    evidence_item.product_id
                    for evidence_item in (result.evidence if result else [])
                    if evidence_item.product_id
                }
                if ids:
                    candidate_sets.append(ids)
            if candidate_sets and item.product_id not in set.intersection(*candidate_sets):
                return "구조화 근거가 선행 검색 후보에 포함되지 않습니다"
        if item.capability == Capability.HOLDING_SEARCH:
            target_keys = {"component_entity_id", "holding_name", "holding_id", "entity_id"}
            target_values = [
                item.structured.get(key)
                for key in target_keys
                if _nonempty(item.structured.get(key))
            ]
            if not target_values:
                return "편입 대상 식별자가 없습니다"
            requested_targets = list(step.query.get("holding_targets") or [])
            if step.depends_on:
                for dependency in step.depends_on:
                    result = prior_results.get(dependency)
                    for relation in result.evidence if result else []:
                        for constraint in step.query.get("relation_constraints") or []:
                            requested_targets.extend(
                                relation_result_values(relation.structured, constraint)
                            )
            if requested_targets and not any(
                _normalize(expected) == _normalize(actual)
                or _normalize(expected) in _normalize(actual)
                or _normalize(actual) in _normalize(expected)
                for expected in requested_targets
                for actual in target_values
            ):
                return "편입 대상이 요청 기업 또는 관계 검색 결과와 일치하지 않습니다"
        if item.capability == Capability.RELATION_SEARCH:
            predicate_keys = {"predicate", "relation_type"}
            source_keys = {"source_entity_id", "source_entity_name"}
            target_keys = {"target_entity_id", "target_entity_name"}
            predicate = _first_nonempty(item.structured, predicate_keys)
            source = _first_nonempty(item.structured, source_keys)
            target = _first_nonempty(item.structured, target_keys)
            if not predicate:
                return "관계 predicate가 없습니다"
            if not source or not target:
                return "관계 양끝 엔티티가 없습니다"
            constraints = step.query.get("relation_constraints") or []
            if constraints and not any(
                _relation_matches(item.structured, constraint) for constraint in constraints
            ):
                return "관계 근거가 요청한 predicate·방향·상장 조건을 만족하지 않습니다"
        if item.capability == Capability.DOCUMENT_SEARCH:
            concept_id = _first_nonempty(item.structured, {"concept_id", "entity_id"})
            if not item.product_id and not concept_id:
                return "문서 근거에 product_id 또는 concept_id가 없습니다"
            if step.query.get("product_mentions") and not _mentions_match(
                item, step.query.get("product_mentions") or []
            ):
                return "문서 근거가 요청한 상품·개념명과 연결되지 않습니다"
            sections = set(step.query.get("document_sections") or [])
            actual_section = _first_nonempty(
                item.structured, {"section", "section_type", "document_section"}
            )
            if sections and actual_section not in sections:
                return "문서 근거의 section이 요청 범위와 다릅니다"
            for constraint in step.query.get("document_constraints") or []:
                if constraint.get("value") is not None and not _matches(
                    item.structured, constraint
                ):
                    return f"문서 근거가 조건 '{constraint.get('field')}'를 만족하지 않습니다"
            for dependency in step.depends_on:
                result = prior_results.get(dependency)
                candidate_ids = {
                    evidence_item.product_id
                    for evidence_item in (result.evidence if result else [])
                    if evidence_item.product_id
                }
                if candidate_ids and item.product_id not in candidate_ids:
                    return "문서 근거가 선행 상품 후보와 일치하지 않습니다"
        if item.capability == Capability.KEYWORD_SEARCH:
            text = " ".join(
                [item.content, *(str(value) for value in item.structured.values())]
            ).lower()
            for theme in step.query.get("themes") or []:
                aliases = THEME_ALIASES.get(str(theme), (str(theme),))
                if not any(_contains_alias(text, alias.lower()) for alias in aliases):
                    return f"키워드 근거가 요청 테마 '{theme}'를 포함하지 않습니다"
        if item.capability == Capability.EVENT_SEARCH and not item.as_of_date:
            return "이벤트 날짜가 없습니다"
        if item.capability == Capability.EVENT_SEARCH:
            if step.query.get("requires_product_link") and not item.product_id:
                return "상품 선택용 이벤트 근거에 product_id가 없습니다"
            concept_id = _first_nonempty(item.structured, {"concept_id", "entity_id"})
            if not step.query.get("requires_product_link") and not item.product_id and not concept_id:
                return "이벤트 근거에 product_id 또는 concept_id가 없습니다"
            if step.query.get("product_mentions") and not _mentions_match(
                item, step.query.get("product_mentions") or []
            ):
                return "이벤트 근거가 요청한 상품·개념명과 연결되지 않습니다"
            temporal = step.query.get("temporal") or {}
            try:
                observed = date.fromisoformat(str(item.as_of_date)[:10])
                start = date.fromisoformat(temporal["start_date"]) if temporal.get("start_date") else None
                end = date.fromisoformat(temporal["end_date"]) if temporal.get("end_date") else None
            except (TypeError, ValueError):
                return "이벤트 날짜 형식이 YYYY-MM-DD가 아닙니다"
            if start and observed < start or end and observed > end:
                return "이벤트 날짜가 요청 기간 밖입니다"
            event_text = " ".join(
                [item.content, *(str(value) for value in item.structured.values())]
            ).lower()
            for theme in step.query.get("themes") or []:
                aliases = THEME_ALIASES.get(str(theme), (str(theme),))
                if not any(_contains_alias(event_text, alias.lower()) for alias in aliases):
                    return f"이벤트 근거가 요청 테마 '{theme}'와 연결되지 않습니다"
        return None


def _nonempty(value: Any) -> bool:
    return value is not None and (not isinstance(value, str) or bool(value.strip()))


def _first_nonempty(values: dict, keys: set[str]) -> Any | None:
    for key in sorted(keys):
        if _nonempty(values.get(key)):
            return values[key]
    return None


def _relation_matches(values: dict, constraint: dict) -> bool:
    predicate = _first_nonempty(values, {"predicate", "relation_type"})
    if _normalize(predicate) != _normalize(constraint.get("predicate")):
        return False
    source_values = [
        values.get(key)
        for key in ("source_entity_id", "source_entity_name")
        if _nonempty(values.get(key))
    ]
    target_values = [
        values.get(key)
        for key in ("target_entity_id", "target_entity_name")
        if _nonempty(values.get(key))
    ]
    anchor = constraint.get("anchor")
    role = constraint.get("anchor_role")
    if anchor:
        source_matches = _endpoint_matches_anchor(anchor, source_values)
        target_matches = _endpoint_matches_anchor(anchor, target_values)
        if role == "source" and not source_matches:
            return False
        if role == "target" and not target_matches:
            return False
        if role == "either" and not (source_matches or target_matches):
            return False
    if constraint.get("result_listed") is True:
        result_role = relation_result_role(values, constraint)
        if result_role is None:
            return False
        candidate_keys = (
            {"source_listed", "source_is_listed", "result_listed", "listed"}
            if result_role == "source"
            else {"target_listed", "target_is_listed", "result_listed", "listed"}
        )
        actual = _first_nonempty(values, candidate_keys)
        if _normalize(actual) != "true":
            return False
    return True


def _endpoint_matches_anchor(anchor: object, endpoint_values: list[object]) -> bool:
    normalized_anchor = _normalize(anchor)
    return bool(normalized_anchor) and any(
        normalized_anchor in _normalize(value)
        or _normalize(value) in normalized_anchor
        for value in endpoint_values
        if _normalize(value)
    )


def _mentions_match(item: Evidence, mentions: list[str]) -> bool:
    searchable = _normalize(
        " ".join([item.content, *(str(value) for value in item.structured.values())])
    )
    return any(_normalize(mention) in searchable for mention in mentions)
