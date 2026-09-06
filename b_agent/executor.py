from __future__ import annotations

import logging

from .evidence_validation import EvidenceValidator
from .models import ExecutionReport, RetrievalBatch, RetrievalPlan, StepOutcome, StepResult
from .retrievers import RetrievalContext, RetrieverRegistry
from .safe_logging import error_type_name, log_failure


logger = logging.getLogger(__name__)


class PlanExecutor:
    def __init__(
        self, registry: RetrieverRegistry, evidence_validator: EvidenceValidator | None = None
    ) -> None:
        self.registry = registry
        self.evidence_validator = evidence_validator or EvidenceValidator()

    def execute(self, plan: RetrievalPlan) -> ExecutionReport:
        results: list[StepResult] = []
        by_id: dict[str, StepResult] = {}

        for step in plan.steps:
            if step.unsupported_reason:
                result = StepResult(
                    step_id=step.step_id,
                    capability=step.capability,
                    outcome=StepOutcome.UNAVAILABLE,
                    message=step.unsupported_reason,
                    coverage_complete=False,
                    coverage_note=step.unsupported_reason,
                )
                results.append(result)
                by_id[step.step_id] = result
                continue
            blocked_by = [dependency for dependency in step.depends_on if dependency not in by_id]
            empty_by = [
                dependency
                for dependency in step.depends_on
                if dependency in by_id and by_id[dependency].outcome == StepOutcome.EMPTY
            ]
            unavailable_by = [
                dependency
                for dependency in step.depends_on
                if dependency in by_id
                and by_id[dependency].outcome
                in {StepOutcome.UNAVAILABLE, StepOutcome.SKIPPED, StepOutcome.FAILED}
            ]
            blocked_by.extend(unavailable_by)
            if empty_by and not blocked_by:
                upstream_coverage = [by_id[dependency].coverage_complete for dependency in empty_by]
                result = StepResult(
                    step_id=step.step_id,
                    capability=step.capability,
                    outcome=StepOutcome.EMPTY,
                    message=f"선행 단계 결과가 0건입니다: {', '.join(empty_by)}",
                    coverage_complete=all(item is True for item in upstream_coverage),
                    coverage_note="선행 단계의 0건 결과를 전파했습니다.",
                    total_hits=0,
                )
            else:
                retriever = self.registry.get(step.capability)
                if retriever is None:
                    result = StepResult(
                        step_id=step.step_id,
                        capability=step.capability,
                        outcome=StepOutcome.UNAVAILABLE,
                        message=f"{step.capability.value} retriever가 등록되지 않았습니다.",
                    )
                elif blocked_by:
                    result = StepResult(
                        step_id=step.step_id,
                        capability=step.capability,
                        outcome=StepOutcome.SKIPPED,
                        message=f"선행 단계 미완료: {', '.join(blocked_by)}",
                    )
                else:
                    result = self._retrieve(step, retriever, by_id)
            results.append(result)
            by_id[step.step_id] = result

        return ExecutionReport(plan=plan, step_results=results)

    def _retrieve(self, step, retriever, by_id: dict[str, StepResult]) -> StepResult:
        try:
            retrieved = retriever.retrieve(
                step,
                RetrievalContext(prior_results=dict(by_id)),
            )
            batch = (
                retrieved
                if isinstance(retrieved, RetrievalBatch)
                else RetrievalBatch(
                    evidence=list(retrieved),
                    coverage_complete=None,
                    coverage_note="Legacy retriever가 coverage를 명시하지 않았습니다.",
                )
            )
            evidence, invalid = self.evidence_validator.validate(
                step, batch.evidence, prior_results=dict(by_id)
            )
            coverage_complete = batch.coverage_complete
            coverage_note = batch.coverage_note
            if invalid:
                coverage_complete = False
                rejected = f"유효하지 않은 근거 {len(invalid)}건을 제외했습니다."
                coverage_note = f"{coverage_note} {rejected}".strip()
            return StepResult(
                step_id=step.step_id,
                capability=step.capability,
                outcome=StepOutcome.SUCCESS if evidence else StepOutcome.EMPTY,
                evidence=evidence,
                message=f"근거 {len(evidence)}건" if evidence else "검색 결과 0건",
                coverage_complete=coverage_complete,
                coverage_note=coverage_note,
                coverage_by_field=dict(batch.coverage_by_field),
                total_hits=batch.total_hits,
                truncated=batch.truncated,
                ambiguous=batch.ambiguous,
            )
        except Exception as exc:  # the boundary converts adapter failures into evidence state
            log_failure(logger, exc, stage="retrieve", step_id=step.step_id)
            return StepResult(
                step_id=step.step_id,
                capability=step.capability,
                outcome=StepOutcome.FAILED,
                message=f"retrieval failed: {error_type_name(exc)}",
            )
