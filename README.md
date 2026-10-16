# Open HRM

A complete, self-hosted HR system for a small or mid-sized company. Attendance,
leave, payroll, contracts, equipment, performance and onboarding — running on
your own database, your own domain, under your own name.

Nothing about the company that wrote it is compiled in. The company name, the
logo, the colours, the working day, the leave entitlements and the email
sender all come from configuration, so making it yours is filling in two
`.env` files rather than editing source.

**260 API endpoints. 490 tests. Deployable in an afternoon.**

```
backend/     FastAPI + PostgreSQL        → your server
frontend/    React + TypeScript + Vite   → any static host
```

---

## What it does

### Attendance and time

- **Check in and out** from the browser, with optional geofencing and office
  network detection. A desk PC has no GPS, so being on the office network
  counts as being at the office — demanding a satellite fix told people
  sitting at their own desks that they were not there.
- **Fingerprint device sync**, mapping each device enrolment to an employee.
  Unmapped names are surfaced rather than silently discarded, with a count of
  how many punches each one is throwing away.
- **Shifts** with start and end times, working days, grace periods and
  per-employee assignment. Lateness and a short day are measured against the
  shift, not against a global guess.
- **Time logs** for the two cases the clock cannot be trusted with: a session
  nobody checked in for, and overtime. Both need two signatures, and nobody
  can sign their own.
- **Auto-checkout** closes a session that has run past its cap, so a forgotten
  checkout does not become a 14-hour day.

### Leave

- Annual, sick, personal, maternity, paternity, bereavement and unpaid, each
  with its own entitlement and accrual rule.
- **Balances by year**, drawn against the year the leave falls in rather than
  the year it was approved in — a December request signed off in January
  belongs to December.
- Personal leave is spent against the month it falls in, so an approver is
  held to the same monthly allowance the request was filed against.
- Half-days, advance-notice rules, overlap detection, and a medical
  certificate requirement that can be enforced per policy.
- Nobody approves their own leave, including administrators.

### Payroll

- Monthly **payroll runs** across the whole company, or a single payslip.
- Allowances, bonuses, arrears, overtime, reimbursements and loan instalments
  swept in automatically; tax, provident fund, EOBI and social security
  deducted.
- **Issued payslips are locked.** A processed or paid payslip cannot be
  edited or deleted — corrections supersede it with a new revision and a
  stated reason, leaving the original on the record. That is what an audit
  needs and an edit destroys.
- **Payslip disputes** an employee can raise, and a correction flow that
  answers them.
- Excel export of a full run, formatted as a payroll sheet a bank will accept.
- Tax certificates, and a public verification page for a payslip.

### Documents and contracts

- **Templates with placeholders** — employee details, pay, job description,
  KPIs, probation — rendered once at issue and hashed, so a document is
  evidence rather than something that re-renders itself.
- **Typed electronic signatures** with an audit trail, and an integrity check
  on every read that recomputes the hash from the stored text.
- **Public verification**: anyone holding a document can confirm it is genuine
  from the code printed on it, without an account.
- Employment letters and verification codes for banks and landlords.

### People

- Employee records, reporting lines, departments, teams.
- **Org chart** in four layouts — top-down, sideways, orbit, or a compact list.
- **Onboarding checklists** per joiner, issuing whichever documents are
  flagged for it.
- **Probation** tracking with confirm, extend and decline decisions.
- **Offboarding**: resignation, notice, clearance, final settlement.
- **Performance reviews** with cycles, self-assessment, manager review and
  KPI scoring.
- **Employee of the Month** with nominations, secret ballots and a decision
  the nominators cannot make.

### Money owed to people

- **Reimbursements** with receipts, approval and payment tracking — approved
  is not paid, and the two are distinguished so nothing is paid twice.
- **Advances and loans** with instalment schedules recovered through payroll,
  and a waiver that is recorded rather than silent.

### Equipment

- A register of what the company owns, by category and location, with tags.
- **Issue and return** against a person, custody history, and condition.
- Requests, receiving, audits and a stock count.
- Reports: who has what, what sits idle, what it is all worth.

### For administrators

- Analytics across headcount, attendance, leave, payroll, overtime and
  compensation.
- A **"needs attention"** panel: unsigned contracts, probation decisions due,
  leave awaiting a decision, equipment unreturned.
