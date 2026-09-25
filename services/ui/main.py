"""APIFICATION: ui-service entry point.

`streamlit run` needs a script path, not a module. This wrapper is a tiny
main() that launches the existing Streamlit app under this container's
sys.path (so `from services.ui import api_client` resolves).

Typical invocation (see Dockerfile):
    streamlit run /app/app/ui/streamlit_app.py --server.port 8501 --server.address 0.0.0.0

There is nothing to do in this file for now; it exists so `services.ui` is a
proper package and so future preflight (env checks, health probes) has a home.
"""
from __future__ import annotations


def main() -> None:  # pragma: no cover — invoked by Docker CMD
    import os
    import subprocess
    import sys
    from pathlib import Path

    repo_root = Path(__file__).resolve().parents[2]
    script = repo_root / "app" / "ui" / "streamlit_app.py"
    port = os.getenv("PORT", "8501")

    sys.exit(subprocess.call([
        sys.executable, "-m", "streamlit", "run", str(script),
        "--server.port", port,
        "--server.address", "0.0.0.0",
        "--server.headless", "true",
    ]))


if __name__ == "__main__":
    main()
