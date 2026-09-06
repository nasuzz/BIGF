# 기업 관계 검색 계약

`relation_search`는 온톨로지에 관계 이름이 있다는 사실만으로 답하지 않는다. 실제 관계는
`core.organization_relation`에 원본 출처와 기준일이 있는 행으로 적재하고, ACTIVE 데이터
버전에 연결된 스냅샷만 검색한다.

## 관계 방향

| predicate | source | target |
| --- | --- | --- |
| `subsidiaryOf` | 자회사 | 모회사 |
| `affiliateOf` | 관계의 한쪽 기업 | 관계의 다른 쪽 기업 |

예를 들어 `에코프로비엠 subsidiaryOf 에코프로`는 source가 에코프로비엠, target이
에코프로다. `affiliateOf`는 대칭 의미지만 저장 방향은 원본 행의 방향을 보존한다.
Gateway의 `result_role=opposite`가 anchor 반대쪽 endpoint를 결정한다.

## 적재 필수값

- `snapshot_id`, `raw_row_id`: 원본과 데이터 버전 추적 키
- `predicate`: `subsidiaryOf` 또는 `affiliateOf`
- `source_entity_name`, `target_entity_name`
- `relation_as_of_date`
- `source_ref`: 공시 URL, 파일·시트·행 등 검증 가능한 위치
- `confidence`: 0 이상 1 이하

법인 코드·종목 코드 등 안정적인 식별자가 있으면 `source_entity_id`,
`target_entity_id`에도 넣는다. 상장 상태를 확인할 수 있을 때만
`source_is_listed`, `target_is_listed`를 채우며, 확인하지 못한 값은 `false`가 아니라
`NULL`로 둔다.

관계 원본은 먼저 `meta.dataset_snapshot`과 `raw.source_row`에
`dataset_code='ORGANIZATION_RELATION'`로 적재한다. 그 스냅샷을
`meta.data_version_snapshot`을 통해 ACTIVE 버전에 연결해야 검색 대상으로 인정된다.

## 검색 함수

```sql
SELECT *
FROM search.find_organization_relations(
    p_anchor => '에코프로',
    p_predicates => ARRAY['subsidiaryOf'],
    p_anchor_role => 'target',
    p_result_role => 'source',
    p_result_listed => true,
    p_limit => 50
);
```

함수는 source/target/either anchor 방향과 source/target/opposite 결과 방향을 지원한다.
이름과 ID는 대소문자·공백을 정규화한 완전일치로만 연결하며, 부분 문자열로 임의의
회사를 선택하지 않는다. 현재 계약의 `traversal_hops`는 1만 지원한다.

## 기존 NCP DB 적용

전체 초기화 스키마를 다시 실행하지 않고 다음 migration을 적용한다.

```text
migrations/V20260906_001__add_organization_relation_search.sql
```

적용 후 관계 원본 스냅샷을 적재·활성화하고 위 SQL과 API 질문을 함께 검증한다.
ACTIVE 관계 스냅샷이 없으면 Gateway는 정상적인 0건이 아니라 미적재 상태로 보고하여
`relation_data_missing` 판정이 가능하게 한다.
