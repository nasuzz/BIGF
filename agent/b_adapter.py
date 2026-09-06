import json
import logging
from dataclasses import replace

from b_agent.answering import AnswerComposer, LLMClient
from b_agent.gateway import DataGateway, registry_from_gateway
from agent.ext_retrievers import register_ext_retrievers
from agent.prospectus_retriever import register_prospectus_retriever
from b_agent.hcx import build_hcx_client_from_env
from b_agent.models import AnswerStatus as BAnswerStatus
from b_agent.models import Evidence as BEvidence
from b_agent.models import ReasonCode as BReasonCode
from b_agent.models import ValueProvenance as BValueProvenance
from b_agent.pipeline import BAgentPipeline, PipelineExecution
from b_agent.postgres_gateway import PostgresDataGateway
from api.db import connection

from contracts import (
    AgentResult,
    AnswerStatus,
    EvidenceItem,
    FillType,
    ReasonCode,
    SearchType,
    ThinkTraceStep,
    Timings,
)

_STATUS_MAP: dict[BAnswerStatus, AnswerStatus] = {
    BAnswerStatus.COMPLETE: AnswerStatus.ANSWERED,
    BAnswerStatus.PARTIAL: AnswerStatus.PARTIAL,
    BAnswerStatus.UNANSWERABLE: AnswerStatus.UNANSWERABLE,
    BAnswerStatus.ERROR: AnswerStatus.UNANSWERABLE,
}

_REASON_CODE_MAP: dict[BReasonCode, ReasonCode] = {
    BReasonCode.AMBIGUOUS_ENTITY: ReasonCode.AMBIGUOUS_ENTITY,
    BReasonCode.NO_MATCH: ReasonCode.ZERO_MATCH,
    BReasonCode.REQUIRED_FIELD_MISSING: ReasonCode.FIELD_NOT_SUPPORTED,
    BReasonCode.HOLDINGS_DATA_MISSING: ReasonCode.HOLDINGS_DATA_MISSING,
    BReasonCode.RELATION_DATA_MISSING: ReasonCode.RELATION_DATA_MISSING,
    BReasonCode.DOCUMENT_DATA_MISSING: ReasonCode.DOCUMENT_DATA_MISSING,
    BReasonCode.TEMPORAL_DATA_MISSING: ReasonCode.OUT_OF_PERIOD,
    BReasonCode.UNSUPPORTED_QUERY: ReasonCode.OUT_OF_SCOPE,
    BReasonCode.RETRIEVAL_ERROR: ReasonCode.RETRIEVAL_ERROR,
}

_CAPABILITY_TO_SEARCH_TYPE = {
    "vector_search": SearchType.VECTOR,
    "structured_search": SearchType.SQL,
    "identity_search": SearchType.SQL,
    "keyword_search": SearchType.SQL,
    "holding_search": SearchType.SQL,
    "relation_search": SearchType.SQL,
    "document_search": SearchType.SQL,
}

_FILL_TYPE_MAP: dict[str, FillType] = {
    "original": FillType.ORIGINAL,
    "official_fill": FillType.OFFICIAL_FILL,
    # DB schema calls the verified manual source ``manual_verified`` while
    # the public response contract retained the older ``manual_fill`` name.
    "manual_verified": FillType.MANUAL_FILL,
    "manual_fill": FillType.MANUAL_FILL,
    "estimated": FillType.ESTIMATED,
}

_FIELD_VALUE_ALIASES: dict[str, tuple[str, ...]] = {
    "canonical_name": ("canonical_name", "name", "product_name"),
    "name": ("name", "canonical_name", "product_name"),
    "currency_code": ("currency_code", "currency"),
    "currency": ("currency", "currency_code"),
    "asset_amount": ("asset_amount", "net_assets", "aum", "net_asset_amount"),
    "net_assets": ("net_assets", "asset_amount", "aum", "net_asset_amount"),
    "expense_ratio_pct": ("expense_ratio_pct", "fee_rate"),
    "fee_rate": ("fee_rate", "expense_ratio_pct"),
    "return_1y_pct": ("return_1y_pct", "one_year_return", "return_1y"),
    "one_year_return": ("one_year_return", "return_1y_pct", "return_1y"),
    "return_1y": ("return_1y", "one_year_return", "return_1y_pct"),
    "risk_label": ("risk_label", "risk_grade", "risk_name"),
    "risk_grade": ("risk_grade", "risk_label", "risk_name"),
    "strategy_text": ("strategy_text", "strategy", "raw_text"),
    "strategy": ("strategy", "strategy_text", "raw_text"),
}

_MISSING = object()

logger = logging.getLogger("product_finder_api")


def _build_pipeline(
    gateway: DataGateway | None = None,
    llm_client: LLMClient | None = None,
) -> BAgentPipeline:
    active_gateway = gateway or PostgresDataGateway(connection)
    active_llm = (
        llm_client if llm_client is not None else build_hcx_client_from_env()
    )
    # HOLDING_SEARCH and EVENT_SEARCH are owned by ext_retrievers.py, not
    # PostgresDataGateway (issue #34). registry_from_gateway() must run first
    # so its capabilities are in place before these two are added on top.
    registry = registry_from_gateway(active_gateway)
    try:
        register_ext_retrievers(registry)
    except RuntimeError:
        # Mirrors api/db.py's handling of a missing DATABASE_URL: log and keep
        # the module importable (needed for tests and tooling that never hit
        # the database) instead of crashing the whole pipeline at import time.
        logger.error(
            "HOLDING_SEARCH/EVENT_SEARCH not registered: DATABASE_URL is not set"
        )
    try:
        register_prospectus_retriever(registry)
    except RuntimeError:
        logger.error(
            "DOCUMENT_SEARCH not registered: DATABASE_URL is not set"
        )
    return BAgentPipeline(
        registry=registry,
        composer=AnswerComposer(llm_client=active_llm),
    )


