"""Generate the public company-report preview using only fictional data."""

from datetime import datetime, timedelta, UTC
from pathlib import Path
from types import SimpleNamespace as NS
from app.reports import freeze_report, pdf_bytes


def example_report(count=70):
    car = NS(name="Example Company Vehicle - Long Range", plate="DEMO AB 123E", policy="employer")
    tariff = NS(effective_from="2026-01-01T00:00:00+00:00", reimbursement_rate="0.30")
    sessions = []
    for index in range(count):
        end = datetime(2026, 10, 1, tzinfo=UTC) + timedelta(hours=index * 8)
        energy = str(10 + index % 19) + ".250"
        sessions.append(
            NS(
                id=f"demo-{index + 1:03}",
                start=(end - timedelta(hours=3)).isoformat(),
                end=end.isoformat(),
                assignment="confirmed",
                reviewed_energy_kwh=energy,
                payload={"energy_kwh": energy, "flags": []},
            )
        )
    snapshot, text, digest = freeze_report(car, sessions, "2026-10", "Europe/Berlin", [tariff])
    return NS(
        id="example-fictional-data",
        policy="employer",
        month="2026-10",
        snapshot=snapshot,
        csv_text=text,
        sha256=digest,
    )


if __name__ == "__main__":
    path = Path("app/web/static/examples/wallbox-export-example.pdf")
    path.write_bytes(pdf_bytes(example_report()))
    print(path)
