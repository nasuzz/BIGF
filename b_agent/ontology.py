from __future__ import annotations

import re
from dataclasses import dataclass


BASE_IRI = "https://miraeasset.example/ontology/"

THEME_ALIASES: dict[str, tuple[str, ...]] = {
    "space_aerospace": ("우주항공", "항공우주", "우주 산업", "우주", "aerospace", "space"),
    "ai": ("인공지능", "생성형 ai", "artificial intelligence", "ai"),
    "semiconductor": ("반도체", "semiconductor", "chip"),
    "robotics": ("로봇", "robotics", "robot"),
    "defense": ("방산", "국방", "defense"),
    "secondary_battery": ("2차전지", "이차전지", "secondary battery", "battery"),
    "nuclear": ("원전", "원자력", "nuclear"),
    "bio_healthcare": ("바이오", "헬스케어", "biotech", "healthcare"),
    "climate_green": ("친환경", "기후", "탄소", "재생에너지", "clean energy", "climate"),
}

REGION_ALIASES: dict[str, tuple[str, ...]] = {
    "한국": ("한국", "국내", "korea"),
    "중국": ("중국", "china", "chinese"),
    "미국": ("미국", "usa", "u.s.", "united states"),
    "일본": ("일본", "japan"),
    "유럽": ("유럽", "europe"),
    "글로벌": ("글로벌", "전세계", "global", "world"),
}

# ``REGION_ALIASES`` describes expressions accepted from a question, while
# these are exact labels observed or commonly emitted by the product masters.
# Keeping the two vocabularies separate prevents a query for the United States
# from matching a value such as ``Global Ex US`` merely because it contains
# the letters "US".
REGION_DATABASE_VALUES: dict[str, tuple[str, ...]] = {
    "한국": (
        "한국",
        "Korea",
        "South Korea",
        "Republic of Korea",
        "Korea, Republic of",
        "KR",
        "KOR",
    ),
    "중국": (
        "중국",
        "China",
        "Mainland China",
        "People's Republic of China",
        "CN",
        "CHN",
    ),
    "미국": (
        "미국",
        "United States of America",
        "United States",
        "USA",
        "US",
        "U.S.",
    ),
    "일본": ("일본", "Japan", "JP", "JPN"),
    "유럽": ("유럽", "Europe", "European Union", "EU"),
    "글로벌": (
        "글로벌",
        "전세계",
        "Global",
        "Global Ex US",
        "Global ex-USA",
        "World",
        "Worldwide",
    ),
}

PRODUCT_CLASSES = {
    "bond": "Bond",
    "public_fund": "PublicFund",
    "domestic_etf": "DomesticETF",
    "foreign_etf": "ForeignETF",
}

RELATION_PREDICATES = {
    "subsidiaryOf": ("Organization", "Organization"),
    "affiliateOf": ("Organization", "Organization"),
    "holds": ("Fund", "Organization"),
}


# Runtime query labels are not necessarily RDF property names. Keep legacy
# callers unchanged while exporting company-level holdings with precise semantics.
RDF_RELATION_NAMES = {name: name for name in RELATION_PREDICATES}
RDF_RELATION_NAMES["holds"] = "hasConstituentCompany"


@dataclass(frozen=True)
class OntologyRegistry:
    base_iri: str = BASE_IRI

    def has_theme(self, value: str) -> bool:
        return value in THEME_ALIASES

    def has_relation(self, value: str) -> bool:
        return value in RELATION_PREDICATES

    def rdf_relation_name(self, runtime_name: str) -> str:
        """Map a supported runtime relation to its RDF local name.

        Mapping is vocabulary-only, not evidence validation. Only verified
        company-level holdings may use the runtime holds mapping. Generic
        company exposure uses hasExposureTo directly; security holdings use
        RDF holds with identified Security nodes.
        """
        return RDF_RELATION_NAMES[runtime_name]

    def render_ttl(self) -> str:
        lines = [
            f"@prefix ma: <{self.base_iri}> .",
            "@prefix owl: <http://www.w3.org/2002/07/owl#> .",
            "@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .",
            "@prefix skos: <http://www.w3.org/2004/02/skos/core#> .",
            "",
            "ma:FinancialProduct a owl:Class .",
            "ma:Fund a owl:Class ; rdfs:subClassOf ma:FinancialProduct .",
            "ma:Organization a owl:Class .",
            "ma:Theme a owl:Class .",
        ]
        for key, class_name in PRODUCT_CLASSES.items():
            parent = "Fund" if "fund" in key or "etf" in key else "FinancialProduct"
            lines.append(
                f'ma:{class_name} a owl:Class ; rdfs:subClassOf ma:{parent} ; skos:notation "{key}" .'
            )
        for predicate, (domain, range_name) in RELATION_PREDICATES.items():
            lines.append(
                f"ma:{self.rdf_relation_name(predicate)} a owl:ObjectProperty ; rdfs:domain ma:{domain} ; rdfs:range ma:{range_name} ."
            )
        for theme, aliases in THEME_ALIASES.items():
            escaped = [alias.replace('"', '\\"') for alias in aliases]
            labels = " ; ".join(
                f'skos:altLabel "{alias}"@{"ko" if re.search(r"[가-힣]", alias) else "en"}'
                for alias in escaped
            )
            lines.append(f'ma:theme_{theme} a ma:Theme ; skos:prefLabel "{theme}" ; {labels} .')
        return "\n".join(lines) + "\n"


DEFAULT_ONTOLOGY = OntologyRegistry()


def canonical_region(value: str) -> str | None:
    """Resolve a question or database region label to the Korean canonical key."""

    normalized = _normalized_region_label(value)
    if not normalized:
        return None
    for canonical, question_aliases in REGION_ALIASES.items():
        candidates = (
            canonical,
            *question_aliases,
            *REGION_DATABASE_VALUES.get(canonical, ()),
        )
        if normalized in {_normalized_region_label(item) for item in candidates}:
            return canonical
    return None


def database_region_values(value: str) -> tuple[str, ...]:
    """Return exact DB labels equivalent to a recognized region expression."""

    canonical = canonical_region(value)
    if canonical is None:
        stripped = str(value or "").strip()
        return (stripped,) if stripped else ()
    return REGION_DATABASE_VALUES[canonical]


def _normalized_region_label(value: str) -> str:
    return re.sub(r"[\s._-]+", "", str(value or "").casefold())
