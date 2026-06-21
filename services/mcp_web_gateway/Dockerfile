FROM python:3.12-slim
WORKDIR /app

# Install the shared package (config base, schemas, etc.)
COPY pyproject.toml ./
COPY shared/ ./shared/
RUN pip install --no-cache-dir -e .

# Install gateway-specific dependencies
COPY services/mcp_web_gateway/requirements.txt ./services/mcp_web_gateway/
RUN pip install --no-cache-dir -r services/mcp_web_gateway/requirements.txt

# Copy gateway source (no other service code required — proxy calls internal MCP over HTTP)
COPY services/mcp_web_gateway/ ./services/mcp_web_gateway/

ENV PYTHONPATH=/app
EXPOSE 8007
CMD ["sh", "-c", "uvicorn services.mcp_web_gateway.main:app --host 0.0.0.0 --port ${PORT:-8007}"]
