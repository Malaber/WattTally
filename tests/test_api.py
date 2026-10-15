import asyncio
import hashlib
import json
from base64 import b64encode
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from itsdangerous import TimestampSigner

from app.config import Settings
from app.main import create_app
from app.models import AuthSession, User


@pytest.fixture
def client(tmp_path):
    settings = Settings(
        secret_key="x" * 40,
        database_url="sqlite+aiosqlite:///" + str(tmp_path / "test.db"),
        collector_enabled=False,
    )
    app = create_app(settings)
    with TestClient(app, base_url="http://localhost:8000") as c:
        c.app = app
        c.settings = settings
        yield c


def authorize(client):
    token = "test-session"

    async def seed():
        async with client.app.state.db() as db:
            user = User(
                id=uuid4(),
                email="owner@example.com",
                display_name="Owner",
                is_admin=True,
                is_approved=True,
            )
            db.add(user)
            await db.flush()
            db.add(
                AuthSession(
                    id=hashlib.sha256(token.encode()).hexdigest(),
                    user_id=user.id,
                    expires_at=(datetime.now(UTC) + timedelta(hours=1)).isoformat(),
                )
            )
            await db.commit()

    asyncio.run(seed())
    cookie = (
        TimestampSigner(client.settings.secret_key)
        .sign(b64encode(json.dumps({"auth_token": token, "csrf": "test-csrf"}).encode()))
        .decode()
    )
    client.cookies.set("session", cookie)
    return {"x-csrf-token": "test-csrf"}


def test_password_login_does_not_exist_and_data_is_protected(client):
    assert client.get("/api/dashboard").status_code == 401
    page = client.get("/")
    assert page.status_code == 200
    assert "data-passkey-login" in page.text
    assert 'name="password"' not in page.text
    assert client.post("/api/login", json={"password": "test"}).status_code in (401, 403, 404)


def test_public_registration_offers_passkey(client):
    r = client.post(
        "/api/v1/auth/register/options",
        json={"email": "owner@example.com", "display_name": "Owner"},
        headers={"origin": "http://localhost:8000"},
    )
    assert r.status_code == 200
    assert r.json()["authenticatorSelection"]["userVerification"] == "required"


def test_cross_origin_passkey_request_rejected(client):
    assert (
        client.post(
            "/api/v1/auth/login/options", json={}, headers={"origin": "https://evil.example"}
        ).status_code
        == 403
    )


def test_connection_never_exposes_token(client, monkeypatch):
    async def get(*args, **kwargs):
        return {"time_zone": "Europe/Berlin"}

    monkeypatch.setattr("app.main.HAClient.get", get)
    h = authorize(client)
    r = client.post(
        "/api/instances",
        json={"name": "Home", "url": "https://ha.example", "token": "secret-token-12345"},
        headers=h,
    )
    assert r.status_code == 200, r.text
    assert "token" not in r.text
    assert "secret-token" not in client.get("/api/dashboard").text


def test_csrf_required_for_configuration(client):
    authorize(client)
    assert client.post("/api/instances", json={}).status_code == 403


def test_logout_revokes_server_session(client):
    authorize(client)
    cookie = client.cookies.get("session")
    r = client.post("/api/v1/auth/logout", json={}, headers={"origin": "http://localhost:8000"})
    assert r.status_code == 200
    client.cookies.clear()
    client.cookies.set("session", cookie)
    assert client.get("/api/dashboard").status_code == 401


def test_admin_account_invites_and_revoke(client):
    headers = authorize(client)
    result = client.post(
        "/api/admin/users",
        json={"email": "member@example.com", "display_name": "Member"},
        headers=headers,
    )
    assert result.status_code == 200, result.text
    token = result.json()["url"].split("#")[1]
    users = client.get("/api/admin/users").json()
    member = next(u for u in users if u["email"] == "member@example.com")
    assert member["is_approved"] and not member["is_admin"] and member["passkeys"] == 0
    origin = {"origin": "http://localhost:8000"}
    assert (
        client.post(
            f"/api/v1/auth/passkey-add/{token}/options", json={}, headers=origin
        ).status_code
        == 200
    )
    assert (
        client.delete(f"/api/admin/users/{member['id']}/invite", headers=headers).status_code == 200
    )
    assert (
        client.post(
            f"/api/v1/auth/passkey-add/{token}/options", json={}, headers=origin
        ).status_code
        == 404
    )
    assert (
        client.post(
            "/api/admin/users",
            json={"email": "member@example.com", "display_name": "Duplicate"},
            headers=headers,
        ).status_code
        == 409
    )


