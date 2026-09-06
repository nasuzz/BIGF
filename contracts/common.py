from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


class AnswerStatus(str, Enum):
    ANSWERED = "answered"
    PARTIAL = "partial"
    NEEDS_CLARIFICATION = "needs_clarification"
    UNANSWERABLE = "unanswerable"


class ReasonCode(str, Enum):
    INVALID_VALUE = "INVALID_VALUE"
    UNRESOLVED_ENTITY = "UNRESOLVED_ENTITY"
    AMBIGUOUS_ENTITY = "AMBIGUOUS_ENTITY"
    FIELD_NOT_SUPPORTED = "FIELD_NOT_SUPPORTED"
    TARGET_VALUE_MISSING = "TARGET_VALUE_MISSING"
    OUT_OF_PERIOD = "OUT_OF_PERIOD"
    ZERO_MATCH = "ZERO_MATCH"
    OUT_OF_SCOPE = "OUT_OF_SCOPE"
    ESTIMATED_ONLY = "ESTIMATED_ONLY"
    HOLDINGS_DATA_MISSING = "HOLDINGS_DATA_MISSING"
    RELATION_DATA_MISSING = "RELATION_DATA_MISSING"
    DOCUMENT_DATA_MISSING = "DOCUMENT_DATA_MISSING"
    RETRIEVAL_ERROR = "RETRIEVAL_ERROR"


class FillType(str, Enum):
    ORIGINAL = "original"
    OFFICIAL_FILL = "official_fill"
    MANUAL_FILL = "manual_fill"
    ESTIMATED = "estimated"


class SearchType(str, Enum):
    SQL = "sql"
    VECTOR = "vector"
    HYBRID = "hybrid"


class EvidenceItem(BaseModel):
    evidence_id: Optional[str] = None
    dataset: Optional[str] = None
    product_id: Optional[str] = None
    product_name: Optional[str] = None
    field: Optional[str] = None
    value: Optional[Any] = None
    text: Optional[str] = None
    source_ref: Optional[str] = None
    as_of_date: Optional[str] = None
    search_type: Optional[SearchType] = None
    score: Optional[float] = None
    fill_type: Optional[FillType] = None

    keyword_rank: Optional[int] = None
    vector_rank: Optional[int] = None
    vector_score: Optional[float] = None
    rrf_score: Optional[float] = None


class ThinkTraceStep(BaseModel):
    step: str
    summary: str


class Timings(BaseModel):
    routing_ms: int = 0
    retrieval_ms: int = 0
    llm_ms: int = 0
    validation_ms: int = 0
    total_ms: int = 0
