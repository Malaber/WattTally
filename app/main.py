import asyncio
import contextlib
import hmac
import secrets
from contextlib import asynccontextmanager
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo
from uuid import UUID
from sqlalchemy.exc import IntegrityError

from alembic import command
from alembic.config import Config
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastpasskey import install_fastpasskey_templates
from pydantic import BaseModel, EmailStr, Field, field_validator
from sqlalchemy import select, delete
from starlette.middleware.sessions import SessionMiddleware

from app.accounting import stamp
from app.auth import install_auth, session_user, issue_link
from app.collector import Collector
from app.config import Settings
from app.database import database
from app.ha import HAClient, TokenVault, discover, validate_url
from app.models import (
    User,
    AuthSession,
    EnrollmentLink,
    Charger,
    Instance,
    Report,
    Session,
    Tariff,
    Vehicle,
    VehicleSource,
)
from app.mobile import install_mobile
from app.reports import freeze_report, pdf_bytes, xlsx_bytes

ROOT = Path(__file__).parent


class AccountInput(BaseModel):
    email: EmailStr
    display_name: str = Field(min_length=1, max_length=100)
    is_admin: bool = False


class AccountAccess(BaseModel):
    is_admin: bool
    is_active: bool
    is_approved: bool


class ConnectionInput(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    url: str
    token: str = Field(min_length=10)


class MappingInput(BaseModel):
    grid_entity: str | None = None
    grid_inverted: bool = False
    enabled: bool = True


class OdometerInput(BaseModel):
    entity_id: str | None = Field(default=None, max_length=255)


class VehicleInput(BaseModel):
    vehicle_model: str = Field(default="", max_length=160)
    driver_name: str = Field(default="", max_length=160)
    driver_address: str = Field(default="", max_length=500)
    report_vin: str = Field(default="", max_length=40)

    name: str = Field(min_length=1, max_length=100)
    plate: str = Field(max_length=30)
    policy: str = "actual"

    @field_validator("policy")
    @classmethod
    def policy_valid(cls, v):
        if v not in {"actual", "employer"}:
            raise ValueError("Unknown policy")
        return v


class ChargerInput(BaseModel):
    instance_id: str
    name: str = Field(min_length=1, max_length=100)
    energy_entity: str = Field(min_length=1)
    power_entity: str = Field(min_length=1)
    session_energy_entity: str | None = None
    state_entity: str = Field(min_length=1)
    active_values: list[str] = Field(default=["charger_charging"], min_length=1)
    default_vehicle_id: str | None = None


class TariffInput(BaseModel):
    instance_id: str | None = None
    vehicle_id: str | None = None
    effective_from: datetime
    grid_rate: Decimal = Field(default=0, ge=0, le=100)
    solar_rate: Decimal = Field(default=0, ge=0, le=100)
    reimbursement_rate: Decimal = Field(default=0, ge=0, le=100)

    @field_validator("effective_from")
    @classmethod
    def aware(cls, v):
        if v.tzinfo is None:
            raise ValueError("Timezone offset required")
        return v


class AssignmentInput(BaseModel):
    vehicle_id: str
    energy_kwh: Decimal | None = Field(default=None, gt=0, allow_inf_nan=False)


class ExportInput(BaseModel):
    vehicle_id: str
    month: str = Field(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")
    timezone: str = "Europe/Berlin"


def public(obj):
    return {c.name: getattr(obj, c.name) for c in obj.__table__.columns if c.name != "token_cipher"}


def create_app(settings=None):
    settings = settings or Settings()
    settings.validate_secrets()
    engine, db_factory = database(settings.database_url)
    vault = TokenVault(settings.secret_key)
    collector = Collector(db_factory, vault, settings)

    @asynccontextmanager
    async def lifespan(app):
        cfg = Config(str(ROOT.parent / "alembic.ini"))
        cfg.set_main_option("script_location", str(ROOT.parent / "migrations"))
        cfg.attributes["database_url"] = settings.database_url
        await asyncio.to_thread(command.upgrade, cfg, "head")
        task = asyncio.create_task(collector.run()) if settings.collector_enabled else None
        yield
        if task:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        await engine.dispose()

    app = FastAPI(
        title="WattTally",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url="/api/openapi.json",
    )
    app.state.db = db_factory
    app.state.collector = collector
    app.state.vault = vault
    templates = Jinja2Templates(directory=ROOT / "web/templates")
    install_fastpasskey_templates(templates.env)
    labels = {
        "settings.security": "Account security",
        "settings.your_passkeys": "Your passkeys",
        "settings.add_another": "Add another passkey",
        "settings.helper": "Use your device or security key to sign in. Keep a second passkey for recovery.",
        "settings.name_this_passkey": "Name this passkey",
        "settings.passkey_name_placeholder": "e.g. iPhone or security key",
        "settings.no_passkeys_title": "No passkeys",
        "settings.no_passkeys_body": "Add a passkey to this account.",
        "settings.confirm_deletion": "Confirm deletion",
        "settings.delete_this_passkey": "Delete this passkey?",
        "settings.delete_help_generic": "Verify with another passkey to remove this one.",
        "settings.continue_to_verification": "Verify and delete",
    }
    templates.env.globals["t"] = lambda key: labels.get(
        key, key.split(".")[-1].replace("_", " ").capitalize()
    )
    install_auth(app, settings, db_factory)
    install_mobile(app, settings, db_factory, templates)

    @app.middleware("http")
    async def protect(request, call_next):
        path = request.url.path
        if path.startswith("/api/") and not path.startswith("/api/v1/auth/"):
            async with db_factory() as db:
                user = await session_user(request, db)
            if not user:
                return JSONResponse({"detail": "Sign in required"}, status_code=401)
            if not user.is_approved:
                return JSONResponse(
                    {"detail": "Account awaiting administrator approval"}, status_code=403
                )
            if path.startswith("/api/admin/") and not user.is_admin:
                return JSONResponse({"detail": "Administrator required"}, status_code=403)
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            if path.startswith("/api/v1/auth/") and path != "/api/v1/auth/mobile/token":
                if request.headers.get("origin") != settings.app_base_url.rstrip("/"):
                    return JSONResponse({"detail": "Invalid request origin"}, status_code=403)
            expected = request.session.get("csrf", "")
            supplied = request.headers.get("x-csrf-token", "")
            if (
                not path.startswith("/api/v1/auth/")
                and not request.headers.get("authorization", "").startswith("Bearer ")
                and (not expected or not hmac.compare_digest(expected, supplied))
            ):
                return JSONResponse(
                    {"detail": "Reload the page before submitting"}, status_code=403
                )
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
        )
        return response

    # Added last: the session middleware wraps authentication/CSRF middleware.
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.secret_key,
        https_only=settings.secure_cookies,
        same_site="strict",
        max_age=43200,
    )
    app.mount("/static", StaticFiles(directory=ROOT / "web/static"), name="static")

    @app.get("/", response_class=HTMLResponse)
    async def home(request: Request):
        request.session.setdefault("csrf", secrets.token_urlsafe(32))
        async with db_factory() as db:
            user = await session_user(request, db)
        return templates.TemplateResponse(
            request=request,
            name="index.html",
            context={
                "authenticated": bool(user),
                "csrf": request.session["csrf"],
                "approved": bool(user and user.is_approved),
                "admin": bool(user and user.is_admin),
            },
        )

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    @app.get("/login")
    async def login_redirect():
        return RedirectResponse("/")

    @app.get("/enroll", response_class=HTMLResponse)
    async def enroll(request: Request):
        request.session.setdefault("csrf", secrets.token_urlsafe(32))
        return templates.TemplateResponse(
            request=request, name="enroll.html", context={"csrf": request.session["csrf"]}
        )

    @app.get("/api/admin/users")
    async def users():
        async with db_factory() as db:
            return [
                {
                    "id": str(u.id),
                    "email": u.email,
                    "display_name": u.display_name,
                    "is_admin": u.is_admin,
                    "is_active": u.is_active,
                    "is_approved": u.is_approved,
                    "passkeys": len(u.passkeys),
                }
                for u in await db.scalars(select(User).order_by(User.email))
            ]

    @app.post("/api/admin/users")
    async def create_user(data: AccountInput):
        async with db_factory() as db:
            user = User(
                email=str(data.email).casefold(),
                display_name=data.display_name,
                is_admin=data.is_admin,
                is_approved=True,
            )
            db.add(user)
            try:
                await db.commit()
            except IntegrityError:
                await db.rollback()
                raise HTTPException(409, "Account already exists") from None
            return await issue_link(db, user, settings.app_base_url)

    @app.post("/api/admin/users/{user_id}/invite")
    async def invite_user(user_id: UUID):
        async with db_factory() as db:
            return await issue_link(db, await require(db, User, user_id), settings.app_base_url)

    @app.delete("/api/admin/users/{user_id}/invite")
    async def revoke_invite(user_id: UUID):
        async with db_factory() as db:
            await db.execute(delete(EnrollmentLink).where(EnrollmentLink.user_id == user_id))
            await db.commit()
        return {"ok": True}

    @app.patch("/api/admin/users/{user_id}")
    async def update_user(user_id: UUID, data: AccountAccess, request: Request):
        async with db_factory() as db:
            actor = await session_user(request, db)
            if actor.id == user_id:
                raise HTTPException(409, "Use another administrator to change your own access")
            user = await require(db, User, user_id)
            for key, value in data.model_dump().items():
                setattr(user, key, value)
            if not user.is_active or not user.is_approved:
                await db.execute(delete(AuthSession).where(AuthSession.user_id == user_id))
                await db.execute(delete(EnrollmentLink).where(EnrollmentLink.user_id == user_id))
            await db.commit()
        return {"ok": True}

    async def require(db, model, key):
        obj = await db.get(model, key)
        if obj is None:
            raise HTTPException(404, "Not found")
        return obj

    @app.get("/api/dashboard")
    async def dashboard():
        async with db_factory() as db:
            result = {}
            for name, model in [
                ("instances", Instance),
                ("vehicles", Vehicle),
                ("vehicle_sources", VehicleSource),
                ("chargers", Charger),
                ("tariffs", Tariff),
                ("sessions", Session),
                ("reports", Report),
            ]:
                objects = (await db.scalars(select(model))).all()
                result[name] = [public(o) for o in objects]
            result["sessions"].sort(key=lambda s: s["start"], reverse=True)
            for report in result["reports"]:
                report.pop("csv_text", None)
            return result

    @app.post("/api/instances")
    async def add_instance(data: ConnectionInput):
        try:
            url = validate_url(data.url)
            config = await HAClient(url, data.token).get("/api/config")
            timezone_name = config.get("time_zone", "Europe/Berlin")
            ZoneInfo(timezone_name)
        except Exception:
            raise HTTPException(
                400, "Cannot read Home Assistant. Check URL, token, TLS and network access."
            ) from None
        async with db_factory() as db:
            instance = Instance(
                name=data.name, url=url, token_cipher=vault.seal(data.token), timezone=timezone_name
            )
            db.add(instance)
            await db.commit()
            return public(instance)

    @app.patch("/api/instances/{key}")
    async def map_instance(key: str, data: MappingInput):
        async with collector.lock, db_factory() as db:
            instance = await require(db, Instance, key)
            if data.grid_entity != instance.grid_entity:
                instance.last_sync = None
            for k, v in data.model_dump().items():
                setattr(instance, k, v)
            await collector.rebuild(db, instance)
            await db.commit()
            return public(instance)

    @app.post("/api/instances/{key}/credentials")
    async def credentials(key: str, data: ConnectionInput):
        try:
            await HAClient(data.url, data.token).get("/api/config")
        except Exception:
            raise HTTPException(400, "Connection check failed") from None
        async with collector.lock, db_factory() as db:
            instance = await require(db, Instance, key)
            # Keep source identity stable; moving to another HA requires a new instance.
            if validate_url(data.url) != instance.url:
                raise HTTPException(
                    400, "Create a new connection for a different Home Assistant URL"
                )
            instance.token_cipher = vault.seal(data.token)
            instance.name = data.name
            await db.commit()
            return public(instance)

    async def odometer_entities(db, source):
        site = await require(db, Instance, source.instance_id)
        try:
            states = await HAClient(site.url, vault.open(site.token_cipher)).states()
        except Exception:
            raise HTTPException(
                400, "Cannot read odometer sensors. Check Home Assistant connection."
            ) from None
        return [
            s
            for s in states
            if s["entity_id"].startswith("sensor.")
            and s.get("attributes", {}).get("unit_of_measurement") in {"km", "mi"}
        ]

    @app.get("/api/vehicle-sources/{key}/odometer")
    async def odometer_options(key: str):
        async with db_factory() as db:
            source = await require(db, VehicleSource, key)
            return [
                {
                    "id": s["entity_id"],
                    "name": s.get("attributes", {}).get("friendly_name", s["entity_id"]),
                    "state": s["state"],
                    "unit": s["attributes"]["unit_of_measurement"],
                }
                for s in await odometer_entities(db, source)
            ]

    @app.patch("/api/vehicle-sources/{key}/odometer")
    async def map_odometer(key: str, data: OdometerInput):
        async with collector.lock, db_factory() as db:
            source = await require(db, VehicleSource, key)
            if data.entity_id and data.entity_id not in {
                s["entity_id"] for s in await odometer_entities(db, source)
            }:
                raise HTTPException(
                    422, "Choose a distance sensor in km or mi from this Home Assistant."
                )
            source.odometer_entity = data.entity_id
            source.odometer_manual = True
            site = await require(db, Instance, source.instance_id)
            site.last_sync = None
            await collector.rebuild(db, site)
            await db.commit()
            return public(source)

    @app.post("/api/instances/{key}/discover")
    async def discovery(key: str):
        async with collector.lock, db_factory() as db:
            instance = await require(db, Instance, key)
            try:
                result = await discover(HAClient(instance.url, vault.open(instance.token_cipher)))
            except Exception:
                raise HTTPException(
                    400, "Discovery failed. Check token permissions and connectivity."
                ) from None
            for found in result["vehicles"]:
                vehicle = await db.scalar(select(Vehicle).where(Vehicle.vin == found["vin"]))
                if vehicle is None:
                    vehicle = Vehicle(
                        vin=found["vin"], name=found["name"], plate="", provider=found["provider"]
                    )
                    db.add(vehicle)
                    await db.flush()
                source = await db.scalar(
                    select(VehicleSource).where(
                        VehicleSource.instance_id == key, VehicleSource.vehicle_id == vehicle.id
                    )
                )
                if found["charging_entity"]:
                    if source is None:
                        db.add(
                            VehicleSource(
                                instance_id=key,
                                vehicle_id=vehicle.id,
                                charging_entity=found["charging_entity"],
                                odometer_entity=found.get("odometer_entity"),
                            )
                        )
                    else:
                        source.charging_entity = found["charging_entity"]
                        if not source.odometer_manual and found.get("odometer_entity"):
                            source.odometer_entity = found["odometer_entity"]
                found["id"] = vehicle.id
            await db.commit()
            return result

    @app.post("/api/instances/{key}/sync")
    async def sync(key: str):
        async with db_factory() as db:
            await require(db, Instance, key)
        await collector.sync(key)
        return {"ok": True}

    @app.patch("/api/vehicles/{key}")
    async def edit_vehicle(key: str, data: VehicleInput):
        async with db_factory() as db:
            vehicle = await require(db, Vehicle, key)
            for k, v in data.model_dump(exclude_unset=True).items():
                setattr(vehicle, k, v)
            await db.commit()
            return public(vehicle)

    @app.post("/api/chargers")
    async def add_charger(data: ChargerInput):
        async with collector.lock, db_factory() as db:
            site = await require(db, Instance, data.instance_id)
            site.last_sync = None
            if data.default_vehicle_id:
                await require(db, Vehicle, data.default_vehicle_id)
            existing = await db.scalar(
                select(Charger).where(
                    Charger.instance_id == data.instance_id,
                    Charger.energy_entity == data.energy_entity,
                )
            )
            if existing:
                raise HTTPException(409, "This energy meter already belongs to a charger")
            charger = Charger(**data.model_dump())
            db.add(charger)
            await db.commit()
            return public(charger)

    @app.patch("/api/chargers/{key}")
    async def edit_charger(key: str, data: ChargerInput):
        async with collector.lock, db_factory() as db:
            charger = await require(db, Charger, key)
            if data.instance_id != charger.instance_id:
                raise HTTPException(400, "A charger cannot be moved to another home")
            site = await require(db, Instance, charger.instance_id)
            if data.default_vehicle_id:
                await require(db, Vehicle, data.default_vehicle_id)
            existing = await db.scalar(
                select(Charger).where(
                    Charger.instance_id == data.instance_id,
                    Charger.energy_entity == data.energy_entity,
                    Charger.id != key,
                )
            )
            if existing:
                raise HTTPException(409, "This energy meter already belongs to a charger")
            # Changing cycle boundaries would leave old sessions alongside rebuilt ones.
            boundary_changed = any(
                getattr(charger, field) != getattr(data, field)
                for field in ("energy_entity", "state_entity", "active_values")
            )
            if boundary_changed and await db.scalar(
                select(Session.id).where(Session.charger_id == key).limit(1)
            ):
                raise HTTPException(
                    409,
                    "This charger has retained sessions. Meter and charging-state changes "
                    "need a reviewed history migration; other settings can still be edited.",
                )
            for field, value in data.model_dump().items():
                setattr(charger, field, value)
            site.last_sync = None
            await db.commit()
            return public(charger)

    @app.post("/api/tariffs")
    async def add_tariff(data: TariffInput):
        if bool(data.instance_id) == bool(data.vehicle_id):
            raise HTTPException(400, "Select either a home tariff or a vehicle reimbursement rate")
        async with collector.lock, db_factory() as db:
            await require(
                db, Instance if data.instance_id else Vehicle, data.instance_id or data.vehicle_id
            )
            values = data.model_dump()
            values["effective_from"] = stamp(data.effective_from)
            for k in ["grid_rate", "solar_rate", "reimbursement_rate"]:
                values[k] = str(values[k])
            tariff = await db.scalar(
                select(Tariff).where(
                    Tariff.instance_id == data.instance_id,
                    Tariff.vehicle_id == data.vehicle_id,
                    Tariff.effective_from == values["effective_from"],
                )
            )
            if tariff is None:
                tariff = Tariff(**values)
                db.add(tariff)
            else:
                for key, value in values.items():
                    setattr(tariff, key, value)
            await db.flush()
            if data.instance_id:
                await collector.rebuild(db, await db.get(Instance, data.instance_id))
            await db.commit()
            return public(tariff)

    @app.patch("/api/sessions/{key}/assignment")
    async def assignment(key: str, data: AssignmentInput):
        async with collector.lock, db_factory() as db:
            session = await require(db, Session, key)
            await require(db, Vehicle, data.vehicle_id)
            if data.energy_kwh is not None:
                if not session.end:
                    raise HTTPException(409, "Wait until charging finishes before confirming.")
                if data.energy_kwh != Decimal(session.payload["energy_kwh"]):
                    raise HTTPException(
                        409, "Energy changed during review. Refresh and confirm the updated total."
                    )
                session.reviewed_energy_kwh = str(data.energy_kwh)
            elif session.vehicle_id != data.vehicle_id:
                session.reviewed_energy_kwh = None
            session.vehicle_id = data.vehicle_id
            session.assignment = "confirmed"
            await db.commit()
            return public(session)

    @app.post("/api/reports")
    async def report(data: ExportInput):
        async with db_factory() as db:
            vehicle = await require(db, Vehicle, data.vehicle_id)
            if not vehicle.plate:
                raise HTTPException(400, "Add the vehicle license plate first")
            sessions = (
                await db.scalars(
                    select(Session).where(Session.vehicle_id == vehicle.id).order_by(Session.start)
                )
            ).all()
            tariffs = (
                await db.scalars(select(Tariff).where(Tariff.vehicle_id == vehicle.id))
            ).all()
            try:
                snapshot, csv_text, digest = freeze_report(
                    vehicle, sessions, data.month, data.timezone, tariffs
                )
            except (ValueError, KeyError) as exc:
                raise HTTPException(400, str(exc)) from None
            report = Report(
                vehicle_id=vehicle.id,
                month=data.month,
                policy=vehicle.policy,
                snapshot=snapshot,
                csv_text=csv_text,
                sha256=digest,
            )
            db.add(report)
            await db.commit()
            return {"id": report.id, "snapshot": snapshot}

    @app.get("/api/reports/{key}/{format}")
    async def download(key: str, format: str):
        async with db_factory() as db:
            report = await require(db, Report, key)
            if format == "csv":
                content, mime = report.csv_text.encode("utf-8"), "text/csv; charset=utf-8"
            elif format == "xlsx":
                content, mime = (
                    await asyncio.to_thread(xlsx_bytes, report),
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
            elif format == "pdf":
                content, mime = await asyncio.to_thread(pdf_bytes, report), "application/pdf"
            else:
                raise HTTPException(404, "Unknown export format")
            return Response(
                content,
                media_type=mime,
                headers={
                    "Content-Disposition": f'attachment; filename="watttally-{report.month}-{report.id[:8]}.{format}"'
                },
            )

    return app
