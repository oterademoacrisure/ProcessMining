"""POINT 21 (Task #21): ProgressEmitter — instrument each pipeline stage ONCE.

A single call site feeds up to three consumers:
  1) pipeline_event row    -> the live UI timeline                (always; fail-safe)
  2) OpenTelemetry span     -> infra tracing backend (Tempo/Jaeger/Azure Monitor)
  3) gen_ai.* span          -> Langfuse (LLM cost/quality) via OTLP — see telemetry.py

Everything beyond (1) is lazy + flag-gated: with observability off there is ZERO
extra dependency and ZERO behaviour change. Writes are wrapped so a telemetry
failure can NEVER break the pipeline.

Usage:
    from app.observability import progress
    progress.configure(config)                      # once, at startup

    with progress.stage(corr_id, tenant_id, "DIAGNOSE", "RCA analysis") as s:
        s.progress("retrieving precedents")
        s.meta(confidence=0.82, precedents=3)
        ...                                          # auto started -> succeeded/failed + duration
"""
from __future__ import annotations

import logging
import time
from typing import Any

log = logging.getLogger(__name__)


# ── optional OpenTelemetry tracer (lazy; None if disabled/uninstalled) ──────────
_TRACER: Any = None


def _tracer():
    """Return an OTel tracer once, or None if OTel isn't available."""
    global _TRACER
    if _TRACER is None:
        try:
            from opentelemetry import trace
            _TRACER = trace.get_tracer("serverops.pipeline")
        except Exception:
            _TRACER = False          # remember "unavailable" so we don't retry imports
    return _TRACER or None


class _Stage:
    """Context manager for one stage: writes started -> succeeded/failed (+duration),
    and (if OTel is on) wraps the work in a span."""

    def __init__(self, emitter: "ProgressEmitter", tenant_id, correlation_id, stage, label, run_id):
        self._e = emitter
        self.tenant_id = tenant_id
        self.correlation_id = correlation_id
        self.stage = stage
        self.label = label
        self.run_id = run_id
        self._meta: dict = {}
        self._span = None
        self._cm = None
        self._t0 = None

    def __enter__(self):
        self._t0 = time.monotonic()
        self._e._write(self.stage, "started", tenant_id=self.tenant_id,
                       correlation_id=self.correlation_id, run_id=self.run_id, step=self.label)
        tr = _tracer() if self._e.otel else None
        if tr:
            try:
                self._cm = tr.start_as_current_span(f"pipeline.{self.stage}")
                self._span = self._cm.__enter__()
                self._span.set_attribute("serverops.stage", self.stage)
                if self.correlation_id:
                    self._span.set_attribute("serverops.correlation_id", str(self.correlation_id))
                if self.tenant_id is not None:
                    self._span.set_attribute("serverops.tenant_id", self.tenant_id)
            except Exception:
                self._span = None
        return self

    def progress(self, message: str, **meta):
        """Record a sub-step (e.g. 'retrieving precedents')."""
        self._e._write(self.stage, "progress", tenant_id=self.tenant_id,
                       correlation_id=self.correlation_id, run_id=self.run_id,
                       step=message, meta=(meta or None))
        if self._span:
            try:
                self._span.add_event(message, attributes={k: str(v) for k, v in meta.items()})
            except Exception:
                pass

    def meta(self, **kv):
        """Attach key/values to the final event (and the span)."""
        self._meta.update(kv)
        if self._span:
            for k, v in kv.items():
                try:
                    self._span.set_attribute(
                        f"serverops.{k}", v if isinstance(v, (str, int, float, bool)) else str(v))
                except Exception:
                    pass

    def skip(self, message: str = ""):
        """Mark this stage skipped (e.g. advisory action, nothing to do)."""
        self._meta["skipped"] = True
        self._e._write(self.stage, "skipped", tenant_id=self.tenant_id,
                       correlation_id=self.correlation_id, run_id=self.run_id,
                       step=self.label, message=message, meta=(self._meta or None))

    def __exit__(self, exc_type, exc, tb):
        dur_ms = int((time.monotonic() - self._t0) * 1000) if self._t0 is not None else None
        if dur_ms is not None:
            self._meta["duration_ms"] = dur_ms
        if exc_type is not None:
            self._e._write(self.stage, "failed", tenant_id=self.tenant_id,
                           correlation_id=self.correlation_id, run_id=self.run_id,
                           step=self.label, level="error",
                           message=f"{exc_type.__name__}: {exc}", meta=(self._meta or None))
            if self._span:
                try:
                    from opentelemetry.trace import Status, StatusCode
                    self._span.record_exception(exc)
                    self._span.set_status(Status(StatusCode.ERROR))
                except Exception:
                    pass
        elif not self._meta.get("skipped"):
            self._e._write(self.stage, "succeeded", tenant_id=self.tenant_id,
                           correlation_id=self.correlation_id, run_id=self.run_id,
                           step=self.label, meta=(self._meta or None))
        if self._cm:
            try:
                self._cm.__exit__(exc_type, exc, tb)
            except Exception:
                pass
        return False  # never suppress the original exception


