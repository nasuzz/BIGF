# 금융상품 온톨로지와 피드백 반영

금융상품 5개 TTL에 대한 기존 피드백과 이관 전 miraeasset-bigf PR #68의 재리뷰를 반영했다. 이 PR은 스키마·오프라인 검증의 수정이며 DB→RDF 적재, API 호출 시 자동 검증, 운영 검색의 OWL 추론 구현은 포함하지 않는다.

## 파일과 로딩

- `common.ttl`: 상품·기업·증권 계층과 관계, 공통 위험등급·자산군·Fund/ETF 속성·SHACL, 테마 이력·근거 문서·상장 정보.
- `bond_kr.ttl`: 프로젝트의 국내채권 정규화 등급·수치 속성 및 검증.
- `etf_kr.ttl`: 국내 ETF 원화 순자산 및 검증.
- `etf_gl.ttl`: 해외 ETF 원통화 자산·가격·거래량 및 검증.
- `fund_pub.ttl`: 시스템의 비상장 공모펀드 클래스 상품 속성 및 검증.

검증은 5개 파일을 명시적으로 함께 읽는다. `.example` IRI는 식별자이며 다운로드 주소가 아니다. 루트 `ontology.ttl`은 실행 코드의 별도 스냅샷으로, 이 5개 파일의 합본이 아니다.

## 피드백 반영 결과

| 피드백 | 판단과 이번 처리 |
| --- | --- |
| holds(ETF, Organization) 예시 모순 | 타당. 아래 예시를 증권·발행사 경유 경로로 명시했다. 실제 편입과 회사 수준 노출을 구분한다. 지적된 외부 문서 9장 원문은 미확인이다. |
| Organization↔Security 다리 부재 | TTL에는 이미 isIssuedBy/ issues와 Issuer 하위 관계가 있다. 새 관계를 중복 추가하지 않고 예시와 조회 테스트를 추가했다. 실제 발행사 매핑 데이터는 별도 필요하다. |
| subsidiaryOf 전이성 | 직접 관계를 유지하고 전이성 미선언을 회귀 검사한다. 문서가 전이 관계로 설명하면 그 설명을 고쳐야 한다. |
| risk_label 문자열과 riskGrade 정수 | 아래 변환 계약을 명시하고 텍스트·범위 밖 값·복수 등급을 거부하도록 검사한다. |
| 1년 수익률 이름 불일치 | common의 Fund 속성 returnOneYearPct로 통합했다. 기존 두 속성을 이용한 데이터는 명시적으로 거부해 마이그레이션을 요구한다. |
| ETF / PublicFund 배타성 | PublicFund를 프로젝트의 PUBLIC_FUND_CLASS, 즉 비상장 공모펀드 클래스 상품으로 한정했다. ETF와 OWL 배타 선언 및 SHACL 검사를 추가했다. 일반적인 공모펀드 전체의 법적 정의를 뜻하지 않는다. |
| 네이밍·순자산 명칭 혼용 | 1년 수익률은 통일했다. 순자산은 원화·원통화 및 원본 단위 확인이 필요하므로 netAssetsKRW, netAssets, nativeAUM의 구분을 유지한다. |
| 개별 ETF 파일의 공통 속성 | volatility, leverageFactor, strategyText를 common으로 옮겼다. 국내·해외 공통으로 적용된다. |
| 채권 수치·등급 검증 누락(P2) | BondShape에 creditRating 허용 목록, couponRate/purchaseYield의 decimal, remainingDays의 nonNegativeInteger 검사와 단일 값 제약을 추가했다. |
| 단일 지표의 복수 값 통과(P2) | 위험등급·순자산·수익률·채권 수치·ETF 수치와 asOfDate에 maxCount 1을 적용했다. |
| 검증 재현 코드 부재(P3) | 검증 CLI와 정상·오류 데이터를 포함한 회귀 테스트를 추가했다. |

`rdfs:range`는 SHACL datatype 검사를 대신하지 않는다. OWL FunctionalProperty도 단순 DB 유일성 검사와 동일하지 않으므로 데이터의 값 개수는 SHACL로 별도 검사한다.

## 회사·증권·ETF 경로

다음은 가상 데이터이며 실제 기업 관계나 편입 사실을 주장하지 않는다.

