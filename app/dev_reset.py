"""Reset the framework to a clean test state.

Deletes rows from event_log, process_case, process_definition, finding, and
(by default) root_cause_report. Deletes reader state files under data/state/.

Flags:
  --keep-reports     Preserve root_cause_report rows (useful when iterating
                     on the UI without losing the audit trail of LLM reports).
                     Note: trigger_finding_id will be set to NULL on retained
                     reports because their findings are gone.

Leaves intact:
  - tenant table (tenant_id=1 stays)
  - alembic_version (schema)
  - legacy tables (db_metrics, pending_approvals, incident_reports)
  - data/input/ files (sample data)

Usage:
    python app/dev_reset.py
    python app/dev_reset.py --keep-reports
"""
import argparse
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from sqlalchemy import text
from app.db.session import SessionLocal
from app.paths import STATE_DIR


def reset(keep_reports: bool = False) -> None:
    with SessionLocal() as s:
        if not keep_reports:
            n = s.execute(text("DELETE FROM root_cause_report")).rowcount
            print(f"  deleted {n:>4} rows from root_cause_report")
        else:
            # Detach reports from findings about to be deleted (FK on delete
            # is SET NULL, so the DELETE FROM finding below would do it
            # anyway — being explicit makes the intent clear and the row
            # count printable).
            n = s.execute(text("UPDATE root_cause_report SET trigger_finding_id = NULL")).rowcount
            print(f"  detached {n:>4} rows from root_cause_report (trigger_finding_id -> NULL)")

        for tbl in ("finding", "event_log", "process_case", "process_definition"):
            n = s.execute(text(f"DELETE FROM {tbl}")).rowcount
            print(f"  deleted {n:>4} rows from {tbl}")
        s.commit()

    # Note: pipeline_event (the Live Activity timeline) is intentionally NOT cleared
    # here — it's kept as a retained audit trail. The Live Activity page defaults to a
    # recent time window (clean view) and offers a filter to review up to the last
    # 30 days of past activity.

    if STATE_DIR.exists():
        removed = 0
        for p in STATE_DIR.glob("*.json"):
            p.unlink()
            removed += 1
        print(f"  removed {removed} state file(s)")

    if keep_reports:
        print("Reset complete (reports kept).")
    else:
        print("Reset complete.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Dev-only reset utility")
    parser.add_argument(
        "--keep-reports",
        action="store_true",
        help="Preserve root_cause_report rows; detach them from findings being deleted",
    )
    args = parser.parse_args()
    reset(keep_reports=args.keep_reports)
