FROM python:3.11-slim

WORKDIR /app

# curl은 헬스체크용
RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

# requirements 먼저 복사해서 캐시 활용 (코드만 바뀌면 pip install 다시 안 돎)
COPY requirements.txt .
# sentence-transformers pulls in torch. This server has no GPU, so install the
# CPU-only wheel first -- otherwise pip resolves the full CUDA build (~3GB of
# nvidia-* packages) and the NCP host's disk fills up mid-build (see #34).
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu
RUN pip install --no-cache-dir -r requirements.txt

# 계약·에이전트·API 코드 복사
COPY contracts/ contracts/
COPY agent/ agent/
COPY api/ api/
COPY b_agent/ b_agent

EXPOSE 8000

# /health의 HTTP 200 계약은 유지하고 응답 본문의 DB 상태를 별도 검사
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-m", "api.readiness_check", "http://localhost:8000/health"]

CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
