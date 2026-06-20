FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml ./
COPY shared/ ./shared/

RUN pip install --no-cache-dir -e .

COPY services/binance_ingest/requirements.txt ./services/binance_ingest/
RUN pip install --no-cache-dir -r services/binance_ingest/requirements.txt

COPY services/binance_ingest/ ./services/binance_ingest/

ENV PYTHONPATH=/app
EXPOSE 8001

CMD ["sh", "-c", "uvicorn services.binance_ingest.main:app --host 0.0.0.0 --port ${PORT:-8001}"]
