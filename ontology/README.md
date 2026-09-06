# 금융상품 온톨로지 5개와 피드백 검토

이 PR은 로컬에서 작성한 5개 TTL을 변경 없이 제안하고, 받은 피드백의 타당성과 후속 작업을 기록한다. 스키마 수정안을 모두 구현한 PR은 아니다. DB 데이터, API 동작, 운영 검색 결과를 변경하지 않는다.

## 파일과 적용 범위

- `common.ttl`: 상품·기업·증권 계층, 관계, 공통 SHACL, 테마·지역 개념.
- `bond_kr.ttl`: 국내채권 속성과 검증 규칙.
- `etf_kr.ttl`: 국내 ETF 속성과 검증 규칙, 일부 ETF 공통 속성.
- `etf_gl.ttl`: 해외 ETF 속성과 검증 규칙, 일부 ETF 공통 속성.
- `fund_pub.ttl`: 공모펀드 속성과 검증 규칙.

5개 파일을 함께 로컬에서 읽어 사용한다. `.example` IRI는 식별자이며 다운로드 주소가 아니다. 원격 imports를 요청하지 않는다. 이 파일들은 스키마와 분류 개념을 포함하며, 실제 상품·보유종목 인스턴스 데이터나 DB→RDF 변환기는 포함하지 않는다.

## 최신 main과의 통합 차이 — 초안 상태의 이유

비교 기준: `af4809765a658fa2f0c827ca84a7d06c919c5200`.

현재 `b_agent/ontology.py`의 `RELATION_PREDICATES`는 `holds: Fund → Organization`을 정의하지만, 이 PR의 TTL은 `holds: Fund → Security`를 정의한다. main에는 로컬 작업본의 확장 클래스 계층, 발행사 연결, `render_concepts_ttl()`도 아직 없다. 루트 `ontology.ttl`은 실행 코드의 별도 스냅샷이며 이 5개 파일을 모두 합친 결과가 아니다.

**루트 TTL과 이 5개 파일을 그대로 합치면 안 된다.** `holds`의 range가 Organization과 Security 양쪽으로 선언되어 둘 다 적용되고, 실제 보유 대상에 배타 클래스 충돌을 일으킬 수 있다. 실행 코드·검색 데이터의 회사/증권 구분과 루트 TTL의 동기화가 통합 전 후속 작업이다. 로컬의 다른 미커밋 변경은 이 PR에 포함하지 않았다.

`common.ttl`의 `BEGIN GENERATED CONCEPTS` 마커는 로컬 확장 코드에서 생성된 출처를 나타낸다. 현재 main만으로 해당 생성 과정을 재현하거나 자동 동기화할 수 있다는 뜻은 아니다. 생성 코드와 검증기의 통합도 후속 작업이다.

## 피드백별 검토

### 1. 대표 예시 `holds(DomesticETF, Organization)` — 타당, 문서 원문 확인 필요

`holds`의 domain은 Fund, range는 Security다. Security는 FinancialProduct의 하위이고 FinancialProduct와 Organization은 배타적이다. 따라서 기업 노드를 보유종목으로 연결하는 예시는 이 TTL과 모순된다.

실제 편입을 표현하려면 `ETF → holds → Security → isIssuedBy → Issuer`로 수정한다. 회사 수준 노출만 확인됐다면 `hasExposureTo`를 사용하되, 이것이 특정 증권 보유의 증거가 되지는 않는다. 피드백에 언급된 설명 문서 9장 원문은 확인하지 못했으므로, 그 문서에 실제로 해당 예시가 있는지는 미확인이다.

### 2. Organization↔Security 다리 부재 — 5개 TTL 기준으로는 사실과 다름

`common.ttl`에 `isIssuedBy: Security → Issuer`, 역관계 `issues: Issuer → Security`, `Issuer ⊂ Organization`이 이미 있다. 필요한 것은 새 관계보다 실제 종목과 발행사의 식별·연결 데이터다. 회사명만으로 증권이나 보유 관계를 만들어서는 안 된다. main 실행 코드에 이 관계가 아직 없는 문제와 TTL에 관계가 없는 문제는 구분해야 한다.

다음은 가상 예시이며 실제 기업 관계나 편입 사실을 주장하지 않는다.

```turtle
@prefix fp: <https://miraeasset.example/ontology/> .
@prefix ex: <https://example.test/> .
ex:parent a fp:Organization ; fp:hasSubsidiary ex:child .
ex:child a fp:Issuer .
ex:stock a fp:Constituent ; fp:isIssuedBy ex:child .
ex:etf a fp:DomesticETF ; fp:productId "example" ;
    fp:currency "KRW" ; fp:holds ex:stock .
```

명시된 방향의 트리플만으로 다음 질의가 가능하다.

