from __future__ import annotations

import unittest

from b_agent.answering import AnswerComposer
from b_agent.hcx import HcxAuthenticationError, HcxTimeoutError
from b_agent.models import (
    AnswerDecision,
    AnswerStatus,
    Capability,
    Evidence,
    ExecutionReport,
    ProductType,
    QueryUnderstanding,
    ReasonCode,
    RetrievalPlan,
    StepOutcome,
    StepResult,
    ValueProvenance,
)


class FakeLLM:
    def __init__(self, response="", error=None):
        self.response = response
        self.error = error
        self.prompts = []

    def generate(self, prompt):
        self.prompts.append(prompt)
        if self.error is not None:
            raise self.error
        return self.response


def _report():
    plan = RetrievalPlan(
        version="test",
        understanding=QueryUnderstanding(
            question="원화 AA- 이상 채권 중 수익률이 높은 상품을 알려줘"
        ),
        steps=[],
    )
    return ExecutionReport(
        plan=plan,
        step_results=[
            StepResult(
                step_id="structured_search",
                capability=Capability.STRUCTURED_SEARCH,
                outcome=StepOutcome.SUCCESS,
            )
        ],
    )


def _evidence():
    return Evidence(
        evidence_id="structured:bond:B2:1",
        capability=Capability.STRUCTURED_SEARCH,
        source_id="bond_master",
        content=(
            '{"name":"테스트 우량채권 2","yield":4.2,'
            '"credit_rating":"AA-","as_of_date":"2026-08-31"}'
        ),
        product_id="B2",
        product_type=ProductType.BOND,
        as_of_date="2026-08-31",
        source_ref="bond.xlsx#row=3",
        structured={
            "name": "테스트 우량채권 2",
            "yield": 4.2,
            "credit_rating": "AA-",
            "as_of_date": "2026-08-31",
        },
    )


def _field_aware_evidence():
    return Evidence(
        evidence_id="structured:bond:FIELD:1",
        capability=Capability.STRUCTURED_SEARCH,
        source_id="bond_master",
        content="{}",
        product_id="FIELD",
        product_type=ProductType.BOND,
        structured={
            "name": "테스트채권",
            "ticker": "REAL",
            "isin": "KR0000000001",
            "currency_code": "KRW",
            "net_asset_amount": 100_000_000,
            "return_1y_pct": 0.2,
            "expense_ratio_pct": 0.15,
        },
    )


def _decision(status=AnswerStatus.COMPLETE, reason_codes=None):
    return AnswerDecision(
        status=status,
        reason_codes=list(reason_codes or []),
        message="검색 근거를 확인했습니다.",
    )


