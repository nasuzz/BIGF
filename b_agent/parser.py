from __future__ import annotations

import re
from collections.abc import Iterable

from .models import (
    Filter,
    Intent,
    Operator,
    ProductType,
    QueryUnderstanding,
    SortDirection,
    SortSpec,
    TemporalConstraint,
)
from .ontology import REGION_ALIASES, THEME_ALIASES
from .schema import all_product_types

PRODUCT_BRANDS = (
    "KODEX",
    "TIGER",
    "RISE",
    "ACE",
    "SOL",
    "HANARO",
    "PLUS",
    "KOSEF",
    "KIWOOM",
    "키움",
    "KBSTAR",
    "TIMEFOLIO",
)

IDENTIFIER_STOPWORDS = {"ETF", "ETN", "AUM", "KRW", "USD", "NAV", "RAG", "LLM", "AI"}
IDENTIFIER_STOPWORDS.update(PRODUCT_BRANDS)
IDENTIFIER_STOPWORDS.update(
    {
        "AAA",
        "AA",
        "A",
        "BBB",
        "BB",
        "B",
        "CCC",
        "CC",
        "C",
        "D",
    }
)

# KRX short codes are six alphanumeric characters. Newer codes may mix digits
# and letters (for example, ``0007F0``), while legacy codes are six digits.
# Requiring at least one digit keeps ordinary six-letter words out of the
# identifier list.
KRX_SHORT_CODE_PATTERN = re.compile(
    r"(?<![A-Z0-9])(?=[A-Z0-9]{6}(?![A-Z0-9]))(?=[A-Z0-9]*\d)"
    r"[A-Z0-9]{6}(?![A-Z0-9])"
)

PRODUCT_QUERY_SUFFIX_PATTERN = re.compile(
    r"(?:의\s*|\s+)(?:투자\s*전략|운용\s*전략|위험\s*등급)"
    r"(?:이랑|랑|과|와|은|는|이|가|을|를)?(?=\s|[?!.,。！？]|$)",
    re.I,
)


