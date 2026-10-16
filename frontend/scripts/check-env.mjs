/**
 * Refuse to build without the settings a deployment cannot do without.
 *
 * This app is handed to other companies to run for their own staff, so the
 * company name, the product name and the API address all come from the build
 * environment. Miss one and the failure is quiet and embarrassing: the browser
 * tab reads "%VITE_BRAND_NAME%", or the portal is live and pointed at
 * somebody else's API.
 *
 * Checked here rather than at runtime because a Vite variable is baked in at
 * build time — by the time anyone opens the page it is far too late.
 */
import { existsSync, readFileSync } from 'node:fs';

const REQUIRED = [
  ['VITE_API_BASE_URL', 'Where the backend lives, e.g. https://api.example.com/api/v1'],
  ['VITE_BRAND_NAME', 'What the software is called, shown in the sidebar and the tab'],
  ['VITE_COMPANY_NAME', 'The registered company name, printed on documents'],
  ['VITE_COMPANY_SHORT_NAME', 'The company name in a sentence, e.g. "EMRChains"'],
];

// Vite loads .env itself; this runs before Vite, so read it the same way.
const fromFile = {};
for (const file of ['.env', '.env.production', '.env.local']) {
  if (!existsSync(file)) continue;
  for (const line of readFileSync(file, 'utf8').split('\n')) {
    const m = line.match(/^\s*([A-Z0-9_]+)\s*=\s*(.*)\s*$/);
    if (m) fromFile[m[1]] = m[2].replace(/^["']|["']$/g, '');
  }
}
const value = (name) => process.env[name] ?? fromFile[name] ?? '';

const missing = REQUIRED.filter(([name]) => !value(name).trim());
if (missing.length === 0) {
  console.log(`Env check: ${REQUIRED.length} required settings present ` +
              `(${value('VITE_BRAND_NAME')} -> ${value('VITE_API_BASE_URL')})`);
  process.exit(0);
}

console.error('Env check: this build is missing settings it cannot run without.\n');
for (const [name, why] of missing) console.error(`  ${name}\n      ${why}`);
console.error('\nCopy .env.example to .env and fill it in. See DEPLOYMENT.md.');
process.exit(1);
