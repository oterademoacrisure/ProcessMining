# APIFICATION: ui-service container.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY services/ui/requirements.txt /app/services/ui/requirements.txt
RUN pip install -r /app/services/ui/requirements.txt

# Bundle only the UI script and the api_client. No DB, no orchestrator.
COPY app/ui /app/app/ui
# app is a namespace so streamlit_app's package imports still resolve.
COPY app/__init__.py* /app/app/
COPY services/__init__.py /app/services/__init__.py
COPY services/ui /app/services/ui

EXPOSE 8501
ENV PORT=8501 \
    CORE_SERVICE_URL=http://core-service:8000 \
    TENANT_ID=1

HEALTHCHECK --interval=15s --timeout=3s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8501/_stcore/health', timeout=2).getcode()==200 else 1)"

CMD ["python", "-m", "services.ui.main"]