class RuleBasedQuestionAnalyzer:
    """Deterministic baseline. An LLM parser may enrich, but never bypass, this contract."""

    def analyze(self, question: str) -> QueryUnderstanding:
        normalized = self._normalize(question)
        lowered = normalized.lower()
        intents = self._intents(lowered)
        product_mentions = self._product_mentions(normalized)
        product_types = self._product_types(lowered)
        if not product_types and any(
            mention.upper().startswith(f"{brand} ") or mention.upper() == brand
            for mention in product_mentions
            for brand in PRODUCT_BRANDS
        ):
            product_types = [ProductType.DOMESTIC_ETF]
        non_product_text = _without_product_mentions(normalized, product_mentions)
        non_product_lowered = non_product_text.lower()
        identifiers = self._identifiers(non_product_text)
        filters = self._filters(non_product_text, non_product_lowered, product_types)
        themes = self._themes(non_product_lowered)
        entities = self._entities(non_product_text, themes)
        sorts = self._sorts(lowered, product_types)
        temporal = self._temporal(lowered)
        limit = self._limit(lowered)

        warnings: list[str] = []
        if not product_types:
            product_types = all_product_types()
            warnings.append("상품 유형이 명시되지 않아 전체 상품군으로 해석했습니다.")
        if not intents:
            intents = [Intent.SEARCH]
        if Intent.RELATION in intents and not entities:
            warnings.append("기업 관계 질문이지만 기준 기업을 명확히 추출하지 못했습니다.")
        if Intent.RECENT_EVENT in intents and temporal is None:
            temporal = TemporalConstraint(relative_months=6)
            warnings.append("최근/트렌드의 기간이 없어 기본값인 최근 6개월을 적용했습니다.")

        return QueryUnderstanding(
            question=question.strip(),
            product_types=product_types,
            intents=_unique(intents),
            product_mentions=_unique(product_mentions),
            identifiers=_unique(identifiers),
            entities=_unique(entities),
            themes=_unique(themes),
            filters=_unique_filters(filters),
            sorts=_unique_sorts(sorts),
            temporal=temporal,
            limit=limit,
            warnings=warnings,
        )

    @staticmethod
    def _normalize(question: str) -> str:
        return re.sub(r"\s+", " ", question).strip()

    @staticmethod
    def _product_types(text: str) -> list[ProductType]:
        result: list[ProductType] = []
        has_etf = "etf" in text
        if has_etf:
            if re.search(r"국내\s*상장|한국\s*상장", text):
                result.append(ProductType.DOMESTIC_ETF)
            elif re.search(r"해외\s*상장|미국\s*상장", text):
                result.append(ProductType.FOREIGN_ETF)
            elif re.search(r"국내\s*etf|한국\s*etf", text):
                result.append(ProductType.DOMESTIC_ETF)
            elif re.search(r"해외\s*etf|미국\s*etf|글로벌\s*etf", text):
                result.append(ProductType.FOREIGN_ETF)
            if not result:
                result.extend([ProductType.DOMESTIC_ETF, ProductType.FOREIGN_ETF])
        if "채권" in text and not has_etf:
            result.append(ProductType.BOND)
        if ("공모펀드" in text or "공모 펀드" in text or "펀드" in text) and not has_etf:
            result.append(ProductType.PUBLIC_FUND)
        return _unique(result)

    @staticmethod
    def _intents(text: str) -> list[Intent]:
        result: list[Intent] = [Intent.SEARCH]
        if re.search(r"추천|골라|선정", text):
            result.append(Intent.RECOMMEND)
        if re.search(r"이상|이하|초과|미만|가능|조건|순서|정렬", text):
            result.append(Intent.FILTER)
        if re.search(r"비교|차이", text):
            result.append(Intent.COMPARE)
        if re.search(r"편입|보유종목|보유 종목|구성종목|구성 종목|담고 있", text):
            result.append(Intent.HOLDINGS)
        if re.search(r"자회사|모회사|계열사|종속회사|관계사", text):
            result.append(Intent.RELATION)
        if re.search(r"전략|운용방식|운용 방식", text):
            result.append(Intent.STRATEGY)
        if re.search(
            r"위험\s*등급|위험요인|위험 요인|리스크|위험을 설명|위험 설명", text
        ):
            result.append(Intent.RISK)
        if re.search(r"구조|조성방식|조성 방식", text):
            result.append(Intent.STRUCTURE)
        if re.search(r"리서치|애널리스트|투자의견|투자 의견|목표주가", text):
            result.append(Intent.RESEARCH)
        period_metric = re.search(
            r"최근\s*\d+\s*(?:개월|년)\s*(?:수익률|성과|변동성|거래량)", text
        )
        if re.search(r"트렌드|동향|최근 이슈", text) or (
            re.search(r"최근\s*\d+\s*(?:개월|년)", text) and not period_metric
        ):
            result.append(Intent.RECENT_EVENT)
        return result

    def _filters(
        self, original: str, text: str, product_types: list[ProductType]
    ) -> list[Filter]:
        result: list[Filter] = []
        if re.search(r"원화|krw", text):
            result.append(Filter("currency", Operator.EQ, "KRW", source_text="원화/KRW"))
        if re.search(r"매수\s*가능|판매\s*(?:중|가능)|거래\s*가능", text):
            result.append(Filter("sale_available", Operator.EQ, True, source_text="판매 또는 거래 가능"))

        rating = re.search(r"\b(AAA|AA[+-]?|A[+-]?|BBB[+-]?|BB[+-]?|B[+-]?)\s*(이상|이하)", original, re.I)
        if rating:
            operator = Operator.CREDIT_AT_LEAST if rating.group(2) == "이상" else Operator.CREDIT_AT_MOST
            result.append(
                Filter("credit_rating", operator, rating.group(1).upper(), source_text=rating.group(0))
            )

        amount_pattern = re.compile(
            r"(?:순자산|aum)[^\d]{0,8}([\d,.]+)\s*(조|억|만)?\s*원?\s*(이상|이하|초과|미만)?",
            re.I,
        )
        amount = amount_pattern.search(original)
        if amount:
            value = _korean_money_to_won(amount.group(1), amount.group(2))
            result.append(
                Filter(
                    "net_assets",
                    _comparison_operator(amount.group(3) or "이상"),
                    value,
                    unit="KRW",
                    source_text=amount.group(0),
                )
            )

        numeric_fields = {
            "fee_rate": ("보수율", "총보수", "수수료"),
            "one_year_return": ("1년 수익률", "연간 수익률"),
            "volatility": ("변동성",),
        }
        if product_types == [ProductType.BOND]:
            numeric_fields.pop("one_year_return")
            numeric_fields["yield"] = ("매수수익률", "채권수익률", "수익률", "금리")
        for logical_field, aliases in numeric_fields.items():
            aliases_pattern = "|".join(re.escape(alias) for alias in aliases)
            match = re.search(
                rf"(?:{aliases_pattern})[^\d+\-]{{0,8}}([+-]?[\d,.]+)\s*%\s*(이상|이하|초과|미만)?",
                original,
                re.I,
            )
            if match:
                result.append(
                    Filter(
                        logical_field,
                        _comparison_operator(match.group(2) or "이상"),
                        float(match.group(1).replace(",", "")),
                        unit="%",
                        source_text=match.group(0),
                    )
                )

        explicit_listing = re.search(r"(?:국내|한국|해외|미국)\s*상장", text, re.I)
        region_text = re.sub(r"(?:국내|한국|해외|미국)\s*상장", " ", text, flags=re.I)
        if not explicit_listing:
            region_text = re.sub(
                r"(?:국내|한국|해외|미국|글로벌)\s*etf", " ", region_text, flags=re.I
            )
        for canonical, aliases in REGION_ALIASES.items():
            if any(alias in region_text for alias in aliases):
                if canonical == "한국" and "채권" in text:
                    continue
                result.append(
                    Filter("investment_region", Operator.CONTAINS, canonical, source_text=canonical)
                )
                break
        return result

    @staticmethod
    def _themes(text: str) -> list[str]:
        return [
            theme
            for theme, aliases in THEME_ALIASES.items()
            if any(_contains_alias(text, alias) for alias in aliases)
        ]

    @staticmethod
    def _entities(
        text: str, themes: Iterable[str], product_mentions: Iterable[str] = ()
    ) -> list[str]:
        del themes  # themes are intentionally kept separate from named entities.
        entities: list[str] = []
        for quoted in re.findall(r"[\"'“”‘’]([^\"'“”‘’]{2,50})[\"'“”‘’]", text):
            entities.append(quoted.strip())

        entity_text = _without_product_mentions(text, product_mentions)

        relation = re.search(
            r"(?P<anchor>[가-힣A-Za-z][가-힣A-Za-z0-9&.\-]{1,30}?)(?:의)?\s+"
            r"(?:(?:상장|비상장)\s+)?(?:자회사|모회사|계열사|종속회사|관계사)",
            entity_text,
            re.I,
        )
        if relation:
            entities.append(relation.group("anchor").strip())

        holding = re.search(
            r"([가-힣A-Za-z][가-힣A-Za-z0-9&.\-]{1,40}?)(?:을|를|이|가)?\s+"
            r"(?:편입(?:한|된|하고|하는)?|보유(?:한|된|하고|하는)?|담고)",
            entity_text,
            re.I,
        )
        if holding:
            candidate = holding.group(1).strip()
            candidate = re.sub(r"^(?:중국|미국|한국|국내|해외)\s+", "", candidate, flags=re.I)
            candidate = re.sub(
                r"(?:의\s*)?(?:자회사|모회사|계열사|종속회사|관계사)$", "", candidate
            ).strip()
            if candidate and candidate not in {"종목", "기업", "자회사", "주식"}:
                entities.append(candidate)
        return entities

    @staticmethod
    def _product_mentions(text: str) -> list[str]:
        brand_pattern = "|".join(PRODUCT_BRANDS)
        starts = list(re.finditer(rf"\b(?:{brand_pattern})\b", text, re.I))
        mentions: list[str] = []
        for index, start in enumerate(starts):
            end = starts[index + 1].start() if index + 1 < len(starts) else len(text)
            candidate = text[start.start() : end]
            candidate = re.split(r"(?:과|와)\s*$|,|비교|추천|알려|설명|찾아|보여", candidate, maxsplit=1)[0]
            candidate = PRODUCT_QUERY_SUFFIX_PATTERN.split(candidate, maxsplit=1)[0]
            candidate = re.sub(r"(?:과|와)\s*$", "", candidate).strip()
            candidate = re.sub(
                r"(?:의\s*)?(?:보유\s*종목|구성\s*종목)(?:을|를|은|는|이|가)?$",
                "",
                candidate,
                flags=re.I,
            ).strip()
            candidate = re.sub(r"\s+ETF(?:를|을|은|는|의)?$", "", candidate, flags=re.I).strip()
            candidate = re.sub(r"(?:을|를|은|는)$", "", candidate).strip()
            if candidate:
                mentions.append(candidate)
        for match in re.finditer(
            r"(?<![가-힣A-Za-z0-9])([가-힣A-Za-z0-9][가-힣A-Za-z0-9&.\- ]{1,30}?펀드)",
            text,
            re.I,
        ):
            candidate = re.sub(
                r"^(?:그|이|저|해당|특정|공모)\s*", "", match.group(1).strip(), flags=re.I
            )
            candidate = re.sub(r"^(?:최근\s*\d+\s*(?:개월|년)\s*)", "", candidate)
            candidate = re.sub(
                r"^(?:(?:매수|거래|판매)\s*가능한|판매\s*중인)\s*", "", candidate,
            )
            if candidate.replace(" ", "") not in {"펀드", "공모펀드", "국내펀드", "해외펀드"}:
                mentions.append(candidate)
        return mentions

    @staticmethod
    def _identifiers(text: str) -> list[str]:
        normalized = text.upper()
        identifiers = re.findall(
            r"(?<![A-Z0-9])[A-Z]{2}[A-Z0-9]{9}[0-9](?![A-Z0-9])", normalized
        )
        identifiers.extend(KRX_SHORT_CODE_PATTERN.findall(normalized))
        for match in re.finditer(
            r"(?<![A-Z0-9&])([A-Z]{1,5}(?:\.[A-Z])?)(?![A-Z0-9&])", normalized
        ):
            value = match.group(1)
            base = value.split(".", 1)[0]
            if base not in IDENTIFIER_STOPWORDS and not re.fullmatch(
                r"(?:AAA|AA|A|BBB|BB|B|CCC|CC|C|D)[+\-]?", base
            ):
                identifiers.append(value)
        return identifiers

    @staticmethod
    def _sorts(text: str, product_types: list[ProductType]) -> list[SortSpec]:
        direction: SortDirection | None = None
        if re.search(r"큰(?:\s*순)?|높은(?:\s*순)?|많은(?:\s*순)?|내림차순", text):
            direction = SortDirection.DESC
        elif re.search(r"작은(?:\s*순)?|낮은(?:\s*순)?|적은(?:\s*순)?|오름차순", text):
            direction = SortDirection.ASC
        if direction is None:
            return []
        if "순자산" in text or "aum" in text:
            return [SortSpec("net_assets", direction)]
        if "수익률" in text or "금리" in text:
            period_field = _return_period_field(text)
            if period_field:
                return [SortSpec(period_field, direction)]
            field = "yield" if product_types == [ProductType.BOND] else "one_year_return"
            return [SortSpec(field, direction)]
        if "보수" in text or "수수료" in text:
            return [SortSpec("fee_rate", direction)]
        if "변동성" in text:
            return [SortSpec("volatility", direction)]
        return []

    @staticmethod
    def _temporal(text: str) -> TemporalConstraint | None:
        if re.search(
            r"최근\s*\d+\s*(?:개월|년)\s*(?:수익률|성과|변동성|거래량)", text
        ):
            return None
        match = re.search(r"최근\s*(\d+)\s*개월", text)
        if match:
            return TemporalConstraint(relative_months=max(1, min(int(match.group(1)), 120)))
        match = re.search(r"최근\s*(\d+)\s*년", text)
        if match:
            return TemporalConstraint(relative_months=max(12, min(int(match.group(1)) * 12, 120)))
        return None

    @staticmethod
    def _limit(text: str) -> int:
        match = re.search(r"(?:상위|최대)?\s*(\d{1,3})\s*개(?!월)", text)
        return max(1, min(int(match.group(1)), 100)) if match else 10


