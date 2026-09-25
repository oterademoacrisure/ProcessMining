"""POINT 21 (Task #21): observability package.

One instrumentation hook (`ProgressEmitter`), three consumers:
  1. pipeline_event table  -> the real-time UI timeline (always on; fail-safe)
  2. OpenTelemetry spans    -> infra tracing (optional: observability.otel_enabled)
  3. Langfuse (via OTLP)     -> LLM / model events (optional: observability.langfuse_enabled)

Import the convenience API from app.observability.progress.
"""
from __future__ import annotations


def bootstrap(config_path=None) -> None:
    """Configure the timeline emitter + optional OpenTelemetry/Langfuse from the
    `observability:` block of modules.yaml. Idempotent and fully fail-safe — call
    once at the start of any entry point (runner / demo / streamlit / scripts)."""
    try:
        import yaml
        from app.paths import PROJECT_ROOT
        from app.observability import progress
        from app.observability.telemetry import init_telemetry
        path = config_path or (PROJECT_ROOT / "config" / "modules.yaml")
        with open(path, encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        progress.configure(cfg)
        init_telemetry(cfg)
    except Exception:
        pass

