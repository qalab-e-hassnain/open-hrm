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

What is done is limiting what a stolen token is worth. Refresh tokens are
recorded server-side and rotate on every use, so a copied one stops working
the moment the real client next refreshes. Presenting a superseded token
means two parties hold one session, and the whole chain is revoked: the
thief loses access, the user is signed out, and the audit log records
`auth.refresh.reuse_detected`. Signing out, changing a password and
deactivating an account all revoke sessions on the server, and no chain can
be refreshed past 30 days from its first sign-in.

The gap that remains is the **access token**, which is bearer-only for its
fifteen minutes and cannot be revoked within them. Checking a revocation
list on every request would mean a database round trip per call; the
mitigation is the short lifetime, and it is a mitigation rather than a fix.

<details>
<summary>What moving to httpOnly cookies would involve</summary>

Written down because "not done" is not a plan, and because the order of
these steps is the part that is easy to get wrong.

1. **Serve the portal and the API from one registered domain** — say
   `hr.example.com` and `hr-api.example.com`. A cookie cannot be sent
   cross-site without `SameSite=None`, which re-opens what the move is for.
   This is the step that decides whether the rest is worth doing: a
   deployment whose portal is on a static host under a different domain
   cannot take this path without a reverse proxy in front of both.
2. **Set the refresh token as a cookie** on login: `HttpOnly`, `Secure`,
   `SameSite=Lax`, `Path=/api/v1/auth`, `Max-Age` matching the token. Scoped
   to the auth path so it is not attached to the couple of hundred other
   endpoints that have no use for it.
3. **Add CSRF protection** to the three routes that would then act on a
   cookie — refresh, logout, login. Double-submit is enough here: a second,
   non-HttpOnly cookie echoed in a header, compared server-side. Without
   it, moving the token to a cookie trades XSS exposure for CSRF exposure.
4. **Keep the access token in memory only** — a JavaScript variable, not
   `localStorage`, rebuilt by calling refresh on page load. This is the step
   that actually removes the XSS prize; steps 2 and 3 only protect the
   refresh token.
5. **Accept both schemes during the transition**, then drop the body-based
   refresh token. Deploying 2–4 at once signs out everyone mid-session.
6. **Tighten the CSP** afterwards: with no token readable from JavaScript,
   the remaining exfiltration routes are worth narrowing again.

The server-side session table this release adds is a prerequisite for all of
it — a cookie you cannot revoke is worse than a `localStorage` token you
can — which is why it came first.

</details>

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
- JWT access tokens, signed with a per-deployment secret, valid 15 minutes.
- Refresh tokens tracked server-side, rotated on every use, with reuse
  detection that revokes the whole session chain, revocation on sign-out,
  password change and deactivation, and a 30-day ceiling per chain.
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
- **Insecure configuration stops the process.** Open CORS, debug on, a
  secret that is missing, short or copied from an example, a plain-http
  portal: in production the API exits at startup rather than serving. Each
  of those is invisible from outside — everything works while the system is
  wide open — so a log line was not enough.
- **Dependency audits that fail the build.** `pip-audit` and `npm audit` run
  in CI with no `|| true`. A vulnerability nobody has recorded a decision
  about breaks the build; one that cannot be fixed is accepted by name in
  `.github/dependency-audit-allowlist.txt` with a reason and a review date,
  and an entry past its review date breaks the build too.
- Concurrent payroll finalization is tested against a real PostgreSQL server
  with two connections: exactly one payslip is issued and the loser gets a
  409, not a 500 halfway through paying people.

## If you are deploying this

Read `backend/DEPLOYMENT.md`. The settings that matter most:

- `ALLOWED_ORIGINS` — never `*` in production.
- `SECRET_KEY` — generate a fresh one; never reuse another deployment's.
- `DEBUG=False`.
- Serve both the API and the portal over HTTPS.

The API logs all four of these at startup if they are wrong.
