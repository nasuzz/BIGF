from __future__ import annotations

from .models import (
    AnswerDecision,
    AnswerStatus,
    Capability,
    ExecutionReport,
    ReasonCode,
    StepOutcome,
)


MISSING_REASON = {
    Capability.IDENTITY_SEARCH: ReasonCode.AMBIGUOUS_ENTITY,
    Capability.HOLDING_SEARCH: ReasonCode.HOLDINGS_DATA_MISSING,
    Capability.RELATION_SEARCH: ReasonCode.RELATION_DATA_MISSING,
    Capability.DOCUMENT_SEARCH: ReasonCode.DOCUMENT_DATA_MISSING,
    Capability.EVENT_SEARCH: ReasonCode.TEMPORAL_DATA_MISSING,
    Capability.STRUCTURED_SEARCH: ReasonCode.REQUIRED_FIELD_MISSING,
    Capability.KEYWORD_SEARCH: ReasonCode.DOCUMENT_DATA_MISSING,
    Capability.VECTOR_SEARCH: ReasonCode.DOCUMENT_DATA_MISSING,
}

CORE_SELECTION_CAPABILITIES = {
    Capability.IDENTITY_SEARCH,
    Capability.STRUCTURED_SEARCH,
    Capability.HOLDING_SEARCH,
    Capability.RELATION_SEARCH,
    Capability.EVENT_SEARCH,
}


class AnswerabilityPolicy:
    def assess(self, report: ExecutionReport, fused_evidence=None) -> AnswerDecision:
        required_ids = {step.step_id for step in report.plan.steps if step.required}
        required_results = [
            result for result in report.step_results if result.step_id in required_ids
        ]
        failed = [result for result in required_results if result.outcome == StepOutcome.FAILED]
        if failed:
            return AnswerDecision(
                status=AnswerStatus.ERROR,
                reason_codes=[ReasonCode.RETRIEVAL_ERROR],
                message="필수 검색 단계 실행 중 오류가 발생했습니다.",
                failed_steps=[result.step_id for result in failed],
            )

        ambiguous = [result for result in required_results if result.ambiguous]
        if ambiguous:
            return AnswerDecision(
                status=AnswerStatus.UNANSWERABLE,
                reason_codes=[ReasonCode.AMBIGUOUS_ENTITY],
                message="상품명 또는 식별자에 대응하는 후보가 여러 개여서 대상을 확정할 수 없습니다.",
                missing_capabilities=[Capability.IDENTITY_SEARCH],
            )

        missing = [
            result
            for result in required_results
            if result.outcome == StepOutcome.UNAVAILABLE
        ]
        missing_capabilities = list(dict.fromkeys(result.capability for result in missing))
        reason_codes = list(
            dict.fromkeys(
                MISSING_REASON.get(result.capability, ReasonCode.UNSUPPORTED_QUERY)
                for result in missing
            )
        )

        evidence_count = len(report.evidence)
        if missing:
            core_missing = any(item in CORE_SELECTION_CAPABILITIES for item in missing_capabilities)
            status = (
                AnswerStatus.UNANSWERABLE
                if core_missing or evidence_count == 0
                else AnswerStatus.PARTIAL
            )
            return AnswerDecision(
                status=status,
                reason_codes=list(
                    dict.fromkeys([*reason_codes, *report.plan.blocker_reason_codes])
                ),
                message=(
                    "핵심 후보를 검증할 필수 데이터가 없습니다."
                    if status == AnswerStatus.UNANSWERABLE
                    else "기본 결과는 확인했지만 설명에 필요한 일부 근거가 없습니다."
                )
                + (" " + " ".join(report.plan.blockers) if report.plan.blockers else ""),
                missing_capabilities=missing_capabilities,
            )

        incomplete = [
            result
            for result in required_results
            if result.outcome in {StepOutcome.SUCCESS, StepOutcome.EMPTY}
            and result.coverage_complete is not True
        ]
        if incomplete:
            capabilities = list(dict.fromkeys(result.capability for result in incomplete))
            coverage_details = [
                result.coverage_note.strip()
                for result in incomplete
                if result.coverage_note.strip()
            ]
            return AnswerDecision(
                status=AnswerStatus.PARTIAL if evidence_count else AnswerStatus.UNANSWERABLE,
                reason_codes=list(
                    dict.fromkeys(
                        [
                            *(
                        MISSING_REASON.get(item, ReasonCode.REQUIRED_FIELD_MISSING)
                        for item in capabilities
                            ),
                            *report.plan.blocker_reason_codes,
                        ]
                    )
                ),
                message=(
                    "검색은 수행했지만 해당 데이터의 커버리지가 불완전합니다."
                    + (" " + " ".join(coverage_details) if coverage_details else "")
                    + (" " + " ".join(report.plan.blockers) if report.plan.blockers else "")
                ),
                missing_capabilities=capabilities,
            )

        if report.plan.blockers:
            return AnswerDecision(
                status=AnswerStatus.UNANSWERABLE,
                reason_codes=(
                    list(dict.fromkeys(report.plan.blocker_reason_codes))
                    or [ReasonCode.UNSUPPORTED_QUERY]
                ),
                message=" ".join(report.plan.blockers),
            )

        if required_results and all(
            result.outcome == StepOutcome.EMPTY for result in required_results
        ):
            return AnswerDecision(
                status=AnswerStatus.COMPLETE,
                reason_codes=[ReasonCode.NO_MATCH],
                message="필요한 데이터가 준비된 상태에서 조건에 맞는 결과가 0건입니다.",
            )

        empty = [result for result in required_results if result.outcome == StepOutcome.EMPTY]
        if empty:
            core_empty = any(result.capability in CORE_SELECTION_CAPABILITIES for result in empty)
            if core_empty:
                return AnswerDecision(
                    status=AnswerStatus.COMPLETE,
                    reason_codes=[ReasonCode.NO_MATCH],
                    message="필수 후보 검색이 정상 수행되었지만 조건에 맞는 결과가 0건입니다.",
                )
            return AnswerDecision(
                status=AnswerStatus.PARTIAL if evidence_count else AnswerStatus.UNANSWERABLE,
                reason_codes=list(
                    dict.fromkeys(
                        MISSING_REASON.get(result.capability, ReasonCode.REQUIRED_FIELD_MISSING)
                        for result in empty
                    )
                ),
                message="상품 후보는 확인했지만 필요한 설명 근거를 찾지 못했습니다.",
            )

        successful_required = [
            result for result in required_results if result.outcome == StepOutcome.SUCCESS
        ]
        if successful_required:
            if fused_evidence is not None:
                fused_capabilities = {item.capability for item in fused_evidence}
                lost = [
                    result.capability
                    for result in successful_required
                    if result.evidence and result.capability not in fused_capabilities
                ]
                if lost:
                    return AnswerDecision(
                        status=AnswerStatus.PARTIAL,
                        reason_codes=[ReasonCode.REQUIRED_FIELD_MISSING],
                        message="필수 근거 일부가 최종 컨텍스트에 포함되지 못했습니다.",
                        missing_capabilities=list(dict.fromkeys(lost)),
                    )
            return AnswerDecision(
                status=AnswerStatus.COMPLETE,
                reason_codes=[],
                message="필수 검색 단계가 모두 수행되어 답변 근거가 확보되었습니다.",
            )

        return AnswerDecision(
            status=AnswerStatus.UNANSWERABLE,
            reason_codes=[ReasonCode.UNSUPPORTED_QUERY],
            message="답변에 사용할 수 있는 검색 경로를 확정하지 못했습니다.",
        )
