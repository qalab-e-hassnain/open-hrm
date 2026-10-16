# Running this HRM for your own company

Written for the engineer setting it up. It assumes you can run Docker and
reach a Postgres database, and nothing else.

Nothing about the company that wrote this is compiled into the code. The
company name, the logo, the portal address, the working day and the leave
entitlements all come from configuration, so making it yours is filling in
two `.env` files rather than editing source.

There are two pieces:

| | What it is | Where it runs |
|---|---|---|
| **API** (this repository) | FastAPI + Postgres | A server you control, behind nginx |
| **Portal** (the frontend repository) | A React app, built to static files | Any static host: Azure Static Web Apps, Netlify, Cloudflare Pages, or the same nginx |

The portal talks to the API over HTTPS. They can live on different hosts and
usually do.

---

## 1. What you need first

- **A Postgres database.** Managed is fine — Supabase, RDS, Cloud SQL, Neon.
  **Put it in the same region as the API.** One query to a database on another
  continent costs about 170ms, a single page makes several, and no amount of
  tuning afterwards gets that back.
- **A place to store files** — profile pictures, ID scans, receipts. Supabase
  Storage, with a bucket and a service-role key. Without it, uploads fail;
  everything else works.
- **An SMTP sender.** Resend is what these instructions assume. Without it the
  system runs and simply sends no mail.
- **Two hostnames**, for example `hr.yourcompany.com` for the portal and
  `hr-api.yourcompany.com` for the API, each with a TLS certificate.

---

## 2. The API

### Configure

```bash
cp .env.example .env
```

Fill it in. Every value is explained in the file. The ones people get wrong:

- **`ENVIRONMENT`** — leave it as `production`. Set it to `development` only
  on a machine you are working on. See below: in production the API refuses
  to start rather than run with settings that leave it exposed.
- **`COMPANY_UTC_OFFSET_HOURS`** — attendance is stored in UTC and shown in
  local time. Wrong value, and every check-in reads at the wrong hour.
- **`PORTAL_URL`** — emails link here. Wrong value sends your staff to
  somebody else's login page.
- **`ALLOWED_ORIGINS`** — your portal's origin. Leave it as `*` and any
  website can call your API with a signed-in user's browser.
- **`SECRET_KEY`** — generate a fresh one. Never reuse another deployment's;
  anyone holding it can mint tokens for your system.

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

### It will not start if any of that is wrong

With `ENVIRONMENT=production`, the API exits at startup — printing exactly
what to fix — rather than serving when:

| | |
|---|---|
| `DEBUG` is on | tracebacks naming your tables and file paths go to callers |
| `ALLOWED_ORIGINS` is `*` | any website can call your API with a signed-in employee's browser and their session |
| `SECRET_KEY` is missing or under 32 characters | tokens can be forged by guessing it |
| `SECRET_KEY` looks copied (`change-me`, `your-secret-key`, one repeated character) | worse: the key is published, so anyone who has read this repository can sign a token for any account |
| `PORTAL_URL` is plain `http` | the sign-in links you email staff are not encrypted |

This used to be a warning in the log. It is a refusal now because every item
on that list is invisible from the outside: the portal loads, people sign in,
payroll runs, and nothing degrades — so nobody investigates. A deployment
that will not start gets looked at within minutes; one that starts insecurely
gets looked at after the incident.

The default is `production`, so a deployment that never sets `ENVIRONMENT`
gets the checks. Anything unrecognised — `prod`, `staging`, a typo — also
counts as production, so a misspelling makes it stricter rather than
switching it off. `development`, `dev`, `local` and `test` turn the refusals
back into warnings.

It also prints advisories at startup, which do **not** stop it: no
`CRON_SECRET` (scheduled work will not run), no SMTP settings (no mail at
all, including the credentials new joiners need), no object storage (uploads
will fail). Those are things somebody notices. The list above is not.

### Create the schema

```bash
alembic upgrade head
```

### Create the first administrator