```turtle
@prefix fp: <https://miraeasset.example/ontology/> .
@prefix ex: <https://example.test/> .
ex:parent a fp:Organization ; fp:hasSubsidiary ex:child .
ex:child a fp:Issuer .
ex:stock a fp:Constituent ; fp:isIssuedBy ex:child .
ex:etf a fp:DomesticETF ; fp:productId "example" ;
    fp:currency "KRW" ; fp:holds ex:stock .
```

```sparql
PREFIX fp: <https://miraeasset.example/ontology/>
PREFIX ex: <https://example.test/>
SELECT DISTINCT ?etf WHERE {
  ex:parent fp:hasSubsidiary ?company .
  ?security fp:isIssuedBy ?company .
  ?etf a fp:DomesticETF ; fp:holds ?security .
}
```

`hasExposureTo`는 회사 수준 노출이며 특정 증권 보유의 증거가 아니다. 회사명만으로 증권 ID나 holds를 생성하지 않는다. 원천에 subsidiaryOf만 저장됐다면 역방향 질의나 별도 역관계 처리가 필요하다. OWL 선언만으로 모든 SPARQL 엔진이 자동 추론하지 않는다. 검증에는 증권과 발행사 등 필요한 타입을 명시한다.

## 값과 스냅샷 변환 계약

하나의 검증 데이터 그래프는 한 시점의 상품 스냅샷으로 취급한다. 단일 지표는 최대 한 값이며, 알려진 기준일은 asOfDate에 최대 한 번 기록한다. 기준일 자체가 미확인이면 임의 날짜를 만들지 않는다. 상품에 서로 다른 날짜의 수치들을 병렬로 붙이지 않는다. 이력은 시점별 그래프를 따로 검증하거나 향후 값·기준일·출처를 묶는 Observation 모델을 도입해야 한다. 현재 maxCount만으로 원천 기준일의 정확성까지 입증하지는 않는다.

- **1년 수익률:** `docs/STRUCTURED_QUERY_CONTRACT.md`의 canonical `return_1y_pct` → `fp:returnOneYearPct` (xsd:decimal). 12.5는 12.5%를 뜻한다. 같은 후행 1년 기간·배당 반영 등 계산 기준·기준일임을 원천에서 확인해야 한다. 원본 비율 0.125를 12.5로 바꾸는 것은 원본 단위가 비율로 확인된 경우에만 한다. 연초 이후 수익률이나 연환산 수익률을 임의로 대체하지 않는다.
- **이전 수익률 속성:** `oneYearReturn`, `returnOneYear`는 활성 속성 정의에서 제거했다. `LegacyReturnShape`가 두 이름의 사용을 거부한다. 단위·산정 기준 확인 후 명시적으로 새 속성에 옮긴다. 해당 속성들에 equivalentProperty만 붙여 ETF/PublicFund 동시 추론을 만들지 않는다. 별칭 추론이 꺼진 검증/질의에서도 동일하게 동작하도록 설계했다.
- **위험등급:** `core.fund_class.risk_grade`는 text이며 검색 뷰 risk_label로 노출된다. `높은위험(2등급)` → 정수 2로 변환하되, 명시된 하나의 등급과 1~6 범위를 확인한다. 결측·모호함·범위 밖 값은 등급을 생성하지 않고 원문과 변환 상태(unknown/invalid)를 적재 감사 기록에 남긴다. 이 PR에는 전체 DB→RDF 변환기는 없다.
- **채권 신용등급:** 허용 목록은 `b_agent/postgres_gateway.py`의 `_CREDIT_RATINGS`에 있는 프로젝트의 정규화된 장기등급 20개와 일치한다. 테스트에서 두 목록의 일치를 확인한다. 원천 표기 AA0는 기존 코드와 테스트에서 확인되며 정규화 시 AA로 매핑한다. 허용 목록을 모든 평가기관·단기등급에 적용 가능한 보편 목록이라고 가정하지 않는다. 다른 체계·미등급·미확인 값은 canonical creditRating에 억지로 넣지 말고 원문과 상태(unrated/unknown/unsupported)를 별도 적재 감사 기록에 보존한다. 실제 원천 미등급 표기의 전체 목록은 미확인이므로 임의 매핑하지 않는다.
- **채권 수치:** couponRate/purchaseYield는 xsd:decimal, remainingDays는 xsd:nonNegativeInteger다. `확인불가` 같은 문자열을 수치 속성에 넣지 않는다. 원문·미확인 상태는 감사 기록에 남기고 수치를 생략한다. 음수 수익률을 임의로 금지하지 않으며 0과 결측을 구분한다.
- **순자산:** 국내 ETF netAssetsKRW는 원화 기준이다. 해외 nativeAUM은 currency의 원통화 기준으로, 환율·기준일 없이 비교하지 않는다. PublicFund netAssets는 원본 단위와 통화를 확인해 기록한다. 명칭만으로 단위 변환을 가정하지 않는다.
- **판매 가능 여부:** 누락은 미확인이며 false가 아니다. 명시적 true/false만 boolean으로 기록한다.