def test_pending_and_member_access_boundaries(client):
    headers = authorize(client)

    async def change(approved):
        from sqlalchemy import select

        async with client.app.state.db() as db:
            user = await db.scalar(select(User))
            user.is_admin = False
            user.is_approved = approved
            await db.commit()

    asyncio.run(change(False))
    assert client.get("/api/dashboard").status_code == 403
    assert "Account created" in client.get("/").text
    assert client.get("/api/admin/users").status_code == 403
    asyncio.run(change(True))
    assert client.get("/api/dashboard").status_code == 200
    assert client.get("/api/admin/users").status_code == 403
    assert client.post("/api/admin/users", json={}, headers=headers).status_code == 403


def test_enrollment_consumed_once_and_expiry(client):
    from types import SimpleNamespace
    from app.auth import Repository, issue_link
    from app.models import EnrollmentLink

    async def exercise():
        async with client.app.state.db() as db:
            user = User(email="invite@example.com", display_name="Invited", is_approved=True)
            db.add(user)
            await db.commit()
            link = await issue_link(db, user, "http://localhost:8000")
            token = link["url"].split("#")[1]
            repo = Repository(db, None)
            credential = SimpleNamespace(
                credential_id="unique-key", public_key=b"key", sign_count=0
            )
            assert await repo.complete_add_link(
                token=token, user_id=user.id, name="Key", credential=credential
            )
            assert (
                await repo.complete_add_link(
                    token=token, user_id=user.id, name="Key", credential=credential
                )
                is None
            )
            assert await repo.add_link_user(token) is None
            await db.refresh(user)
            link = await issue_link(db, user, "http://localhost:8000")
            token = link["url"].split("#")[1]
            row = await db.get(EnrollmentLink, hashlib.sha256(token.encode()).hexdigest())
            row.expires_at = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
            await db.commit()
            assert await repo.add_link_user(token) is None
            assert (
                await repo.complete_add_link(
                    token=token, user_id=user.id, name="Key", credential=credential
                )
                is None
            )

    asyncio.run(exercise())


def test_mobile_pkce_login_replay_and_logout(client):
    import base64
    from urllib.parse import parse_qs, urlsplit

    headers = authorize(client)
    verifier = "v" * 43
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    )
    response = client.get(
        "/api/v1/auth/mobile/authorize", params={"state": "s" * 43, "code_challenge": challenge}
    )
    assert response.status_code == 200
    assert "Connect iOS app" in response.text
    assert client.post("/api/mobile/approve").status_code == 403
    result = client.post("/api/mobile/approve", headers=headers)
    assert result.status_code == 200
    callback = urlsplit(result.json()["callback"])
    assert callback.scheme == "de.malaber.watttally" and callback.netloc == "auth"
    code = parse_qs(callback.query)["code"][0]
    assert (
        client.post(
            "/api/v1/auth/mobile/token", json={"code": code, "code_verifier": "x" * 43}
        ).status_code
        == 401
    )
    response = client.post(
        "/api/v1/auth/mobile/token", json={"code": code, "code_verifier": verifier}
    )
    assert response.status_code == 200, response.text
    token = response.json()["access_token"]
    assert (
        client.post(
            "/api/v1/auth/mobile/token", json={"code": code, "code_verifier": verifier}
        ).status_code
        == 401
    )
    client.cookies.clear()
    bearer = {"Authorization": "Bearer " + token}
    assert client.get("/api/dashboard", headers=bearer).status_code == 200
    assert client.post("/api/mobile/logout", headers=bearer).status_code == 200
    assert client.get("/api/dashboard", headers=bearer).status_code == 401


def test_browser_token_cannot_bypass_csrf_as_bearer(client):
    authorize(client)
    assert (
        client.get("/api/dashboard", headers={"Authorization": "Bearer test-session"}).status_code
        == 401
    )


def test_mobile_authorization_rejects_unapproved_and_expired_codes(client):
    from app.models import MobileCode

    async def seed():
        async with client.app.state.db() as db:
            user = User(email="pending@example.com", display_name="Pending")
            db.add(user)
            await db.flush()
            import base64

            challenge = (
                base64.urlsafe_b64encode(hashlib.sha256(("v" * 43).encode()).digest())
                .decode()
                .rstrip("=")
            )
            for code, expiry in [
                ("p" * 43, timedelta(minutes=1)),
                ("e" * 43, timedelta(minutes=-1)),
            ]:
                db.add(
                    MobileCode(
                        id=hashlib.sha256(code.encode()).hexdigest(),
                        user_id=user.id,
                        challenge=challenge,
                        expires_at=(datetime.now(UTC) + expiry).isoformat(),
                    )
                )
            await db.commit()

    asyncio.run(seed())
    for code in ["p" * 43, "e" * 43]:
        assert (
            client.post(
                "/api/v1/auth/mobile/token", json={"code": code, "code_verifier": "v" * 43}
            ).status_code
            == 401
        )


