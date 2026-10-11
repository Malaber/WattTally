"""The HA boundary deliberately exposes read operations only."""

import base64
import hashlib
import json
from urllib.parse import urlsplit

import httpx
from cryptography.fernet import Fernet
from websockets.asyncio.client import connect


class TokenVault:
    def __init__(self, secret):
        self.fernet = Fernet(base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest()))

    def seal(self, token):
        return self.fernet.encrypt(token.encode()).decode()

    def open(self, cipher):
        return self.fernet.decrypt(cipher.encode()).decode()


def validate_url(url):
    p = urlsplit(url)
    if (
        p.scheme not in {"http", "https"}
        or not p.hostname
        or p.username
        or p.password
        or p.query
        or p.fragment
        or p.path not in {"", "/"}
    ):
        raise ValueError(
            "Use an http(s) Home Assistant origin without credentials, path, or query."
        )
    return url.rstrip("/")


class HAClient:
    def __init__(self, url, token):
        self.url = validate_url(url)
        self.token = token

    async def get(self, path, params=None):
        if path not in {"/api/states", "/api/config"} and not path.startswith(
            "/api/history/period/"
        ):
            raise ValueError("Read endpoint not allowed")
        async with httpx.AsyncClient(timeout=45, follow_redirects=False) as client:
            response = await client.get(
                self.url + path, params=params, headers={"Authorization": f"Bearer {self.token}"}
            )
            response.raise_for_status()
            return response.json()

    async def states(self):
        return await self.get("/api/states")

    async def history(self, start, end, entities):
        return await self.get(
            "/api/history/period/" + start,
            {
                "end_time": end,
                "filter_entity_id": ",".join(sorted(entities)),
                "minimal_response": "",
            },
        )

    async def registry(self):
        url = self.url.replace("https://", "wss://", 1).replace("http://", "ws://", 1)
        async with connect(url + "/api/websocket", open_timeout=15, max_size=16_000_000) as ws:
            await ws.recv()
            await ws.send(json.dumps({"type": "auth", "access_token": self.token}))
            if json.loads(await ws.recv()).get("type") != "auth_ok":
                raise ValueError("Home Assistant authentication failed")
            await ws.send(json.dumps({"id": 1, "type": "config/entity_registry/list"}))
            result = json.loads(await ws.recv())
            if not result.get("success"):
                return []
            return result["result"]


class LeapmotorAdapter:
    provider = "leapmotor"

    def discover(self, states, registry):
        platforms = {r["entity_id"]: r.get("platform") for r in registry}
        found = {}
        for s in states:
            a = s.get("attributes", {})
            vin = a.get("vin")
            if not vin or (platforms.get(s["entity_id"]) != "leapmotor" and not a.get("car_id")):
                continue
            item = found.setdefault(
                vin,
                {
                    "vin": vin,
                    "provider": self.provider,
                    "name": a.get("car_type", "Leapmotor"),
                    "charging_entity": None,
                },
            )
            entity = s["entity_id"]
            if entity.endswith("_evcc_ladestatus"):
                item["charging_entity"] = entity
                item["name"] = a.get("friendly_name", item["name"]).replace(" EVCC Ladestatus", "")
            elif (
                entity.startswith("binary_sensor.")
                and (entity.endswith("_ladt") or entity.endswith("_charging"))
                and not item["charging_entity"]
            ):
                item["charging_entity"] = entity
        return list(found.values())


ADAPTERS = [LeapmotorAdapter()]


async def discover(client):
    states = await client.states()
    registry = await client.registry()
    vehicles = [v for adapter in ADAPTERS for v in adapter.discover(states, registry)]
    entities = [
        {
            "id": s["entity_id"],
            "name": s.get("attributes", {}).get("friendly_name", s["entity_id"]),
            "unit": s.get("attributes", {}).get("unit_of_measurement"),
            "state": s["state"],
            "state_class": s.get("attributes", {}).get("state_class"),
        }
        for s in states
        if s["entity_id"].split(".")[0] in {"sensor", "binary_sensor", "switch"}
    ]
    return {"vehicles": vehicles, "entities": entities}
