import os

from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from app.config import settings

# Configure engine based on database type
connect_args = {}
if settings.DATABASE_URL.startswith("sqlite"):
    # SQLite-specific configuration
    connect_args = {"check_same_thread": False}
    engine = create_engine(
        settings.DATABASE_URL,
        connect_args=connect_args,
        echo=False  # Set to True for SQL debugging
    )
else:
    # PostgreSQL through the Supabase session pooler.
    #
    # Every number below is set by two measured facts, so change them together
    # or not at all:
    #
    #   * The database is in ap-northeast-1 and the API is not, so ONE ROUND
    #     TRIP COSTS ~170ms and OPENING A NEW CONNECTION COSTS ~2.8s — TCP,
    #     TLS and SCRAM are several round trips before a query is even sent.
    #     The cost of a request is therefore the NUMBER of round trips it
    #     makes, not the work the database does.
    #
    #   * The pooler runs in session mode and allows 15 client connections in
    #     TOTAL, across every worker, migration and script.
    #
    # pool_pre_ping is off deliberately. It issues SELECT 1 on every checkout,
    # which is free on a local database and was adding a whole round trip to
    # every single request here — the single largest avoidable cost in the
    # API. pool_recycle gives the same protection against a connection the
    # pooler has closed underneath us, and it costs nothing because the age
    # check is local.
    #
    # pool_recycle is long, and short of the pooler's own idle timeout. It
    # used to be 280s, which meant a quiet portal threw away healthy
    # connections every five minutes and some unlucky request paid 2.8s to
    # open a new one.
    #
    # pool_use_lifo keeps the same few connections hot instead of rotating
    # through all of them. At this traffic FIFO left every connection idle
    # just long enough to be recycled, so the pool was never warm.
    workers = max(1, int(os.getenv("WEB_CONCURRENCY", "2")))
    # 15 clients in total, less three kept back for alembic and the scripts.
    budget = max(2, (15 - 3) // workers)
    engine = create_engine(
        settings.DATABASE_URL,
        pool_pre_ping=False,
        pool_size=max(1, budget - 2),
        max_overflow=min(2, budget),
        pool_recycle=1500,
        pool_use_lifo=True,
        # Wait for a connection rather than failing. A request that waits is
        # slow; a request that is refused is the 503 the dashboard was showing.
        pool_timeout=30,
        connect_args={
            # Keep the connection alive across a long-haul link so it is not
            # dropped silently while idle, which without pre_ping would
            # surface as a failed request rather than a reconnect.
            "keepalives": 1,
            "keepalives_idle": 30,
            "keepalives_interval": 10,
            "keepalives_count": 5,
            "connect_timeout": 10,
            "application_name": "hrm-api",
            **connect_args,
        },
    )

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


def get_db():
    """Dependency for getting database session"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