```sparql
PREFIX fp: <https://miraeasset.example/ontology/>
PREFIX ex: <https://example.test/>
SELECT DISTINCT ?etf WHERE {
  ex:parent fp:hasSubsidiary ?company .
  ?security fp:isIssuedBy ?company .
  ?etf a fp:DomesticETF ; fp:holds ?security .
}
```

데이터에 `subsidiaryOf`만 저장했다면 역방향 질의나 역관계 추론 처리가 필요하다. OWL 역관계 선언만으로 모든 SPARQL 엔진이 자동 추론하는 것은 아니다. SHACL 검증에는 발행사 등 필요한 타입을 명시한다.

### 3. subsidiaryOf 전이성 — 문서가 TTL에 맞춰져야 함

현재 TTL은 직접 자회사 관계이며 `owl:TransitiveProperty`를 선언하지 않는다. 여러 단계의 기업 관계는 각 관계의 근거와 기준일을 확인해 탐색해야 한다. 설명 문서가 전이 관계라고 한다면 그 설명을 수정한다. 전이성 추가는 이번 대응책이 아니다.

### 4. riskGrade와 risk_label — 변환 계약 보완 필요

TTL의 riskGrade는 정수이며 SHACL에서 1~6을 검사한다. main의 `b_agent/postgres_gateway.py`는 risk_grade를 risk_label에 매핑한다. 실제 운영 데이터 전체는 확인하지 않았다.

원본이 `높은위험(2등급)` 같은 문자열이라면 2로 변환하는 명시적 계약이 필요하다. 원문을 보존하고, 결측·모호한 값·범위 밖 값은 임의 등급으로 채우지 않고 변환 오류나 미확인으로 처리해야 한다. 이 PR은 변환기를 구현하지 않는다.

### 5. oneYearReturn / returnOneYear — 통합 필요, 단순 equivalentProperty 추가는 부적절

두 속성의 domain은 각각 ETF와 PublicFund다. 현 상태에서 equivalentProperty만 추가하면 한 속성의 사용이 두 클래스의 타입 추론으로 이어질 수 있다. 여기에 클래스 배타 선언까지 추가하면 충돌한다.

`docs/STRUCTURED_QUERY_CONTRACT.md`와 실행 코드에서는 return_1y와 one_year_return을 canonical return_1y_pct로 통일한다. RDF에서도 단위(퍼센트/비율), 기준일, 산정 방식이 같은지 확인한 뒤 Fund 수준의 공통 속성과 기존 속성의 domain을 함께 정리해야 한다. 이름이 비슷하다는 이유만으로 경제적 의미의 동일성을 가정하지 않는다.

### 6. ETF / PublicFund 배타성 — 클래스 범위 확정 후 적용

시스템 product_type의 배타성과 개념 클래스의 배타성은 별개다. PublicFund를 시스템의 비상장 공모펀드 클래스 상품으로 한정할 것인지 먼저 명시해야 한다. 그 의미가 확정되면 OWL 배타 선언과 SHACL 검사를 함께 추가한다. 현재 TTL에는 둘의 배타 선언이 없다.

### 7. 네이밍 혼용 — 단위 보존을 전제로 정리

수익률 명명은 통일할 가치가 있다. netAssetsKRW와 netAssets는 통화·단위·대상 범위를 확인한 뒤 공통화해야 한다. 원화라는 정보를 이름만 정리하다 잃으면 안 된다.

### 8. 개별 ETF 파일에 공통 속성 배치 — 타당한 모듈화 개선

oneYearReturn, volatility, leverageFactor, strategyText의 domain은 ETF이므로 공통 모듈로 옮기는 것이 명확하다. 현재는 5개 파일을 함께 로드해야 한다. 루트 ontology.ttl에 모든 정의가 합쳐진다는 설명은 현재 저장소 구조와 다르다.

## 유지할 설계와 후속 우선순위

회사와 증권의 구분, 판매 가능 값의 결측을 false로 간주하지 않는 원칙, 국내/해외 통화 구분, 위험등급 SHACL 범위는 유지한다. FunctionalProperty는 OWL에서 단순 DB 유일성 검사와 같지 않으므로 실제 값 개수 검증은 SHACL과 함께 다룬다.

1. 실행 코드와 TTL의 holds 의미를 통일하고 대표 예시를 수정한다.
2. 실제 증권 ID·발행사 연결 데이터 및 조회 경로를 확인한다.
3. 수익률의 단위·산정 기준과 공통 속성을 정리한다.
4. 위험등급 변환 계약, PublicFund 범위, 공통 속성 위치와 생성 코드 동기화를 정리한다.

## 검증 범위

RDFLib 7.6.0 / pySHACL 0.40.1로 5개 TTL을 함께 파싱하고 SHACL 자체 유효성을 검사한다. 자동 domain/range 추론과 원격 imports는 끈다. 검증 결과는 PR 본문에 기록한다. 운영 DB 전체, 실행 코드와의 동기화, API 연동이 검증됐다는 의미는 아니다.
