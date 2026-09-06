from __future__ import annotations

from .models import Capability, Intent, ReasonCode, RetrievalPlan
from .ontology import DEFAULT_ONTOLOGY
from .schema import supports_field


class PlanValidationError(ValueError):
    pass


class PlanValidator:
    def validate(self, plan: RetrievalPlan) -> list[str]:
        warnings: list[str] = []
        ids = [step.step_id for step in plan.steps]
        if len(ids) != len(set(ids)):
            raise PlanValidationError("RetrievalPlan의 step_id가 중복되었습니다.")

        positions = {step_id: index for index, step_id in enumerate(ids)}
        for index, step in enumerate(plan.steps):
            for dependency in step.depends_on:
                if dependency not in positions:
                    raise PlanValidationError(f"존재하지 않는 의존 단계: {dependency}")
                if positions[dependency] >= index:
                    raise PlanValidationError(f"선행 단계가 아닌 의존성: {step.step_id}->{dependency}")
            if step.top_k < 1 or step.top_k > 1000:
                raise PlanValidationError(f"top_k 범위 오류: {step.step_id}")
            for relation in step.query.get("relation_constraints") or []:
                predicate = relation.get("predicate")
                if predicate and not DEFAULT_ONTOLOGY.has_relation(predicate):
                    raise PlanValidationError(f"온톨로지에 없는 관계 predicate: {predicate}")

        for theme in plan.understanding.themes:
            if not DEFAULT_ONTOLOGY.has_theme(theme):
                raise PlanValidationError(f"온톨로지에 없는 테마: {theme}")

        for query_filter in plan.understanding.filters:
            supported = [
                product_type
                for product_type in plan.understanding.product_types
                if supports_field(product_type, query_filter.field)
            ]
            if not supported:
                warnings.append(
                    f"선택된 상품군에서 논리 필드 '{query_filter.field}'를 지원하지 않습니다."
                )
            elif len(supported) < len(plan.understanding.product_types):
                names = ", ".join(str(item) for item in supported)
                warnings.append(
                    f"논리 필드 '{query_filter.field}'는 일부 상품군({names})에만 적용됩니다."
                )

        for sort in plan.understanding.sorts:
            supported = [
                product_type
                for product_type in plan.understanding.product_types
                if supports_field(product_type, sort.field)
            ]
            if not supported:
                warnings.append(
                    f"선택된 상품군에서 정렬 필드 '{sort.field}'를 지원하지 않습니다."
                )
            elif len(supported) < len(plan.understanding.product_types):
                names = ", ".join(str(item) for item in supported)
                warnings.append(
                    f"정렬 필드 '{sort.field}'는 일부 상품군({names})에만 적용됩니다."
                )

        product_types = {str(item) for item in plan.understanding.product_types}
        mixed_etf = {"domestic_etf", "foreign_etf"} <= product_types
        uses_aum = any(item.field == "net_assets" for item in plan.understanding.filters) or any(
            item.field == "net_assets" for item in plan.understanding.sorts
        )
        if mixed_etf and uses_aum:
            warnings.append(
                "국내·해외 ETF 순자산은 통화와 기준이 달라 환율·단위 정규화 없이 직접 비교할 수 없습니다."
            )
        if "foreign_etf" in product_types and uses_aum:
            warnings.append(
                "해외 ETF 원본 순자산은 종목별 표시 통화 기준이어서 환산 기준 없이 비교할 수 없습니다."
            )
        if any(
            step.capability == Capability.EVENT_SEARCH for step in plan.steps
        ) and plan.understanding.temporal and not any(
            (step.query.get("temporal") or {}).get("start_date")
            for step in plan.steps
            if step.capability == Capability.EVENT_SEARCH
        ):
            warnings.append("최근 기간의 재현을 위한 데이터 스냅샷 기준일이 지정되지 않았습니다.")
        return warnings

    def blocking_issues(self, plan: RetrievalPlan) -> list[str]:
        issues: list[str] = []
        product_types = {str(item) for item in plan.understanding.product_types}
        mixed_etf = {"domestic_etf", "foreign_etf"} <= product_types
        uses_aum = any(item.field == "net_assets" for item in plan.understanding.filters) or any(
            item.field == "net_assets" for item in plan.understanding.sorts
        )
        if mixed_etf and uses_aum:
            issues.append(
                "국내·해외 ETF 순자산은 통화·단위·기준일을 정규화하기 전에는 하나의 순위로 비교할 수 없습니다."
            )

        if "foreign_etf" in product_types and uses_aum:
            issues.append(
                "해외 ETF 순자산은 원 통화가 섞여 있어 KRW 환산 필드와 동일 기준일 환율이 준비되기 전에는 필터·정렬할 수 없습니다."
            )

        for item in [*plan.understanding.filters, *plan.understanding.sorts]:
            supported = [
                product_type
                for product_type in plan.understanding.product_types
                if supports_field(product_type, item.field)
            ]
            if supported and len(supported) < len(plan.understanding.product_types):
                issues.append(
                    f"'{item.field}' 값이 없는 상품군이 포함되어 전체 상품군 기준 비교·필터를 확정할 수 없습니다."
                )

        understanding = plan.understanding
        if Intent.RELATION in understanding.intents and not understanding.entities:
            issues.append("기업 관계를 조회할 기준 기업이 명확하지 않습니다.")
        if (
            Intent.HOLDINGS in understanding.intents
            and Intent.RELATION not in understanding.intents
            and not understanding.entities
            and not understanding.product_mentions
            and not understanding.identifiers
        ):
            issues.append("편입 여부를 조회할 기업 또는 상품 식별자가 명확하지 않습니다.")
        if any(step.capability == Capability.EVENT_SEARCH for step in plan.steps):
            event = next(
                step for step in plan.steps if step.capability == Capability.EVENT_SEARCH
            )
            temporal = event.query.get("temporal") or {}
            if temporal.get("relative_months") and not temporal.get("start_date"):
                issues.append(
                    "최근 기간을 확정할 데이터 스냅샷 기준일이 없어 재현 가능한 날짜 범위를 만들 수 없습니다."
                )

        meaningful_constraint = bool(
            understanding.filters
            or understanding.sorts
            or understanding.themes
            or understanding.entities
            or understanding.product_mentions
            or understanding.identifiers
            or set(understanding.intents)
            & {
                Intent.STRATEGY,
                Intent.RISK,
                Intent.STRUCTURE,
                Intent.RECENT_EVENT,
                Intent.RESEARCH,
                Intent.HOLDINGS,
                Intent.RELATION,
                Intent.COMPARE,
            }
        )
        if not meaningful_constraint:
            issues.append(
                "상품을 고를 조건·정렬·주제·식별자가 없어 임의의 첫 N개를 답변하지 않습니다."
            )
        return list(dict.fromkeys(issues))

    def blocking_reason_codes(self, plan: RetrievalPlan) -> list[ReasonCode]:
        reasons: list[ReasonCode] = []
        understanding = plan.understanding
        if Intent.RELATION in understanding.intents and not understanding.entities:
            reasons.append(ReasonCode.AMBIGUOUS_ENTITY)
        if (
            Intent.HOLDINGS in understanding.intents
            and Intent.RELATION not in understanding.intents
            and not understanding.entities
            and not understanding.product_mentions
            and not understanding.identifiers
        ):
            reasons.append(ReasonCode.AMBIGUOUS_ENTITY)
        if self.blocking_issues(plan) and not reasons:
            reasons.append(ReasonCode.UNSUPPORTED_QUERY)
        elif len(self.blocking_issues(plan)) > len(reasons):
            reasons.append(ReasonCode.UNSUPPORTED_QUERY)
        return list(dict.fromkeys(reasons))
