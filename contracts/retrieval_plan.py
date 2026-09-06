from typing import Any, Optional

from pydantic import BaseModel, Field

from .common import EvidenceItem

ALLOWED_FILTER_OPERATORS = ["eq", "in", "gte", "lte", "between", "contains", "top_n"]


class RetrievalPlan(BaseModel):
    intent: str = Field(..., description="예: filter_and_explain, exact_lookup, compare, aggregate")
    dataset: str = Field(..., description="domestic_bond | domestic_etf | overseas_etf | public_fund")
    filters: dict[str, Any] = Field(default_factory=dict, description=f"허용 연산자: {ALLOWED_FILTER_OPERATORS}")
    semantic_query: Optional[str] = Field(None, description="해외 ETF 전략 등 Vector 검색과 결합할 때만 사용")
    sort: Optional[dict[str, str]] = None
    top_k: int = 5
    required_fields: list[str] = Field(default_factory=list)


class SearchStrategyRequest(BaseModel):
    semantic_query: str = Field(..., description="사용자 의미를 보존한 한국어 질의 (Vector 검색용)")
    keyword_query: Optional[str] = Field(None, description="영어 전략문 키워드 검색용 (예: 'artificial intelligence semiconductor')")
    filters: dict[str, Any] = Field(default_factory=dict, description="region, asset_class 등 SQL 선필터")
    top_k: int = 5


class Coverage(BaseModel):
    field_supported: Optional[bool] = None
    requested_value_valid: Optional[bool] = None
    requested_value: Optional[str] = None
    available_values: Optional[list[str]] = None

    required_fields: list[str] = Field(default_factory=list)
    missing_fields: list[str] = Field(default_factory=list)
    unsupported_fields: list[str] = Field(default_factory=list)

    match_count: int = 0


class RetrievalResult(BaseModel):
    evidence: list[EvidenceItem] = Field(default_factory=list)
    coverage: Coverage = Field(default_factory=Coverage)
    reason_hint: Optional[str] = Field(
        None, description="ReasonCode 값 중 하나 (예: INVALID_VALUE, ZERO_MATCH)"
    )
    retrieval_ms: int = 0
