"""Run inside the built container against its fresh, disposable database."""
import asyncio

from sqlalchemy import text

from app.main import create_app


async def check():
    app = create_app()
    async with app.router.lifespan_context(app):
        async with app.state.db() as db:
            assert await db.scalar(text("SELECT count(*) FROM users")) == 0
        print("Container startup and database migrations passed")


asyncio.run(check())
