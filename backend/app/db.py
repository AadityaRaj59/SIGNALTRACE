from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from backend.app.core.config import get_settings

settings = get_settings()
connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
engine = create_engine(settings.database_url, connect_args=connect_args, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)

class Base(DeclarativeBase):
    pass

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def ensure_schema():
    """Create tables and additively migrate older SQLite/SQL databases (new nullable/defaulted columns only)."""
    from sqlalchemy import inspect, text
    from backend.app import models  # noqa: F401  (register tables)
    Base.metadata.create_all(bind=engine)
    insp = inspect(engine)
    for table in Base.metadata.sorted_tables:
        existing = {c["name"] for c in insp.get_columns(table.name)}
        for col in table.columns:
            if col.name not in existing:
                ddl = col.type.compile(engine.dialect)
                default = f" DEFAULT {col.server_default.arg.text if hasattr(col.server_default.arg,'text') else repr(col.server_default.arg)}" if col.server_default is not None else ""
                with engine.begin() as conn:
                    conn.execute(text(f'ALTER TABLE {table.name} ADD COLUMN {col.name} {ddl}{default}'))
