"""Native app authorization: browser passkey, explicit consent, one-use S256 code."""

import base64
import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode
from fastapi import HTTPException, Query, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import delete
from app.accounting import stamp
from app.auth import session_user
from app.models import AuthSession, MobileCode, User


class Exchange(BaseModel):
    code: str = Field(pattern=r"^[A-Za-z0-9_-]{43}$")
    code_verifier: str = Field(pattern=r"^[A-Za-z0-9._~-]{43,128}$")


def install_mobile(app, settings, db_factory, templates):
    @app.get("/api/v1/auth/mobile/authorize")
    async def authorize(
        request: Request,
        state: str = Query(pattern=r"^[A-Za-z0-9_-]{43,128}$"),
        code_challenge: str = Query(pattern=r"^[A-Za-z0-9_-]{43}$"),
    ):
        request.session["mobile_flow"] = {
            "state": state,
            "challenge": code_challenge,
            "until": datetime.now(UTC).timestamp() + 300,
        }
        return RedirectResponse("/mobile", status_code=303)

    @app.get("/mobile")
    async def consent(request: Request):
        flow = request.session.get("mobile_flow", {})
        if flow.get("until", 0) <= datetime.now(UTC).timestamp():
            raise HTTPException(400, "Sign-in expired. Start again in WattTally.")
        request.session.setdefault("csrf", secrets.token_urlsafe(32))
        async with db_factory() as db:
            user = await session_user(request, db)
        return templates.TemplateResponse(
            request=request,
            name="mobile.html",
            context={"csrf": request.session["csrf"], "user": user},
        )

    @app.post("/api/mobile/approve")
    async def approve(request: Request):
        # Only the browser that began this flow may approve it.
        if request.headers.get("authorization"):
            raise HTTPException(403, "Browser approval required")
        flow = request.session.pop("mobile_flow", {})
        if flow.get("until", 0) <= datetime.now(UTC).timestamp():
            raise HTTPException(400, "Sign-in expired. Start again in WattTally.")
        async with db_factory() as db:
            user = await session_user(request, db)
            if not user or not user.is_approved:
                raise HTTPException(403, "Household approval required")
            await db.execute(
                delete(MobileCode).where(MobileCode.expires_at <= stamp(datetime.now(UTC)))
            )
            code = secrets.token_urlsafe(32)
            db.add(
                MobileCode(
                    id=hashlib.sha256(code.encode()).hexdigest(),
                    user_id=user.id,
                    challenge=flow["challenge"],
                    expires_at=stamp(datetime.now(UTC) + timedelta(minutes=2)),
                )
            )
            await db.commit()
        return {
            "callback": "de.malaber.watttally://auth?"
            + urlencode({"state": flow["state"], "code": code})
        }

    @app.post("/api/v1/auth/mobile/token")
    async def exchange(data: Exchange):
        challenge = (
            base64.urlsafe_b64encode(hashlib.sha256(data.code_verifier.encode()).digest())
            .decode()
            .rstrip("=")
        )
        async with db_factory() as db:
            user_id = (
                await db.execute(
                    delete(MobileCode)
                    .where(
                        MobileCode.id == hashlib.sha256(data.code.encode()).hexdigest(),
                        MobileCode.challenge == challenge,
                        MobileCode.expires_at > stamp(datetime.now(UTC)),
                    )
                    .returning(MobileCode.user_id)
                )
            ).scalar_one_or_none()
            user = await db.get(User, user_id) if user_id else None
            if not user or not user.is_active or not user.is_approved:
                await db.commit()
                raise HTTPException(401, "Sign-in expired or account not approved")
            token = secrets.token_urlsafe(32)
            db.add(
                AuthSession(
                    id=hashlib.sha256(token.encode()).hexdigest(),
                    user_id=user.id,
                    kind="mobile",
                    expires_at=stamp(datetime.now(UTC) + timedelta(days=30)),
                )
            )
            await db.commit()
        return {"access_token": token, "user_id": str(user.id), "token_type": "bearer"}

    @app.post("/api/mobile/logout")
    async def logout(request: Request):
        authorization = request.headers.get("authorization", "")
        if not authorization.startswith("Bearer "):
            raise HTTPException(401, "Mobile session required")
        async with db_factory() as db:
            await db.execute(
                delete(AuthSession).where(
                    AuthSession.id == hashlib.sha256(authorization[7:].encode()).hexdigest(),
                    AuthSession.kind == "mobile",
                )
            )
            await db.commit()
        return {"ok": True}
