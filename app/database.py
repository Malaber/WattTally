from pathlib import Path

from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


def database(url):
    if url.startswith("sqlite") and ":memory:" not in url:
        Path(url.split("///", 1)[1]).parent.mkdir(parents=True, exist_ok=True)
    engine = create_async_engine(url)
    if url.startswith("sqlite"):

        @event.listens_for(engine.sync_engine, "connect")
        def pragmas(connection, _):
            cursor = connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA busy_timeout=10000")
            cursor.close()

    return engine, async_sessionmaker(engine, expire_on_commit=False)
