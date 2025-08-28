from __future__ import annotations
from pathlib import Path
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

def make_engine(db_path: Path):
    # SQLite DB file; check_same_thread=False so Qt threads won't choke.
    return create_engine(
        f"sqlite:///{db_path}",
        future=True,
        connect_args={"check_same_thread": False}
    )

def make_session_factory(engine):
    # expire_on_commit=False keeps objects usable after commit
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)

def init_db(engine, Base):
    Base.metadata.create_all(engine)

    # Older installations may lack the ``created_at`` and ``updated_at`` columns.
    # SQLite doesn't support automatic schema migrations, so we ensure the
    # columns exist manually.  ``datetime('now')`` matches the server_default
    # used in the SQLAlchemy model definition.
    with engine.begin() as conn:
        columns = {
            row[1] for row in conn.execute(text("PRAGMA table_info(patients)"))
        }
        if "created_at" not in columns:
            conn.execute(
                text(
                    "ALTER TABLE patients ADD COLUMN created_at TEXT DEFAULT (datetime('now'))"
                )
            )
        if "updated_at" not in columns:
            conn.execute(
                text(
                    "ALTER TABLE patients ADD COLUMN updated_at TEXT DEFAULT (datetime('now'))"
                )
            )
