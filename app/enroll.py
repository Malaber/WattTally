"""Create an administrator or generate a one-use passkey link for an existing account."""

import argparse
import asyncio
from pathlib import Path
from alembic import command
from alembic.config import Config
from email_validator import validate_email
from sqlalchemy import select
from app.auth import issue_link
from app.config import Settings
from app.database import database
from app.models import User


async def enroll(args):
    settings = Settings()
    settings.validate_secrets()
    cfg = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    cfg.attributes["database_url"] = settings.database_url
    await asyncio.to_thread(command.upgrade, cfg, "head")
    engine, factory = database(settings.database_url)
    try:
        async with factory() as db:
            email = validate_email(args.email, check_deliverability=False).normalized.casefold()
            user = await db.scalar(select(User).where(User.email == email))
            if user is None:
                user = User(
                    email=email,
                    display_name=args.name or email,
                    is_admin=args.admin,
                    is_approved=True,
                )
                db.add(user)
                await db.commit()
            elif args.admin:
                user.is_admin = True
                user.is_approved = True
                await db.commit()
            link = await issue_link(db, user, settings.app_base_url)
            print(link["url"])
    finally:
        await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("email")
    parser.add_argument("--name")
    parser.add_argument("--admin", action="store_true")
    asyncio.run(enroll(parser.parse_args()))
