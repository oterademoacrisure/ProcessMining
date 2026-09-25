import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.secrets import load_secrets

load_secrets()   # .env locally; Azure Key Vault when AZURE_KEY_VAULT_URL is set


def _normalize_url(url: str) -> str:
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    return url


DATABASE_URL = _normalize_url(os.getenv("DATABASE_URL", ""))

# pool_pre_ping: test a connection before use (drops dead ones). pool_recycle: recycle
# connections older than 5 min so Neon's serverless idle-suspend doesn't hand us a
# half-closed SSL socket ("SSL connection has been closed unexpectedly").
engine = create_engine(DATABASE_URL, pool_pre_ping=True, pool_recycle=300, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
