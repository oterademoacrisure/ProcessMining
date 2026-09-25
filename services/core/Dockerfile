# APIFICATION: core-service container.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# System deps for psycopg2 + faiss + numpy wheels.
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential libpq-dev curl \
    && rm -rf /var/lib/apt/lists/*

COPY services/core/requirements.txt /app/services/core/requirements.txt
RUN pip install -r /app/services/core/requirements.txt

# App source (no UI, no reader implementations — but readers.base + persistence
# stay because persist_events still runs here).
COPY app /app/app
COPY config /app/config
COPY alembic /app/alembic
COPY alembic.ini /app/alembic.ini
COPY services/__init__.py /app/services/__init__.py
COPY services/core /app/services/core

# Strip out ui/ and the concrete readers — we bundle only readers.base +
# readers.persistence in the core; the concrete *_reader.py files belong to
# reader-service. (No-op if they're not present.)
RUN rm -rf /app/app/ui \
 && find /app/app/readers -maxdepth 1 -type f -name "*_reader.py" -delete \
 && rm -f /app/app/readers/reader_state.py

VOLUME ["/app/.data"]

EXPOSE 8000
ENV PORT=8000 \
    MODULES_YAML=/app/config/modules.yaml \
    READER_SERVICE_URL=http://reader-service:8100 \
    TENANT_ID=1 \
    INTERVAL_SEC=10

HEALTHCHECK --interval=15s --timeout=3s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2).getcode()==200 else 1)"

CMD ["uvicorn", "services.core.main:app", "--host", "0.0.0.0", "--port", "8000"]
