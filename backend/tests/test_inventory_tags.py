"""
Tag generation.

The design asks for sequence numbers "issued by the system and never reused,
including after scrapping". That one sentence rules out the obvious
implementation, so it gets its own tests.
"""
from app.models.inventory import InvTagCounter
from app.utils import inv_tags


_SERIAL = iter(f"SN{n:05d}" for n in range(1, 1000))


def _make(env, category_key="cat_equipment", **extra):
    payload = {"category_id": env.ids[category_key], "name": "Thing",
               "serial_number": next(_SERIAL),
               "current_location_id": env.ids["loc_store"]}
    payload.update(extra)
    r = env.post("custodian", "/api/v1/inventory/items", payload)
    assert r.status_code == 201, r.text
    return r.json()


def test_tags_run_in_sequence_within_a_prefix(env):
    tags = [_make(env)["tag"] for _ in range(3)]
    assert tags == ["QGT-EQ-0001", "QGT-EQ-0002", "QGT-EQ-0003"]


def test_each_prefix_counts_separately(env):
    assert _make(env, "cat_equipment")["tag"] == "QGT-EQ-0001"
    assert _make(env, "cat_devkit")["tag"] == "QGT-DK-0001"
    assert _make(env, "cat_equipment")["tag"] == "QGT-EQ-0002"


def test_a_scrapped_tag_is_never_handed_out_again(env):
    first = _make(env)
    env.post("admin", f"/api/v1/inventory/items/{first['id']}/scrap",
             {"reason": "Dropped"})
    second = _make(env)
    assert second["tag"] == "QGT-EQ-0002"
    assert second["tag"] != first["tag"]


def test_the_counter_only_ever_goes_up(env):
    """
    The case MAX(tag)+1 gets wrong.

    Under a scan-based scheme a gap in the tag sequence — from a scrap, or
    from a row that never made it — would be handed back out, and two
    different oscilloscopes would end up wearing the same sticker a year
    apart. A counter cannot do that.
    """
    from app.models.inventory import InvTagCounter

    first = _make(env)
    assert first["tag"] == "QGT-EQ-0001"
    env.post("admin", f"/api/v1/inventory/items/{first['id']}/scrap",
             {"reason": "Dropped at intake"})
    db = env.session()
    try:
        assert db.query(InvTagCounter).filter(
            InvTagCounter.prefix == "QGT-EQ-").one().next_number == 2
    finally:
        db.close()
    assert _make(env)["tag"] == "QGT-EQ-0002"


def test_a_project_scoped_prefix_carries_the_project(env):
    item = _make(env, "cat_proto", project_code="CBOXX", project_id=3)
    assert item["tag"] == "QGT-PR-CBOXX-01"
    again = _make(env, "cat_proto", project_code="CBOXX", project_id=3)
    assert again["tag"] == "QGT-PR-CBOXX-02"
    other = _make(env, "cat_proto", project_code="KORA", project_id=4)
    assert other["tag"] == "QGT-PR-KORA-01"


def test_a_supplied_tag_is_used_as_given(env):
    """A machine that already wears a sticker keeps the number on the sticker."""
    item = _make(env, tag="QG-LT-001")
    assert item["tag"] == "QG-LT-001"


def test_a_duplicate_tag_is_refused(env):
    _make(env, tag="QG-LT-001")
    r = env.post("custodian", "/api/v1/inventory/items", {
        "category_id": env.ids["cat_equipment"], "name": "Other",
        "tag": "QG-LT-001"})
    assert r.status_code == 400
    assert "already in use" in r.json()["detail"]


def test_reserving_a_number_stops_the_generator_colliding_with_it(env):
    """
    What the migration does when it meets a legacy tag in the generated format.

    Without it the counter would eventually reach a number already printed on
    something sitting on a shelf.
    """
    db = env.session()
    try:
        inv_tags.reserve_at_least(db, "QGT-EQ-", 42)
        db.commit()
        assert db.query(InvTagCounter).filter(
            InvTagCounter.prefix == "QGT-EQ-").one().next_number == 43
    finally:
        db.close()
    assert _make(env)["tag"] == "QGT-EQ-0043"


def test_a_serial_is_required_where_the_category_says_so(env):
    r = env.post("custodian", "/api/v1/inventory/items", {
        "category_id": env.ids["cat_equipment"], "name": "Scope with no serial",
        "current_location_id": env.ids["loc_store"]})
    # The fixture's equipment category requires one.
    assert r.status_code == 400
    assert "serial number" in r.json()["detail"]
