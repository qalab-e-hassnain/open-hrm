# Security

## Reporting a vulnerability

Email **qalab@quickgentech.com**. Please do not open a public issue for
anything exploitable. Tell me what you found, how to reproduce it, and what
you think it lets someone do. I will confirm I have read it.

This is maintained alongside other work, so I cannot promise a response time.
If something is serious and you do not hear back, say so publicly — a known
hole is less dangerous than a silent one.

## What has been reviewed

Honestly: not enough for you to take on trust.

The system has **not had an independent security review**. What exists is the
care taken while writing it and a test suite that covers permission
boundaries in places. That is not the same thing, and anyone putting real
employment records into it should arrange a review of their own.

## Known weaknesses

Listed because you will find them anyway, and finding them here is better
than finding them later.

**Tokens live in browser storage.** An XSS bug anywhere in the portal would
expose an access and refresh token. The portal ships a strict
Content-Security-Policy, which narrows how such a bug could be exploited, but
the real fix — httpOnly, Secure cookies with CSRF protection — is not done.

**Public verification endpoints are unauthenticated by design.** Anyone
holding a verification code can confirm a document is genuine without an
account, which is the point: a bank checking an employment letter has no
login. Codes are 12 bytes of `secrets.token_urlsafe`, so guessing is not
practical, but they are **not rate-limited and not revocable**. If a code
leaks, the document it describes stays verifiable.

**Uploads are served through the API** so access follows the same permissions
as everything else, and a signed URL is needed for an `<img>` tag. Signatures
expire; the response is cached hard afterwards, which is safe because a
stored object never changes, but it means a browser keeps a copy after access
would otherwise have lapsed.

**Time zones are a fixed UTC offset**, not a named zone. Correct where the
offset never changes; wrong where daylight saving applies.

**Background jobs ride on portal traffic** unless you configure a scheduler.
A job that must run overnight will not, if nobody opens the portal.

## What the system does do

- Passwords hashed with bcrypt; a minimum length enforced on every path,
  including admin-created accounts.
- JWT access and refresh tokens, signed with a per-deployment secret.
- Role-based access with four roles, plus object-level checks — a manager
  reads their own reports, not everybody.
- Pay, banking and identity fields are stripped from list responses by
  default and returned only to callers who ask and may see them.
- Nobody approves their own leave, claim, advance or time log. For time logs
  the rule is enforced on the model, so a script cannot bypass it either.
- An issued payslip cannot be edited or deleted; corrections supersede it and
  leave the original on record.
- Documents are hashed at issue and the hash recomputed on every read.
- An audit log records who did what.
- A strict Content-Security-Policy, generated per deployment.
- Insecure configuration — open CORS, debug on, a weak secret, a plain-http
  portal — is reported in the log the moment the process starts.

## If you are deploying this

Read `backend/DEPLOYMENT.md`. The settings that matter most:

- `ALLOWED_ORIGINS` — never `*` in production.
- `SECRET_KEY` — generate a fresh one; never reuse another deployment's.
- `DEBUG=False`.
- Serve both the API and the portal over HTTPS.

The API logs all four of these at startup if they are wrong.
