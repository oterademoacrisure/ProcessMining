# APIFICATION: reader-service container.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Deps first so layer caches on code-only changes.
COPY services/reader/requirements.txt /app/services/reader/requirements.txt
RUN pip install -r /app/services/reader/requirements.txt

# Source: only what the reader actually imports.
COPY app/readers /app/app/readers
COPY app/orchestrator/registry.py /app/app/orchestrator/registry.py
COPY app/orchestrator/__init__.py /app/app/orchestrator/__init__.py
COPY app/paths.py /app/app/paths.py
COPY app/secrets.py /app/app/secrets.py
COPY app/__init__.py* /app/app/
COPY app/mule /app/app/mule
COPY app/observability /app/app/observability
COPY config/modules.yaml /app/config/modules.yaml
COPY services/__init__.py /app/services/__init__.py
COPY services/reader /app/services/reader

# The reader-service does not touch the DB, but app/observability/progress.py
# tries a lazy import of app.db.session — behind a try/except so it stays
# fail-safe. That's fine here; we keep progress emissions off in the reader
# process anyway (no observability config passed) so the DB import is never
# exercised.

# Data volumes are provided at runtime:
#   /app/data/input   — bind-mount of the host input tree
#   /app/data/state   — named volume for reader checkpoints
VOLUME ["/app/data/input", "/app/data/state"]

EXPOSE 8100
ENV PORT=8100 \
    MODULES_YAML=/app/config/modules.yaml

HEALTHCHECK --interval=15s --timeout=3s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8100/health', timeout=2).getcode()==200 else 1)"

CMD ["uvicorn", "services.reader.main:app", "--host", "0.0.0.0", "--port", "8100"]