Every other account is made from inside the portal, so without this there is
no way in.

```bash
python scripts/bootstrap.py \
  --admin-email you@yourcompany.com \
  --admin-name "Your Name" \
  --shift-start 09:00 --shift-end 17:00
```

It prompts for a password, or generates one and prints it once. It also
creates a default working pattern — attendance measures lateness and a short
day against a shift, and with none defined it can answer neither — and a
starting set of equipment categories.

Run it once. Running it again changes nothing, so it is safe if you are not
sure whether it finished.

**It creates no contract, handbook, job description or policy document.**
Those are legal documents belonging to you, not samples to inherit from
somebody else. Write your own in the portal under **HR Documents**.

### Run it

```bash
docker compose build api
docker compose run --rm --no-deps api alembic upgrade head
docker compose up -d api
curl -s localhost:8002/health      # {"status":"healthy"}
```

### Put nginx in front

`deploy/nginx.example.conf` is a working example: TLS,
gzip, a redirect from port 80, and `server_tokens off` so the server stops
announcing its exact version. Copy it, change the hostname, point it at port
8002, and get a certificate with certbot.

---

## 3. The portal

In the frontend repository:

```bash
cp .env.example .env
```

Four values are required and the build **refuses to start** without them,
because the alternative is a live portal whose browser tab reads
`%VITE_BRAND_NAME%` or which talks to the wrong API:

| | |
|---|---|
| `VITE_API_BASE_URL` | `https://hr-api.yourcompany.com/api/v1` — include `/api/v1` |
| `VITE_BRAND_NAME` | What the software is called |
| `VITE_COMPANY_NAME` | Your registered name, printed on documents |
| `VITE_COMPANY_SHORT_NAME` | The same company in a sentence |

Then:

```bash
npm ci
npm run build          # output in dist/
```

Upload `dist/` to your static host. The build writes
`dist/staticwebapp.config.json` with a Content-Security-Policy pointing at
**your** API — if your host is not Azure Static Web Apps, copy those headers
into its own configuration, or the policy will not be applied at all.

### Replace the artwork

- `public/logo.webp` — your logo, square, shown in the sidebar and on login.
- `src/index.css` — the colours. Change `--brand-red` and the three shades
  beside it; everything else is derived from them.
- `public/handbook/` — **delete what is there.** It is another company's
  employee handbook. Put yours in its place or remove the Handbook page.

---

## 4. Before you let anyone in

- [ ] Sign in as the administrator and change the generated password.
- [ ] Set the leave entitlements for your company in
      `app/utils/leave_policy.py` (`LeavePolicyConfig`) — annual, sick,
      personal, parental days, and the probation length. These are numbers in
      a policy, not law, and the defaults are somebody else's.
- [ ] Write your employment contract and policy templates under **HR
      Documents**. Mark the contract *include in onboarding* so new joiners
      are issued it automatically.
- [ ] Check the working pattern under **Shifts** matches your actual day.
- [ ] Send yourself a test email — add a user with your own address and see
      that the welcome email arrives with a working sign-in button. If it
      does not, the from address is probably not on a domain verified with
      your mail provider; mail is accepted and silently dropped in that case.
- [ ] Add your people under **User Management**.

---

## 5. One migration you cannot undo

`alembic downgrade` works for every migration except one. **inv02**
(`b2c3d4e5f6a7`, "assets become inv_items") is deliberately irreversible: it
folds a category enum and a free-text location column into rows that cannot
be told apart afterwards, so there is nothing honest to write in its
downgrade. It raises instead of pretending.

What that means in practice:

```bash
alembic downgrade b2c3d4e5f6a7   # fine — rolls back everything above it
alembic downgrade base           # fails, on purpose
```

If you need the schema from before inv02, restore from a backup taken before
you upgraded. That is the only route, which is a reason to have taken one —
see `scripts/backup.sh`.

CI exercises the rollback down to that revision and back up on every push, so
everything above the line stays reversible.

