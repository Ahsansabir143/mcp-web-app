FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml ./
COPY shared/ ./shared/
RUN pip install --no-cache-dir -e .

COPY services/analytics/requirements.txt ./services/analytics/
RUN pip install --no-cache-dir -r services/analytics/requirements.txt

COPY services/analytics/ ./services/analytics/

ENV PYTHONPATH=/app
EXPOSE 8003

CMD ["sh", "-c", "uvicorn services.analytics.main:app --host 0.0.0.0 --port ${PORT:-8003}"]
