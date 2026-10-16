from sqlalchemy import Column, Integer, String, Date, Boolean, DateTime, UniqueConstraint
from datetime import datetime

from app.database import Base


class Holiday(Base):
    """
    A public holiday the company observes.

    A table rather than a list in code because Pakistan's calendar is half
    fixed and half lunar: Kashmir Day and Independence Day fall on the same
    date every year, while the Eids, Ashura and Eid Milad-un-Nabi move with the
    Islamic calendar and are confirmed by announcement, often only weeks ahead.
    A hardcoded list would be wrong every year and could only be corrected by a
    deploy.
    """
    __tablename__ = "holidays"

    id = Column(Integer, primary_key=True, index=True)
    holiday_date = Column(Date, nullable=False, index=True)
    name = Column(String, nullable=False)

    # False for a date pencilled in from an unconfirmed lunar estimate. It still
    # counts as a holiday, but the flag says which entries an admin should
    # confirm against the official notification each year.
    is_confirmed = Column(Boolean, default=True, nullable=False, server_default="true")

    # Lunar dates shift; fixed national days do not. Only the lunar ones need
    # revisiting annually, and this is what tells them apart.
    is_recurring = Column(Boolean, default=False, nullable=False, server_default="false")

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("holiday_date", name="uq_holiday_date"),
    )

    def __repr__(self):
        return f"<Holiday {self.holiday_date} {self.name}>"
