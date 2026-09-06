from __future__ import annotations

import calendar
from datetime import date

from .models import (
    Capability,
    Intent,
    ProductType,
    QueryUnderstanding,
    RetrievalPlan,
    RetrievalStep,
    to_primitive,
)
from .validation import PlanValidator


class RetrievalPlanner:
    VERSION = "2026-09-06.1"

    def __init__(
        self,
        validator: PlanValidator | None = None,
        snapshot_date: str | date | None = None,
    ) -> None:
        self.validator = validator or PlanValidator()
        self.snapshot_date = _coerce_date(snapshot_date)

    def build(self, understanding: QueryUnderstanding) -> RetrievalPlan:
        steps: list[RetrievalStep] = []
        relation_step: str | None = None
        holding_step: str | None = None
        structured_dependencies: list[str] = []

        explanation_intents = {
            Intent.STRATEGY,
            Intent.RISK,
            Intent.STRUCTURE,
            Intent.RECENT_EVENT,
            Intent.RESEARCH,
        }
        selection_intents = {
            Intent.FILTER,
            Intent.RECOMMEND,
            Intent.COMPARE,
            Intent.HOLDINGS,
            Intent.RELATION,
        }
        intent_set = set(understanding.intents)
        explicit_explanation = bool(
            intent_set & {Intent.STRATEGY, Intent.RISK, Intent.STRUCTURE, Intent.RESEARCH}
        ) or (Intent.RECENT_EVENT in intent_set and bool(understanding.product_mentions))
        pure_explanation = explicit_explanation and not bool(
            intent_set & selection_intents
        ) and not understanding.filters and not understanding.sorts
        selection_required = not pure_explanation

        has_identity = bool(understanding.product_mentions or understanding.identifiers)
        if has_identity:
            steps.append(
                RetrievalStep(
                    step_id="identity_search",
                    capability=Capability.IDENTITY_SEARCH,
                    query=self._query_for(Capability.IDENTITY_SEARCH, understanding),
                    required=selection_required,
                    top_k=max(understanding.limit * 2, 20),
                    rationale="상품명·티커·ISIN·별칭을 먼저 영속 상품 ID로 해석합니다.",
                )
            )
            if selection_required:
                structured_dependencies.append("identity_search")

        needs_text = bool(
            set(understanding.intents)
            & {
                Intent.STRATEGY,
                Intent.RISK,
                Intent.STRUCTURE,
                Intent.RECENT_EVENT,
                Intent.RESEARCH,
            }
        ) or bool(understanding.themes) or (
            Intent.RECOMMEND in understanding.intents and not understanding.filters
        )

        if Intent.RELATION in understanding.intents:
            relation_step = "relation_search"
            steps.append(
                RetrievalStep(
                    step_id=relation_step,
                    capability=Capability.RELATION_SEARCH,
                    query=self._query_for(Capability.RELATION_SEARCH, understanding),
                    required=True,
                    top_k=50,
                    rationale="명시된 모회사·자회사·계열 관계를 먼저 확정합니다.",
                )
            )

        if Intent.HOLDINGS in understanding.intents:
            holding_step = "holding_search"
            holding_dependencies = []
            if has_identity:
                holding_dependencies.append("identity_search")
            if relation_step:
                holding_dependencies.append(relation_step)
            steps.append(
                RetrievalStep(
                    step_id=holding_step,
                    capability=Capability.HOLDING_SEARCH,
                    query=self._query_for(Capability.HOLDING_SEARCH, understanding),
                    required=True,
                    depends_on=holding_dependencies,
                    top_k=100,
                    rationale="ETF 편입 여부는 상품명이나 전략문으로 추정하지 않고 편입 데이터로 검증합니다.",
                )
            )
            structured_dependencies.append(holding_step)

        if Intent.RECENT_EVENT in understanding.intents:
            steps.append(
                RetrievalStep(
                    step_id="event_search",
                    capability=Capability.EVENT_SEARCH,
                    query=self._query_for(
                        Capability.EVENT_SEARCH,
                        understanding,
                        requires_product_link=selection_required,
                    ),
                    required=True,
                    top_k=50,
                    rationale="기간 조건이 있는 주제·동향은 날짜가 있는 이벤트 근거가 필요합니다.",
                )
            )
        if needs_text:
            region_vector_candidates = (
                selection_required
                and set(understanding.product_types) == {ProductType.FOREIGN_ETF}
                and (
                    Intent.STRATEGY in intent_set
                    or bool(understanding.themes)
                )
                and any(
                    item.field == "investment_region"
                    for item in understanding.filters
                )
            )
            keyword_required = (
                selection_required
                and not region_vector_candidates
                and (holding_step is None or bool(understanding.themes))
            )
            steps.append(
                RetrievalStep(
                    step_id="keyword_search",
                    capability=Capability.KEYWORD_SEARCH,
                    query=self._query_for(Capability.KEYWORD_SEARCH, understanding),
                    required=keyword_required,
                    top_k=max(understanding.limit * 4, 30),
                    rationale="상품명·기초지수·기존 전략문을 BM25 계열 키워드 검색으로 찾습니다.",
                )
            )
            if keyword_required:
                structured_dependencies.append("keyword_search")
            steps.append(
                RetrievalStep(
                    step_id="vector_search",
                    capability=Capability.VECTOR_SEARCH,
                    query=self._query_for(Capability.VECTOR_SEARCH, understanding),
                    required=region_vector_candidates,
                    depends_on=["identity_search"] if has_identity else [],
                    top_k=max(understanding.limit * 4, 30),
                    rationale=(
                        "해외 ETF의 지역 조건 안에서 전략 의미가 가까운 상품 후보를 찾습니다."
                        if region_vector_candidates
                        else "임베딩 인덱스가 준비되면 의미 유사 검색을 보조 경로로 사용합니다."
                    ),
                )
            )
            if region_vector_candidates:
                structured_dependencies.append("vector_search")

        if Intent.RECENT_EVENT in understanding.intents and selection_required:
            structured_dependencies.append("event_search")

        if selection_required:
            steps.append(
                RetrievalStep(
                    step_id="structured_search",
                    capability=Capability.STRUCTURED_SEARCH,
                    query=self._query_for(Capability.STRUCTURED_SEARCH, understanding),
                    required=True,
                    depends_on=list(dict.fromkeys(structured_dependencies)),
                    top_k=max(understanding.limit * 3, 20),
                    rationale="숫자·등급·판매 여부·통화 조건과 명시적 정렬을 결정론적으로 처리합니다.",
                )
            )

        needs_documents = bool(
            set(understanding.intents) & {Intent.RISK, Intent.STRUCTURE, Intent.RESEARCH}
        ) or (
            Intent.STRATEGY in understanding.intents
            and any(
                str(item) in {"public_fund", "domestic_etf"}
                for item in understanding.product_types
            )
        )
        if needs_documents:
            dependencies = ["structured_search"] if selection_required else []
            steps.append(
                RetrievalStep(
                    step_id="document_search",
                    capability=Capability.DOCUMENT_SEARCH,
                    query=self._query_for(Capability.DOCUMENT_SEARCH, understanding),
                    required=True,
                    depends_on=dependencies,
                    top_k=max(understanding.limit * 3, 20),
                    rationale="구조·주요 위험·리서치 의견은 실제 문서 근거가 필요합니다.",
                )
            )

        plan = RetrievalPlan(
            version=self.VERSION,
            understanding=understanding,
            steps=steps,
            warnings=list(understanding.warnings),
        )
        plan.warnings.extend(self.validator.validate(plan))
        plan.blockers.extend(self.validator.blocking_issues(plan))
        plan.blocker_reason_codes.extend(self.validator.blocking_reason_codes(plan))
        return plan

    def _query_for(
        self,
        capability: Capability,
        understanding: QueryUnderstanding,
        *,
        requires_product_link: bool = False,
    ) -> dict:
        query = self._base_query(understanding)
        query["requires_product_link"] = requires_product_link
        query["text_entities"] = (
            []
            if capability in {Capability.KEYWORD_SEARCH, Capability.VECTOR_SEARCH}
            and Intent.HOLDINGS in understanding.intents
            else list(understanding.entities)
        )
        query["holding_targets"] = (
            [] if Intent.RELATION in understanding.intents else list(understanding.entities)
        )
        if capability == Capability.RELATION_SEARCH:
            constraints = query.get("relation_constraints") or []
            query["relation_predicates"] = list(
                dict.fromkeys(
                    constraint.get("predicate")
                    for constraint in constraints
                    if constraint.get("predicate")
                )
            )
            query["listed_only"] = any(
                constraint.get("result_listed") is True for constraint in constraints
            )
        return query

    def _base_query(self, understanding: QueryUnderstanding) -> dict:
        return {
            "question": understanding.question,
            "intents": [str(item) for item in understanding.intents],
            "product_types": [str(item) for item in understanding.product_types],
            "product_mentions": list(understanding.product_mentions),
            "identifiers": list(understanding.identifiers),
            "entities": list(understanding.entities),
            "themes": list(understanding.themes),
            "filters": [to_primitive(item) for item in understanding.filters],
            "sorts": [to_primitive(item) for item in understanding.sorts],
            "temporal": self._resolved_temporal(understanding),
            "relation_constraints": self._relation_constraints(understanding),
            "document_sections": self._document_sections(understanding),
            "document_constraints": self._document_constraints(understanding),
            "limit": understanding.limit,
        }

    def _resolved_temporal(self, understanding: QueryUnderstanding) -> dict | None:
        temporal = understanding.temporal
        if temporal is None:
            return None
        payload = to_primitive(temporal)
        if temporal.relative_months and self.snapshot_date:
            payload["end_date"] = self.snapshot_date.isoformat()
            payload["start_date"] = _subtract_months(
                self.snapshot_date, temporal.relative_months
            ).isoformat()
        return payload

    @staticmethod
    def _relation_constraints(understanding: QueryUnderstanding) -> list[dict]:
        text = understanding.question
        anchor = understanding.entities[0] if understanding.entities else None
        constraints: list[dict] = []
        if "자회사" in text or "종속회사" in text:
            constraints.append(
                {
                    "predicate": "subsidiaryOf",
                    "anchor": anchor,
                    "anchor_role": "target",
                    "result_role": "source",
                    "result_listed": True if "상장 자회사" in text else None,
                    "traversal_hops": 1,
                }
            )
        if "모회사" in text:
            constraints.append(
                {
                    "predicate": "subsidiaryOf",
                    "anchor": anchor,
                    "anchor_role": "source",
                    "result_role": "target",
                    "result_listed": None,
                    "traversal_hops": 1,
                }
            )
        if "계열사" in text or "관계사" in text:
            constraints.append(
                {
                    "predicate": "affiliateOf",
                    "anchor": anchor,
                    "anchor_role": "either",
                    "result_role": "opposite",
                    "result_listed": True if "상장" in text else None,
                    "traversal_hops": 1,
                }
            )
        return constraints

    @staticmethod
    def _document_sections(understanding: QueryUnderstanding) -> list[str]:
        sections: list[str] = []
        if Intent.STRUCTURE in understanding.intents:
            sections.append("structure")
        if Intent.STRATEGY in understanding.intents:
            sections.append("strategy")
        if Intent.RISK in understanding.intents:
            sections.append("risk")
        if Intent.RESEARCH in understanding.intents:
            sections.append("research_opinion")
        return sections

    @staticmethod
    def _document_constraints(understanding: QueryUnderstanding) -> list[dict]:
        text = understanding.question
        constraints: list[dict] = []
        if Intent.RESEARCH in understanding.intents:
            sentiment = None
            if "긍정" in text or "매수" in text:
                sentiment = "positive"
            elif "부정" in text or "매도" in text:
                sentiment = "negative"
            constraints.append(
                {"field": "sentiment", "operator": "eq", "value": sentiment}
            )
        return constraints


def _coerce_date(value: str | date | None) -> date | None:
    if value is None:
        return None
    if isinstance(value, date):
        return value
    return date.fromisoformat(value)


def _subtract_months(value: date, months: int) -> date:
    month_index = value.year * 12 + value.month - 1 - months
    year, month_zero = divmod(month_index, 12)
    month = month_zero + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)
