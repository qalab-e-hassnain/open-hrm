"""
Unhandled exceptions, kept where they can actually be read.

The application logs a traceback whenever a request 500s, but those logs live
in the hosting platform's log stream — which is reachable by whoever holds that
subscription and nobody else. In practice that has meant a 500 gets reported as
"there is an error" with no way to find out what it was, on an API that is
otherwise healthy when checked a minute later.

The database is the one place both the running app and anyone maintaining it
can see. A short reference goes back to the caller, so a report can name the
exact failure instead of describing it.
"""
from sqlalchemy import Column, Integer, String, Text, DateTime
from datetime import datetime

from app.database import Base


class ErrorLog(Base):
    __tablename__ = "error_logs"

    id = Column(Integer, primary_key=True, index=True)
    # Short, human-quotable. Goes back in the 500 body.
    reference = Column(String(12), unique=True, index=True, nullable=False)
    occurred_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)

    method = Column(String, nullable=True)
    path = Column(String, nullable=True, index=True)
    query = Column(String, nullable=True)
    actor_email = Column(String, nullable=True, index=True)
    client_ip = Column(String, nullable=True)

    exception_type = Column(String, nullable=True, index=True)
    message = Column(Text, nullable=True)
    traceback = Column(Text, nullable=True)
