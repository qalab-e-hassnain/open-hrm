# Building the portal for your own company

Written for the engineer setting it up. The API has the fuller guide — this
covers only the portal. Read `DEPLOYMENT.md` in the API repository first.

## Configure

```bash
cp .env.example .env
```

Four values are required. The build **refuses to start** without them,
because the alternative is a live portal whose browser tab reads
`%VITE_BRAND_NAME%`, or one quietly pointed at somebody else's API.

| | |
|---|---|
| `VITE_API_BASE_URL` | `https://hr-api.yourcompany.com/api/v1` — include `/api/v1` |
| `VITE_BRAND_NAME` | What the software is called: sidebar, login, browser tab |
| `VITE_COMPANY_NAME` | Your registered name, as printed on documents |
| `VITE_COMPANY_SHORT_NAME` | The same company in a sentence |

Everything else in `.env.example` is optional and appears on printed
documents. Left blank, the line is omitted rather than showing an empty
label.

Nothing here is secret — every value is baked into files the browser
downloads. Anything that must stay private belongs in the API's `.env`.

## Build

```bash
npm ci
npm run build
```

The build runs four gates before it will produce anything:

- **env** — the four required values above are present
- **CSP hash** — the inline theme script in `index.html` matches the hash in
  the Content-Security-Policy. Edit that script without updating the hash and
  the browser silently refuses to run it; the only symptom is a white flash
  on every load for anyone using dark mode
- **class names** — no CSS class a content blocker would hide. This is not
  hypothetical: a page whose classes began with `ad-` was invisible to
  everyone running an ad blocker, with no error anywhere
- **types** — `tsc`

Afterwards it writes `dist/staticwebapp.config.json` with a
Content-Security-Policy naming **your** API origin.

## Host it

Upload `dist/`. Any static host works.

Two things the host must do, or the app breaks in ways that look like bugs:

1. **Serve `index.html` for unknown paths.** It is a single-page app; without
   this, refreshing on `/dashboard` is a 404.
2. **Apply the headers** in `dist/staticwebapp.config.json`. Azure Static Web
   Apps reads that file directly. On Netlify, Cloudflare Pages, nginx or
   anything else, copy the `globalHeaders` into that host's own
   configuration — otherwise the Content-Security-Policy is simply absent.

Exclude `/handbook/*` and `/assets/*` from the single-page fallback. The
handbook is a real PDF and an iframe load counts as a navigation, so without
the exclusion the browser is handed the app shell instead of the file.

## Make it yours

- **`public/logo.webp`** — your logo, square. Sidebar and login screen.
- **`src/index.css`** — the colours. Change `--brand-red` and the three
  shades beside it; the rest is derived.
- **`public/handbook/`** — **delete what is there.** It is another company's
  employee handbook. Put yours in its place, or remove the Handbook entry
  from the sidebar in `src/components/common/Navigation.tsx`.

## Checking it worked

Open the portal and confirm:

- the browser tab shows your brand name, not `%VITE_BRAND_NAME%`
- the login screen shows your company name and logo
- signing in works — if every request fails, the API's `ALLOWED_ORIGINS`
  does not list this origin, or the CSP is pointing at the wrong API
- the browser console is clean. A blocked request names the directive that
  blocked it, which tells you which part of the policy is wrong
