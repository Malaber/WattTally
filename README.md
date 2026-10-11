# WattTally

Self-hosted charging records for multiple Home Assistant instances and vehicles.
Python 3.14, FastAPI, SQLAlchemy, Alembic, SQLite, Jinja2 and plain JavaScript,
following Malaber/Planini. Authentication uses the same pinned FastPasskey library.

## Run locally

```sh
python3.14 -m venv .venv
.venv/bin/pip install -e '.[dev]'
cp .env.example .env
# Set WATTTALLY_SECRET_KEY to a random secret (at least 32 characters).
# For example: python3 -c 'import secrets; print(secrets.token_urlsafe(48))'
.venv/bin/uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8000
```

Open http://localhost:8000. Keep the hostname consistent: passkeys for localhost
will not work on 127.0.0.1. Database migrations run at startup.

Create the first administrator from the server shell:

```bash
.venv/bin/python -m app.enroll you@example.com --admin --name "Your name"
```

Open the printed one-use link and create a passkey. Links expire after 24 hours;
issuing another link revokes the previous one. Raw tokens are only shown once,
stored as hashes, and consumed atomically after successful enrollment. No email
is sent. Re-running the command creates a recovery link for the existing account;
it adds a key without deleting existing passkeys.

Public **Create account** enrolls a passkey immediately. New accounts await an
administrator's approval before accessing shared household data. Administrators
use **Accounts** to approve, disable, promote, create accounts, generate links,
and revoke invitations. Approved members share all homes, cars and reports.
Use the shell command with `--admin` to bootstrap administration; public signup
never grants administrator rights. Login remains passkey-only.

## Set up a home

1. Add its Home Assistant URL and dedicated access token. The app checks the
   connection and discovers Leapmotor cars by VIN. Tokens are encrypted in SQLite
   using a key derived from `WATTTALLY_SECRET_KEY`; never change that key without
   re-entering tokens. Keep the key and database backups secure.
2. Use **Discover & map** to select cumulative charger energy (kWh or Wh), charger
   power (kW or W), charging state, and grid power. Grid power must be positive for
   import; select **Invert sign** for a sensor with the opposite convention.
3. Add each charger's active state values. TopAC defaults to `charger_charging`.
   Map each physical charger only once. Duplicate HA energy entities must not be
   configured as separate chargers.
4. Add license plates and choose actual-cost or employer reports. A default car is
   optional and constitutes an explicit attribution assumption. Shared chargers
   should have no default.
5. Add dated home tariffs and/or dated per-car reimbursement rates. Solar can cost
   zero or a chosen opportunity cost. Rates use EUR/kWh. The browser interprets
   tariff input in its local timezone and sends an explicit UTC timestamp.
6. Sync history. The collector repeats every 60 seconds and retains raw readings
   locally. Cars newly exposed by the Leapmotor integration are discovered during
   subsequent collection. Record the selected HA entities with Recorder.

All HA traffic is read-only: GET states/config/history plus the read-only entity
registry WebSocket query for discovery. The app never calls HA services, changes
entities, wakes cars or controls charging. HA access tokens themselves may have
write privileges; use an upstream read-only proxy if enforced restriction is needed.

## Sessions and vehicle assignment

Charging state transitions define cycles. Interruptions of up to 30 seconds merge
into the same cycle; a 30-second tail captures delayed final meter updates. Meter
increases supply energy, with reset/gap flags instead of invented consumption.
Zero-energy cycles remain visible but are omitted from reports.

Car charging signals overlapping the session produce a suggested assignment.
This is correlation, not a physical vehicle ID from the wallbox: simultaneous
charging elsewhere can be ambiguous. A suggestion must be confirmed before export.
Missing/conflicting candidates remain unassigned and visible as mapping errors.
A configured default is used only when no candidates exist. Explicit corrections
survive subsequent imports. Different VINs remain different vehicles, even for two
Leapmotors with the same model/name; the same VIN across homes is one vehicle.

