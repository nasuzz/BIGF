import unittest

from agent.b_adapter import _REASON_CODE_MAP
from b_agent.models import ReasonCode as BReasonCode
from contracts import ReasonCode


class ReasonCodeMappingTest(unittest.TestCase):
    def test_every_b_reason_code_has_an_explicit_contract_mapping(self):
        self.assertEqual(set(BReasonCode), set(_REASON_CODE_MAP))

    def test_missing_data_reasons_remain_distinct(self):
        expected = {
            BReasonCode.HOLDINGS_DATA_MISSING: ReasonCode.HOLDINGS_DATA_MISSING,
            BReasonCode.RELATION_DATA_MISSING: ReasonCode.RELATION_DATA_MISSING,
            BReasonCode.DOCUMENT_DATA_MISSING: ReasonCode.DOCUMENT_DATA_MISSING,
        }

        for source, target in expected.items():
            with self.subTest(source=source):
                self.assertEqual(_REASON_CODE_MAP[source], target)

        self.assertEqual(len(set(expected.values())), len(expected))

    def test_retrieval_error_is_not_collapsed_into_an_answerability_reason(self):
        self.assertEqual(
            _REASON_CODE_MAP[BReasonCode.RETRIEVAL_ERROR],
            ReasonCode.RETRIEVAL_ERROR,
        )


if __name__ == "__main__":
    unittest.main()
