from app.ha import LeapmotorAdapter, TokenVault, validate_url
import pytest


def test_two_leapmotors_are_distinguished_by_vin():
    states = [
        {
            "entity_id": f"sensor.car_{i}_evcc_ladestatus",
            "state": "C",
            "attributes": {
                "vin": f"VIN{i}",
                "car_id": str(i),
                "car_type": "T03",
                "friendly_name": f"Car {i} EVCC Ladestatus",
            },
        }
        for i in (1, 2)
    ]
    cars = LeapmotorAdapter().discover(states, [])
    assert len(cars) == 2
    assert {c["vin"] for c in cars} == {"VIN1", "VIN2"}
    assert cars[0]["charging_entity"] != cars[1]["charging_entity"]


def test_token_encryption_and_invalid_origins():
    vault = TokenVault("x" * 40)
    cipher = vault.seal("secret")
    assert "secret" not in cipher
    assert vault.open(cipher) == "secret"
    for url in [
        "file:///etc/passwd",
        "https://user:secret@ha.example",
        "https://ha.example/api?token=secret",
    ]:
        with pytest.raises(ValueError):
            validate_url(url)
