import unittest

from b_agent.models import (
    Filter,
    Operator,
    ProductType,
    QueryUnderstanding,
    RetrievalPlan,
    SortDirection,
    SortSpec,
)
from b_agent.validation import PlanValidator


MIXED_WARNING = "국내·해외 ETF 순자산은 통화와 기준이 달라"
MIXED_BLOCKER = "국내·해외 ETF 순자산은 통화·단위·기준일을 정규화하기 전에는"
FOREIGN_WARNING = "해외 ETF 원본 순자산은 종목별 표시 통화 기준"
FOREIGN_BLOCKER = "해외 ETF 순자산은 원 통화가 섞여 있어"


class MixedEtfAumValidationTest(unittest.TestCase):
    def setUp(self):
        self.validator = PlanValidator()

    @staticmethod
    def _plan(product_types, *, aum_filter=False, aum_sort=False):
        understanding = QueryUnderstanding(
            question="상품 비교",
            product_types=list(product_types),
            filters=(
                [Filter("net_assets", Operator.GTE, 100_000_000_000)]
                if aum_filter
                else []
            ),
            sorts=(
                [SortSpec("net_assets", SortDirection.DESC)]
                if aum_sort
                else []
            ),
        )
        return RetrievalPlan(version="test", understanding=understanding, steps=[])

    def _messages(self, plan):
        return self.validator.validate(plan), self.validator.blocking_issues(plan)

    def test_exact_domestic_and_foreign_etfs_are_blocked_for_aum_filter(self):
        plan = self._plan(
            [ProductType.DOMESTIC_ETF, ProductType.FOREIGN_ETF],
            aum_filter=True,
        )

        warnings, blockers = self._messages(plan)

        self.assertTrue(any(MIXED_WARNING in item for item in warnings))
        self.assertTrue(any(MIXED_BLOCKER in item for item in blockers))

    def test_additional_product_type_does_not_bypass_mixed_etf_aum_block(self):
        plan = self._plan(
            [
                ProductType.DOMESTIC_ETF,
                ProductType.FOREIGN_ETF,
                ProductType.BOND,
            ],
            aum_sort=True,
        )

        warnings, blockers = self._messages(plan)

        self.assertTrue(any(MIXED_WARNING in item for item in warnings))
        self.assertTrue(any(MIXED_BLOCKER in item for item in blockers))

    def test_domestic_etf_alone_has_no_mixed_etf_aum_message(self):
        plan = self._plan([ProductType.DOMESTIC_ETF], aum_sort=True)

        warnings, blockers = self._messages(plan)

        self.assertFalse(any(MIXED_WARNING in item for item in warnings))
        self.assertFalse(any(MIXED_BLOCKER in item for item in blockers))

    def test_foreign_etf_alone_keeps_foreign_currency_block_only(self):
        plan = self._plan([ProductType.FOREIGN_ETF], aum_filter=True)

        warnings, blockers = self._messages(plan)

        self.assertFalse(any(MIXED_WARNING in item for item in warnings))
        self.assertFalse(any(MIXED_BLOCKER in item for item in blockers))
        self.assertTrue(any(FOREIGN_WARNING in item for item in warnings))
        self.assertTrue(any(FOREIGN_BLOCKER in item for item in blockers))

    def test_mixed_etfs_without_aum_have_no_currency_comparison_messages(self):
        plan = self._plan([ProductType.DOMESTIC_ETF, ProductType.FOREIGN_ETF])

        warnings, blockers = self._messages(plan)

        self.assertFalse(any(MIXED_WARNING in item for item in warnings))
        self.assertFalse(any(MIXED_BLOCKER in item for item in blockers))
        self.assertFalse(any(FOREIGN_WARNING in item for item in warnings))
        self.assertFalse(any(FOREIGN_BLOCKER in item for item in blockers))


if __name__ == "__main__":
    unittest.main()
