from datetime import UTC, datetime, timedelta
from types import SimpleNamespace as NS
import pytest
from sqlalchemy import select, func
from app.database import database
from app.models import Base, Instance, Charger, Reading, Session, Vehicle, VehicleSource
from app.collector import Collector
from app.ha import TokenVault


@pytest.mark.asyncio
async def test_repeated_import_deduplicates_and_preserves_assignment(tmp_path, monkeypatch):
    engine, dbs = database("sqlite+aiosqlite:///" + str(tmp_path / "collector.db"))
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    vault = TokenVault("x" * 40)
    now = datetime.now(UTC)

    def ts(minutes):
        return (now + timedelta(minutes=minutes)).isoformat()

    def rows(entity, values, unit=None):
        return [
            {
                "entity_id": entity,
                "state": str(value),
                "last_changed": ts(minute),
                "attributes": {"unit_of_measurement": unit},
            }
            for minute, value in values
        ]

    async def history(*args):
        return [
            rows("state", [(-60, "idle"), (-50, "charger_charging"), (-10, "idle")]),
            rows("energy", [(-60, 10), (-10, 12)], "kWh"),
            rows("power", [(-60, 3)], "kW"),
            rows("grid", [(-60, 1)], "kW"),
            rows("car", [(-55, "on"), (-5, "off")]),
        ]

    async def states(*args):
        return []

    monkeypatch.setattr("app.collector.HAClient.history", history)
    monkeypatch.setattr("app.collector.HAClient.states", states)
    async with dbs() as db:
        site = Instance(
            name="Home",
            url="https://ha.example",
            token_cipher=vault.seal("test"),
            grid_entity="grid",
        )
        db.add(site)
        car = Vehicle(name="Tommy", vin="VIN123", plate="NW FS 97E")
        db.add(car)
        await db.flush()
        charger = Charger(
            instance_id=site.id,
            name="Wallbox",
            energy_entity="energy",
            power_entity="power",
            state_entity="state",
        )
        db.add(charger)
        db.add(VehicleSource(instance_id=site.id, vehicle_id=car.id, charging_entity="car"))
        await db.commit()
        site_id = site.id
        car_id = car.id
    collector = Collector(dbs, vault, NS(history_days=1, poll_seconds=60))
    await collector.sync(site_id)
    async with dbs() as db:
        count = await db.scalar(select(func.count()).select_from(Reading))
        sessions = (await db.scalars(select(Session))).all()
        assert len(sessions) == 1
        assert sessions[0].payload["energy_kwh"] == "2"
        assert sessions[0].assignment == "suggested"
        sessions[0].assignment = "confirmed"
        sessions[0].vehicle_id = car_id
        await db.commit()
    await collector.sync(site_id)
    async with dbs() as db:
        assert await db.scalar(select(func.count()).select_from(Reading)) == count
        session = await db.scalar(select(Session))
        assert session.assignment == "confirmed"
        assert (await db.get(Instance, site_id)).error is None
    await engine.dispose()
