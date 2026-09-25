# POINT 19: read-only probe for pgvector (server version, available, enabled).
from app.db.session import engine
from sqlalchemy import text

with engine.connect() as c:
    v = c.execute(text("SELECT version()")).scalar()
    print("SERVER:", v.split(",")[0])
    avail = c.execute(text(
        "SELECT name, default_version, installed_version "
        "FROM pg_available_extensions WHERE name='vector'"
    )).fetchall()
    print("AVAILABLE_TO_INSTALL:", bool(avail), avail)
    inst = c.execute(text(
        "SELECT extname, extversion FROM pg_extension WHERE extname='vector'"
    )).fetchall()
    print("CURRENTLY_ENABLED:", bool(inst), inst)
