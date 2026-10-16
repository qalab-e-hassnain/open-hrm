/**
 * Fail the build on class names a content blocker would hide.
 *
 * This is not hypothetical. The advances page used an `ad-` prefix for every
 * one of its classes — ad-head, ad-btn, ad-tabs, ad-dialog — and ad blockers
 * hide elements whose class starts with `ad-`. Anyone running one saw a page
 * with nothing on it but "You have not requested an advance", and could not
 * request an advance at all. Nothing was broken, nothing logged an error, and
 * it rendered perfectly in a browser without an extension, which is why it
 * survived review and two rounds of testing.
 *
 * The failure mode is invisible to us and total for the user, so it is worth
 * a build step.
 */
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join, extname } from 'node:path';

// Words that appear in the generic element-hiding rules shipped by EasyList,
// uBlock Origin and AdGuard.
const RISKY = new Set([
  'ad', 'ads', 'advert', 'adverts', 'advertisement', 'advertising',
  'banner', 'banners', 'sponsor', 'sponsored', 'sponsorship',
  'promo', 'promotion', 'popup', 'adsense', 'adbox', 'adslot', 'doubleclick',
]);

const walk = (dir) => readdirSync(dir).flatMap((entry) => {
  const full = join(dir, entry);
  return statSync(full).isDirectory() ? walk(full) : [full];
});

const found = new Map();
const note = (name, file) => {
  if (!found.has(name)) found.set(name, new Set());
  found.get(name).add(file);
};

for (const file of walk('src')) {
  const ext = extname(file);
  const text = readFileSync(file, 'utf8');

  if (ext === '.tsx' || ext === '.ts') {
    for (const m of text.matchAll(/className=(?:"([^"]*)"|\{`([^`]*)`\}|\{'([^']*)'\})/g)) {
      const chunk = m[1] ?? m[2] ?? m[3] ?? '';
      for (const token of chunk.match(/[A-Za-z][A-Za-z0-9_-]*/g) ?? []) {
        if (token.split(/[-_]/).some((word) => RISKY.has(word.toLowerCase()))) note(token, file);
      }
    }
  }
  if (ext === '.css') {
    for (const m of text.matchAll(/\.([A-Za-z][A-Za-z0-9_-]*)/g)) {
      if (m[1].split(/[-_]/).some((word) => RISKY.has(word.toLowerCase()))) note(m[1], file);
    }
  }
}

if (found.size === 0) {
  console.log('Blocked-class check: no class names a content blocker would hide');
  process.exit(0);
}

console.error('Blocked-class check: these class names would be hidden by ad blockers.\n');
for (const [name, files] of [...found].sort()) {
  console.error(`  ${name}`);
  for (const file of [...files].sort()) console.error(`      ${file}`);
}
console.error('\nRename them. "ad-" becomes "adv-", "banner" becomes "callout" or "notice".');
process.exit(1);
