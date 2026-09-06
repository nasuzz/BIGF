"""PR #39: accept ordinary product qualifiers without accepting invented names."""

import unittest

from b_agent.answering import AnswerComposer, GroundingValidationError, _validate_grounded_answer
from b_agent.models import ValueProvenance
from tests.test_hcx_answering import FakeLLM, _decision, _evidence, _report


class GroundedProductDescriptorsTest(unittest.TestCase):
    def assert_accepted(self, response, evidence):
        _validate_grounded_answer(response, evidence)
        payload = AnswerComposer(FakeLLM(response)).compose(
            "issue-39", _report(), _decision(), evidence,
        )
        self.assertEqual(payload.answer, response)  # Do not let fallback hide rejection.

    def test_rating_currency_and_comparison_descriptors_are_not_product_names(self):
        evidence = _evidence()
        evidence.structured["currency"] = "KRW"
        for phrase in (
            "AA- 채권", "AA- 등급 채권", "AA- 등급의 채권",
            "AA- 이상 채권", "AA- 이상의 채권", "AA-이상 채권",
            "AA- 이하 채권", "AA- 등급 이하의 채권",
            "AA 미만 채권", "A+ 초과 채권",
            "AA 미만의 채권", "A+ 초과의 채권",
            "원화 채권", "원화 표시 채권", "원화 표시된 채권",
        ):
            with self.subTest(phrase=phrase):
                response = f"테스트 우량채권 2는 {phrase}입니다. [structured:bond:B2:1]"
                self.assert_accepted(response, [evidence])

    def test_foreign_currency_descriptor_is_not_a_product_name(self):
        evidence = _evidence()
        evidence.structured["currency"] = "USD"
        self.assert_accepted(
            "테스트 우량채권 2는 외화 표시 채권입니다. [structured:bond:B2:1]", [evidence],
        )

    def test_unknown_products_still_fail_beside_allowed_descriptors(self):
        for phrase in ("가짜채권", "가짜 ETF", "원화 가짜채권", "가짜등급 채권", "가짜원화 ETF"):
            with self.subTest(phrase=phrase):
                response = f"테스트 우량채권 2와 {phrase}를 확인했습니다. [structured:bond:B2:1]"
                with self.assertRaises(GroundingValidationError):
                    _validate_grounded_answer(response, [_evidence()])

    def test_ratings_must_be_supported_by_the_cited_evidence(self):
        for phrase in ("AAA 등급 채권", "AA 이상 채권", "A+ 이하 채권", "AA- 미만 채권", "AA- 초과 채권"):
            with self.subTest(phrase=phrase):
                response = f"테스트 우량채권 2는 {phrase}입니다. [structured:bond:B2:1]"
                with self.assertRaisesRegex(GroundingValidationError, "credit rating"):
                    _validate_grounded_answer(response, [_evidence()])

    def test_missing_and_ineligible_ratings_are_not_grounding_sources(self):
        for ineligible in (False, True):
            with self.subTest(ineligible=ineligible):
                evidence = _evidence()
                if ineligible:
                    evidence.value_provenance = [ValueProvenance(
                        field_name="credit_rating", fill_type="estimated", evidence_eligible=False,
                    )]
                else:
                    evidence.structured.pop("credit_rating")
                with self.assertRaisesRegex(GroundingValidationError, "credit rating"):
                    _validate_grounded_answer(
                        "테스트 우량채권 2는 AA- 등급 채권입니다. [structured:bond:B2:1]", [evidence],
                    )

    def test_rating_cannot_be_borrowed_from_an_uncited_product(self):
        first = _evidence()
        second = _evidence()
        second.evidence_id = "other"
        second.structured["name"] = "다른 상품"
        second.structured["credit_rating"] = "AAA"
        with self.assertRaisesRegex(GroundingValidationError, "credit rating"):
            _validate_grounded_answer(
                "테스트 우량채권 2는 AAA 등급 채권입니다. [structured:bond:B2:1]", [first, second],
            )

    def test_neutral_rating_zero_matches_canonical_rating(self):
        evidence = _evidence()
        evidence.structured["credit_rating"] = "AA0"
        for grade in ("AA", "AA0"):
            self.assert_accepted(
                f"테스트 우량채권 2는 {grade} 등급 채권입니다. [structured:bond:B2:1]", [evidence],
            )

    def test_currency_qualifier_requires_cited_eligible_currency(self):
        for currency, phrase in (("KRW", "외화"), ("USD", "원화"), (None, "원화")):
            with self.subTest(currency=currency, phrase=phrase):
                evidence = _evidence()
                evidence.structured["currency"] = currency
                with self.assertRaisesRegex(GroundingValidationError, "currency qualifier"):
                    _validate_grounded_answer(
                        f"테스트 우량채권 2는 {phrase} 채권입니다. [structured:bond:B2:1]", [evidence],
                    )
        evidence = _evidence()
        evidence.structured["currency"] = "KRW"
        evidence.value_provenance = [ValueProvenance(
            field_name="currency", fill_type="estimated", evidence_eligible=False,
        )]
        with self.assertRaisesRegex(GroundingValidationError, "currency qualifier"):
            _validate_grounded_answer(
                "테스트 우량채권 2는 원화 채권입니다. [structured:bond:B2:1]", [evidence],
            )


if __name__ == "__main__":
    unittest.main()
