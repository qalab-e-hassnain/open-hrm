from pydantic_settings import BaseSettings
from typing import List
class Settings(BaseSettings):
    # Application
    APP_NAME: str = "HRM"
    DEBUG: bool = True
    API_V1_STR: str = "/api/v1"
    
    # Database
    DATABASE_URL: str
    
    # Security
    SECRET_KEY: str
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 15   # short-lived access token
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7      # long-lived refresh token
    
    # CORS
    ALLOWED_ORIGINS: str = "*"
    
    # Geofencing - Office Location (the company Office)
    OFFICE_LATITUDE: float = 33.64604610906695
    OFFICE_LONGITUDE: float =  72.99670052053322
    OFFICE_RADIUS_METERS: float = 100.0

    # Comma-separated public IPs/CIDRs of the office connection. Client GPS is
    # attacker-controlled, so the geofence alone cannot prove someone is on
    # site; the source address can't be forged over an established TCP session.
    #
    # Must be the office's PUBLIC address (what https://ifconfig.me returns
    # from an office machine), not a LAN range like 192.168.18.0/24 — that is
    # never visible to a server outside the LAN.
    OFFICE_IP_RANGES: str = ""

    # Off by default on purpose: switching this on with the wrong value blocks
    # check-in for the entire company. Confirm the address via
    # GET /api/v1/attendance/debug/geofencing from an office machine first,
    # then enable.
    #
    # Note this is a different question from the GPS fallback. Listing the
    # office address in OFFICE_IP_RANGES lets the network stand in for a
    # location fix, which is what makes GPS-less desk PCs work; this flag
    # bans off-network check-in outright. Leaving it false is what keeps the
    # fallback fail-safe — a stale address only means desk PCs need GPS
    # again, where they already stood, rather than locking everyone out.
    ENFORCE_OFFICE_IP: bool = False
    
    # Overtime & Work Policy
    STANDARD_WORK_HOURS_PER_DAY: float = 8.0
    OVERTIME_THRESHOLD: float = 8.0
    WEEKDAY_OVERTIME_RATE: float = 1
    WEEKEND_OVERTIME_RATE: float = 1
    HOLIDAY_OVERTIME_RATE: float = 1
    CALCULATE_OVERTIME_BONUS: bool = True
    OVERTIME_BONUS_CURRENCY: str = "PKR"

    # A session is closed automatically once it reaches the employee's standard
    # hours; the employee must check in again for the extra time to count as
    # overtime. Set false to let a single session run unbounded again.
    AUTO_CHECKOUT_AT_STANDARD_HOURS: bool = True

    # Notify admins on every employee check-in/check-out. Off by default: with a
    # full directory this is the highest-volume event by far and would bury the
    # notifications that need action. Leave/reimbursement requests and password
    # resets always notify admins regardless of this flag.
    NOTIFY_ADMINS_ON_ATTENDANCE: bool = False

    # Company details (single-company deployment) — printed on payslips and
    # tax certificates. These are the registered particulars, so they appear on
    # documents an employee may hand to a bank or the FBR; keep them exact.
    COMPANY_NAME: str = "Your Company"
    # National Tax Number. Named COMPANY_REG_NO for backward compatibility with
    # any environment already setting that key.
    COMPANY_REG_NO: str = ""
    COMPANY_ADDRESS: str = ""
    COMPANY_EMAIL: str = ""

    # Hours ahead of UTC the company actually operates in. Timestamps are
    # stored in UTC and dates like a last working day are local, so anything
    # that subtracts one from the other has to reconcile them first — between
    # midnight and 5am in Pakistan the two disagree by a day, and a day of
    # notice is real money.
    COMPANY_UTC_OFFSET_HOURS: int = 5

    # Notice an employee serves on resignation. A default, not a rule — an
    # administrator may agree a different last working day when approving.
    NOTICE_PERIOD_DAYS: int = 30

    # --- Exit settlement policy -------------------------------------------
    # These four settings are company policy, not law, and each one moves real
    # money. They are settings rather than constants so the answer can change
    # without a code change, and every computed settlement is a DRAFT that HR
    # reviews and approves before anyone is paid.
    #
    # Gratuity is OFF by default. The Standing Orders Ordinance threshold is
    # twenty or more workers and the company is below it, so gratuity here is a
    # contractual promise rather than a statutory one. Switch it on only if the
    # employment contracts actually promise it.
    GRATUITY_ENABLED: bool = False
    # Days of wages per completed year of service. Thirty is one month's pay
    # per year, which is the usual formulation.
    GRATUITY_DAYS_PER_YEAR: int = 30
    # Part-years count once they exceed this, per the Ordinance's "any part
    # thereof in excess of six months".
    GRATUITY_PART_YEAR_MONTHS: int = 6

    # Unused annual leave paid out on exit. Carryover is included by default —
    # it is leave the employee earned and was allowed to keep.
    ENCASH_ANNUAL_CARRYOVER: bool = True
    # "gross" values a leave day at the full monthly salary divided by the
    # working month; "basic" values it at the payslip's Basic Pay, which is
    # gross/1.1 and therefore about 9% less.
    LEAVE_ENCASHMENT_BASIS: str = "gross"

    # Recover pay in lieu when someone leaves inside their notice period.
    RECOVER_NOTICE_SHORTFALL: bool = True

    # --- Lab inventory policy ---------------------------------------------
    # Settings rather than constants, for the same reason the settlement policy
    # above is: each one is a company decision that will be revisited, and none
    # of them should need a deploy to change.
    #
    # Holds expire so that "still out with somebody" is a fact somebody
    # re-confirmed recently, not an assumption from six months ago. Renewal is
    # one tap, but it forces a look at the shelf.
    INV_HOLD_DAYS_ASSET: int = 30
    INV_HOLD_DAYS_CONSUMABLE: int = 14
    # Warn the holder this many days before a hold falls due.
    INV_HOLD_WARN_DAYS: int = 3
    # Above this, per unit, a thing is tracked individually (Class A). Below it,
    # count it and do not chase it. Precision on resistor counts is not worth
    # the labour it costs.
    INV_CLASS_A_THRESHOLD: float = 40000.0
    # Component counts inside this band are noted and ignored.
    INV_STOCK_TOLERANCE_PCT: float = 10.0
    # An item sitting in quarantine longer than this is nagged about weekly.
    INV_QUARANTINE_DAYS: int = 30
    # Leave longer than this, with equipment still checked out, tells the
    # custodian so they can decide whether to reclaim it.
    INV_LONG_LEAVE_DAYS: int = 7
    # An asset that has not moved in this long is a candidate for reallocation.
    INV_IDLE_ASSET_DAYS: int = 180

    # Office fingerprint device. Read-only: the HRM pulls badge records, it
    # never writes to the device.
    #
    # NOTE: this device is currently reachable on a public address with no
    # authentication, and serves Werkzeug's development server. Anyone holding
    # the URL can download the whole company's attendance. It belongs behind a
    # firewall rule or a VPN; the integration does not depend on it staying
    # public.
    BIOMETRIC_BASE_URL: str = "http://45.94.58.200:1976"
    # The same device also serves raw scan events on another port. This is the
    # better source: individual punches rather than a pre-aggregated in/out
    # pair, and timestamps that carry their own +05:00 offset instead of
    # leaving the reader to assume one.
    BIOMETRIC_LOGS_URL: str = "http://45.94.58.200:5050"
    BIOMETRIC_DEVICE_MAC: str = "24:0A:C4:C0:82:D4"
    BIOMETRIC_TIMEOUT_SECONDS: float = 20.0
    # Currency for salary/tax figures shown on certificates.
    CURRENCY: str = "PKR"

    # Supabase Storage (required by the Vercel deployment for uploads)
    SUPABASE_URL: str = ""
    SUPABASE_SERVICE_ROLE_KEY: str = ""
    SUPABASE_STORAGE_BUCKET: str = "hrm-uploads"

    # Where the portal lives, for the emails that tell somebody to go there.
    #
    # Every email the system sends used to announce that something had
    # happened — your account is ready, your leave was approved, your payslip
    # is out — and none of them said where to go and look at it. A new joiner
    # got a password with nothing to type it into.
    #
    # Settable because this moves: a staging portal and the live one send the
    # same mail and must not point at each other.
    PORTAL_URL: str = "http://localhost:5173"

    # Branding, for a deployment that is not ours.
    #
    # This product is handed to other companies to run for their own staff, so
    # anything a recipient sees has to come from configuration rather than
    # from a string in the source. BRAND_NAME is the short name on emails and
    # the API root; HR_REPLY_TO is where a reply to an automated email should
    # actually land, which is never the no-reply sender.
    BRAND_NAME: str = "HR Portal"
    # A square logo, served over https. Left blank, emails show the name
    # alone, which is correct for a tenant who has not supplied one.
    BRAND_LOGO_URL: str = ""
    HR_REPLY_TO: str = ""

    # Email (Optional)
    SMTP_HOST: str = ""
    SMTP_PORT: int = 587
    SMTP_USER: str = ""
    SMTP_PASSWORD: str = ""
    # The verified sender address shown to recipients. Kept separate from
    # SMTP_USER because some providers (e.g. Resend) authenticate with a
    # fixed username that is not itself a mailable address.
    SMTP_FROM_EMAIL: str = ""

    # Shared secret Vercel Cron Jobs send as `Authorization: Bearer <value>`
    # (Vercel attaches this itself once CRON_SECRET is set as an env var on
    # the project — see app/api/v1/cron.py). Empty means the cron endpoint
    # refuses every request, so a misconfigured deployment fails closed
    # rather than running the sweep unauthenticated.
    CRON_SECRET: str = ""
    
    class Config:
        env_file = ".env"
        case_sensitive = True
    
    @property
    def cors_origins(self) -> List[str]:
        if self.ALLOWED_ORIGINS == "*":
            return ["*"]
        return [origin.strip() for origin in self.ALLOWED_ORIGINS.split(",")
                if origin.strip()]

    def unsafe_for_production(self) -> List[str]:
        """
        Settings that are fine on a laptop and dangerous on the internet.

        Checked at startup rather than left to a warning in a document,
        because the person deploying is rarely the person who read the
        document, and every one of these fails silently: the system works
        perfectly while being wide open.
        """
        problems = []
        if self.ALLOWED_ORIGINS.strip() == "*":
            problems.append(
                "ALLOWED_ORIGINS is '*', so any website can call this API with "
                "a signed-in employee's browser. Set it to your portal's "
                "origin, e.g. https://hr.yourcompany.com")
        if self.DEBUG:
            problems.append(
                "DEBUG is on, which returns tracebacks to callers. Set DEBUG=False")
        if not self.SECRET_KEY or len(self.SECRET_KEY) < 32:
            problems.append(
                "SECRET_KEY is missing or too short to resist guessing. "
                "Generate one: python -c \"import secrets; print(secrets.token_urlsafe(48))\"")
        if self.PORTAL_URL.startswith("http://") and "localhost" not in self.PORTAL_URL:
            problems.append(
                f"PORTAL_URL is plain http ({self.PORTAL_URL}), so the links "
                "emailed to staff are not encrypted")
        return problems


settings = Settings()