## 재현 가능한 오프라인 검증

저장소 루트에서 개발용 가상환경으로 실행한다. 의존성은 선택 설치이며 서버 필수 의존성에 추가하지 않았다.

```sh
python -m pip install -e ".[ontology-validation]"
python scripts/validate_ontology.py
python -m unittest tests.test_ontology_schema -v
```

실제 Turtle 스냅샷을 검사하려면:

```sh
python scripts/validate_ontology.py --data /absolute/path/products.ttl
```

검증기는 5개 파일만 명시적으로 로드하고 `shacl_graph`와 `ont_graph`를 지정한다. `inference="none"`, `do_owl_imports=False`, `meta_shacl=True`로 검사한다. 성공은 종료 코드 0, 데이터/스키마 오류는 1, 검증 의존성 누락은 2다. 입력 파일이 없으면 스키마 검증만 수행한 것으로 표시한다. 일반 unittest 탐색에서는 의존성 누락 시 이 테스트를 건너뛴다. 따라서 TTL 검증에는 위 CLI와 테스트를 함께 실행해야 한다. 전용 GitHub Actions는 선택 의존성을 설치하고 CLI가 성공한 뒤 테스트를 실행한다.

테스트에는 리뷰의 잘못된 채권 값을 각각 독립적으로 거부하는 사례, 복수 위험등급·AUM, 모든 단일 수치 속성의 문자열/중복, 원래의 회사/증권·통화 제약, 정상 경로 SPARQL, 공통 수익률로 인한 잘못된 클래스 추론 방지가 포함된다. 추론 테스트는 별도 그래프에만 OWL RL을 적용하며 기본 SHACL 검증 설정을 바꾸지 않는다.

## 과제 안내문 대조 후 보완

과제설명서의 데이터 범위(5쪽)와 예시 질의(12쪽), 기술세션의 의미 기반 검색 구조를 기준으로 다음을 보완했다. 예시 슬라이드의 위험등급 1~5를 데이터 규칙으로 복사하지 않고 프로젝트의 1~6 체계를 유지한다.

- **공통 위험등급:** riskGrade의 domain과 SHACL을 FinancialProduct로 옮겼다. 채권·국내 ETF·공모펀드 등 원천 등급이 있는 상품에 사용할 수 있다. 해외 상품에 확인되지 않은 등급을 생성하지 않는다. 채권 신용등급 creditRating과는 별개다.
- **투자자산군:** AssetType/hasAssetType 및 주식형·채권형·단기금융·혼합형 개념을 추가했다. 목록은 폐쇄형 허용 목록이 아니며 원천의 다른 자산군도 AssetType으로 명시해 확장할 수 있다. MMF 등 원본 상품 분류와 자산군의 대응은 실제 데이터 사전 확인 후 매핑한다. 혼합 자산을 고려해 복수 자산군을 허용한다.
- **테마 연결 이력:** ThemeAssociation 레코드 하나가 상품(concernsProduct), 테마(associationTheme), 근거로 확인된 연결 날짜(linkedOn), 근거 문서(supportedBy)를 묶는다. linkedOn은 추출 작업일이나 문서 발행일로 임의 대체하지 않는다. 최초 편입일이나 종료일까지 입증하는 필드도 아니다. 날짜를 확인할 수 없으면 날짜 없는 investsInTheme 관계만 사용할 수 있지만 기간 질의의 근거로 삼지 않는다.
- **근거 문서:** sourceIdentifier는 필수이며 원문 식별자를 보존한다. sourceLocation은 페이지·행·URL 등 위치, documentDate는 문서 기준일, riskFactorText는 문서의 위험요인 내용을 담는다. 위험등급 숫자와 위험요인 서술을 구분한다. describedBy 또는 supportedBy로 명시적 Document에 연결한다. 원문 확인·권한·출처 우선순위는 적재/검색 계층에서 별도 적용해야 한다.
- **상장 국가:** ListingMarket의 marketCountryCode는 ISO 3166-1 alpha-2 형식의 두 자리 대문자로 기록한다. SHACL 정규식은 형식만 검사하며 실제 국가 코드 유효성은 매핑 단계에서 확인한다. hasInvestmentRegion과 다른 개념이다. isListedOn은 공통 상품 속성으로 옮겨 국내 ETF와 증권에도 쓸 수 있게 했다.
- **상장 자회사:** ListingObservation이 증권/ETF(listedProduct), 시장(listingMarket), 확인 기준일(asOfDate), 근거(supportedBy)를 묶는다. 회사 자체를 listedProduct로 넣지 않는다. 발행사로 연결된 증권의 해당 시점 상장 관측을 확인한다. 과거 관측이 현재 상장을 보장하지 않으며, 날짜 없는 isListedOn만으로 과거 시점의 상장 여부를 추정하지 않는다.

