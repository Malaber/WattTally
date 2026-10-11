"""Deterministic session reconstruction from retained observations; no network or database."""

from bisect import bisect_right
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from hashlib import sha256

ZERO = Decimal(0)


def instant(value):
    if isinstance(value, datetime):
        return value.astimezone(UTC)
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def stamp(value):
    return instant(value).isoformat()


def number(value):
    try:
        n = Decimal(str(value))
        return n if n.is_finite() else None
    except InvalidOperation, TypeError:
        return None


def power(row):
    if row is None:
        return None
    n = number(row.state)
    if n is None:
        return None
    if row.unit == "W":
        return n / 1000
    return n if row.unit == "kW" else None


def energy(row):
    if row is None:
        return None
    n = number(row.state)
    if n is None:
        return None
    if row.unit == "Wh":
        return n / 1000
    return n if row.unit == "kWh" else None


def allocate(charger_kw, grid_kw, total_chargers_kw=None):
    """House gets local energy first. Concurrent cars share grid proportionally."""
    charger_kw = max(ZERO, Decimal(str(charger_kw)))
    grid_kw = max(ZERO, Decimal(str(grid_kw)))
    total = (
        max(charger_kw, Decimal(str(total_chargers_kw)))
        if total_chargers_kw is not None
        else charger_kw
    )
    grid = min(grid_kw, total) * charger_kw / total if total else ZERO
    return grid, charger_kw - grid


class Series:
    def __init__(self, rows):
        self.rows = sorted(rows, key=lambda r: r.timestamp)
        self.times = [instant(r.timestamp) for r in self.rows]

    def at(self, time):
        i = bisect_right(self.times, time) - 1
        return self.rows[i] if i >= 0 else None


@dataclass
class Window:
    start: datetime
    end: datetime | None
    cutoff: datetime
    partial: bool = False


def windows(series, active_values, now):
    result = []
    start = end = None
    partial = False
    for i, row in enumerate(series.rows):
        t = instant(row.timestamp)
        if row.state in active_values:
            if start is not None and end is not None and t > end + timedelta(seconds=30):
                result.append(Window(start, end, end + timedelta(seconds=30), partial))
                start = None
            if start is None:
                start, partial = t, i == 0
            end = None
        elif start is not None and end is None:
            end = t
    if start is not None:
        closed = end is not None and now >= end + timedelta(seconds=30)
        result.append(
            Window(
                start,
                end if closed else None,
                min(now, end + timedelta(seconds=30)) if end else now,
                partial,
            )
        )
    return result


def rate_at(tariffs, time):
    applicable = [r for r in tariffs if instant(r.effective_from) <= time]
    return max(applicable, key=lambda r: r.effective_from) if applicable else None


def partition(delta, start, end, charger, chargers, by_entity, grid_entity, inverted, tariffs):
    # Integrate the power proportions over each meter interval, then reconcile to metered kWh.
    relevant = [grid_entity] + [c.power_entity for c in chargers]
    points = {start, end}
    for entity in relevant:
        series = by_entity.get(entity)
        if series:
            points.update(
                series.times[bisect_right(series.times, start) : bisect_right(series.times, end)]
            )
    points.update(
        instant(r.effective_from) for r in tariffs if start < instant(r.effective_from) < end
    )
    points = sorted(points)
    slices = []
    timing_fallback = []
    for a, b in zip(points, points[1:]):
        seconds = Decimal(str((b - a).total_seconds()))
        powers = {c.id: power(by_entity.get(c.power_entity, Series([])).at(a)) for c in chargers}
        own = powers[charger.id]
        grid = power(by_entity.get(grid_entity, Series([])).at(a)) if grid_entity else None
        known = own is not None and grid is not None and all(p is not None for p in powers.values())
        weight = max(own, ZERO) * seconds if own is not None else seconds
        if known:
            g, s = allocate(
                own, -grid if inverted else grid, sum(max(p, ZERO) for p in powers.values())
            )
            fraction = g / own if own > 0 else ZERO
        else:
            fraction = None
        rate = rate_at(tariffs, a)
        slices.append((weight, seconds, fraction, rate))
        if known:
            average = delta * 3600 / Decimal(str((end - start).total_seconds()))
            others = sum(max(p, ZERO) for key, p in powers.items() if key != charger.id)
            fallback_grid, _ = allocate(average, -grid if inverted else grid, average + others)
            timing_fallback.append(
                (seconds, seconds, fallback_grid / average if average else ZERO, rate)
            )
        else:
            timing_fallback.append((seconds, seconds, None, rate))
    total_weight = sum(s[0] for s in slices)
    timing_estimate = total_weight <= 0
    if timing_estimate and (end - start).total_seconds() > 30:
        return ZERO, ZERO, delta, ZERO, ZERO, {"missing_power"}
    if timing_estimate:
        slices = timing_fallback
        total_weight = sum(s[0] for s in slices)
    if total_weight <= 0:
        return ZERO, ZERO, delta, ZERO, ZERO, {"missing_power"}
    gk = sk = uk = gc = sc = ZERO
    flags = {"power_timing_estimate"} if timing_estimate else set()
    for weight, _, fraction, rate in slices:
        amount = delta * weight / total_weight
        if fraction is None:
            uk += amount
            if amount:
                flags.add("missing_grid_or_power")
            continue
        grid_amount = amount * fraction
        solar_amount = amount - grid_amount
        gk += grid_amount
        sk += solar_amount
        if rate is None:
            if amount:
                flags.add("missing_tariff")
        else:
            gc += grid_amount * Decimal(rate.grid_rate)
            sc += solar_amount * Decimal(rate.solar_rate)
    return gk, sk, uk, gc, sc, flags


