FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml ./
COPY shared/ ./shared/
RUN pip install --no-cache-dir -e .

COPY services/gateway_api/requirements.txt ./services/gateway_api/
RUN pip install --no-cache-dir -r services/gateway_api/requirements.txt

COPY services/gateway_api/ ./services/gateway_api/

ENV PYTHONPATH=/app
EXPOSE 8000

CMD ["uvicorn", "services.gateway_api.main:app", "--host", "0.0.0.0", "--port", "8000"]
