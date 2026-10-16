"""
One shape of datetime crossing the API boundary.

Everything inside this system is naive UTC: `datetime.utcnow()` in the code,
TIMESTAMP WITHOUT TIME ZONE in the database. Browsers do not send that. A form
built on `new Date().toISOString()` sends `2026-09-07T00:00:00.000Z`, and a
picker in Pakistan can send `+05:00` — both parse into an offset-aware datetime.

Mixing the two fails in two different ways, and the quiet one is worse:

  * compared against `datetime.utcnow()` it raises outright, which is how a
    manual time log came back as a 500 with "can't compare offset-naive and
    offset-aware datetimes";
  * written to a naive column it does not raise at all. The offset is dropped
    and the wrong instant is stored, five hours out, with nothing to show for it.

So the conversion happens once, at the edge, rather than at each comparison.
"""
from datetime import datetime, timezone
from typing import Annotated, Optional

from pydantic import AfterValidator


def to_naive_utc(value: Optional[datetime]) -> Optional[datetime]:
    """An aware datetime as the same instant in UTC, without the tzinfo."""
    if value is None or value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


# Use in request schemas wherever a datetime arrives from a client.
NaiveUTC = Annotated[datetime, AfterValidator(to_naive_utc)]
