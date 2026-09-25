"""POINT 21 (Task #21): optional OpenTelemetry bootstrap.

Sets up the tracer provider + OTLP exporters so the spans produced by
ProgressEmitter actually ship somewhere:

  - Langfuse  — LLM / model events. Langfuse exposes a native OTLP endpoint
                (/api/public/otel), so we send to it WITHOUT the Langfuse SDK —
                instrument once with OpenTelemetry, no vendor lock-in.
  - Generic OTLP / APM — infra tracing (Azure Monitor, Grafana Tempo, Jaeger…)
                via the standard OTEL_EXPORTER_OTLP_ENDPOINT env var.

No-op unless `observability.otel_enabled` is true AND opentelemetry is installed.
Call init_telemetry(config) once at process startup (runner + Streamlit).

Required deps when enabled (kept OUT of the default install):
    pip install opentelemetry-sdk opentelemetry-exporter-otlp

Env (set in .env / Key Vault when enabling):
    OTEL_EXPORTER_OTLP_ENDPOINT   # infra/APM collector (optional)
    LANGFUSE_HOST                 # default https://cloud.langfuse.com
    LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY
"""
from __future__ import annotations

import base64
import logging
import os

log = logging.getLogger(__name__)

_INITIALISED = False


def init_telemetry(config: dict | None) -> bool:
    """Configure OpenTelemetry exporters. Returns True if tracing is active."""
    global _INITIALISED
    if _INITIALISED:
        return True
    obs = (config or {}).get("observability", {}) if isinstance(config, dict) else {}
    if not bool(obs.get("otel_enabled", False)):
        return False
    try:
        from opentelemetry import trace
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import (
            BatchSpanProcessor, SimpleSpanProcessor, SpanExporter, SpanExportResult,
        )
    except Exception:
        log.warning("observability.otel_enabled but opentelemetry-sdk not installed — "
                    "run: pip install opentelemetry-sdk  (add opentelemetry-exporter-otlp for Langfuse/APM)")
        return False

    def _otlp_exporter(**kw):
        # OTLP exporter is a SEPARATE package; import lazily so the console
        # exporter still works with just opentelemetry-sdk installed.
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        return OTLPSpanExporter(**kw)

    class _PrettySpan(SpanExporter):
        """Print ONE tidy line per span to the terminal (demo-friendly)."""
        _KEYS = ("serverops.stage", "serverops.correlation_id", "gen_ai.request.model",
                 "gen_ai.usage.input_tokens", "gen_ai.usage.output_tokens")

        def export(self, spans):
            for s in spans:
                dur = (s.end_time - s.start_time) / 1e6 if (s.end_time and s.start_time) else 0.0
                a = dict(s.attributes or {})
                bits = "  ".join(f"{k.split('.')[-1]}={a[k]}" for k in self._KEYS if k in a)
                st = s.status.status_code.name if s.status else ""
                print(f"[OTel] {s.name:22} {st:6} {dur:7.1f}ms  {bits}")
            return SpanExportResult.SUCCESS

        def shutdown(self):
            return None

        def force_flush(self, timeout_millis: int = 30000):
            return True

    service = obs.get("otel_service_name", "serverops")
    provider = TracerProvider(resource=Resource.create({"service.name": service}))
    exporters = 0

    # 0) Console — print spans to stdout. The zero-infra way to SEE OpenTelemetry
    #    working (no Langfuse, no APM, no Docker needed). Great for local/dev.
    if bool(obs.get("otel_console", False)):
        provider.add_span_processor(SimpleSpanProcessor(_PrettySpan()))
        exporters += 1

    # 1) Generic OTLP / APM backend (infra) — honours OTEL_EXPORTER_OTLP_ENDPOINT.
    if os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT"):
        try:
            provider.add_span_processor(BatchSpanProcessor(_otlp_exporter()))
            exporters += 1
        except Exception:
            log.warning("OTEL_EXPORTER_OTLP_ENDPOINT set but opentelemetry-exporter-otlp "
                        "not installed — run: pip install opentelemetry-exporter-otlp")

    # 2) Langfuse as an OTLP backend (LLM/model events) — basic-auth with the keys.
    if bool(obs.get("langfuse_enabled", False)):
        host = os.getenv("LANGFUSE_HOST", "https://cloud.langfuse.com").rstrip("/")
        pk, sk = os.getenv("LANGFUSE_PUBLIC_KEY"), os.getenv("LANGFUSE_SECRET_KEY")
        if pk and sk:
            try:
                auth = base64.b64encode(f"{pk}:{sk}".encode()).decode()
                provider.add_span_processor(BatchSpanProcessor(_otlp_exporter(
                    endpoint=f"{host}/api/public/otel/v1/traces",
                    headers={"Authorization": f"Basic {auth}"},
                )))
                exporters += 1
            except Exception:
                log.warning("langfuse_enabled but opentelemetry-exporter-otlp not installed — "
                            "run: pip install opentelemetry-exporter-otlp")
        else:
            log.warning("langfuse_enabled but LANGFUSE_PUBLIC_KEY/SECRET_KEY not set — skipping")

    if exporters == 0:
        log.warning("otel_enabled but no exporters configured (set OTEL_EXPORTER_OTLP_ENDPOINT "
                    "and/or LANGFUSE_* ) — spans will be dropped")
        return False

    trace.set_tracer_provider(provider)
    _INITIALISED = True
    log.info("OpenTelemetry initialised (service=%s, exporters=%d)", service, exporters)
    return True