def reconstruct(charger, chargers, readings, sources, tariffs, now):
    by_entity = {}
    for row in readings:
        by_entity.setdefault(row.entity_id, []).append(row)
    by_entity = {k: Series(v) for k, v in by_entity.items()}
    states = by_entity.get(charger.state_entity, Series([]))
    meter = by_entity.get(charger.energy_entity, Series([]))
    results = []
    meter_positions = meter.times
    for w in windows(states, charger.active_values, now):
        flags = {"partial_start"} if w.partial else set()
        baseline = meter.at(w.start)
        previous = energy(baseline)
        previous_time = w.start
        total = grid = solar = unknown = grid_cost = solar_cost = ZERO
        if previous is None and not w.partial:
            session_meter = by_entity.get(getattr(charger, "session_energy_entity", None))
            i = bisect_right(meter.times, w.start)
            if session_meter and i < len(meter.rows):
                first = meter.rows[i]
                first_time = instant(first.timestamp)
                session_row = session_meter.at(first_time)
                counter = energy(first)
                session_value = energy(session_row)
                if (
                    (first_time - w.start).total_seconds() <= 30
                    and session_row
                    and abs((first_time - instant(session_row.timestamp)).total_seconds()) <= 2
                    and counter is not None
                    and session_value is not None
                    and ZERO <= session_value <= counter
                ):
                    previous = counter - session_value
                    flags.add("baseline_from_session_meter")
        if previous is None:
            flags.add("missing_meter_baseline")
        for row in meter.rows[
            bisect_right(meter_positions, w.start) : bisect_right(meter_positions, w.cutoff)
        ]:
            t = instant(row.timestamp)
            value = energy(row)
            if value is None or previous is None:
                flags.add("meter_gap")
            elif value < previous:
                flags.add("meter_reset")
            elif value > previous:
                delta = value - previous
                total += delta
                g, s, u, gc, sc, f = partition(
                    delta,
                    previous_time,
                    t,
                    charger,
                    chargers,
                    by_entity,
                    charger.grid_entity,
                    charger.grid_inverted,
                    tariffs,
                )
                grid += g
                solar += s
                unknown += u
                grid_cost += gc
                solar_cost += sc
                flags.update(f)
            previous, previous_time = value, t
        if any(
            r.state in {"unavailable", "unknown"}
            for r in states.rows
            if w.start <= instant(r.timestamp) <= w.cutoff
        ):
            flags.add("state_gap")
        candidates = set()
        for source in sources:
            series = by_entity.get(source.charging_entity, Series([]))
            at_start = series.at(w.start)
            active = at_start is not None and at_start.state in source.charging_values
            active |= any(
                r.state in source.charging_values
                for r in series.rows
                if w.start <= instant(r.timestamp) <= w.cutoff
            )
            if active:
                candidates.add(source.vehicle_id)
        vehicle_id = next(iter(candidates)) if len(candidates) == 1 else None
        assignment = "suggested" if vehicle_id else "unassigned"
        # A configured default is an explicit user policy, not automatic identification.
        if not candidates and charger.default_vehicle_id:
            vehicle_id, assignment = charger.default_vehicle_id, "default"
        payload = {
            "energy_kwh": str(total),
            "grid_kwh": str(grid.quantize(Decimal("0.000000001"))),
            "solar_kwh": str(solar.quantize(Decimal("0.000000001"))),
            "unknown_kwh": str(unknown.quantize(Decimal("0.000000001"))),
            "grid_cost": str(grid_cost.quantize(Decimal("0.000000001"))),
            "solar_cost": str(solar_cost.quantize(Decimal("0.000000001"))),
            "flags": sorted(flags),
            "candidate_vehicle_ids": sorted(candidates),
            "allocation": "house-first-v1",
        }
        results.append(
            {
                "id": sha256(f"{charger.id}:{stamp(w.start)}".encode()).hexdigest()[:32],
                "charger_id": charger.id,
                "start": stamp(w.start),
                "end": stamp(w.end) if w.end else None,
                "vehicle_id": vehicle_id,
                "assignment": assignment,
                "payload": payload,
            }
        )
    return results