## 6. Backups, and proving they work

```bash
BACKUP_DIR=/mnt/backups ./scripts/backup.sh
BACKUP_PASSPHRASE=... BACKUP_DIR=/mnt/backups ./scripts/backup.sh   # encrypted
```

Two things about this system make backups easy to get wrong.

**The database dump is not a backup of this system.** Profile pictures, CNIC
scans, receipts and signed documents live in object storage, and the database
holds only their names. Restore the database alone and every document in the
system is a broken link. `backup.sh` backs up both and says so loudly if it
cannot reach the storage.

**A backup nobody has restored is not a backup, it is a file.** Restore into
a scratch database on a schedule, so the first time you do it is not during
an incident:

```bash
./scripts/restore.sh /mnt/backups/hrm-20261016T030000Z.dump \
    --into postgresql://postgres@localhost:5432/hrm_restore_drill
```

It verifies the dump's checksum before starting, refuses to restore over a
database that already has tables unless you pass `--force`, **fails on any
pg_restore error** rather than reporting success over a half-restored
database, and then checks what came back: row counts, that the core tables
exist, and that the migration version matches the code in your checkout — a
dump taken before a migration restores perfectly and then fails on the first
request with a missing column.

A crontab that does both, with the drill on Sundays:

```cron
15 3 * * *  cd /srv/hrm/backend && BACKUP_DIR=/mnt/backups BACKUP_PASSPHRASE=... ./scripts/backup.sh >> /var/log/hrm-backup.log 2>&1
40 4 * * 0  cd /srv/hrm/backend && ./scripts/restore.sh "$(ls -t /mnt/backups/hrm-*.dump* | head -1)" --into postgresql://postgres@localhost:5432/hrm_restore_drill --force >> /var/log/hrm-restore-drill.log 2>&1
```

Both scripts exit non-zero on failure, so whatever runs them can alert. The
drill is the half people skip, and it is the half that tells you whether the
other half works.

## 7. Things worth knowing

**Background jobs run on portal traffic.** Reminder sweeps, device syncs and
exit completions ride on incoming requests rather than a scheduler, so
nothing runs overnight if nobody opens the portal. For anything that must
happen on a schedule, call `POST /api/v1/cron/run-due` from a real scheduler
and set `CRON_SECRET`.

**The database connection pool is sized for the server's limits.** It reads
`WEB_CONCURRENCY` and keeps headroom for migrations and scripts. If you move
to a database that allows more connections, raise the budget in
`app/database.py`; the comment there explains the arithmetic.

**Uploads are served through the API, not from storage directly**, so file
access follows the same permissions as everything else. They are cached hard
and for ever, which is safe because every upload gets a fresh name.

**Attendance feeds payroll.** If you connect a fingerprint device, map each
device name to an employee under **Fingerprint Device**. A name left ignored
quietly discards that person's punches, and nothing will tell you.

---

## 8. Where to look when something is wrong

| Symptom | Usually |
|---|---|
| Portal loads, every request fails | `ALLOWED_ORIGINS` does not list the portal's origin, or the built CSP points at the wrong API |
| Browser tab reads `%VITE_BRAND_NAME%` | Built without `.env` — the build gate should have stopped this |
| No email arrives, no error | From address not on a domain verified with your mail provider |
| Everyone's hours look shifted | `COMPANY_UTC_OFFSET_HOURS` |
| `relation "users" does not exist` | `alembic upgrade head` has not run |
| Intermittent 503s under load | Pool exceeds what the database allows — see `app/database.py` |
| API exits at startup, logs "REFUSING TO START" | One of the settings in section 2 is unsafe for production; the message lists each one and its fix |
| Everyone signed out after a password change | Expected — changing a password revokes every remembered session. The device that changed it is handed a replacement |
| A user signed out and says they did nothing | A refresh token was presented twice, so that session was revoked. Look for `auth.refresh.reuse_detected` in the audit log |

The API logs to stdout; `docker compose logs -f api`.
