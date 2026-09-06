import logging
import os
import sys
import time
import uuid
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, Query, Request
from fastapi.responses import JSONResponse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

load_dotenv()

from contracts import (
    AgentResult,
    ExternalAnswerResponse,
    FillType,
    HealthResponse,
)

from agent.b_adapter import answer_question
from api.db import check_connection
from b_agent.hcx import hcx_is_configured
from b_agent.safe_logging import log_failure


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger("product_finder_api")

app = FastAPI(title="Product Finder Answer API", version="0.2.0")


def _serialize_context(agent_result: AgentResult) -> str:
    # Fusion already bounds the evidence used to compose the answer. A second
    # limit here can remove cited evidence after grounding has been validated.
    usable = [ev for ev in agent_result.retrieved_context if ev.fill_type != FillType.ESTIMATED]

    if not usable:
        return ""

    groups: dict[str, list[str]] = {}
    for i, ev in enumerate(usable, start=1):
        label = ev.evidence_id or f"E{i}"
        parts = groups.setdefault(label, [f"[{label}]"])
        shared = []
        if ev.product_id:
            shared.append(f"상품ID: {ev.product_id}")
        if ev.product_name:
            shared.append(f"상품명: {ev.product_name}")
        if ev.text:
            shared.append(f"내용: {ev.text}")
        for value in shared:
            if value not in parts:
                parts.append(value)

        # Keep each field's value, date, and source together under its original
        # evidence ID, without repeating the shared product/content payload.
        field_parts = []
        if ev.field:
            field_parts.append(f"필드: {ev.field}")
        if ev.value is not None:
            field_parts.append(f"값: {ev.value}")
        if ev.as_of_date:
            field_parts.append(f"기준일: {ev.as_of_date}")
        if ev.source_ref:
            field_parts.append(f"출처: {ev.source_ref}")
        if field_parts:
            parts.append(" / ".join(field_parts))
    return "\n".join(" / ".join(parts) for parts in groups.values())


def _serialize_trace(agent_result: AgentResult) -> str:
    if not agent_result.think_trace:
        return ""
    return "\n".join(step.summary for step in agent_result.think_trace)


def _fallback_response(
    question_id: str, question: str, think_trace: str, answer: str
) -> ExternalAnswerResponse:
    return ExternalAnswerResponse(
        question_id=question_id or "",
        question=question or "",
        retrieved_context="",
        think_trace=think_trace,
        answer=answer,
    )


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    db_ok = check_connection()
    llm_ready = hcx_is_configured()

    return HealthResponse(
        status="ok" if db_ok else "degraded",
        database="connected" if db_ok else "disconnected",
        llm="ready" if llm_ready else "not_ready",
        data_version=os.getenv("DATA_VERSION", "unknown"),
    )


@app.get("/answer", response_model=ExternalAnswerResponse)
def get_answer(
    request: Request,
    question_id: str = Query(default=""),
    question: str = Query(default=""),
) -> JSONResponse:
    request_id = str(uuid.uuid4())
    start = time.monotonic()


    if not question_id.strip() or not question.strip():
        result = _fallback_response(
            question_id,
            question,
            think_trace="상태=unanswerable; 원본상태=bad_request; 사유=question_id 또는 question 누락",
            answer="질문 또는 질문 ID가 비어 있어 답변을 생성할 수 없습니다.",
        )
        logger.info("request_id=%s status=bad_request question_id=%s", request_id, question_id)
        return JSONResponse(content=result.model_dump())

    try:
        agent_result: AgentResult = answer_question(question_id, question)

        response = ExternalAnswerResponse(
            question_id=question_id,
            question=question,
            retrieved_context=_serialize_context(agent_result),
            think_trace=_serialize_trace(agent_result),
            answer=agent_result.answer,
        )

        total_ms = int((time.monotonic() - start) * 1000)
        logger.info(
            "request_id=%s question_id=%s status=%s reason_code=%s total_ms=%s",
            request_id,
            question_id,
            agent_result.status,
            agent_result.reason_code,
            total_ms,
        )
        return JSONResponse(content=response.model_dump())

    except Exception as exc:
        log_failure(
            logger, exc, stage="api", request_id=request_id, question_id=question_id
        )
        result = _fallback_response(
            question_id,
            question,
            think_trace="상태=unanswerable; 원본상태=error; 사유=RETRIEVAL_ERROR; 처리단계=api",
            answer="일시적인 시스템 오류로 답변을 생성하지 못했습니다. 근거 없는 상품은 생성하지 않았습니다.",
        )
        return JSONResponse(content=result.model_dump(), status_code=200)
