FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml ./
COPY shared/ ./shared/
RUN pip install --no-cache-dir -e .

COPY services/strategy_service/requirements.txt ./services/strategy_service/
RUN pip install --no-cache-dir -r services/strategy_service/requirements.txt

COPY services/strategy_service/ ./services/strategy_service/

ENV PYTHONPATH=/app
EXPOSE 8004

CMD ["sh", "-c", "uvicorn services.strategy_service.main:app --host 0.0.0.0 --port ${PORT:-8004}"]