class HcxAnswerComposerTest(unittest.TestCase):
    def test_grounded_answer_is_accepted_and_prompt_contains_evidence_packet(self):
        llm = FakeLLM(
            "테스트 우량채권 2의 수익률은 4.2%이며 기준일은 "
            "2026-08-31입니다. [structured:bond:B2:1]"
        )

        payload = AnswerComposer(llm).compose(
            "Q-HCX", _report(), _decision(), [_evidence()]
        )

        self.assertIn("수익률은 4.2%", payload.answer)
        self.assertIn("[structured:bond:B2:1]", payload.answer)
        self.assertEqual(len(llm.prompts), 1)
        self.assertIn('"source_ref":"bond.xlsx#row=3"', llm.prompts[0])
        self.assertIn('"evidence_id":"structured:bond:B2:1"', llm.prompts[0])
        self.assertNotIn("내부 추론", payload.think_trace)

    def test_unanswerable_and_error_do_not_call_hcx(self):
        for status in (AnswerStatus.UNANSWERABLE, AnswerStatus.ERROR):
            with self.subTest(status=status):
                llm = FakeLLM(error=AssertionError("must not be called"))

                payload = AnswerComposer(llm).compose(
                    "Q-HCX", _report(), _decision(status), []
                )

                self.assertEqual(llm.prompts, [])
                self.assertIn("검증해 답할 수 없습니다", payload.answer)

    def test_no_match_does_not_call_hcx(self):
        llm = FakeLLM(error=AssertionError("must not be called"))

        payload = AnswerComposer(llm).compose(
            "Q-HCX",
            _report(),
            _decision(reason_codes=[ReasonCode.NO_MATCH]),
            [],
        )

        self.assertEqual(llm.prompts, [])
        self.assertIn("일치하는 상품이 없습니다", payload.answer)

    def test_empty_evidence_never_calls_hcx_even_if_status_is_complete(self):
        llm = FakeLLM(error=AssertionError("must not be called"))

        payload = AnswerComposer(llm).compose(
            "Q-HCX", _report(), _decision(), []
        )

        self.assertEqual(llm.prompts, [])
        self.assertIn("현재 데이터에서 다음 결과", payload.answer)

    def test_timeout_and_authentication_failure_use_deterministic_fallback(self):
        errors = [
            HcxTimeoutError("HCX request timed out"),
            HcxAuthenticationError("HCX authentication failed"),
        ]
        for error in errors:
            with self.subTest(error=type(error).__name__):
                llm = FakeLLM(error=error)

                payload = AnswerComposer(llm).compose(
                    "Q-HCX", _report(), _decision(), [_evidence()]
                )

                self.assertIn("현재 데이터에서 다음 결과", payload.answer)
                self.assertIn("테스트 우량채권 2", payload.answer)
                self.assertNotIn(str(error), payload.answer)

    def test_unsupported_number_date_product_and_citation_are_rejected(self):
        responses = [
            "테스트 우량채권 2의 수익률은 99%입니다. [structured:bond:B2:1]",
            "테스트 우량채권 2의 기준일은 2025-01-01입니다. [structured:bond:B2:1]",
            "가짜 우량채권의 수익률은 4.2%입니다. [structured:bond:B2:1]",
            "테스트 우량채권 2의 수익률은 4.2%입니다. [unknown:evidence]",
            "테스트 우량채권 2의 수익률은 4.2%입니다.",
        ]

        for response in responses:
            with self.subTest(response=response):
                payload = AnswerComposer(FakeLLM(response)).compose(
                    "Q-HCX", _report(), _decision(), [_evidence()]
                )

                self.assertIn("현재 데이터에서 다음 결과", payload.answer)
                self.assertNotEqual(payload.answer, response)

    def test_same_number_from_another_field_cannot_ground_return_claim(self):
        evidence = _field_aware_evidence()
        evidence.structured["net_asset_amount"] = 5
        response = (
            "테스트채권의 수익률은 5%입니다. "
            "[structured:bond:FIELD:1]"
        )

        payload = AnswerComposer(FakeLLM(response)).compose(
            "Q-HCX", _report(), _decision(), [evidence]
        )

        self.assertIn("현재 데이터에서 다음 결과", payload.answer)
        self.assertNotEqual(payload.answer, response)

    def test_explicit_return_period_cannot_use_another_period(self):
        evidence = _field_aware_evidence()
        evidence.structured["return_1m_pct"] = 5
        response = (
            "테스트채권의 1년 수익률은 5%입니다. "
            "[structured:bond:FIELD:1]"
        )

        payload = AnswerComposer(FakeLLM(response)).compose(
            "Q-HCX", _report(), _decision(), [evidence]
        )

        self.assertIn("현재 데이터에서 다음 결과", payload.answer)
        self.assertNotEqual(payload.answer, response)

    def test_all_product_names_in_a_cited_sentence_must_be_grounded(self):
        response = (
            "테스트채권과 가짜채권 ETF를 추천합니다. "
            "[structured:bond:FIELD:1]"
        )

        payload = AnswerComposer(FakeLLM(response)).compose(
            "Q-HCX", _report(), _decision(), [_field_aware_evidence()]
        )

        self.assertIn("현재 데이터에서 다음 결과", payload.answer)
        self.assertNotEqual(payload.answer, response)

    def test_correct_fields_and_converted_units_remain_grounded(self):
        response = (
            "테스트채권의 순자산은 1억원이고 총보수는 15bps이며 "
            "1년 수익률은 0.2%입니다. [structured:bond:FIELD:1]"
        )

        payload = AnswerComposer(FakeLLM(response)).compose(
            "Q-HCX", _report(), _decision(), [_field_aware_evidence()]
        )

        self.assertEqual(payload.answer, response)

    def test_korean_money_units_are_compared_in_base_currency(self):
        amounts = ("100,000,000원", "10,000만원", "1억원", "0.0001조원")

        for amount in amounts:
            with self.subTest(amount=amount):
                response = (
                    f"테스트채권의 순자산은 {amount}입니다. "
                    "[structured:bond:FIELD:1]"
                )
                payload = AnswerComposer(FakeLLM(response)).compose(
                    "Q-HCX", _report(), _decision(), [_field_aware_evidence()]
                )

                self.assertEqual(payload.answer, response)

    def test_each_sentence_requires_citations_for_its_own_product(self):
        first = _field_aware_evidence()
        first.structured["name"] = "첫번째 ETF"
        second = _field_aware_evidence()
        second.evidence_id = "structured:bond:FIELD:2"
        second.product_id = "FIELD-2"
        second.structured["name"] = "두번째 ETF"
        response = (
            "첫번째 ETF를 확인했습니다. [structured:bond:FIELD:1] "
            "두번째 ETF도 확인했습니다. [structured:bond:FIELD:1]"
        )

        payload = AnswerComposer(FakeLLM(response)).compose(
            "Q-HCX", _report(), _decision(), [first, second]
        )

        self.assertIn("현재 데이터에서 다음 결과", payload.answer)
        self.assertNotEqual(payload.answer, response)

    def test_unsupported_ticker_and_isin_are_rejected(self):
        responses = [
            "테스트채권의 티커는 FAKE입니다. [structured:bond:FIELD:1]",
            "테스트채권의 ISIN은 US0000000000입니다. [structured:bond:FIELD:1]",
        ]

        for response in responses:
            with self.subTest(response=response):
                payload = AnswerComposer(FakeLLM(response)).compose(
                    "Q-HCX", _report(), _decision(), [_field_aware_evidence()]
                )

                self.assertIn("현재 데이터에서 다음 결과", payload.answer)
                self.assertNotEqual(payload.answer, response)

    def test_supported_ticker_and_isin_are_accepted(self):
        response = (
            "테스트채권의 티커는 REAL이고 ISIN은 KR0000000001입니다. "
            "[structured:bond:FIELD:1]"
        )

        payload = AnswerComposer(FakeLLM(response)).compose(
            "Q-HCX", _report(), _decision(), [_field_aware_evidence()]
        )

        self.assertEqual(payload.answer, response)

    def test_ineligible_filled_value_is_hidden_from_hcx_and_fallback(self):
        evidence = _evidence()
        evidence.structured["name"] = "추정 상품명"
        evidence.value_provenance = [
            ValueProvenance(
                field_name="name",
                fill_type="estimated",
                evidence_eligible=False,
                was_missing=True,
            )
        ]
        llm = FakeLLM(error=HcxTimeoutError("HCX request timed out"))

        payload = AnswerComposer(llm).compose(
            "Q-HCX", _report(), _decision(), [evidence]
        )

        self.assertNotIn("추정 상품명", llm.prompts[0])
        self.assertNotIn("추정 상품명", payload.answer)

    def test_unlisted_source_is_rejected_even_when_it_extends_real_filename(self):
        response = (
            "테스트 우량채권 2의 출처는 bond.xlsx-가짜.pdf입니다. "
            "[structured:bond:B2:1]"
        )

        payload = AnswerComposer(FakeLLM(response)).compose(
            "Q-HCX", _report(), _decision(), [_evidence()]
        )

        self.assertIn("현재 데이터에서 다음 결과", payload.answer)
        self.assertNotEqual(payload.answer, response)


if __name__ == "__main__":
    unittest.main()