def _comparison_operator(word: str) -> Operator:
    return {
        "이상": Operator.GTE,
        "이하": Operator.LTE,
        "초과": Operator.GT,
        "미만": Operator.LT,
    }.get(word, Operator.GTE)


def _without_product_mentions(text: str, product_mentions: Iterable[str]) -> str:
    result = text
    for mention in product_mentions:
        result = re.sub(
            rf"(?<![가-힣A-Za-z0-9]){re.escape(mention)}(?:의)?",
            " ",
            result,
            flags=re.I,
        )
    return re.sub(r"\s+", " ", result).strip()


def _korean_money_to_won(number: str, unit: str | None) -> int:
    multipliers = {None: 1, "만": 10_000, "억": 100_000_000, "조": 1_000_000_000_000}
    return int(float(number.replace(",", "")) * multipliers[unit])


def _contains_alias(text: str, alias: str) -> bool:
    if re.fullmatch(r"[a-z0-9.]+", alias) and len(alias.replace(".", "")) <= 3:
        return re.search(rf"(?<![a-z0-9]){re.escape(alias)}(?![a-z0-9])", text) is not None
    return alias in text


def _return_period_field(text: str) -> str | None:
    month = re.search(r"(?:최근\s*)?(1|3|6|18)\s*개월\s*수익률", text)
    if month:
        return f"return_{month.group(1)}m"
    year = re.search(r"(?:최근\s*)?(1|2|3|5)\s*년\s*수익률", text)
    if year:
        return f"return_{year.group(1)}y"
    return None


def _unique(values: Iterable):
    return list(dict.fromkeys(values))


def _unique_filters(values: Iterable[Filter]) -> list[Filter]:
    seen: set[tuple] = set()
    result: list[Filter] = []
    for item in values:
        key = (item.field, item.operator, str(item.value), item.unit)
        if key not in seen:
            seen.add(key)
            result.append(item)
    return result


def _unique_sorts(values: Iterable[SortSpec]) -> list[SortSpec]:
    return list({(item.field, item.direction): item for item in values}.values())
