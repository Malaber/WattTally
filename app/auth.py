import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi import HTTPException, Request
from fastpasskey import (
    FastPasskey,
    PasskeyConflictError,
    PasskeyRouterConfig,
    create_passkey_router,
)
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

from app.accounting import instant, stamp
from app.models import AuthSession, EnrollmentLink, Passkey, User


class Repository:
    def __init__(self, db, request):
        self.db, self.request = db, request

    async def user_by_email(self, email):
        return await self.db.scalar(select(User).where(User.email == email.casefold()))

    async def user_by_id(self, user_id):
        return await self.db.get(User, UUID(str(user_id)))

    async def passkey_by_credential_id(self, credential_id):
        return await self.db.scalar(select(Passkey).where(Passkey.credential_id == credential_id))

    def new_key(self, user_id, name, credential):
        return Passkey(
            user_id=user_id,
            name=name,
            credential_id=credential.credential_id,
            public_key=credential.public_key,
            sign_count=credential.sign_count,
            last_used_at=datetime.now(UTC),
        )

    async def register_user(self, *, user_id, email, display_name, passkey_name, credential):
        user = User(id=user_id, email=email.casefold(), display_name=display_name)
        user.passkeys = [self.new_key(user_id, passkey_name, credential)]
        self.db.add(user)
        try:
            await self.db.commit()
        except IntegrityError as exc:
            await self.db.rollback()
            raise PasskeyConflictError from exc
        return user

    async def add_link_user(self, token):
        link = await self.db.get(EnrollmentLink, hashlib.sha256(token.encode()).hexdigest())
        if not link or instant(link.expires_at) <= datetime.now(UTC):
            return None
        user = await self.user_by_id(link.user_id)
        return user if user and user.is_active else None

    async def complete_add_link(self, *, token, user_id, name, credential):
        claimed = await self.db.execute(
            delete(EnrollmentLink).where(
                EnrollmentLink.id == hashlib.sha256(token.encode()).hexdigest(),
                EnrollmentLink.user_id == user_id,
                EnrollmentLink.expires_at > stamp(datetime.now(UTC)),
            )
        )
        if claimed.rowcount != 1:
            await self.db.rollback()
            return None
        user = await self.user_by_id(user_id)
        if not user or not user.is_active:
            await self.db.rollback()
            return None
        self.db.add(self.new_key(user_id, name, credential))
        try:
            await self.db.commit()
        except IntegrityError as exc:
            await self.db.rollback()
            raise PasskeyConflictError from exc
        return user

    async def replace_passkeys(self, *, user_id, passkey_name, credential):
        await self.db.execute(delete(Passkey).where(Passkey.user_id == user_id))
        await self.add_passkey(user_id=user_id, name=passkey_name, credential=credential)
        await self.db.execute(delete(AuthSession).where(AuthSession.user_id == user_id))
        await self.db.commit()
        self.db.expire_all()
        return await self.user_by_id(user_id)

    async def add_passkey(self, *, user_id, name, credential):
        key = self.new_key(user_id, name, credential)
        self.db.add(key)
        await self.db.commit()
        return key

    async def record_passkey_use(self, passkey, *, new_sign_count):
        passkey.sign_count = new_sign_count
        passkey.last_used_at = datetime.now(UTC)
        await self.db.commit()

    async def rename_passkey(self, passkey, *, name, new_sign_count):
        passkey.name = name
        await self.record_passkey_use(passkey, new_sign_count=new_sign_count)
        return passkey

    async def delete_passkey(self, *, user_id, passkey_id, confirming_passkey, new_sign_count):
        await self.record_passkey_use(confirming_passkey, new_sign_count=new_sign_count)
        await self.db.execute(
            delete(Passkey).where(Passkey.id == passkey_id, Passkey.user_id == user_id)
        )
        await self.db.commit()

    async def authenticate(self, request, user):
        if not user.is_active:
            raise HTTPException(403, "Account disabled")
        await self.logout(request)
        token = secrets.token_urlsafe(32)
        self.db.add(
            AuthSession(
                id=hashlib.sha256(token.encode()).hexdigest(),
                user_id=user.id,
                expires_at=stamp(datetime.now(UTC) + timedelta(hours=12)),
            )
        )
        await self.db.commit()
        request.session["auth_token"] = token
        return user

    async def logout(self, request):
        token = request.session.pop("auth_token", None)
        if token:
            await self.db.execute(
                delete(AuthSession).where(
                    AuthSession.id == hashlib.sha256(token.encode()).hexdigest()
                )
            )
            await self.db.commit()

    def access_token(self, user):
        # Browser uses HttpOnly session cookie. No reusable bearer token is issued.
        return ""


async def session_user(request, db):
    token = request.session.get("auth_token")
    if not token:
        return None
    session = await db.get(AuthSession, hashlib.sha256(token.encode()).hexdigest())
    if not session or instant(session.expires_at) <= datetime.now(UTC):
        return None
    user = await db.get(User, session.user_id)
    return user if user and user.is_active else None


def install_auth(app, settings, db_factory):
    async def repository(request: Request):
        async with db_factory() as db:
            yield Repository(db, request)

    async def current_user(request: Request):
        async with db_factory() as db:
            user = await session_user(request, db)
            if not user:
                raise HTTPException(401, "Sign in required")
            return user

    app.include_router(
        create_passkey_router(
            PasskeyRouterConfig(
                service_factory=lambda: FastPasskey(
                    rp_name="WattTally",
                    rp_id=settings.webauthn_rp_id,
                    origin=settings.app_base_url,
                    flow_ttl=timedelta(minutes=5),
                ),
                repository_dependency=repository,
                current_user_dependency=current_user,
                enable_add_link_routes=True,
            )
        ),
        prefix="/api/v1",
    )


async def issue_link(db, user, base_url):
    if not user.is_active:
        raise HTTPException(409, "Account disabled")
    token = secrets.token_urlsafe(32)
    await db.execute(delete(EnrollmentLink).where(EnrollmentLink.user_id == user.id))
    expires = stamp(datetime.now(UTC) + timedelta(hours=24))
    db.add(
        EnrollmentLink(
            id=hashlib.sha256(token.encode()).hexdigest(), user_id=user.id, expires_at=expires
        )
    )
    await db.commit()
    return {"url": base_url.rstrip("/") + "/enroll#" + token, "expires_at": expires}