_PIPELINE = _build_pipeline()


def _public_evidence(ev: BEvidence) -> BEvidence:
    """Project field eligibility onto every public copy of a value.

    Raw content can contain the same values as structured fields, so dropping
    only field-level items is insufficient. Rebuild it from the eligible
    projection whenever provenance excludes a field, for any search capability.
    Evidence without exclusions keeps its original content.
    """
    excluded = {
        source.field_name
        for source in ev.value_provenance
        if not source.evidence_eligible
        or _FILL_TYPE_MAP.get(source.fill_type) in {None, FillType.ESTIMATED}
    }
    if not excluded:
        return ev

    # Use the same aliases as field-value mapping in both directions. An
    # ineligible logical field must also suppress its DB column, and vice versa.
    alias_groups = (
        *_FIELD_VALUE_ALIASES.values(),
        ("strategy_text", "strategy", "raw_text", "normalized_text", "evidence_excerpt"),
    )
    for field in tuple(excluded):
        for aliases in alias_groups:
            if field in aliases:
                excluded.update(aliases)
    structured = {
        key: value for key, value in ev.structured.items() if key not in excluded
    }
    return replace(
        ev,
        structured=structured,
        content=json.dumps(structured, ensure_ascii=False, default=str, sort_keys=True)
        if structured else "",
    )


def _evidence_to_items(ev: BEvidence) -> list[EvidenceItem]:
    public = _public_evidence(ev)
    product_name = None
    if public.structured:
        product_name = (
            public.structured.get("name")
            or public.structured.get("product_name")
            or public.structured.get("canonical_name")
        )

    common = {
        "evidence_id": ev.evidence_id,
        "dataset": ev.product_type.value if ev.product_type else None,
        "product_id": ev.product_id,
        "product_name": product_name,
        "text": public.content,
        "search_type": _CAPABILITY_TO_SEARCH_TYPE.get(ev.capability.value),
        "score": ev.score,
    }
    field_items: list[EvidenceItem] = []
    for source in ev.value_provenance:
        fill_type = _FILL_TYPE_MAP.get(source.fill_type)
        if fill_type is None:
            continue
        # Estimated values are carried to the contract so the API can exclude
        # them explicitly. Other ineligible fill types are not factual evidence.
        if not source.evidence_eligible and fill_type != FillType.ESTIMATED:
            continue
        value = _field_value(ev if fill_type == FillType.ESTIMATED else public, source)
        if value is _MISSING or value is None:
            continue
        field_items.append(
            EvidenceItem(
                **common,
                field=source.field_name,
                value=value,
                source_ref=source.source_reference or ev.source_ref,
                as_of_date=source.source_as_of_date or ev.as_of_date,
                fill_type=fill_type,
            )
        )

    if field_items:
        return field_items
    return [
        EvidenceItem(
            **common,
            field=None,
            value=None,
            source_ref=ev.source_ref,
            as_of_date=ev.as_of_date,
            fill_type=None,
        )
    ]


def _field_value(ev: BEvidence, source: BValueProvenance):
    candidates = _FIELD_VALUE_ALIASES.get(source.field_name, (source.field_name,))
    for candidate in candidates:
        if candidate in ev.structured:
            return ev.structured[candidate]
    return _MISSING


def _trace_from_execution(
    execution: PipelineExecution, status: AnswerStatus
) -> list[ThinkTraceStep]:
    if execution.error_stage:
        return [
            ThinkTraceStep(
                step="validate",
                summary=(
                    "상태=unanswerable; 원본상태=error; "
                    "사유=RETRIEVAL_ERROR; 처리단계=adapter"
                ),
            )
        ]
    if execution.plan is None or execution.report is None:
        return [ThinkTraceStep(step="route", summary="질문 유형: 빈 질문")]

    plan = execution.plan
    routes = ", ".join(
        f"{item.capability.value}:{item.outcome.value}({len(item.evidence)})"
        for item in execution.report.step_results
    )
    return [
        ThinkTraceStep(
            step="route",
            summary=(
                f"질문 유형: {[item.value for item in plan.understanding.intents]} / "
                f"대상: {[item.value for item in plan.understanding.product_types]}"
            ),
        ),
        ThinkTraceStep(step="retrieve", summary=f"검색 경로: {routes or '없음'}"),
        ThinkTraceStep(
            step="validate",
            summary=(
                f"판정: {status.value} / "
                f"B상태: {execution.assessment.status.value} / "
                f"{execution.assessment.message}"
            ),
        ),
    ]


def answer_question(question_id: str, question: str) -> AgentResult:
    execution = _PIPELINE.execute(question_id, question)
    decision = execution.assessment
    status = _STATUS_MAP.get(decision.status, AnswerStatus.UNANSWERABLE)
    reason_code = (
        _REASON_CODE_MAP.get(decision.reason_codes[0])
        if decision.reason_codes
        else None
    )
    timings = execution.timings

    return AgentResult(
        retrieved_context=[
            item
            for evidence in execution.fused_evidence
            for item in _evidence_to_items(evidence)
        ],
        think_trace=_trace_from_execution(execution, status),
        answer=execution.answer,
        status=status,
        reason_code=reason_code,
        timings=Timings(
            routing_ms=timings.routing_ms,
            retrieval_ms=timings.retrieval_ms,
            validation_ms=timings.validation_ms,
            llm_ms=timings.llm_ms,
            total_ms=timings.total_ms,
        ),
    )