class ProgressEmitter:
    def __init__(self, enabled: bool = True, otel: bool = False):
        self.enabled = enabled
        self.otel = otel

    def stage(self, correlation_id, tenant_id, stage: str, label: str = "", run_id: str | None = None):
        return _Stage(self, tenant_id, correlation_id, stage, label, run_id)

    def emit(self, stage: str, status: str, **kw):
        """Fire a single one-off event (no enter/exit)."""
        self._write(stage, status, **kw)

    def generation(self, *, tenant_id, correlation_id, model: str | None,
                   usage: dict | None = None, meta: dict | None = None):
        """Record an LLM call (a 'model event').

        -> timeline: a DIAGNOSE sub-step ("LLM call (model)").
        -> OTel: a `gen_ai.*` span that Langfuse / any OTLP backend understands
                 (model, token usage), so cost + quality show up there.
        `usage` keys: input, output, total (token counts)."""
        m = dict(meta or {})
        if model:
            m["model"] = model
        if usage:
            m.update({f"tokens_{k}": v for k, v in usage.items()})
        self._write("DIAGNOSE", "progress", tenant_id=tenant_id, correlation_id=correlation_id,
                    step="AI reasoning over the evidence and historical data", meta=(m or None))
        tr = _tracer() if self.otel else None
        if tr:
            try:
                with tr.start_as_current_span("gen_ai.chat") as sp:
                    sp.set_attribute("gen_ai.system", "azure_openai")
                    if model:
                        sp.set_attribute("gen_ai.request.model", model)
                    if usage:
                        if usage.get("input") is not None:
                            sp.set_attribute("gen_ai.usage.input_tokens", usage["input"])
                        if usage.get("output") is not None:
                            sp.set_attribute("gen_ai.usage.output_tokens", usage["output"])
                    if correlation_id:
                        sp.set_attribute("serverops.correlation_id", str(correlation_id))
            except Exception:
                pass

    # ── the only place that touches the DB ──────────────────────────────────────
    def _write(self, stage, status, *, tenant_id, correlation_id=None, run_id=None,
               step=None, message=None, level="info", meta=None):
        if not self.enabled:
            return
        try:
            from app.db.session import SessionLocal
            from app.db.models import PipelineEvent
            with SessionLocal() as s:
                s.add(PipelineEvent(
                    tenant_id=tenant_id, correlation_id=correlation_id, run_id=run_id,
                    stage=stage, step=(step or None), status=status, level=level,
                    message=message, meta=meta,
                ))
                s.commit()
        except Exception:
            log.debug("progress: emit failed (non-fatal)", exc_info=True)


# ── module-level singleton + convenience API ────────────────────────────────────
_EMITTER = ProgressEmitter(enabled=True, otel=False)


def configure(config: dict | None) -> ProgressEmitter:
    """Set flags from the `observability:` block of modules.yaml (call once at startup)."""
    obs = (config or {}).get("observability", {}) if isinstance(config, dict) else {}
    _EMITTER.enabled = bool(obs.get("progress_events", True))
    _EMITTER.otel = bool(obs.get("otel_enabled", False))
    return _EMITTER


def stage(correlation_id, tenant_id, stage_name: str, label: str = "", run_id: str | None = None):
    return _EMITTER.stage(correlation_id, tenant_id, stage_name, label, run_id)


def emit(stage_name: str, status: str, **kw):
    _EMITTER.emit(stage_name, status, **kw)


def generation(**kw):
    _EMITTER.generation(**kw)
