FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml ./
COPY shared/ ./shared/
RUN pip install --no-cache-dir -e .

COPY services/normalizer/requirements.txt ./services/normalizer/
RUN pip install --no-cache-dir -r services/normalizer/requirements.txt

COPY services/normalizer/ ./services/normalizer/

ENV PYTHONPATH=/app
EXPOSE 8002

CMD ["uvicorn", "services.normalizer.main:app", "--host", "0.0.0.0", "--port", "8002"]
