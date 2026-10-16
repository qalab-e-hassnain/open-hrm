"""
Shared fixtures for the lab inventory tests.

A scratch SQLite database per test, the five people the permission matrix in
section 9 distinguishes between, and the seed data the migrations lay down in
production. The existing test modules predate this file and bring their own
fixtures; nothing here changes them.
"""
import os

os.environ.setdefault("DATABASE_URL", "sqlite://")
os.environ.setdefault("SECRET_KEY", "testing-secret-not-a-real-one")

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.security import create_access_token, get_password_hash
from app.database import Base, get_db
from app.main import app
from app.models.inventory import (InvCategory, InvLocation, InvTagCounter,
                                  LocationTypeEnum, TrackingTypeEnum)
from app.models.user import RoleEnum, User
from app.utils import scheduler


# The reference rows the inv01 migration lays down in production. The test
# suite builds its schema with Base.metadata.create_all and never runs a
# migration, so anything that depends on a category or a shelf existing has to
# be given one here. Exported rather than inlined because the older
# test_lifecycle_api module needs the same rows for the deprecated
# /api/v1/assets endpoints, which now resolve categories by name.
# (name, tracking_type, tag_prefix, serial_required) — mirrors the inv01 seed.
LEGACY_CATEGORIES = [
    ("Laptop", "serial", "QGT-EQ-", True), ("Desktop", "serial", "QGT-EQ-", True),
    ("Monitor", "serial", "QGT-EQ-", False), ("Phone", "serial", "QGT-EQ-", True),
    ("SIM", "serial", None, False), ("Access card", "serial", None, False),
    ("Peripheral", "serial", "QGT-EQ-", False), ("Furniture", "serial", None, False),
    ("Licence", "serial", None, False), ("Other", "serial", None, False),
]

LAB_CATEGORIES = [
    ("Test and measurement", "serial", "QGT-EQ-", True),
    ("Dev kits and compute", "serial", "QGT-DK-", True),
    ("Sensors and modules", "serial", "QGT-MD-", False),
    ("Prototype boards", "project", "QGT-PR-", False),
    ("Client hardware", "project", "QGT-CL-", False),
    ("Components and consumables", "quantity", "QGT-ST-", False),
]

SEED_LOCATIONS = [
    ("SITE", "NSTP NUST, Islamabad", None, LocationTypeEnum.INTERNAL),
    ("STORE", "Store room", "SITE", LocationTypeEnum.INTERNAL),
    ("L1-R1-S1-B01", "Lab 1 Rack 1 Shelf 1 Bin 01", "SITE", LocationTypeEnum.INTERNAL),
    ("QUAR", "Quarantine", "SITE", LocationTypeEnum.QUARANTINE),
    ("CL-KORA", "Kora client shelf", "SITE", LocationTypeEnum.CLIENT_SHELF),
]


def seed_inventory_reference(db) -> dict:
    """Categories, locations and tag counters. Idempotent; returns their ids."""
    ids = {}
    for name, tracking, prefix, serial_required in LEGACY_CATEGORIES + LAB_CATEGORIES:
        row = db.query(InvCategory).filter(InvCategory.name == name).first()
        if row is None:
            row = InvCategory(name=name, tracking_type=tracking, tag_prefix=prefix,
                              serial_required=serial_required,
                              domain="office" if (name, tracking, prefix, serial_required)
                              in LEGACY_CATEGORIES else "lab")
            db.add(row)
            db.flush()
        ids[f"cat:{name}"] = row.id
        if prefix and not db.query(InvTagCounter).filter(
                InvTagCounter.prefix == prefix).first():
            db.add(InvTagCounter(prefix=prefix, next_number=1))

    for code, name, parent, kind in SEED_LOCATIONS:
        row = db.query(InvLocation).filter(InvLocation.code == code).first()
        if row is None:
            row = InvLocation(code=code, name=name, location_type=kind.value,
                              parent_id=ids.get(f"loc:{parent}") if parent else None)
            db.add(row)
            db.flush()
        ids[f"loc:{code}"] = row.id

    db.commit()
    return ids


