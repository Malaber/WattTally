from decimal import Decimal as D
from types import SimpleNamespace as NS

from app.accounting import allocate, instant, reconstruct


def row(entity, time, state, unit=None):
    return NS(entity_id=entity, timestamp=f"2026-10-01T{time}+00:00", state=str(state), unit=unit)


def fixture():
    charger = NS(
        id="c",
        state_entity="state",
        power_entity="power",
        energy_entity="energy",
        active_values=["charging"],
        grid_entity="grid",
        grid_inverted=False,
        default_vehicle_id=None,
    )
    rows = [
        row("state", "09:59:00", "idle"),
        row("state", "10:00:00", "charging"),
        row("state", "11:00:00", "idle"),
        row("power", "09:59:00", 3, "kW"),
        row("grid", "09:59:00", 1000, "W"),
        row("energy", "09:59:00", 100, "kWh"),
        row("energy", "11:00:00", 103, "kWh"),
    ]
    tariff = NS(effective_from="2026-01-01T00:00:00+00:00", grid_rate="0.3", solar_rate="0.08")
    return charger, rows, [tariff]


def build(charger, rows, tariffs, sources=()):
    return reconstruct(charger, [charger], rows, sources, tariffs, instant("2026-10-01T12:00:00Z"))


def test_house_first_example():
    assert allocate(3, 1) == (D(1), D(2))
    assert allocate(3, 5) == (D(3), D(0))
    assert allocate(3, -2) == (D(0), D(3))
    assert allocate(0, 2) == (D(0), D(0))


def test_concurrent_chargers_do_not_double_count_grid():
    a = allocate(3, 4, 9)
    b = allocate(6, 4, 9)
    assert a[0] + b[0] == D(4)


def test_session_and_tariff():
    c, rows, tariffs = fixture()
    s = build(c, rows, tariffs)[0]
    assert s["payload"]["energy_kwh"] == "3"
    assert D(s["payload"]["grid_kwh"]) == 1
    assert D(s["payload"]["solar_kwh"]) == 2
    assert D(s["payload"]["grid_cost"]) == D("0.3")
    assert D(s["payload"]["solar_cost"]) == D("0.16")
    assert s["assignment"] == "unassigned"
    assert s["payload"]["flags"] == []


def test_missing_grid_not_solar():
    c, rows, tariffs = fixture()
    rows = [r for r in rows if r.entity_id != "grid"]
    p = build(c, rows, tariffs)[0]["payload"]
    assert D(p["unknown_kwh"]) == 3
    assert D(p["solar_kwh"]) == 0


def test_counter_reset_is_flagged_not_billed_as_negative():
    c, rows, tariffs = fixture()
    rows[-1].state = "1"
    p = build(c, rows, tariffs)[0]["payload"]
    assert "meter_reset" in p["flags"]
    assert D(p["energy_kwh"]) == 0


def test_trailing_meter_update_included():
    c, rows, tariffs = fixture()
    rows.append(row("energy", "11:00:01", "103.002", "kWh"))
    assert D(build(c, rows, tariffs)[0]["payload"]["energy_kwh"]) == D("3.002")


def test_conflicting_cars_unassigned_even_with_default():
    c, rows, tariffs = fixture()
    c.default_vehicle_id = "one"
    rows += [row("car1", "10:00:00", "C"), row("car2", "10:00:00", "C")]
    sources = [
        NS(vehicle_id=str(i), charging_entity="car" + str(i), charging_values=["C"]) for i in [1, 2]
    ]
    s = build(c, rows, tariffs, sources)[0]
    assert s["vehicle_id"] is None
    assert sorted(s["payload"]["candidate_vehicle_ids"]) == ["1", "2"]


def test_separate_cycles_have_stable_ids():
    c, rows, tariffs = fixture()
    first = build(c, rows, tariffs)[0]
    rows.extend(
        [
            row("state", "11:05:00", "charging"),
            row("state", "11:10:00", "idle"),
            row("energy", "11:10:00", "104", "kWh"),
        ]
    )
    sessions = build(c, rows, tariffs)
    assert len(sessions) == 2
    assert sessions[0]["id"] == first["id"]


def test_unit_conversion_and_inverted_grid():
    c, rows, tariffs = fixture()
    c.grid_inverted = True
    next(r for r in rows if r.entity_id == "grid").state = "-1000"
    for r in rows:
        if r.entity_id == "energy":
            r.state = str(D(r.state) * 1000)
            r.unit = "Wh"
    assert D(build(c, rows, tariffs)[0]["payload"]["grid_kwh"]) == 1


def test_delayed_power_update_does_not_erase_measured_energy():
    c, rows, tariffs = fixture()
    next(r for r in rows if r.entity_id == "power").state = "0"
    next(
        r for r in rows if r.entity_id == "energy" and r.state == "103"
    ).timestamp = "2026-10-01T10:00:10+00:00"
    next(r for r in rows if r.entity_id == "energy" and r.state == "103").state = "100.01"
    p = build(c, rows, tariffs)[0]["payload"]
    assert D(p["unknown_kwh"]) == 0
    assert abs(D(p["grid_kwh"]) - D("0.002777778")) < D("0.00000001")
    assert "power_timing_estimate" in p["flags"]


def test_missing_baseline_recovered_from_simultaneous_session_meter():
    c, rows, tariffs = fixture()
    c.session_energy_entity = "session_energy"
    rows = [r for r in rows if not (r.entity_id == "energy" and r.state == "100")]
    rows += [
        row("energy", "10:00:10", "100.01", "kWh"),
        row("session_energy", "10:00:10", "0.01", "kWh"),
    ]
    p = build(c, rows, tariffs)[0]["payload"]
    assert D(p["energy_kwh"]) == 3
    assert "missing_meter_baseline" not in p["flags"]
    assert "baseline_from_session_meter" in p["flags"]
