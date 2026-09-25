"""Mulesoft runtime correlation core.

Ported from the standalone `mule-rca` module (Phase-2). Contains the wire
schema (`MuleIntegrationEvent`) and the deterministic outcome-classification
logic (the §8 rules) that turn raw Mule events into failure-localization
evidence. The standalone module's DuckDB persistence and registry stubs are
intentionally NOT ported — serverops owns persistence (Postgres + the shared
event_log table) and wires sources via config/modules.yaml.
"""