class Env:
    """Everything a test needs, in one place."""

    def __init__(self, client, Session):
        self.client = client
        self.Session = Session
        self.ids = {}
        self.tokens = {}

    def hdr(self, who: str) -> dict:
        return {"Authorization": f"Bearer {self.tokens[who]}"}

    def get(self, who, url, **kw):
        return self.client.get(url, headers=self.hdr(who), **kw)

    def post(self, who, url, json=None, **kw):
        return self.client.post(url, json=json or {}, headers=self.hdr(who), **kw)

    def put(self, who, url, json=None, **kw):
        return self.client.put(url, json=json or {}, headers=self.hdr(who), **kw)

    def delete(self, who, url, **kw):
        return self.client.delete(url, headers=self.hdr(who), **kw)

    def session(self):
        return self.Session()


@pytest.fixture
def env(monkeypatch):
    # The signed-in user is cached per process to save a round trip to a
    # distant database (app/core/user_cache). Every test builds a brand new
    # database, so a user remembered by the previous test would be handed
    # back here with an id that belongs to a database that no longer exists.
    from app.core import user_cache
    user_cache.forget()

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    from app import models as _models  # noqa: F401 — registers every table
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autocommit=False, autoflush=False)

    # The traffic hook would otherwise fire on the first request and reach out
    # to the office fingerprint device from a test run.
    monkeypatch.setattr(scheduler, "should_poll", lambda: False)
    # Nothing in a test run should try to send mail.
    monkeypatch.setattr("app.utils.email.send_email",
                        lambda *a, **k: None, raising=False)

    def override_get_db():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    e = Env(TestClient(app), Session)

    db = Session()
    try:
        people = {
            "admin": ("admin@qgt.test", "Ayesha Admin", RoleEnum.ADMIN, {}),
            "hr": ("hr@qgt.test", "Hina HR", RoleEnum.MANAGER, {"is_hr": True}),
            "custodian": ("store@qgt.test", "Kamran Custodian", RoleEnum.EMPLOYEE,
                          {"is_lab_custodian": True}),
            "lead": ("lead@qgt.test", "Lubna Lead", RoleEnum.MANAGER, {}),
            "eng": ("eng@qgt.test", "Imran Engineer", RoleEnum.EMPLOYEE, {}),
            "eng2": ("eng2@qgt.test", "Sana Engineer", RoleEnum.EMPLOYEE, {}),
        }
        for key, (email, name, role, extra) in people.items():
            user = User(email=email, full_name=name, role=role,
                        hashed_password=get_password_hash("pw"), is_active=True,
                        base_salary=100000.0, **extra)
            db.add(user)
            db.flush()
            e.ids[key] = user.id
            e.tokens[key] = create_access_token({"sub": email})

        # Both engineers report to the lead, so "own team" approval is testable.
        for key in ("eng", "eng2"):
            db.query(User).filter(User.id == e.ids[key]).update(
                {"manager_id": e.ids["lead"]})

        seeded = seed_inventory_reference(db)
        for key, name in (("equipment", "Test and measurement"),
                          ("devkit", "Dev kits and compute"),
                          ("proto", "Prototype boards"),
                          ("client", "Client hardware"),
                          ("stock", "Components and consumables"),
                          ("laptop", "Laptop")):
            e.ids[f"cat_{key}"] = seeded[f"cat:{name}"]
        for key, code in (("site", "SITE"), ("store", "STORE"),
                          ("lab1", "L1-R1-S1-B01"), ("quarantine", "QUAR"),
                          ("client_shelf", "CL-KORA")):
            e.ids[f"loc_{key}"] = seeded[f"loc:{code}"]

        db.commit()
    finally:
        db.close()

    yield e

    app.dependency_overrides.clear()
    Base.metadata.drop_all(engine)


@pytest.fixture
def item(env):
    """One serial-tracked instrument, on the shelf in the store room."""
    r = env.post("custodian", "/api/v1/inventory/items", {
        "category_id": env.ids["cat_equipment"],
        "name": "Rigol DS1054Z oscilloscope",
        "serial_number": "DS1ZA0000001",
        "purchase_cost": 85000.0,
        "replacement_cost": 95000.0,
        "current_location_id": env.ids["loc_store"],
    })
    assert r.status_code == 201, r.text
    return r.json()


@pytest.fixture
def stock(env):
    """One quantity-tracked bin of parts."""
    r = env.post("custodian", "/api/v1/inventory/items", {
        "category_id": env.ids["cat_stock"],
        "name": "0402 10k resistor",
        "mpn": "RC0402FR-0710KL",
        "qty": 5000,
        "min_qty": 500,
        "unit_cost": 0.4,
        "current_location_id": env.ids["loc_lab1"],
    })
    assert r.status_code == 201, r.text
    return r.json()
