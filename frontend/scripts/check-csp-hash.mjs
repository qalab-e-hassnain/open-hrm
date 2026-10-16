/**
 * Fail the build if the CSP no longer matches the inline script.
 *
 * index.html carries one inline script: the theme check that runs before
 * first paint so dark mode does not flash white. The Content-Security-Policy
 * in public/staticwebapp.config.json allows it by its sha256.
 *
 * Change one without the other and nothing complains at build time — the
 * browser simply refuses to run the script, and the only symptom is a white
 * flash on every page load for people using dark mode. That is exactly the
 * kind of fault nobody files a bug for, so it is caught here instead.
 */
import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';

const html = readFileSync(new URL('../index.html', import.meta.url), 'utf8');
const config = JSON.parse(
  readFileSync(new URL('../public/staticwebapp.config.json', import.meta.url), 'utf8'),
);

const scripts = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)].map((m) => m[1]);
if (scripts.length !== 1) {
  console.error(
    `CSP check: expected exactly one inline script in index.html, found ${scripts.length}.\n` +
    'Every inline script needs its own hash in the policy, or none at all.',
  );
  process.exit(1);
}

const expected = `sha256-${createHash('sha256').update(scripts[0]).digest('base64')}`;
const policy = config.globalHeaders?.['Content-Security-Policy'] ?? '';

if (!policy.includes(`'${expected}'`)) {
  console.error(
    'CSP check: the inline script in index.html does not match the policy.\n' +
    `  expected in script-src: '${expected}'\n` +
    '  fix: put that value in globalHeaders["Content-Security-Policy"]\n' +
    '       in public/staticwebapp.config.json',
  );
  process.exit(1);
}

console.log(`CSP check: inline script matches ${expected}`);
