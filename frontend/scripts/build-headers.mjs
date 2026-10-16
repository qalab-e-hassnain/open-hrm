/**
 * Point the Content-Security-Policy at this deployment's own API.
 *
 * The policy names the API origin in connect-src and img-src, because every
 * request and every avatar comes from there. That origin differs per
 * deployment, and a policy carrying somebody else's host blocks the portal
 * from talking to its own backend — a completely dead site whose only symptom
 * is a console message nobody is looking at.
 *
 * Runs after vite build and rewrites the copy in dist/, leaving the checked-in
 * template alone.
 */
import { readFileSync, writeFileSync, existsSync } from 'node:fs';

const TEMPLATE = 'public/staticwebapp.config.json';
const OUT = 'dist/staticwebapp.config.json';
const PLACEHOLDER = 'https://API_ORIGIN_PLACEHOLDER';

const fromFile = {};
for (const file of ['.env', '.env.production', '.env.local']) {
  if (!existsSync(file)) continue;
  for (const line of readFileSync(file, 'utf8').split('\n')) {
    const m = line.match(/^\s*([A-Z0-9_]+)\s*=\s*(.*)\s*$/);
    if (m) fromFile[m[1]] = m[2].replace(/^["']|["']$/g, '');
  }
}
const apiBase = process.env.VITE_API_BASE_URL ?? fromFile.VITE_API_BASE_URL ?? '';
if (!apiBase) {
  console.error('build-headers: VITE_API_BASE_URL is not set; check:env should have caught this.');
  process.exit(1);
}

let origin;
try {
  origin = new URL(apiBase).origin;
} catch {
  console.error(`build-headers: VITE_API_BASE_URL is not a URL: ${apiBase}`);
  process.exit(1);
}

const config = JSON.parse(readFileSync(TEMPLATE, 'utf8'));
const policy = config.globalHeaders?.['Content-Security-Policy'];
if (!policy) {
  console.error(`build-headers: no Content-Security-Policy in ${TEMPLATE}`);
  process.exit(1);
}

config.globalHeaders['Content-Security-Policy'] = policy.split(PLACEHOLDER).join(origin);
writeFileSync(OUT, JSON.stringify(config, null, 2) + '\n');

const still = config.globalHeaders['Content-Security-Policy'].includes(PLACEHOLDER);
if (still) {
  console.error('build-headers: the placeholder origin survived the rewrite.');
  process.exit(1);
}
console.log(`build-headers: CSP points at ${origin}`);