ThemeAssociation과 ListingObservation의 각 레코드는 독립적인 날짜를 가진다. 상품 자체의 asOfDate/maxCount 1과 충돌하지 않고 이력 레코드를 여러 개 보존할 수 있다. 이 구조가 직접 자회사/보유 관계의 유효기간까지 모두 모델링하는 것은 아니다. 해당 관계의 시점 일치도 검색 시 별도 확인해야 한다.

추론을 끈 검증에서는 데이터가 참조하는 자산군·테마 개념도 `a fp:AssetType` / `a fp:Theme` 타입을 입력 그래프에 명시한다. 스키마에 선언된 개념이라고 입력 검증기가 자동으로 타입을 보충한다고 가정하지 않는다.

가상 데이터 전체는 `tests/fixtures/ontology/guide_examples.ttl`에 있다. 실제 기업 관계·보유종목 사실을 주장하지 않는다. 다음 질의도 회귀 테스트에 포함한다.

```sparql
PREFIX fp: <https://miraeasset.example/ontology/>
# 미국에 상장된 주식형 해외 ETF. 투자 지역이 중국이어도 조건을 만족할 수 있다.
SELECT ?etf WHERE {
  ?etf a fp:ForeignETF ; fp:hasAssetType fp:asset_equity ; fp:isListedOn ?market .
  ?market fp:marketCountryCode "US" .
}
```

```sparql
PREFIX fp: <https://miraeasset.example/ontology/>
PREFIX xsd: <http://www.w3.org/2001/XMLSchema#>
# 예시 기준일 2026-07-11의 최근 6개월. 실제 조회는 확정된 기준일로 기간을 계산한다.
SELECT DISTINCT ?product WHERE {
  ?event a fp:ThemeAssociation ; fp:concernsProduct ?product ;
    fp:associationTheme fp:theme_space_aerospace ; fp:linkedOn ?date ; fp:supportedBy ?source .
  FILTER(?date >= "2026-01-11"^^xsd:date && ?date <= "2026-07-11"^^xsd:date)
}
```

관측 레코드에서 현재 investsInTheme/isListedOn 관계를 자동 추론하지 않는다. 이력의 증거와 현재 상태를 혼동하지 않도록 질의에서 명시적으로 선택한다. 레코드 추가는 실제 이력 데이터 확보나 서버 API 구현 완료를 뜻하지 않는다.

## 통합 전 남은 범위 — 초안 유지 이유

이관된 저장소의 초기 스냅샷 `aad41d6` 기준의 `b_agent/ontology.py`는 여전히 `holds: Fund → Organization`이며 이 스키마는 `Fund → Security`다. **루트 ontology.ttl과 이 5개를 그대로 합치면 안 된다.** 양쪽 range가 함께 적용되어 기업/증권 배타성과 충돌할 수 있다. 실행 검색은 기업 단위 관계를 사용하므로 관계 정의만 바꾸는 수정도 하지 않는다.

`common.ttl`의 `BEGIN GENERATED CONCEPTS`는 로컬 확장 코드에서 생성된 출처를 나타낸다. 해당 생성 코드와 자동 동기화 검증기는 아직 이 PR에 없다. 현재 main에서 같은 생성 명령을 재현할 수 있다는 뜻은 아니다.

운영 통합 전에는 실행 코드·루트 TTL·개념 생성기를 함께 동기화하고, 실제 종목 식별자 및 발행사 연결 근거를 확보하며, 원천 단위/등급 계약과 스냅샷 적재를 검증해야 한다. 외부 설명 문서 9장, 운영 DB 전량, API 연동은 이번 검증 대상이 아니다. 제안서에는 “5개 온톨로지와 SHACL 검증 규칙·오프라인 회귀 테스트”까지 기술한다.
