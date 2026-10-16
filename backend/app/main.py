import sys
from pathlib import Path
import logging

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)

# Add project root to Python path when running directly
# This allows the file to be run as: python app/main.py
project_root = Path(__file__).parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from fastapi import Depends, FastAPI, Request, status, HTTPException
from starlette.background import BackgroundTask, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import ProgrammingError, OperationalError
import traceback

# PostgreSQL-specific imports (only if using PostgreSQL)
try:
    from psycopg2.errors import UndefinedColumn, UndefinedTable
    HAS_POSTGRES = True
except ImportError:
    HAS_POSTGRES = False
    # Create dummy classes for SQLite compatibility
    class UndefinedColumn(Exception):
        pass
    class UndefinedTable(Exception):
        pass
from app.config import settings
from app.api.deps import require_admin_or_hr
from app.api.v1 import auth, users, teams, leaves, reimbursements, payroll, payroll_runs, payslips, attendance, notifications, upload, announcements, cron, time_logs, documents, onboarding, offboarding, biometric, jobs, assets, inventory, loans, settlements, probation, shifts, performance, analytics

# Settings that are fine on a laptop and dangerous on the internet, reported
# the moment the process starts.
#
# Every one of them fails silently: the system works perfectly while being
# wide open, so nobody finds out until somebody else does. A log line at
# startup is seen by whoever deployed it, which a warning in a document is
# not.
_unsafe = settings.unsafe_for_production()
if _unsafe:
    _log = logging.getLogger("app.startup")
    _log.warning("=" * 68)
    _log.warning("INSECURE CONFIGURATION — %d problem(s)", len(_unsafe))
    for _problem in _unsafe:
        _log.warning("  * %s", _problem)
    _log.warning("=" * 68)


app = FastAPI(
    title=settings.APP_NAME,
    description="Human Resource Management System API",
    version="1.0.0",
    docs_url=None,
    redoc_url=None,
    openapi_url=None
)


# Custom exception handler for validation errors (422)
@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """Provide clearer error messages for validation errors"""
    errors = exc.errors()
    error_messages = []
    
    for error in errors:
        field = " -> ".join(str(loc) for loc in error["loc"])
        message = error["msg"]
        error_type = error["type"]
        
        # Provide user-friendly messages
        if error_type == "value_error.missing":
            error_messages.append(f"Missing required field: {field}")
        elif error_type == "type_error":
            if "datetime" in str(error.get("ctx", {})) or "date" in field.lower():
                error_messages.append(
                    f"Invalid date format for {field}. "
                    f"Accepted formats: YYYY-MM-DD, DD/MM/YYYY, or ISO datetime"
                )
            else:
                error_messages.append(f"Invalid type for {field}: {message}")
        elif error_type == "value_error" and "Unable to parse date" in message:
            error_messages.append(
                f"{field}: {message}. "
                f"Please use format: DD/MM/YYYY (e.g., 25/12/2025) or YYYY-MM-DD"
            )
        elif "date" in field.lower() and "past" in message.lower():
            error_messages.append(f"{field}: Dates must be today or in the future")
        elif "date" in field.lower() and ("before" in message.lower() or "equal" in message.lower()):
            if "equal" in message.lower():
                error_messages.append(f"{field}: Start date must be before or equal to end date (single-day leaves are allowed)")
            else:
                error_messages.append(f"{field}: Start date must be before end date")
        elif "leave_type" in field.lower():
            error_messages.append(f"{field}: {message}")
        else:
            error_messages.append(f"{field}: {message}")
    
    # Convert errors to JSON-serializable format
    serializable_errors = []
    for error in errors:
        serializable_error = {
            "type": error.get("type"),
            "loc": error.get("loc"),
            "msg": error.get("msg"),
            "input": error.get("input")
        }
        # Convert ctx to string if it contains non-serializable objects
        if "ctx" in error:
            ctx = error["ctx"]
            if isinstance(ctx, dict):
                serializable_ctx = {}
                for k, v in ctx.items():
                    # Convert exceptions and other non-serializable objects to strings
                    if isinstance(v, (Exception, BaseException)):
                        serializable_ctx[k] = str(v)
                    else:
                        try:
                            # Try to serialize the value
                            import json
                            json.dumps(v)
                            serializable_ctx[k] = v
                        except (TypeError, ValueError):
                            # If not serializable, convert to string
                            serializable_ctx[k] = str(v)
                serializable_error["ctx"] = serializable_ctx
            elif isinstance(ctx, (Exception, BaseException)):
                serializable_error["ctx"] = str(ctx)
            else:
                serializable_error["ctx"] = str(ctx) if ctx else None
        serializable_errors.append(serializable_error)
    
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={
            "detail": error_messages if error_messages else "Validation error",
            "errors": serializable_errors
        }
    )


