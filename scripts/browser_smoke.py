"""Exercise real WebAuthn with Chromium's virtual authenticator against an isolated app."""

import os
import secrets
import subprocess
import sys
import tempfile
import time
from pathlib import Path
import urllib.request
from playwright.sync_api import sync_playwright

root = Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory() as temporary:
    secret = secrets.token_urlsafe(40)
    env = {
        **os.environ,
        "WATTTALLY_SECRET_KEY": secret,
        "WATTTALLY_APP_BASE_URL": "http://localhost:8765",
        "WATTTALLY_WEBAUTHN_RP_ID": "localhost",
        "WATTTALLY_DATABASE_URL": "sqlite+aiosqlite:///" + temporary + "/test.db",
        "WATTTALLY_COLLECTOR_ENABLED": "false",
    }
    log = open(Path(temporary) / "server.log", "w+")
    server = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "app.main:create_app",
            "--factory",
            "--host",
            "127.0.0.1",
            "--port",
            "8765",
        ],
        cwd=root,
        env=env,
        stdout=log,
        stderr=log,
    )
    try:
        for _ in range(100):
            try:
                urllib.request.urlopen("http://localhost:8765/health", timeout=1)
                break
            except Exception:
                time.sleep(0.1)
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            cdp = page.context.new_cdp_session(page)
            cdp.send("WebAuthn.enable")
            cdp.send(
                "WebAuthn.addVirtualAuthenticator",
                {
                    "options": {
                        "protocol": "ctap2",
                        "transport": "internal",
                        "hasResidentKey": True,
                        "hasUserVerification": True,
                        "isUserVerified": True,
                        "automaticPresenceSimulation": True,
                    }
                },
            )
            link = subprocess.check_output(
                [
                    sys.executable,
                    "-m",
                    "app.enroll",
                    "demo@example.com",
                    "--admin",
                    "--name",
                    "Demo Owner",
                ],
                env=env,
                cwd=root,
                text=True,
            ).strip()
            page.goto(link)
            page.locator("[data-passkey-add-link-button]").click()
            page.locator("#overview").wait_for(timeout=15000)
            page.locator("#logout").click()
            page.locator("[data-passkey-login-button]").click()
            page.locator("#overview").wait_for(timeout=15000)
            seed = """
import asyncio
from app.config import Settings
from app.database import database
from app.models import Instance,Vehicle,Charger,Session,Tariff
async def main():
 e,dbs=database(Settings().database_url)
 async with dbs() as db:
  site=Instance(id='demo-home',name='Demo home',url='https://example.invalid',token_cipher='not-a-token',enabled=False)
  car=Vehicle(id='demo-car',vin='DEMO-VIN',name='Tommy (demo)',plate='NW FS 97E',policy='employer')
  db.add_all([site,car]);await db.flush()
  charger=Charger(id='demo-charger',instance_id=site.id,name='Carport TopAC',energy_entity='sensor.demo_energy',power_entity='sensor.demo_power',state_entity='sensor.demo_status')
  db.add(charger);await db.flush()
  db.add(Session(id='demo-session',charger_id=charger.id,start='2026-10-03T09:12:15+00:00',end='2026-10-03T15:16:13+00:00',vehicle_id=car.id,assignment='suggested',payload={'energy_kwh':'11.313','grid_kwh':'4.313','solar_kwh':'7','unknown_kwh':'0','grid_cost':'1.2939','solar_cost':'0','flags':['partial_start']}))
  db.add(Session(id='active-session',charger_id=charger.id,start='2026-10-10T09:00:00+00:00',vehicle_id=car.id,assignment='suggested',payload={'energy_kwh':'0','grid_kwh':'0','solar_kwh':'0','unknown_kwh':'0','grid_cost':'0','solar_cost':'0','flags':[]}))
  db.add(Tariff(vehicle_id=car.id,effective_from='2026-01-01T00:00:00+00:00',reimbursement_rate='0.30'))
  await db.commit()
 await e.dispose()
asyncio.run(main())
"""
            subprocess.run([sys.executable, "-c", seed], env=env, cwd=root, check=True)
            page.reload()
            page.locator("[data-tab=settings]").click()
            vehicle_form = page.locator('[data-vehicle="demo-car"]')
            for field, value in {"vehicle_model": "Example E20", "driver_name": "Alex Example", "driver_address": "Example Street 12\n12345 Town", "report_vin": "REPORTVIN"}.items():
                vehicle_form.locator(f"[name={field}]").fill(value)
            with page.expect_response("**/api/vehicles/demo-car") as saved:
                vehicle_form.get_by_role("button", name="Save car").click()
            assert saved.value.ok
            page.reload()
            page.locator("[data-tab=settings]").click()
            assert page.locator('[data-vehicle="demo-car"] [name=driver_name]').input_value() == "Alex Example"
            page.locator("[data-tab=sessions]").click()
            page.locator("[data-assignment=demo-session] button").click()
            page.get_by_text("confirmed", exact=True).wait_for()
            page.locator("[data-assignment=active-session]").get_by_role("button", name="Confirm car", exact=True).click()
            page.get_by_text("Charging now — car confirmed", exact=True).wait_for()
            page.locator("[data-tab=exports]").click()
            page.locator("#report-form [name=month]").fill("2026-10")
            page.locator("#report-form button").click()
            page.locator("#report-list a").first.wait_for()
            with page.expect_download() as download:
                page.locator("#report-list a").filter(has_text="CSV").click()
            csv_path = download.value.path()
            csv_text = Path(csv_path).read_text()
            assert "NW FS 97E" in csv_text and "3.39" in csv_text
            assert "solar" not in csv_text.lower()
            assert "Alex Example" in csv_text and "REPORTVIN" in csv_text
            for tab in ["sessions", "exports", "settings", "account", "overview"]:
                page.locator(f"[data-tab={tab}]").click()
                assert page.locator("#" + tab).is_visible()
            # Mapping edits expose existing values; new chargers require explicit choices.
            page.route("**/api/instances/demo-home/discover", lambda route: route.fulfill(json={
                "vehicles": [], "entities": [
                    {"id": "sensor.demo_energy", "name": "Energy", "unit": "kWh", "state_class": "total_increasing"},
                    {"id": "sensor.demo_power", "name": "Power", "unit": "W"},
                    {"id": "sensor.demo_status", "name": "Charging state", "unit": None},
                ]
            }))
            page.route("**/api/instances/demo-home/sync", lambda route: route.fulfill(json={"ok": True}))
            page.locator("[data-tab=settings]").click()
            page.get_by_role("button", name="Edit mapping", exact=True).click()
            page.locator("#charger-form h3").filter(has_text="Edit charger").wait_for()
            assert page.locator("#state-options").input_value() == "sensor.demo_status"
            assert page.locator("#power-options").input_value() == "sensor.demo_power"
            page.locator("#charger-form [name=name]").fill("Corrected wallbox")
            page.get_by_role("button", name="Save mapping & sync", exact=True).click()
            page.locator("#charger-list").get_by_text("Corrected wallbox", exact=True).wait_for()
            page.get_by_role("button", name="Discover & map", exact=True).click()
            page.locator("#charger-form h3").filter(has_text="Add charger").wait_for()
            assert page.locator("#power-options").input_value() == ""
            assert page.locator("#state-options").input_value() == ""
            page.locator("[data-tab=overview]").click()
            output = root / "artifacts"
            output.mkdir(exist_ok=True)
            page.screenshot(path=str(output / "dashboard-desktop.png"), full_page=True)
            page.set_viewport_size({"width": 390, "height": 844})
            page.screenshot(path=str(output / "dashboard-mobile.png"), full_page=True)
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
            page.locator("[data-tab=admin]").click()
            page.locator("#account-form [name=display_name]").fill("Invited Member")
            page.locator("#account-form [name=email]").fill("invited@example.com")
            page.locator("#account-form button").click()
            page.locator("#invite-result").wait_for(state="visible")
            invite = page.locator("#invite-url").input_value()
            assert "/enroll#" in invite
            page.locator("#logout").click()
            page.locator("[data-passkey-login-button]").wait_for()
            page.goto(invite)
            page.locator("[data-passkey-add-link-button]").click()
            page.locator("#overview").wait_for(timeout=15000)
            assert page.locator("[data-tab=admin]").count() == 0
            # Native login consent and code exchange share this real passkey browser session.
            import base64
            import hashlib
            from urllib.parse import parse_qs, urlsplit

            verifier = "v" * 43
            challenge = (
                base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
                .decode()
                .rstrip("=")
            )
            page.goto(
                "http://localhost:8765/api/v1/auth/mobile/authorize?state="
                + "s" * 43
                + "&code_challenge="
                + challenge
            )
            page.get_by_role("button", name="Connect iOS app").wait_for()
            csrf = page.locator('meta[name="csrf-token"]').get_attribute("content")
            approved = page.request.post(
                "http://localhost:8765/api/mobile/approve", headers={"X-CSRF-Token": csrf}
            )
            assert approved.ok, approved.text()
            callback = urlsplit(approved.json()["callback"])
            assert callback.scheme == "de.malaber.watttally"
            code = parse_qs(callback.query)["code"][0]
            exchanged = page.request.post(
                "http://localhost:8765/api/v1/auth/mobile/token",
                data={"code": code, "code_verifier": verifier},
            )
            assert exchanged.ok, exchanged.text()
            native_headers = {"Authorization": "Bearer " + exchanged.json()["access_token"]}
            assert page.request.get(
                "http://localhost:8765/api/dashboard", headers=native_headers
            ).ok
            assert page.request.post(
                "http://localhost:8765/api/mobile/logout", headers=native_headers
            ).ok
            page.goto("http://localhost:8765/")
            page.locator("#logout").click()
            page.locator("[data-auth-tab-trigger=signup]").click()
            page.locator("[name=display_name]").fill("Public Member")
            page.locator("[name=email]").fill("public@example.com")
            page.locator("[data-passkey-register-button]").click()
            page.get_by_role("heading", name="Account created").wait_for(timeout=15000)
            assert page.request.get("http://localhost:8765/api/dashboard").status == 403
            assert page.locator("#overview").count() == 0
            assert not errors, errors
            browser.close()
            print(
                "PASS: real passkey registration, logout, login, all navigation tabs, mobile overflow, no browser errors"
            )
    finally:
        server.terminate()
        server.wait(timeout=10)
        if server.returncode not in (0, -15):
            log.seek(0)
            print(log.read()[-4000:])
        log.close()
