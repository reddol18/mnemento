from pathlib import Path

import pytest

from mnemento import Ledger

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_DIR = ROOT / "schemas"

AT = "2026-10-01T10:00:00+09:00"


@pytest.fixture
def ledger(tmp_path):
    led = Ledger.open(tmp_path / "test.db")
    led.schemas.load_dir(SCHEMA_DIR)
    yield led
    led.close()


def make_app(ledger, n, *, status="applied", applied_at="2026-10-02", platform="saramin", at=AT, **extra):
    """Create a fictional application entity app_<platform>_<n>."""
    entity_id = f"app_{platform}_{n}"
    doc = {
        "company_id": f"co_fake{n}",
        "platform": platform,
        "status": status,
        "applied_at": applied_at,
        **extra,
    }
    return ledger.record_event(
        entity_id, "created", doc, at, by="test_agent", evidence="fixture", entity_type="application"
    )