# Global exception handler for database schema errors
@app.exception_handler(ProgrammingError)
async def database_schema_error_handler(request: Request, exc: ProgrammingError):
    """Handle database schema errors gracefully.

    The client never sees migration/schema internals — that used to leak
    straight through (e.g. "Please run database migration... alembic
    upgrade head") on any page that hit a stale-schema query, which is both
    a confusing message for an end user and free information for anyone
    probing the API. The real detail is logged server-side instead, which
    this handler previously didn't do at all — so a genuine schema issue
    had no log trail beyond what the client saw.
    """
    error_message = str(exc.orig) if hasattr(exc, 'orig') else str(exc)
    logger = logging.getLogger(__name__)

    if "UndefinedColumn" in error_message or ("column" in error_message.lower() and "does not exist" in error_message.lower()) or "no such column" in error_message.lower():
        logger.error("Schema error (missing column) on %s %s: %s", request.method, request.url.path, error_message)
    elif "UndefinedTable" in error_message or ("relation" in error_message.lower() and "does not exist" in error_message.lower()) or "no such table" in error_message.lower():
        logger.error("Schema error (missing table) on %s %s: %s", request.method, request.url.path, error_message)
    else:
        logger.error("Database error on %s %s: %s", request.method, request.url.path, error_message)

    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"detail": "Something went wrong while loading this. Please try again in a moment."},
    )


# Handler for SQLAlchemy OperationalError
@app.exception_handler(OperationalError)
async def database_operational_error_handler(request: Request, exc: OperationalError):
    """Handle database operational errors gracefully"""
    logging.getLogger(__name__).error(
        "Database connection failed for %s %s (%s)",
        request.method,
        request.url.path,
        type(exc.orig).__name__ if hasattr(exc, "orig") else type(exc).__name__,
    )
    
    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        content={"detail": "Database connection error. Please try again later."},
    )

# Catch-all for anything not handled by the specific handlers above (e.g. a
# stray AttributeError/KeyError from a bug). Without this, FastAPI/Starlette's
# default behavior returns a bare "Internal Server Error" as plain text, not
# JSON — the frontend's error handling expects a JSON {"detail": ...} body,
# so an uncaught bug would otherwise surface as a raw, unstyled error.
@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    """
    Log the real exception, keep a copy somewhere readable, and return a clean
    message to the client.

    The traceback goes to the platform log stream as before, and also to the
    database — a 500 reported by a user is otherwise undiagnosable by anyone
    without access to the hosting subscription, which is how "there is a 500"
    turns into a guessing exercise against an API that looks healthy by the
    time it is checked.
    """
    logging.getLogger(__name__).exception(
        "Unhandled exception on %s %s", request.method, request.url.path
    )
    reference = record_unhandled_exception(request, exc)
    body = {
        "detail": "Something went wrong on our end. Please try again in a moment.",
        "error": "Internal server error",
    }
    if reference:
        body["reference"] = reference
        body["detail"] = (
            "Something went wrong on our end. Please try again in a moment. "
            f"If you report this, quote {reference}."
        )
    return JSONResponse(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, content=body)



