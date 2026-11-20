import os

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

# Railway (and most hosts) hand out postgresql:// URLs, which SQLAlchemy maps to psycopg2;
# we ship psycopg 3.
DATABASE_URL = os.environ["DATABASE_URL"].replace("postgresql://", "postgresql+psycopg://", 1)

engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    # Never put bind parameters (emails, secrets, card-derived data) into exception text.
    hide_parameters=True,
    pool_timeout=5,
    # lock_timeout for the whole connection, so lock waits outside the executor (e.g. in a
    # dependency) can't hang forever; the executor also sets it per transaction.
    connect_args={"connect_timeout": 5, "options": "-c lock_timeout=5s"},
)
SessionLocal = sessionmaker(engine)


class Base(DeclarativeBase):
    pass


def get_db():
    with SessionLocal() as session:
        yield session
