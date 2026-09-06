from pydantic import BaseModel, Field


class ExternalAnswerResponse(BaseModel):
    question_id: str = Field(..., description="요청받은 question_id 그대로 반환")
    question: str = Field(..., description="요청받은 question 그대로 반환")
    retrieved_context: str = Field(..., description="답변에 사용한 근거를 [E1]... 형태로 직렬화한 문자열")
    think_trace: str = Field(..., description="질문 분석·검색·검증 과정 요약 (3~5줄)")
    answer: str = Field(..., description="최종 답변 (답변불가/부분/재질문도 이 필드에 포함)")

    class Config:
        json_schema_extra = {
            "example": {
                "question_id": "Q-001",
                "question": "미국 시장에 투자하고 보수가 0.5% 이하인 AI 테마 ETF 알려줘",
                "retrieved_context": "[E1] 상품명: ABC AI ETF / 필드: 총보수 / 값: 0.42% / 기준일: 2026-08-31 / 출처: overseas_etf.xlsx:Sheet1:238",
                "think_trace": "질문 유형: 조건 및 전략 복합 검색\n검색 계획: 미국 ETF 중 총보수 0.5% 이하 후보 조회\n추가 검색: 후보 상품의 AI 투자전략 의미 검색\n검색 결과: 관련 상품 3개\n검증 결과: 상품명·보수·전략 근거 일치",
                "answer": "조건에 맞는 ETF는 ABC AI ETF(총보수 0.42%, 2026-08-31 기준)입니다.",
            }
        }


class HealthResponse(BaseModel):
    status: str = Field(..., description="ok | degraded | down")
    database: str = Field(..., description="connected | disconnected")
    llm: str = Field(..., description="ready | not_ready")
    data_version: str = Field(..., description="예: data_v1")
