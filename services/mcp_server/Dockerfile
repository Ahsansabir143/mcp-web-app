FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml ./
COPY shared/ ./shared/
RUN pip install --no-cache-dir -e .

COPY services/mcp_server/requirements.txt ./services/mcp_server/
RUN pip install --no-cache-dir -r services/mcp_server/requirements.txt

COPY services/mcp_server/ ./services/mcp_server/

ENV PYTHONPATH=/app
EXPOSE 8006

CMD ["uvicorn", "services.mcp_server.main:app", "--host", "0.0.0.0", "--port", "8006"]