def test_charger_mapping_repair_preserves_history(client):
    from app.models import Charger, Instance, Reading, Session

    headers = authorize(client)

    async def seed():
        async with client.app.state.db() as db:
            db.add(Instance(id="home", name="Home", url="https://ha.invalid", token_cipher="x"))
            await db.flush()
            db.add(
                Charger(
                    id="charger",
                    instance_id="home",
                    name="Wallbox",
                    energy_entity="sensor.energy",
                    power_entity="sensor.house",
                    state_entity="sensor.backup",
                )
            )
            db.add(
                Reading(
                    instance_id="home",
                    entity_id="sensor.backup",
                    timestamp="2026-10-01T00:00:00+00:00",
                    state="idle",
                )
            )
            await db.commit()

    asyncio.run(seed())
    mapping = dict(
        instance_id="home",
        name="Wallbox",
        energy_entity="sensor.energy",
        power_entity="sensor.power",
        state_entity="sensor.charging",
        session_energy_entity="sensor.session_energy",
        active_values=["charging"],
    )
    assert client.patch("/api/chargers/charger", json=mapping).status_code == 403
    response = client.patch("/api/chargers/charger", json=mapping, headers=headers)
    assert response.status_code == 200, response.text
    assert response.json()["id"] == "charger"
    assert response.json()["state_entity"] == "sensor.charging"
    for bad in [dict(mapping, state_entity=""), dict(mapping, active_values=[])]:
        assert client.patch("/api/chargers/charger", json=bad, headers=headers).status_code == 422
    assert (
        client.patch(
            "/api/chargers/charger", json=dict(mapping, instance_id="other"), headers=headers
        ).status_code
        == 400
    )

    async def retain():
        async with client.app.state.db() as db:
            assert (await db.get(Instance, "home")).last_sync is None
            assert (await db.get(Reading, 1)).state == "idle"
            db.add(
                Session(
                    id="cycle",
                    charger_id="charger",
                    start="2026-10-01T01:00:00+00:00",
                    assignment="confirmed",
                    payload={},
                )
            )
            await db.commit()

    asyncio.run(retain())
    assert (
        client.patch(
            "/api/chargers/charger",
            json=dict(mapping, state_entity="sensor.other"),
            headers=headers,
        ).status_code
        == 409
    )
    assert (
        client.patch(
            "/api/chargers/charger",
            json=dict(mapping, power_entity="sensor.corrected"),
            headers=headers,
        ).status_code
        == 200
    )


def test_charge_confirmation_checks_displayed_energy(client):
    from app.models import Charger, Instance, Session, Vehicle

    headers = authorize(client)

    async def seed():
        async with client.app.state.db() as db:
            db.add(Instance(id="home", name="Home", url="https://ha.invalid", token_cipher="x"))
            db.add(Vehicle(id="car", vin="test", name="Car", plate="TEST"))
            await db.flush()
            db.add(
                Charger(
                    id="charger",
                    instance_id="home",
                    name="Wallbox",
                    energy_entity="sensor.energy",
                    power_entity="sensor.power",
                    state_entity="sensor.state",
                )
            )
            await db.flush()
            db.add(
                Session(
                    id="cycle",
                    charger_id="charger",
                    start="2026-10-01T00:00:00+00:00",
                    end="2026-10-01T01:00:00+00:00",
                    assignment="suggested",
                    vehicle_id="car",
                    payload={"energy_kwh": "20", "flags": ["meter_gap"]},
                )
            )
            await db.commit()

    asyncio.run(seed())
    url = "/api/sessions/cycle/assignment"
    assert (
        client.patch(
            url, headers=headers, json={"vehicle_id": "car", "energy_kwh": "19"}
        ).status_code
        == 409
    )
    response = client.patch(url, headers=headers, json={"vehicle_id": "car", "energy_kwh": "20"})
    assert response.status_code == 200
    assert response.json()["reviewed_energy_kwh"] == "20"
    assert response.json()["payload"]["flags"] == ["meter_gap"]

    async def reopen():
        async with client.app.state.db() as db:
            (await db.get(Session, "cycle")).end = None
            await db.commit()

    asyncio.run(reopen())
    assert (
        client.patch(
            url, headers=headers, json={"vehicle_id": "car", "energy_kwh": "20"}
        ).status_code
        == 409
    )
