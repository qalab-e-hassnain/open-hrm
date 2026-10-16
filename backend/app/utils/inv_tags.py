"""
Asset tags: QGT-EQ-0042 and friends.

The design asks for sequence numbers that are "issued by the system and never
reused, including after scrapping". That rules out MAX(tag)+1, because a
scrapped tag stays in the table and a deleted one would hand its number back to
the next item. A counter that only ever goes up is the whole trick.
"""
import re
from typing import Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models.inventory import InvCategory, InvItem, InvTagCounter

# Width of the numeric part. Four digits covers a lab several times this size,
# and a tag that is always the same length is easier to read off a 25x15mm
# label than one that grows a character at item 1000.
PAD = 4

# Prefixes the design names in section 5. A category carries its own, so this
# is documentation plus the seed for categories created before anyone picked
# one.
KNOWN_PREFIXES = {
    "QGT-EQ-": "Test and measurement equipment, tools",
    "QGT-DK-": "Dev kits, eval boards, compute modules",
    "QGT-MD-": "Sensors, displays, cameras, modules",
    "QGT-PR-": "Prototype boards, project scoped",
    "QGT-CL-": "Client-owned or client-loaned hardware",
    "QGT-ST-": "Stock lines, tagged at bin level",
}

_PROJECT_SCOPED = {"QGT-PR-", "QGT-CL-"}


def next_number(db: Session, prefix: str) -> int:
    """
    Claim the next number for this prefix, atomically.

    One statement, no row lock held across a round trip: UPDATE ... RETURNING
    is atomic on Postgres and on the SQLite the tests run against (3.35+). Two
    custodians receiving deliveries at the same table cannot be handed the same
    number.
    """
    row = db.execute(
        text("UPDATE inv_tag_counters SET next_number = next_number + 1 "
             "WHERE prefix = :p RETURNING next_number - 1"),
        {"p": prefix},
    ).first()
    if row is None:
        # First tag ever issued against this prefix.
        db.add(InvTagCounter(prefix=prefix, next_number=2))
        db.flush()
        return 1
    return int(row[0])


def generate(db: Session, category: InvCategory,
             project_code: Optional[str] = None) -> str:
    """
    A tag for a new item in this category.

    Project-scoped prefixes (prototypes, client hardware) carry the project in
    the middle — QGT-PR-CBOXX-03 — because the question asked of a prototype
    board is almost always "which of CBOXX's boards is this", and a flat
    sequence answers it only by looking the number up.
    """
    prefix = (category.tag_prefix or "QGT-EQ-").strip()
    if not prefix.endswith("-"):
        prefix += "-"

    if prefix in _PROJECT_SCOPED and project_code:
        scope = re.sub(r"[^A-Za-z0-9]", "", project_code).upper()[:8] or "GEN"
        counter_key = f"{prefix}{scope}-"
        return f"{counter_key}{next_number(db, counter_key):02d}"

    return f"{prefix}{next_number(db, prefix):0{PAD}d}"


def is_available(db: Session, tag: str) -> bool:
    return db.query(InvItem.id).filter(InvItem.tag == tag).first() is None


def reserve_at_least(db: Session, prefix: str, number: int) -> None:
    """
    Make sure the counter for `prefix` will never issue `number` again.

    Used by the migration when it meets a legacy tag that happens to match the
    generated format: without this the generator would eventually collide with
    a sticker already on a machine.
    """
    counter = db.query(InvTagCounter).filter(InvTagCounter.prefix == prefix).first()
    if counter is None:
        db.add(InvTagCounter(prefix=prefix, next_number=number + 1))
    elif counter.next_number <= number:
        counter.next_number = number + 1
    db.flush()
