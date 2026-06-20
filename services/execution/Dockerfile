FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml ./
COPY shared/ ./shared/
RUN pip install --no-cache-dir -e .

COPY services/execution/requirements.txt ./services/execution/
RUN pip install --no-cache-dir -r services/execution/requirements.txt

COPY services/execution/ ./services/execution/

ENV PYTHONPATH=/app
EXPOSE 8005

CMD ["uvicorn", "services.execution.main:app", "--host", "0.0.0.0", "--port", "8005"]