- Announcements, notifications, audit log, scheduled job health.

---

## How it is built

| | |
|---|---|
| **API** | Python 3.12, FastAPI, SQLAlchemy 2.0, Alembic, PostgreSQL |
| **Portal** | React 19, TypeScript, Vite, Zustand |
| **Auth** | JWT access and refresh tokens, role-based access |
| **Files** | Supabase Storage, served through the API so access follows the same permissions as everything else |
| **Email** | Any SMTP provider (Resend, SES, Postmark, Gmail) |
| **Tests** | 490, against an in-memory database |

Four roles — employee, manager, HR, administrator — with HR holding
admin-level reach over everything except admin accounts themselves.

---

## Getting it running

Full instructions: **[backend/DEPLOYMENT.md](backend/DEPLOYMENT.md)** and
**[frontend/DEPLOYMENT.md](frontend/DEPLOYMENT.md)**. The short version:

```bash
git clone https://github.com/qalab-e-hassnain/open-hrm.git
cd open-hrm
```

**API**

```bash
cd backend
cp .env.example .env          # database URL, secret key, your company details
pip install -r requirements.txt
alembic upgrade head
python scripts/bootstrap.py --admin-email you@yourcompany.com --admin-name "Your Name"
uvicorn app.main:app --reload
```

`bootstrap.py` creates the first administrator — there is no other way in,
since every other account is made from inside the portal — along with a
default working pattern and a starting equipment register. It is idempotent,
and it prompts for a password rather than taking one on the command line
where it would sit in your shell history.

**Portal**

```bash
cd frontend
cp .env.example .env          # API address, your company and product name
npm ci
npm run build                 # output in dist/
```

The build refuses to start without the four settings it cannot work without.
That gate exists because the alternative is quiet: a live portal whose browser
tab reads `%VITE_BRAND_NAME%`, or one pointed at the wrong API.

**Docker**

```bash
cd backend && docker compose up -d api
```

### What you must change

- `backend/.env` and `frontend/.env` — your company, your database, your mail.
- `frontend/public/logo.webp` — your logo.
- `frontend/src/index.css` — `--brand-red` and the three shades beside it.
- `frontend/public/handbook/` — **empty**. Put your own handbook here or
  remove the Handbook page.
- `backend/app/utils/leave_policy.py` — the entitlements. These are numbers in
  a policy, not law, and the defaults are examples.
- `frontend/src/pages/admin/roleDefaults.ts` — starter job descriptions.
  Generic examples; replace with your own.

No contract, handbook or policy document ships with this. Those are legal
documents belonging to whoever runs the deployment — write your own in the
portal under **HR Documents**.

---

## Things worth knowing before you rely on it

**Background jobs run on portal traffic.** Reminder sweeps and device syncs
ride on incoming requests rather than a scheduler, so nothing runs overnight
if nobody opens the portal. For anything that must happen on a schedule, call
`POST /api/v1/cron/run-due` from a real scheduler and set `CRON_SECRET`.

**Put the database in the same region as the API.** One query across an ocean
costs about 170ms and a page makes several. No amount of tuning afterwards
gets that back.

**Attendance feeds payroll.** If you connect a fingerprint device, map every
device name. A name left ignored discards that person's punches, and nothing
will tell you.

**Tokens are held in browser storage.** An XSS bug would expose them. Moving
them to httpOnly cookies is the known next step and is not done.

---

## Licence

MIT — see [LICENSE](LICENSE). Use it, change it, sell it, run it for your own
company or somebody else's. Keep the copyright notice.

It comes with no warranty. It handles salaries, contracts and personal data:
read the code, test it against your own policies, and have somebody qualified
check the employment documents you generate with it before anyone signs one.

---

## Who built this

**Qalab Hassnain Agha** — Co-Founder & Chief Technology Officer,
Quickgen Technologies.

Built this HRM to run a real company's people operations — attendance,
payroll, contracts and equipment for a working team — and opened it up so
others can do the same without starting over.

- GitHub — [@qalab-e-hassnain](https://github.com/qalab-e-hassnain)
- Email — qalab@quickgentech.com

Issues and pull requests welcome. If you deploy it somewhere, I would like to
hear about it.
