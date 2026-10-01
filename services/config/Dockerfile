# config-service container. Standalone: no app/ code, no DB driver.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY services/config/requirements.txt /app/services/config/requirements.txt
RUN pip install -r /app/services/config/requirements.txt

COPY services/__init__.py /app/services/__init__.py
COPY services/config /app/services/config

EXPOSE 8200
ENV PORT=8200 \
    AGGREGATOR_SERVICE_URL=http://core-service:8000

HEALTHCHECK --interval=15s --timeout=3s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8200/health', timeout=2).getcode()==200 else 1)"

CMD ["uvicorn", "services.config.main:app", "--host", "0.0.0.0", "--port", "8200"]
