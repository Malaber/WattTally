from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import JSON, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def uid():
    return str(uuid4())


def now():
    return datetime.now(UTC).isoformat()


class Base(DeclarativeBase):
    pass


class Instance(Base):
    __tablename__ = "instances"
    id: Mapped[str] = mapped_column(primary_key=True, default=uid)
    name: Mapped[str]
    url: Mapped[str]
    token_cipher: Mapped[str]
    timezone: Mapped[str] = mapped_column(default="Europe/Berlin")
    grid_entity: Mapped[str | None]
    grid_inverted: Mapped[bool] = mapped_column(default=False)
    enabled: Mapped[bool] = mapped_column(default=True)
    last_sync: Mapped[str | None]
    error: Mapped[str | None]


class Vehicle(Base):
    __tablename__ = "vehicles"
    id: Mapped[str] = mapped_column(primary_key=True, default=uid)
    vin: Mapped[str] = mapped_column(unique=True)
    name: Mapped[str]
    plate: Mapped[str]
    vehicle_model: Mapped[str] = mapped_column(default="", server_default="")
    driver_name: Mapped[str] = mapped_column(default="", server_default="")
    driver_address: Mapped[str] = mapped_column(default="", server_default="")
    report_vin: Mapped[str] = mapped_column(default="", server_default="")
    provider: Mapped[str] = mapped_column(default="leapmotor")
    policy: Mapped[str] = mapped_column(default="actual")


class VehicleSource(Base):
    __tablename__ = "vehicle_sources"
    __table_args__ = (UniqueConstraint("instance_id", "vehicle_id"),)
    id: Mapped[str] = mapped_column(primary_key=True, default=uid)
    instance_id: Mapped[str] = mapped_column(ForeignKey("instances.id"))
    vehicle_id: Mapped[str] = mapped_column(ForeignKey("vehicles.id"))
    charging_entity: Mapped[str]
    odometer_entity: Mapped[str | None] = mapped_column(default=None)
    charging_values: Mapped[list] = mapped_column(JSON, default=lambda: ["on", "C", "charging"])


class Charger(Base):
    __tablename__ = "chargers"
    __table_args__ = (UniqueConstraint("instance_id", "energy_entity"),)
    id: Mapped[str] = mapped_column(primary_key=True, default=uid)
    instance_id: Mapped[str] = mapped_column(ForeignKey("instances.id"))
    name: Mapped[str]
    energy_entity: Mapped[str]
    power_entity: Mapped[str]
    session_energy_entity: Mapped[str | None]
    state_entity: Mapped[str]
    active_values: Mapped[list] = mapped_column(JSON, default=lambda: ["charger_charging"])
    default_vehicle_id: Mapped[str | None] = mapped_column(ForeignKey("vehicles.id"))


class Tariff(Base):
    __tablename__ = "tariffs"
    id: Mapped[str] = mapped_column(primary_key=True, default=uid)
    instance_id: Mapped[str | None] = mapped_column(ForeignKey("instances.id"))
    vehicle_id: Mapped[str | None] = mapped_column(ForeignKey("vehicles.id"))
    effective_from: Mapped[str]
    grid_rate: Mapped[str] = mapped_column(default="0")
    solar_rate: Mapped[str] = mapped_column(default="0")
    reimbursement_rate: Mapped[str] = mapped_column(default="0")


class Reading(Base):
    __tablename__ = "readings"
    __table_args__ = (UniqueConstraint("instance_id", "entity_id", "timestamp"),)
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    instance_id: Mapped[str] = mapped_column(ForeignKey("instances.id"), index=True)
    entity_id: Mapped[str] = mapped_column(index=True)
    timestamp: Mapped[str] = mapped_column(String, index=True)
    state: Mapped[str]
    unit: Mapped[str | None]


class Session(Base):
    __tablename__ = "sessions"
    id: Mapped[str] = mapped_column(primary_key=True)
    charger_id: Mapped[str] = mapped_column(ForeignKey("chargers.id"), index=True)
    start: Mapped[str] = mapped_column(index=True)
    end: Mapped[str | None]
    vehicle_id: Mapped[str | None] = mapped_column(ForeignKey("vehicles.id"))
    assignment: Mapped[str] = mapped_column(default="unassigned")
    reviewed_energy_kwh: Mapped[str | None]
    payload: Mapped[dict] = mapped_column(JSON)


class Report(Base):
    __tablename__ = "reports"
    id: Mapped[str] = mapped_column(primary_key=True, default=uid)
    created_at: Mapped[str] = mapped_column(default=now)
    vehicle_id: Mapped[str] = mapped_column(ForeignKey("vehicles.id"))
    month: Mapped[str]
    policy: Mapped[str]
    snapshot: Mapped[dict] = mapped_column(JSON)
    csv_text: Mapped[str]
    sha256: Mapped[str]


class User(Base):
    __tablename__ = "users"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    email: Mapped[str] = mapped_column(unique=True)
    display_name: Mapped[str]
    is_admin: Mapped[bool] = mapped_column(default=False)
    is_approved: Mapped[bool] = mapped_column(default=False)
    is_active: Mapped[bool] = mapped_column(default=True)
    passkeys: Mapped[list[Passkey]] = relationship(
        back_populates="user", lazy="selectin", cascade="all, delete-orphan"
    )


class Passkey(Base):
    __tablename__ = "passkeys"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    name: Mapped[str]
    credential_id: Mapped[str] = mapped_column(unique=True)
    public_key: Mapped[bytes]
    sign_count: Mapped[int]
    created_at: Mapped[datetime] = mapped_column(default=lambda: datetime.now(UTC))
    last_used_at: Mapped[datetime | None]
    user: Mapped[User] = relationship(back_populates="passkeys", lazy="selectin")


class AuthSession(Base):
    __tablename__ = "auth_sessions"
    id: Mapped[str] = mapped_column(primary_key=True)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    expires_at: Mapped[str]
    kind: Mapped[str] = mapped_column(default="browser")


class EnrollmentLink(Base):
    __tablename__ = "enrollment_links"
    id: Mapped[str] = mapped_column(primary_key=True)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    expires_at: Mapped[str]


class MobileCode(Base):
    __tablename__ = "mobile_codes"
    id: Mapped[str] = mapped_column(primary_key=True)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    challenge: Mapped[str]
    expires_at: Mapped[str]
