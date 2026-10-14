import asyncio

from sqlalchemy import text

from app.database import database


def test_backfill_write_does_not_block_dashboard_read(tmp_path):
    async def exercise():
        engine, _ = database("sqlite+aiosqlite:///" + str(tmp_path / "history.db"))
        try:
            async with engine.begin() as connection:
                await connection.execute(text("CREATE TABLE sample (value TEXT)"))
                await connection.execute(text("INSERT INTO sample VALUES ('committed')"))
            async with engine.connect() as writer, engine.connect() as reader:
                # Force dirty-page spill, as a large history import does in production.
                await writer.execute(text("PRAGMA cache_size=10"))
                await writer.execute(
                    text("UPDATE sample SET value=:value"), {"value": "x" * 1000000}
                )
                await reader.execute(text("PRAGMA busy_timeout=100"))
                assert await reader.scalar(text("SELECT value FROM sample")) == "committed"
                await reader.rollback()
                await writer.commit()
                assert await reader.scalar(text("SELECT length(value) FROM sample")) == 1000000
        finally:
            await engine.dispose()

    asyncio.run(exercise())