Session capture polls HA **history**, not just current state, so short cycles between
polls are retained. Imports overlap by 15 minutes and deduplicate by home/entity/time.
Initial import requests nine days. Backfill is bounded to the same window: outages
beyond HA's detailed retention cannot be fully recovered. Keep app and HA backups.
Long-term HA aggregate statistics are not used to invent historical session rows.
Raw readings are currently retained indefinitely; database growth needs monitoring.
Reconstruction currently reads retained history; very large installations will need
incremental aggregation. SQLite is the supported initial database.

## House-first solar allocation

For one charger:

```text
car_grid_kW  = min(max(grid_import_kW, 0), car_charging_kW)
car_solar_kW = car_charging_kW - car_grid_kW
```

Example: PV produces 3 kW, the car charges at 3 kW, the home imports 1 kW.
The car gets 1 kW grid + 2 kW solar; the house consumes the other 1 kW of PV.
PV measurement is not needed for this rule. Battery provenance is intentionally
ignored: “solar” here means supply not attributed to grid import by this rule.

Concurrent chargers in one HA instance share the site's grid contribution in
proportion to charging power. Power fractions are integrated between source changes
and reconciled to measured charger energy. Missing/unavailable grid/power data stays
unknown and blocks actual-cost exports. Short (at most 30-second) metered intervals with delayed zero-power telemetry use
measured average charging power and carry a `power_timing_estimate` flag. Longer
missing-power intervals remain unknown. An optional session-energy counter can recover
a missing cumulative baseline only when readings align within two seconds and occur
within 30 seconds of a known start.

Grid values are held until the next recorded
state change, as HA may not record unchanged values. Sensor outages should report
`unavailable` to avoid silently holding stale readings.

## Reports

Database is the source of truth. Creating a report freezes a canonical UTF-8 CSV
snapshot and SHA-256 checksum; Excel and PDF are generated from that exact snapshot.
Corrections and tariff edits affect new reports, never existing downloads.

- **Actual costs:** grid/solar kWh and costs, including tariffs effective during
  each metered interval. Unknown allocation or missing rates blocks export.
- **Employer:** total energy × agreed reimbursement rate, effective at session
  start. Solar share, grid share and actual household costs are excluded entirely
  from CSV, Excel and PDF. No solar discount is applied.
- Whole sessions belong to the report month in which charging starts, in the chosen
  report timezone. Cross-month sessions are not split. Rates use decimal arithmetic;
  amounts round per session to cents, then sum. PDFs show kWh to three decimals.
- Incomplete meter sessions, active cycles and unconfirmed suggestions block export
  for the selected car. Unassigned sessions are shown separately for review; a report
  does not claim that they belong to any particular vehicle.

## Tests

```sh
.venv/bin/pytest -q
.venv/bin/ruff check app tests scripts
.venv/bin/playwright install chromium
.venv/bin/python scripts/browser_smoke.py
```

The browser smoke test uses an isolated database and a virtual authenticator; no
Home Assistant access. It exercises real passkey enrollment/login and responsive UI.

## Scope and future clients

Leapmotor discovery ships first. Provider adapters live in `app/ha.py`; Tesla and
other makes can implement discovery without changing stored vehicles or reports.
The authenticated JSON API (schema at `/api/openapi.json`) already exposes sessions and assignment review for a
future iOS client. Native passkey association, APNs device registration and push
notifications are not implemented yet. No live Home Assistant settings are changed.

## Releases and contributions

The initial release is 0.1.0. Subsequent changes use a feature branch and pull
request; make focused commits as milestones are verified. CI runs Python checks
and real browser passkey tests before building the container. Pull requests build
without publishing. Successful pushes to `main` publish
`ghcr.io/malaber/watttally:main`; `development` publishes `:development` when that
branch is introduced through the normal review process. Version tags such as
`v0.1.0` publish `:0.1.0` and the stable `:latest` tag. Images also receive a
`sha-<full commit>` tag. Prefer immutable image digests for deployments.

Production/test deployment proposals:
- [selfhosted !32](https://gitlab.com/malaber-ansible/selfhosted/-/merge_requests/32)
- [infra !40](https://gitlab.com/Malaber/infra/-/merge_requests/40)

The initial release publishes no development branch image. To test the initial
release, override the test role image with `ghcr.io/malaber/watttally:0.1.0` after
CI publishes it. The separate test deployment still uses its own data and secrets.