def record_unhandled_exception(request: Request, exc: Exception):
    """
    Persist one failure. Returns a short reference, or None if it could not be
    stored — this must never raise, or a 500 becomes a 500 about the 500.
    """
    import secrets
    import traceback as tb

    try:
        from app.database import SessionLocal
        from app.models.error_log import ErrorLog
        from app.utils import audit

        reference = secrets.token_hex(4).upper()
        actor = None
        auth = request.headers.get("authorization") or ""
        if auth.lower().startswith("bearer "):
            try:
                from app.core.security import decode_access_token
                payload = decode_access_token(auth.split(" ", 1)[1])
                actor = (payload or {}).get("sub")
            except Exception:
                actor = None

        db = SessionLocal()
        try:
            db.add(ErrorLog(
                reference=reference, method=request.method,
                path=str(request.url.path),
                query=str(request.url.query)[:500] or None,
                actor_email=actor, client_ip=audit.client_ip(request),
                exception_type=type(exc).__name__, message=str(exc)[:2000],
                traceback="".join(tb.format_exception(
                    type(exc), exc, exc.__traceback__))[-8000:],
            ))
            db.commit()
            return reference
        finally:
            db.close()
    except Exception:
        logging.getLogger(__name__).exception("Could not persist the failure detail")
        return None


