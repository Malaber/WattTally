import asyncio
import logging
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert

from app.accounting import instant, reconstruct, stamp
from app.ha import HAClient, LeapmotorAdapter
from app.models import Charger, Instance, Reading, Session, Tariff, Vehicle, VehicleSource

log = logging.getLogger(__name__)


class Collector:
    def __init__(self, sessionmaker, vault, settings):
        self.db = sessionmaker
        self.vault = vault
        self.settings = settings
        self.lock = asyncio.Lock()

    async def sync(self, instance_id):
        async with self.lock:
            async with self.db() as db:
                site = await db.get(Instance, instance_id)
                if not site or not site.enabled:
                    return
                chargers = list(
                    (await db.scalars(select(Charger).where(Charger.instance_id == site.id))).all()
                )
                sources = list(
                    (
                        await db.scalars(
                            select(VehicleSource).where(VehicleSource.instance_id == site.id)
                        )
                    ).all()
                )
                entities = {
                    e for c in chargers for e in [c.energy_entity, c.power_entity, c.state_entity]
                }
                entities.update(
                    c.session_energy_entity for c in chargers if c.session_energy_entity
                )
                entities.update(s.charging_entity for s in sources)
                entities.update(s.odometer_entity for s in sources if s.odometer_entity)
                if site.grid_entity:
                    entities.add(site.grid_entity)
                now = datetime.now(UTC)
                if not entities:
                    return
                start = (
                    instant(site.last_sync) - timedelta(minutes=15)
                    if site.last_sync
                    else now - timedelta(days=self.settings.history_days)
                )
                # HA cannot return arbitrarily old detailed history; keep a bounded request.
                start = max(start, now - timedelta(days=self.settings.history_days))
                try:
                    client = HAClient(site.url, self.vault.open(site.token_cipher))
                    current = await client.states()
                    for found in LeapmotorAdapter().discover(current, []):
                        vehicle = await db.scalar(
                            select(Vehicle).where(Vehicle.vin == found["vin"])
                        )
                        if vehicle is None:
                            vehicle = Vehicle(
                                vin=found["vin"], name=found["name"], plate="", provider="leapmotor"
                            )
                            db.add(vehicle)
                            await db.flush()
                        source = await db.scalar(
                            select(VehicleSource).where(
                                VehicleSource.instance_id == site.id,
                                VehicleSource.vehicle_id == vehicle.id,
                            )
                        )
                        if found["charging_entity"] and source is None:
                            source = VehicleSource(
                                instance_id=site.id,
                                vehicle_id=vehicle.id,
                                charging_entity=found["charging_entity"],
                            )
                            db.add(source)
                            await db.flush()
                            sources.append(source)
                            entities.add(source.charging_entity)
                        if source and found.get("odometer_entity"):
                            source.odometer_entity = found["odometer_entity"]
                            entities.add(source.odometer_entity)
                    retained_entities = set(
                        await db.scalars(
                            select(Reading.entity_id)
                            .where(Reading.instance_id == site.id)
                            .distinct()
                        )
                    )
                    # Fetch one day at a time to bound HA responses for long backfills.
                    cursor = start
                    while cursor < now:
                        end = min(now, cursor + timedelta(days=1))
                        histories = await client.history(stamp(cursor), stamp(end), entities)
                        batch = []
                        for rows in histories:
                            if not rows:
                                continue
                            entity = rows[0]["entity_id"]
                            unit = rows[0].get("attributes", {}).get("unit_of_measurement")
                            for row in rows:
                                unit = row.get("attributes", {}).get("unit_of_measurement", unit)
                                timestamp = stamp(row.get("last_changed") or row["last_updated"])
                                # Initial snapshots synthesize a time at the query boundary. Keep the
                                # first baseline only; later overlaps already have the retained state.
                                if (
                                    instant(timestamp) == cursor
                                    and site.last_sync
                                    and entity in retained_entities
                                ):
                                    continue
                                batch.append(
                                    dict(
                                        instance_id=site.id,
                                        entity_id=entity,
                                        timestamp=timestamp,
                                        state=row["state"],
                                        unit=unit,
                                    )
                                )
                        for offset in range(0, len(batch), 150):
                            await db.execute(
                                insert(Reading)
                                .values(batch[offset : offset + 150])
                                .on_conflict_do_nothing(
                                    index_elements=["instance_id", "entity_id", "timestamp"]
                                )
                            )
                        cursor = end
                    site.last_sync = stamp(now)
                    site.error = None
                    await self.rebuild(db, site, chargers, sources, now)
                    await db.commit()
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    await db.rollback()
                    site = await db.get(Instance, instance_id)
                    # Never persist exception messages containing URLs, response bodies, or tokens.
                    site.error = f"Read failed ({type(exc).__name__}). Check connection and token."
                    await db.commit()
                    log.warning(
                        "Home Assistant read failed for instance %s (%s)",
                        instance_id,
                        type(exc).__name__,
                    )

    async def rebuild(self, db, site, chargers=None, sources=None, now=None):
        chargers = (
            chargers
            if chargers is not None
            else list(
                (await db.scalars(select(Charger).where(Charger.instance_id == site.id))).all()
            )
        )
        sources = (
            sources
            if sources is not None
            else list(
                (
                    await db.scalars(
                        select(VehicleSource).where(VehicleSource.instance_id == site.id)
                    )
                ).all()
            )
        )
        readings = list(
            (
                await db.scalars(
                    select(Reading)
                    .where(Reading.instance_id == site.id)
                    .order_by(Reading.timestamp)
                )
            ).all()
        )
        tariffs = list(
            (await db.scalars(select(Tariff).where(Tariff.instance_id == site.id))).all()
        )
        now = now or datetime.now(UTC)
        for charger in chargers:
            config = SimpleNamespace(
                **{
                    k: getattr(charger, k)
                    for k in [
                        "id",
                        "energy_entity",
                        "power_entity",
                        "session_energy_entity",
                        "state_entity",
                        "active_values",
                        "default_vehicle_id",
                    ]
                },
                grid_entity=site.grid_entity,
                grid_inverted=site.grid_inverted,
            )
            rebuilt = await asyncio.to_thread(
                reconstruct, config, chargers, readings, sources, tariffs, now
            )
            for values in rebuilt:
                session = await db.get(Session, values["id"])
                if session:
                    for k, v in values.items():
                        if k in {"vehicle_id", "assignment"} and session.assignment == "confirmed":
                            continue
                        setattr(session, k, v)
                else:
                    db.add(Session(**values))

    async def run(self):
        while True:
            try:
                async with self.db() as db:
                    ids = list(
                        (
                            await db.scalars(select(Instance.id).where(Instance.enabled.is_(True)))
                        ).all()
                    )
                for instance_id in ids:
                    await self.sync(instance_id)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Collector iteration failed")
            await asyncio.sleep(max(15, self.settings.poll_seconds))
