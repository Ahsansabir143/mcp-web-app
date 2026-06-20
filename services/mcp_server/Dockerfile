FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml ./
COPY shared/ ./shared/
RUN pip install --no-cache-dir -e .

COPY services/mcp_server/requirements.txt ./services/mcp_server/
RUN pip install --no-cache-dir -r services/mcp_server/requirements.txt

# mcp_server service code
COPY services/mcp_server/ ./services/mcp_server/

# strategy_service is lazy-imported at runtime by facades/strategy.py
COPY services/strategy_service/ ./services/strategy_service/

ENV PYTHONPATH=/app
EXPOSE 8006

CMD ["sh", "-c", "uvicorn services.mcp_server.main:app --host 0.0.0.0 --port ${PORT:-8006}"]
