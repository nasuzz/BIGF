from typing import Optional

from pydantic import BaseModel, Field

from .common import AnswerStatus, EvidenceItem, ReasonCode, ThinkTraceStep, Timings


class AgentResult(BaseModel):
    retrieved_context: list[EvidenceItem] = Field(
        default_factory=list, description="답변에 실제로 사용한 근거만 (검색은 됐지만 안 쓴 건 제외)"
    )
    think_trace: list[ThinkTraceStep] = Field(
        default_factory=list, description="HCX 원문 추론이 아닌 처리 단계 요약 (route/retrieve/validate 등)"
    )
    answer: str
    status: AnswerStatus
    reason_code: Optional[ReasonCode] = None
    timings: Timings = Field(default_factory=Timings)
