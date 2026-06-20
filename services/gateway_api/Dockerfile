FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml ./
COPY shared/ ./shared/
RUN pip install --no-cache-dir -e .

COPY services/gateway_api/requirements.txt ./services/gateway_api/
RUN pip install --no-cache-dir -r services/gateway_api/requirements.txt

# gateway_api service code
COPY services/gateway_api/ ./services/gateway_api/

# mcp_server facades are imported directly by gateway routes
COPY services/mcp_server/ ./services/mcp_server/

# strategy_service is lazy-imported at runtime by mcp_server/facades/strategy.py
COPY services/strategy_service/ ./services/strategy_service/

# execution controls only (KillSwitchControl, CooldownControl used by ops/kill_switch.py)
# Full execution/ is NOT copied to avoid pulling in aiohttp and unrelated adapters.
COPY services/execution/__init__.py ./services/execution/
COPY services/execution/controls/ ./services/execution/controls/

ENV PYTHONPATH=/app
EXPOSE 8007

CMD ["sh", "-c", "uvicorn services.gateway_api.main:app --host 0.0.0.0 --port ${PORT:-8007}"]
