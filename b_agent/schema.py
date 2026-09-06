from __future__ import annotations

from dataclasses import dataclass

from .models import ProductType


@dataclass(frozen=True)
class FieldSpec:
    logical_name: str
    raw_columns: tuple[str, ...]
    value_type: str
    unit: str | None = None
    description: str = ""
    derivation: str = "direct"


FIELD_REGISTRY: dict[ProductType, dict[str, FieldSpec]] = {
    ProductType.BOND: {
        "product_id": FieldSpec("product_id", ("pd_no",), "text"),
        "name": FieldSpec("name", ("pd_nm", "pd_abrv_nm"), "text"),
        "issuer": FieldSpec("issuer", ("pd_pbcm",), "text"),
        "currency": FieldSpec("currency", ("curr_cd",), "text"),
        "credit_rating": FieldSpec("credit_rating", ("crd_grd",), "credit_rating"),
        "sale_available": FieldSpec(
            "sale_available",
            ("buyable_quantity", "bdbns_abl_chnl_tcd"),
            "boolean",
            description="true/false/unknown 삼상 값",
            derivation=(
                "buyable_quantity > 0이고 검증된 매수 채널이 있을 때만 true; "
                "관련 원본 값이 비어 있으면 false가 아니라 unknown"
            ),
        ),
        "yield": FieldSpec("yield", ("buy_yield", "applied_yield"), "number", "%"),
        "maturity_date": FieldSpec("maturity_date", ("mat_dt",), "date"),
        "remaining_days": FieldSpec("remaining_days", ("remaining_days",), "number", "day"),
        "risk_grade": FieldSpec("risk_grade", ("pd_risk_gcd", "pd_risk_nm"), "text"),
    },
    ProductType.PUBLIC_FUND: {
        "product_id": FieldSpec("product_id", ("itm_no", "std_itm_no"), "text"),
        "name": FieldSpec("name", ("itm_nm", "itm_abrv_nm"), "text"),
        "currency": FieldSpec("currency", ("curr_cd",), "text"),
        "net_assets": FieldSpec("net_assets", ("fd_nast_suma",), "number"),
        "sale_available": FieldSpec(
            "sale_available", ("prvo_pbff_desc", "sale_yn", "thco_sale_yn"), "boolean",
            description="MASTER_STATUS_ONLY: 공모·판매중·당사 취급이 확인된 매수 후보",
            derivation="공모 AND 판매중 AND thco_sale_yn=Y; sale_status 충돌·미확인은 unknown",
        ),
        "risk_grade": FieldSpec(
            "risk_grade", ("zrin_fd_ivst_risk_gcd", "zrin_fd_ivst_risk_grd_nm"), "text"
        ),
        "investment_region": FieldSpec(
            "investment_region",
            ("fd_ivst_rgn_desc", "ovrs_fd_desc"),
            "text",
            derivation="A의 허용값 사전으로 국가·권역명을 canonical region 값에 매핑",
        ),
        "one_year_return": FieldSpec("one_year_return", ("fd_yr1_ern_r",), "number", "%"),
        "return_1m": FieldSpec("return_1m", ("fd_mm1_ern_r",), "number", "%"),
        "return_3m": FieldSpec("return_3m", ("fd_mm3_ern_r",), "number", "%"),
        "return_6m": FieldSpec("return_6m", ("fd_mm6_ern_r",), "number", "%"),
        "return_18m": FieldSpec("return_18m", ("fd_mm18_ern_r",), "number", "%"),
        "return_1y": FieldSpec("return_1y", ("fd_yr1_ern_r",), "number", "%"),
        "return_2y": FieldSpec("return_2y", ("fd_yr2_ern_r",), "number", "%"),
        "return_3y": FieldSpec("return_3y", ("fd_yr3_ern_r",), "number", "%"),
        "return_5y": FieldSpec("return_5y", ("fd_yr5_ern_r",), "number", "%"),
        "fee_rate": FieldSpec(
            "fee_rate",
            ("or_co_rwrd_r", "sale_co_rwrd_r", "trusc_rwrd_r"),
            "number",
            "%",
            description="A가 산출 근거와 스케일을 확정한 canonical 총보수만 사용",
            derivation="원본 한 컬럼을 총보수로 간주하지 않으며 문서화된 합산식 없이는 unknown",
        ),
        "strategy": FieldSpec(
            "strategy", ("prfd_attr_search_text", "zrin_attr_nms", "zrin_ptn_nm"), "text"
        ),
    },
    ProductType.DOMESTIC_ETF: {
        "product_id": FieldSpec("product_id", ("pd_itm_no", "pd_isin_cd"), "text"),
        "name": FieldSpec("name", ("pd_nm", "pd_abrv_nm"), "text"),
        "ticker": FieldSpec("ticker", ("pd_ticker", "pd_ric"), "text"),
        "currency": FieldSpec("currency", ("pd_curr_cd",), "text"),
        "net_assets": FieldSpec("net_assets", ("pd_net_tamt", "du_last_aum"), "number", "KRW"),
        "sale_available": FieldSpec(
            "sale_available",
            ("pd_sale_yn", "pd_tr_yn"),
            "boolean",
            description="판매 및 거래정지 상태를 함께 반영한 삼상 값",
            derivation="MASTER_STATUS_ONLY: pd_sale_yn=1 AND pd_tr_yn=0; pd_tr_yn=1은 거래정지; 미확인 코드는 unknown",
        ),
        "risk_grade": FieldSpec("risk_grade", ("pd_risk_cd", "pd_risk_nm"), "text"),
        "investment_region": FieldSpec(
            "investment_region",
            ("wu_inv_rgn", "ref_geo_focus"),
            "text",
            derivation="A의 허용값 사전으로 국가·권역명을 canonical region 값에 매핑",
        ),
        "asset_type": FieldSpec("asset_type", ("wu_inv_ast_type", "ref_ast_type"), "text"),
        "strategy": FieldSpec("strategy", ("cu_strtegy", "cu_base_index", "ref_base_index"), "text"),
        "fee_rate": FieldSpec("fee_rate", ("cu_charge_rt", "cu_charge_etc_rt"), "number", "%"),
        "one_year_return": FieldSpec("one_year_return", ("du_er_1y",), "number", "%"),
        "return_1m": FieldSpec("return_1m", ("du_er_1m",), "number", "%"),
        "return_3m": FieldSpec("return_3m", ("du_er_3m",), "number", "%"),
        "return_6m": FieldSpec("return_6m", ("du_er_6m",), "number", "%"),
        "return_1y": FieldSpec("return_1y", ("du_er_1y",), "number", "%"),
        "volatility": FieldSpec("volatility", ("du_vlty_1y", "du_vlty_6m"), "number", "%"),
        "leverage_factor": FieldSpec("leverage_factor", ("cu_lev_fector",), "number"),
    },
    ProductType.FOREIGN_ETF: {
        "product_id": FieldSpec("product_id", ("pd_itm_no", "pd_isin_cd"), "text"),
        "name": FieldSpec("name", ("pd_nm", "pd_abrv_nm"), "text"),
        "ticker": FieldSpec("ticker", ("pd_abrv_nm", "pd_itm_no"), "text"),
        "currency": FieldSpec("currency", ("pd_curr_cd", "pd_trd_ccy"), "text"),
        "net_assets": FieldSpec("net_assets", ("du_last_aum",), "number", "native_currency"),
        "sale_available": FieldSpec(
            "sale_available",
            ("pd_sale_yn", "pd_tr_yn"),
            "boolean",
            description="판매 및 거래정지 상태를 함께 반영한 삼상 값",
            derivation="MASTER_STATUS_ONLY: pd_sale_yn=1 AND pd_tr_yn=0; pd_tr_yn=1은 거래정지; 미확인 코드는 unknown",
        ),
        "investment_region": FieldSpec(
            "investment_region",
            ("wu_inv_rgn", "pd_mkt_id"),
            "text",
            derivation="상장 시장과 투자 지역을 분리하고 국가·권역을 canonical 값으로 매핑",
        ),
        "asset_type": FieldSpec("asset_type", ("wu_inv_ast_type",), "text"),
        "strategy": FieldSpec("strategy", ("cu_strtegy", "cu_base_index"), "text"),
        "fee_rate": FieldSpec("fee_rate", ("cu_charge_rt",), "number", "%"),
        "one_day_return": FieldSpec("one_day_return", ("du_er_1d",), "number", "%"),
        "leverage_factor": FieldSpec("leverage_factor", ("cu_lev_fector",), "number"),
        "cik": FieldSpec("cik", ("pd_us_cik",), "text"),
    },
}


def all_product_types() -> list[ProductType]:
    return list(ProductType)


def supports_field(product_type: ProductType, logical_field: str) -> bool:
    return logical_field in FIELD_REGISTRY.get(product_type, {})


def raw_columns(product_type: ProductType, logical_field: str) -> tuple[str, ...]:
    spec = FIELD_REGISTRY.get(product_type, {}).get(logical_field)
    return spec.raw_columns if spec else ()
