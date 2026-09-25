"""POINT 20 (Task #20): raise a Jira ticket for a remediation command execution.

Hooked at the end of run_workflow (right after Step-7 capture_precedent). One ticket
per executed command, via the local Jira MCP server. Fail-safe and idempotent.

Gating (config in the remediation block of modules.yaml):
  jira_enabled    : master on/off
  jira_real_only  : if true, only file when executor_mode == real (production setting);
                    default false so the mock-executor demo also files tickets
  jira_project_key / jira_issue_type : Jira target
Skips actions that never executed (final_state == 'rejected') and already-ticketed ones.
"""
from __future__ import annotations

import logging

from app.db.models import RemediationAction, RootCauseReport
from app.db.session import SessionLocal
from app.integrations.jira_mcp_client import file_ticket

log = logging.getLogger(__name__)


def create_jira_ticket(action_id: int, final_state: str, config: dict | None = None) -> str | None:
    config = config or {}
    if not bool(config.get("jira_enabled", False)):
        return None
    if final_state == "rejected":          # allow-list denied — command never ran
        return None
    if bool(config.get("jira_real_only", False)) and str(config.get("executor_mode", "mock")).lower() != "real":
        return None

    try:
        with SessionLocal() as s:
            action = s.get(RemediationAction, action_id)
            if action is None or not action.command:
                return None
            if action.jira_key:               # idempotent — already filed
                return action.jira_key
            report = s.get(RootCauseReport, action.report_id)
            primary = ((report.payload or {}).get("primary_trigger") or {}) if report else {}
            system = primary.get("subject")
            inc = report.servicenow_number if report else None
            command, exit_code = action.command, action.exit_code
            output = (action.execution_output or "")[:2000]
            verify = action.verify_result or ""
            tenant_id, report_id = action.tenant_id, action.report_id

        summary = f"Automated remediation executed: {command}"[:250]
        description = (
            f"serverops executed a remediation command (action #{action_id}, "
            f"final state: {final_state}).\n\n"
            f"Command:        {command}\n"
            f"Exit code:      {exit_code}\n"
            f"Affected system:{system}\n"
            f"ServiceNow:     {inc}\n"
            f"Report:         {report_id}\n"
            f"Tenant:         {tenant_id}\n\n"
            f"Output:\n{output}\n\n"
            f"Verification:   {verify}\n"
        )
        # Dispatch to the configured MCP server (own local | atlassian hosted).
        result = file_ticket(
            summary=summary,
            description=description,
            project=config.get("jira_project_key", "KAN"),
            issuetype=config.get("jira_issue_type", "Task"),
            labels=["serverops", "remediation"],
            # A verified fix is completed work -> mark the audit ticket Done. A
            # failed/unverified execution stays Open so a human reviews it.
            mark_done=(final_state == "verified"),
            config=config,
        )
        if not result or not result.get("key"):
            return None
        key = result["key"]

        with SessionLocal() as s:
            a = s.get(RemediationAction, action_id)
            if a is not None:
                a.jira_key = key
                s.commit()
        log.info("create_jira_ticket: action=%s -> Jira %s (%s)", action_id, key, result.get("mode"))
        return key
    except Exception:
        log.exception("create_jira_ticket: failed (non-fatal); remediation continues")
        return None
