from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class StringEnum(str, Enum):
    def __str__(self) -> str:
        return self.value


class ProductType(StringEnum):
    BOND = "bond"
    PUBLIC_FUND = "public_fund"
    DOMESTIC_ETF = "domestic_etf"
    FOREIGN_ETF = "foreign_etf"


class Intent(StringEnum):
    SEARCH = "search"
    FILTER = "filter"
    RECOMMEND = "recommend"
    COMPARE = "compare"
    HOLDINGS = "holdings"
    RELATION = "relation"
    STRATEGY = "strategy"
    RISK = "risk"
    STRUCTURE = "structure"
    RECENT_EVENT = "recent_event"
    RESEARCH = "research"


class Capability(StringEnum):
    IDENTITY_SEARCH = "identity_search"
    STRUCTURED_SEARCH = "structured_search"
    KEYWORD_SEARCH = "keyword_search"
    VECTOR_SEARCH = "vector_search"
    HOLDING_SEARCH = "holding_search"
    RELATION_SEARCH = "relation_search"
    DOCUMENT_SEARCH = "document_search"
    EVENT_SEARCH = "event_search"


class Operator(StringEnum):
    EQ = "eq"
    NE = "ne"
    GT = "gt"
    GTE = "gte"
    LT = "lt"
    LTE = "lte"
    IN = "in"
    CONTAINS = "contains"
    CREDIT_AT_LEAST = "credit_at_least"
    CREDIT_AT_MOST = "credit_at_most"


class SortDirection(StringEnum):
    ASC = "asc"
    DESC = "desc"


class StepOutcome(StringEnum):
    SUCCESS = "success"
    EMPTY = "empty"
    UNAVAILABLE = "unavailable"
    SKIPPED = "skipped"
    FAILED = "failed"


class AnswerStatus(StringEnum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    UNANSWERABLE = "unanswerable"
    ERROR = "error"


class ReasonCode(StringEnum):
    """Local defaults. They can be mapped to A's registry at integration time."""

    AMBIGUOUS_ENTITY = "ambiguous_entity"
    NO_MATCH = "no_match"
    REQUIRED_FIELD_MISSING = "required_field_missing"
    HOLDINGS_DATA_MISSING = "holdings_data_missing"
    RELATION_DATA_MISSING = "relation_data_missing"
    DOCUMENT_DATA_MISSING = "document_data_missing"
    TEMPORAL_DATA_MISSING = "temporal_data_missing"
    UNSUPPORTED_QUERY = "unsupported_query"
    RETRIEVAL_ERROR = "retrieval_error"


@dataclass(frozen=True)
class Filter:
    field: str
    operator: Operator
    value: Any
    unit: str | None = None
    source_text: str | None = None


@dataclass(frozen=True)
class SortSpec:
    field: str
    direction: SortDirection


@dataclass(frozen=True)
class TemporalConstraint:
    relative_months: int | None = None
    start_date: str | None = None
    end_date: str | None = None


@dataclass
class QueryUnderstanding:
    question: str
    product_types: list[ProductType] = field(default_factory=list)
    intents: list[Intent] = field(default_factory=list)
    product_mentions: list[str] = field(default_factory=list)
    identifiers: list[str] = field(default_factory=list)
    entities: list[str] = field(default_factory=list)
    themes: list[str] = field(default_factory=list)
    filters: list[Filter] = field(default_factory=list)
    sorts: list[SortSpec] = field(default_factory=list)
    temporal: TemporalConstraint | None = None
    limit: int = 10
    warnings: list[str] = field(default_factory=list)


@dataclass
class RetrievalStep:
    step_id: str
    capability: Capability
    query: dict[str, Any]
    required: bool = True
    depends_on: list[str] = field(default_factory=list)
    top_k: int = 20
    rationale: str = ""
    unsupported_reason: str | None = None


@dataclass
class RetrievalPlan:
    version: str
    understanding: QueryUnderstanding
    steps: list[RetrievalStep]
    capability_snapshot_id: str | None = None
    warnings: list[str] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)
    blocker_reason_codes: list[ReasonCode] = field(default_factory=list)

    @property
    def required_capabilities(self) -> list[Capability]:
        return list(dict.fromkeys(s.capability for s in self.steps if s.required))

    def to_dict(self) -> dict[str, Any]:
        return to_primitive(self)


@dataclass
class ValueProvenance:
    """Field-level source metadata returned by ``meta.value_provenance``."""

    field_name: str
    fill_type: str
    evidence_eligible: bool
    was_missing: bool = False
    source_reference: str | None = None
    source_as_of_date: str | None = None
    fill_confidence: float | None = None


@dataclass
class Evidence:
    evidence_id: str
    capability: Capability
    source_id: str
    content: str
    score: float = 0.0
    record_id: str | None = None
    product_id: str | None = None
    product_type: ProductType | None = None
    as_of_date: str | None = None
    source_ref: str | None = None
    structured: dict[str, Any] = field(default_factory=dict)
    provenance: dict[str, Any] = field(default_factory=dict)
    value_provenance: list[ValueProvenance] = field(default_factory=list)


@dataclass
class StepResult:
    step_id: str
    capability: Capability
    outcome: StepOutcome
    evidence: list[Evidence] = field(default_factory=list)
    message: str = ""
    coverage_complete: bool | None = None
    coverage_note: str = ""
    coverage_by_field: dict[str, dict[str, int]] = field(default_factory=dict)
    total_hits: int | None = None
    truncated: bool = False
    ambiguous: bool = False


@dataclass
class RetrievalBatch:
    evidence: list[Evidence] = field(default_factory=list)
    coverage_complete: bool | None = None
    coverage_note: str = ""
    coverage_by_field: dict[str, dict[str, int]] = field(default_factory=dict)
    total_hits: int | None = None
    truncated: bool = False
    ambiguous: bool = False


@dataclass
class ExecutionReport:
    plan: RetrievalPlan
    step_results: list[StepResult]

    @property
    def evidence(self) -> list[Evidence]:
        return [item for result in self.step_results for item in result.evidence]


@dataclass
class AnswerDecision:
    status: AnswerStatus
    reason_codes: list[ReasonCode]
    message: str
    missing_capabilities: list[Capability] = field(default_factory=list)
    failed_steps: list[str] = field(default_factory=list)

    @property
    def reason_code(self) -> ReasonCode | None:
        return self.reason_codes[0] if self.reason_codes else None


@dataclass
class AnswerPayload:
    question_id: str
    question: str
    retrieved_context: str
    think_trace: str
    answer: str

    def to_dict(self) -> dict[str, str]:
        return {
            "question_id": str(self.question_id),
            "question": str(self.question),
            "retrieved_context": str(self.retrieved_context),
            "think_trace": str(self.think_trace),
            "answer": str(self.answer),
        }


def to_primitive(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if hasattr(value, "__dataclass_fields__"):
        return {key: to_primitive(item) for key, item in asdict(value).items()}
    if isinstance(value, dict):
        return {str(key): to_primitive(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_primitive(item) for item in value]
    return value