# CORS is configured through ALLOWED_ORIGINS. Set it to the deployed frontend
# origin in Vercel, rather than allowing every website in production.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS", "PATCH"],
    allow_headers=["*"],
    expose_headers=["*"],
    max_age=3600,
)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    """
    The headers a browser needs to be told, on every response.

    This API serves no HTML at all — docs_url, redoc_url and openapi_url are
    all off — so the policy can be the strictest one there is: nothing may be
    loaded, and nothing may frame it. That makes these cheap to apply and
    impossible to outgrow by accident; if an HTML page is ever served from
    here, this will stop it and should be revisited then rather than loosened
    pre-emptively.

    They live here rather than in the nginx config because this file is
    deployed and version controlled, and the server in front of it has been
    reconfigured by hand before.
    """
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
    # Only over TLS, and only once the proxy says the original request was —
    # sending it over plain HTTP is meaningless and sending it from a local
    # development server would pin a developer's browser to HTTPS.
    forwarded = request.headers.get("x-forwarded-proto", request.url.scheme)
    if forwarded == "https":
        response.headers.setdefault(
            "Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    return response


@app.middleware("http")
async def run_due_sweeps(request: Request, call_next):
    """
    Let the portal's traffic drive the scheduled sweeps.

    F1 gives us no scheduler — no Always On, no WebJobs — so the reminders,
    exit completions and device syncs ride on requests instead. The work is
    attached as a background task, so it runs after the response has been sent
    and no user ever waits for it. See app/utils/scheduler.py.
    """
    response = await call_next(request)
    # A CORS preflight is not a person arriving, and there are two of them for
    # every real request. Everything else counts, /health included — a warmup
    # ping is as good a heartbeat as a login.
    if request.method == "OPTIONS":
        return response
    try:
        from app.utils import scheduler
        if scheduler.should_poll():
            poll = BackgroundTask(scheduler.poll)
            if response.background is None:
                response.background = poll
            else:
                # An endpoint already queued work — send an email, say. Chain
                # rather than replace, or that work silently stops happening.
                chained = BackgroundTasks()
                chained.tasks.append(response.background)
                chained.tasks.append(poll)
                response.background = chained
    except Exception:
        # A sweep that cannot even be scheduled must not break the response
        # the user actually asked for.
        logging.getLogger(__name__).exception("Could not schedule the due-sweep poll")
    return response

# Include routers
app.include_router(auth.router, prefix=f"{settings.API_V1_STR}/auth", tags=["Authentication"])
app.include_router(users.router, prefix=f"{settings.API_V1_STR}/users", tags=["Users"])
app.include_router(teams.router, prefix=f"{settings.API_V1_STR}/teams", tags=["Teams"])
app.include_router(leaves.router, prefix=f"{settings.API_V1_STR}/leaves", tags=["Leaves"])
app.include_router(reimbursements.router, prefix=f"{settings.API_V1_STR}/reimbursements", tags=["Reimbursements"])
# Registered before the payroll router so /payroll/runs/... is matched by the
# sheet routes rather than falling through to payroll's own paths.
app.include_router(payroll_runs.router, prefix=f"{settings.API_V1_STR}/payroll", tags=["Payroll Sheet"])
app.include_router(payroll.router, prefix=f"{settings.API_V1_STR}/payroll", tags=["Payroll"])
app.include_router(payslips.router, prefix=f"{settings.API_V1_STR}/payslips", tags=["Payslips"])
app.include_router(attendance.router, prefix=f"{settings.API_V1_STR}/attendance", tags=["Attendance"])
app.include_router(notifications.router, prefix=f"{settings.API_V1_STR}/notifications", tags=["Notifications"])
app.include_router(upload.router, prefix=f"{settings.API_V1_STR}/upload", tags=["Uploads"])
app.include_router(announcements.router, prefix=f"{settings.API_V1_STR}/announcements", tags=["Announcements"])
app.include_router(cron.router, prefix=f"{settings.API_V1_STR}/cron", tags=["Cron"])
app.include_router(time_logs.router, prefix=f"{settings.API_V1_STR}/time-logs", tags=["Time Logs"])
app.include_router(documents.router, prefix=f"{settings.API_V1_STR}/documents", tags=["Documents"])
app.include_router(onboarding.router, prefix=f"{settings.API_V1_STR}/onboarding", tags=["Onboarding"])
app.include_router(offboarding.router, prefix=f"{settings.API_V1_STR}/offboarding", tags=["Offboarding"])
app.include_router(biometric.router, prefix=f"{settings.API_V1_STR}/biometric", tags=["Biometric Device"])
app.include_router(jobs.router, prefix=f"{settings.API_V1_STR}/admin/jobs", tags=["Scheduled Jobs"])
app.include_router(inventory.router, prefix=f"{settings.API_V1_STR}/inventory", tags=["Lab Inventory"])
# Deprecated: the old asset register, served out of the inventory tables.
# Kept mounted for one release so the portal can move separately.
app.include_router(assets.router, prefix=f"{settings.API_V1_STR}/assets", tags=["Assets"])
app.include_router(loans.router, prefix=f"{settings.API_V1_STR}/loans", tags=["Advances & Loans"])
app.include_router(settlements.router, prefix=f"{settings.API_V1_STR}/settlements", tags=["Final Settlement"])
app.include_router(probation.router, prefix=f"{settings.API_V1_STR}/probation", tags=["Probation"])
app.include_router(shifts.router, prefix=f"{settings.API_V1_STR}/shifts", tags=["Shifts"])
app.include_router(performance.router, prefix=f"{settings.API_V1_STR}/performance", tags=["Performance"])
app.include_router(analytics.router, prefix=f"{settings.API_V1_STR}/analytics", tags=["Analytics"])


@app.get("/")
async def root():
    return {
        "message": f"{settings.APP_NAME} API",
        "version": "1.0.0",
        "docs": "/docs"
    }


@app.get("/health")
async def health_check():
    return {"status": "healthy"}


@app.get("/debug/geofencing")
async def debug_geofencing(
    request: Request,
    lat: float = None,
    lon: float = None,
    current_user=Depends(require_admin_or_hr),
):
    """
    Inspect geofencing configuration, and report the source address the server
    actually observes for this request.

    Admin-only: it discloses the office coordinates and the network policy.
    Open it from a machine on the office connection to read the public IP that
    belongs in OFFICE_IP_RANGES — a LAN address such as 192.168.18.x will never
    appear here, because every office request is NAT-translated on the way out.
    """
    from app.core.geofencing import (
        get_distance_from_office, is_within_office_boundary,
        is_office_network, office_networks,
    )
    from app.utils import audit

    seen_ip = audit.client_ip(request)
    response = {
        "your_source_ip": seen_ip,
        "office_network": {
            "enforced": settings.ENFORCE_OFFICE_IP,
            "configured_ranges": [str(n) for n in office_networks()],
            "your_ip_matches": is_office_network(seen_ip),
        },
        "office_location": {
            "latitude": settings.OFFICE_LATITUDE,
            "longitude": settings.OFFICE_LONGITUDE
        },
        "office_radius_meters": settings.OFFICE_RADIUS_METERS
    }
    
    if lat is not None and lon is not None:
        distance = get_distance_from_office(lat, lon)
        is_within = is_within_office_boundary(lat, lon)
        
        response["test_location"] = {
            "latitude": lat,
            "longitude": lon
        }
        response["distance_from_office_meters"] = round(distance, 2)
        response["is_within_boundary"] = is_within
        response["result"] = "✅ ALLOWED" if is_within else "❌ REJECTED"
    
    return response


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=settings.DEBUG)

