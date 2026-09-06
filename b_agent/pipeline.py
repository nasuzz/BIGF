from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

from .answerability import AnswerabilityPolicy
from .answering import AnswerComposer
from .executor import PlanExecutor
from .fusion import EvidenceFusion
from .models import (
    AnswerDecision,
    AnswerPayload,
    AnswerStatus,
    Evidence,
    ExecutionReport,
    ReasonCode,
    RetrievalPlan,
)
from .parser import RuleBasedQuestionAnalyzer
from .planner import RetrievalPlanner
from .retrievers import RetrieverRegistry
from .safe_logging import log_failure


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PipelineTimings:
    routing_ms: int = 0
    retrieval_ms: int = 0
    validation_ms: int = 0
    llm_ms: int = 0
    total_ms: int = 0


@dataclass
class PipelineExecution:
    """Shared internal result used by both ``run`` and the API adapter."""

    question_id: str
    question: str
    plan: RetrievalPlan | None
    report: ExecutionReport | None
    fused_evidence: list[Evidence]
    assessment: AnswerDecision
    retrieved_context: str
    think_trace: str
    answer: str
    timings: PipelineTimings = field(default_factory=PipelineTimings)
    error_stage: str | None = None

    def to_answer_payload(self) -> AnswerPayload:
        return AnswerPayload(
            question_id=self.question_id,
            question=self.question,
            retrieved_context=self.retrieved_context,
            think_trace=self.think_trace,
            answer=self.answer,
        )


_EMPTY_QUESTION_ANSWER = "질문이 비어 있어 검색 계획을 만들 수 없습니다."
_PIPELINE_ERROR_ANSWER = (
    "질문 처리 중 오류가 발생했습니다. 근거 없는 답변은 생성하지 않았습니다."
)


class BAgentPipeline:
    def __init__(
        self,
        registry: RetrieverRegistry | None = None,
        analyzer: RuleBasedQuestionAnalyzer | None = None,
        planner: RetrievalPlanner | None = None,
        fusion: EvidenceFusion | None = None,
        policy: AnswerabilityPolicy | None = None,
        composer: AnswerComposer | None = None,
        snapshot_date: str | None = None,
    ) -> None:
        self.registry = registry or RetrieverRegistry()
        self.analyzer = analyzer or RuleBasedQuestionAnalyzer()
        self.planner = planner or RetrievalPlanner(snapshot_date=snapshot_date)
        self.executor = PlanExecutor(self.registry)
        self.fusion = fusion or EvidenceFusion()
        self.policy = policy or AnswerabilityPolicy()
        self.composer = composer or AnswerComposer()

    def plan(self, question: str) -> RetrievalPlan:
        plan = self.planner.build(self.analyzer.analyze(question))
        plan.capability_snapshot_id = self.registry.snapshot_id
        for step in plan.steps:
            retriever = self.registry.get(step.capability)
            checker = getattr(retriever, "unsupported_reason", None)
            reason = checker(step) if callable(checker) else None
            if isinstance(reason, str) and reason:
                step.unsupported_reason = reason
                if step.required:
                    plan.blockers.append(reason)
                    if ReasonCode.UNSUPPORTED_QUERY not in plan.blocker_reason_codes:
                        plan.blocker_reason_codes.append(ReasonCode.UNSUPPORTED_QUERY)
        return plan

    def execute(self, question_id: str, question: str) -> PipelineExecution:
        """Run the pipeline once while preserving evidence, assessment, and timings."""

        started_at = time.monotonic()
        safe_question = str(question or "").strip()
        if not safe_question:
            elapsed_ms = _elapsed_ms(started_at)
            assessment = AnswerDecision(
                status=AnswerStatus.UNANSWERABLE,
                reason_codes=[ReasonCode.UNSUPPORTED_QUERY],
                message="질문이 비어 있어 검색 계획을 만들 수 없습니다.",
            )
            return PipelineExecution(
                question_id=str(question_id),
                question="",
                plan=None,
                report=None,
                fused_evidence=[],
                assessment=assessment,
                retrieved_context="[]",
                think_trace="상태=unanswerable; 사유=unsupported_query; 최종근거=0건",
                answer=_EMPTY_QUESTION_ANSWER,
                timings=PipelineTimings(routing_ms=elapsed_ms, total_ms=elapsed_ms),
            )

        plan: RetrievalPlan | None = None
        report: ExecutionReport | None = None
        evidence: list[Evidence] = []
        routing_ms = 0
        retrieval_ms = 0
        validation_ms = 0
        llm_ms = 0
        stage = "route"
        stage_started_at = time.monotonic()
        try:
            plan = self.plan(safe_question)
            routing_ms = _elapsed_ms(stage_started_at)

            stage = "retrieve"
            stage_started_at = time.monotonic()
            report = self.executor.execute(plan)
            evidence = self.fusion.fuse(
                report.evidence,
                required_capabilities=report.plan.required_capabilities,
            )
            retrieval_ms = _elapsed_ms(stage_started_at)

            stage = "validate"
            stage_started_at = time.monotonic()
            decision = self.policy.assess(report, evidence)
            validation_ms = _elapsed_ms(stage_started_at)

            stage = "compose"
            stage_started_at = time.monotonic()
            payload = self.composer.compose(question_id, report, decision, evidence)
            llm_ms = _elapsed_ms(stage_started_at)
            return PipelineExecution(
                question_id=str(question_id),
                question=payload.question,
                plan=plan,
                report=report,
                fused_evidence=list(evidence),
                assessment=decision,
                retrieved_context=payload.retrieved_context,
                think_trace=payload.think_trace,
                answer=payload.answer,
                timings=PipelineTimings(
                    routing_ms=routing_ms,
                    retrieval_ms=retrieval_ms,
                    validation_ms=validation_ms,
                    llm_ms=llm_ms,
                    total_ms=_elapsed_ms(started_at),
                ),
            )
        except Exception as exc:
            stage_elapsed_ms = _elapsed_ms(stage_started_at)
            if stage == "route":
                routing_ms = stage_elapsed_ms
            elif stage == "retrieve":
                retrieval_ms = stage_elapsed_ms
            elif stage == "validate":
                validation_ms = stage_elapsed_ms
            elif stage == "compose":
                llm_ms = stage_elapsed_ms

            log_failure(logger, exc, stage=stage, question_id=str(question_id))
            assessment = AnswerDecision(
                status=AnswerStatus.ERROR,
                reason_codes=[ReasonCode.RETRIEVAL_ERROR],
                message="파이프라인 실행 중 오류가 발생했습니다.",
                failed_steps=[stage],
            )
            return PipelineExecution(
                question_id=str(question_id),
                question=safe_question,
                plan=plan,
                report=report,
                fused_evidence=[],
                assessment=assessment,
                retrieved_context="[]",
                think_trace=(
                    "상태=error; 사유=retrieval_error; "
                    f"처리단계={stage}; 최종근거=0건"
                ),
                answer=_PIPELINE_ERROR_ANSWER,
                timings=PipelineTimings(
                    routing_ms=routing_ms,
                    retrieval_ms=retrieval_ms,
                    validation_ms=validation_ms,
                    llm_ms=llm_ms,
                    total_ms=_elapsed_ms(started_at),
                ),
                error_stage=stage,
            )

    def run(self, question_id: str, question: str) -> AnswerPayload:
        return self.execute(question_id, question).to_answer_payload()


def _elapsed_ms(started_at: float) -> int:
    return int((time.monotonic() - started_at) * 1000)
