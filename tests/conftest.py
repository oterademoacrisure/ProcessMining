"""POINT 19 (Task #19): make `app...` importable when running pytest from serverops."""
import os
import sys

from dotenv import load_dotenv

SERVEROPS_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SERVEROPS_ROOT)
load_dotenv(os.path.join(SERVEROPS_ROOT, ".env"))
