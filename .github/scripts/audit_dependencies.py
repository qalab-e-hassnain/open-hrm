#!/usr/bin/env python3
"""
Fail the build on a dependency with a known vulnerability.

Runs pip-audit over the API's requirements and npm audit over the portal's
lockfile, and exits non-zero on anything that is not accepted in
.github/dependency-audit-allowlist.txt.

    python .github/scripts/audit_dependencies.py
    python .github/scripts/audit_dependencies.py --backend-only

Why this exists rather than calling the two tools directly: both of them exit
non-zero on any finding, including ones with no released fix, so a CI step
that calls them straight ends up suffixed with `|| true` and stops meaning
anything. This keeps the failure — the only thing that makes an audit
load-bearing — while giving a documented, dated way to accept a finding that
genuinely cannot be fixed today.

It also fails on an allowlist entry whose review date has passed, so an
accepted finding cannot be accepted for ever by accident.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
ALLOWLIST = ROOT / ".github" / "dependency-audit-allowlist.txt"
GHSA_IN_URL = re.compile(r"(GHSA-[0-9a-z]{4}-[0-9a-z]{4}-[0-9a-z]{4})", re.I)


@dataclass(frozen=True)
class Accepted:
    advisory: str
    review_by: dt.date
    reason: str


@dataclass
class Finding:
    advisory: str
    package: str
    installed: str
    severity: str
    fixed_in: str
    aliases: frozenset[str] = field(default_factory=frozenset)

    @property
    def ids(self) -> frozenset[str]:
        return frozenset({self.advisory.upper()} | {a.upper() for a in self.aliases})

    def __str__(self) -> str:
        fix = f"fixed in {self.fixed_in}" if self.fixed_in else "NO FIX RELEASED"
        sev = f"{self.severity:9}" if self.severity else " " * 9
        return (f"{self.advisory:24} {sev} {self.package} "
                f"{self.installed} ({fix})")


def read_allowlist() -> list[Accepted]:
    if not ALLOWLIST.exists():
        return []
    accepted: list[Accepted] = []
    for number, raw in enumerate(ALLOWLIST.read_text().splitlines(), 1):
        line = raw.split("#", 1)[0].strip() if not raw.lstrip().startswith("#") else ""
        if not line:
            continue
        parts = line.split(None, 2)
        if len(parts) < 3:
            sys.exit(f"{ALLOWLIST.name}:{number}: expected "
                     f"'<advisory-id> <YYYY-MM-DD> <reason>', got {raw.strip()!r}")
        advisory, date_text, reason = parts
        try:
            review_by = dt.date.fromisoformat(date_text)
        except ValueError:
            sys.exit(f"{ALLOWLIST.name}:{number}: {date_text!r} is not a date "
                     f"in YYYY-MM-DD form")
        accepted.append(Accepted(advisory.upper(), review_by, reason.strip()))
    return accepted


def run(command: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(command, cwd=cwd, capture_output=True, text=True)


def audit_python() -> list[Finding]:
    requirements = ROOT / "backend" / "requirements.txt"
    print(f"pip-audit {requirements.relative_to(ROOT)}")
    result = run(["pip-audit", "-r", str(requirements), "--format", "json",
                  "--progress-spinner", "off"], ROOT)
    # pip-audit exits 1 when it finds something, which is not an error here —
    # but it also exits non-zero when it cannot resolve a dependency, and
    # that difference only shows up in whether the output parses.
    try:
        report = json.loads(result.stdout or "{}")
    except json.JSONDecodeError:
        print(result.stdout, file=sys.stderr)
        print(result.stderr, file=sys.stderr)
        sys.exit("pip-audit produced no JSON report — it failed to run, which "
                 "is not the same as finding nothing. See the output above.")

    findings = []
    for dependency in report.get("dependencies", []):
        for vuln in dependency.get("vulns", []):
            fixes = vuln.get("fix_versions") or []
            findings.append(Finding(
                advisory=vuln.get("id", "?"),
                package=dependency.get("name", "?"),
                installed=dependency.get("version", "?"),
                severity="",
                fixed_in=", ".join(fixes),
                aliases=frozenset(vuln.get("aliases") or ()),
            ))
    return findings


def audit_npm(minimum: str = "high") -> list[Finding]:
    frontend = ROOT / "frontend"
    if not (frontend / "package-lock.json").exists():
        print("no frontend/package-lock.json — skipping npm audit")
        return []
    print(f"npm audit (frontend, {minimum} and above)")
    result = run(["npm", "audit", "--json"], frontend)
    try:
        report = json.loads(result.stdout or "{}")
    except json.JSONDecodeError:
        print(result.stdout, file=sys.stderr)
        print(result.stderr, file=sys.stderr)
        sys.exit("npm audit produced no JSON report — it failed to run.")

    ranked = ["info", "low", "moderate", "high", "critical"]
    floor = ranked.index(minimum)
    findings: dict[tuple[str, str], Finding] = {}
    for name, entry in (report.get("vulnerabilities") or {}).items():
        # `via` holds either advisory objects or the names of other packages
        # that depend on this one. A string-only entry is a package made
        # vulnerable by something else, and that something else has its own
        # entry here — so reporting it again names the same advisory twice
        # under a package that has nothing to fix.
        advisories = [v for v in entry.get("via", []) if isinstance(v, dict)]
        if not advisories:
            continue

        fix = entry.get("fixAvailable")
        fixed_in = ""
        if isinstance(fix, dict):
            fixed_in = f"{fix.get('name', name)} {fix.get('version', '')}".strip()
        elif fix is True:
            fixed_in = "available"

        for advisory in advisories:
            # The advisory's OWN severity, not the package's. npm rolls a
            # package up to its worst advisory, so taking the rolled-up value
            # for each one promotes every moderate finding under a high
            # package to high — which reported 93 findings where npm counts
            # 15, and buried the ones that matter.
            severity = advisory.get("severity", "info")
            if severity not in ranked or ranked.index(severity) < floor:
                continue
            url = advisory.get("url", "")
            ids = {str(advisory.get("source", "?"))}
            ids |= {m.group(1).upper() for m in GHSA_IN_URL.finditer(url)}
            primary = next((i for i in sorted(ids) if i.startswith("GHSA")),
                           str(advisory.get("source", "?")))
            package = advisory.get("name", name)
            # Deduplicated: npm lists an advisory once per dependency path,
            # and the same finding repeated is the same finding.
            findings[(primary, package)] = Finding(
                advisory=primary,
                package=package,
                installed=advisory.get("range", entry.get("range", "?")),
                severity=severity,
                fixed_in=fixed_in,
                aliases=frozenset(ids),
            )
    return sorted(findings.values(),
                  key=lambda f: (-ranked.index(f.severity), f.package, f.advisory))


@dataclass
class Verdict:
    """What the audit decided, separated from how it reports it."""
    tolerated: list[tuple[Finding, Accepted]] = field(default_factory=list)
    unexpected: list[Finding] = field(default_factory=list)
    expired: list[Accepted] = field(default_factory=list)
    unused: list[Accepted] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.unexpected and not self.expired


def classify(findings: list[Finding], accepted: list[Accepted],
             today: dt.date) -> Verdict:
    """
    Decide, for each finding, whether somebody has accepted it in writing.

    Kept free of subprocesses and printing so it can be tested directly. The
    expiry rule is the load-bearing part: without it an accepted finding
    stays accepted for ever, and the allowlist becomes the `|| true` this
    script was written to remove.
    """
    verdict = Verdict()
    by_id = {a.advisory: a for a in accepted}
    matched: set[str] = set()

    for finding in findings:
        entry = next((by_id[i] for i in sorted(finding.ids) if i in by_id), None)
        if entry is None:
            verdict.unexpected.append(finding)
        else:
            verdict.tolerated.append((finding, entry))
            matched.add(entry.advisory)

    verdict.expired = [a for a in accepted if a.review_by < today]
    # An entry that matches nothing is either a finding that has been fixed
    # since, or a typo in the id — which would mean a real vulnerability is
    # silently not being accepted by the entry meant to accept it.
    verdict.unused = [a for a in accepted
                      if a.advisory not in matched and a.review_by >= today]
    return verdict


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend-only", action="store_true")
    parser.add_argument("--frontend-only", action="store_true")
    parser.add_argument("--npm-level", default="high",
                        choices=["low", "moderate", "high", "critical"])
    args = parser.parse_args()

    accepted = read_allowlist()
    today = dt.date.today()

    findings: list[Finding] = []
    if not args.frontend_only:
        findings += audit_python()
    if not args.backend_only:
        findings += audit_npm(args.npm_level)

    verdict = classify(findings, accepted, today)

    print()
    if verdict.tolerated:
        print(f"Accepted, per {ALLOWLIST.name}:")
        for finding, entry in verdict.tolerated:
            print(f"  {finding}")
            print(f"    review by {entry.review_by}: {entry.reason}")
        print()

    if verdict.unused:
        print(f"::warning::{len(verdict.unused)} allowlist entr(y/ies) match "
              f"nothing any more and can be deleted: "
              f"{', '.join(a.advisory for a in verdict.unused)}")
        print()

    # An allowlist entry that has outlived its review date is the failure mode
    # this whole mechanism exists to prevent, so it is a failure, not a note.
    if verdict.expired:
        print(f"{len(verdict.expired)} accepted finding(s) are past their "
              f"review date:")
        for entry in verdict.expired:
            print(f"  {entry.advisory}  review was due {entry.review_by} "
                  f"({(today - entry.review_by).days} days ago)")
            print(f"    reason given: {entry.reason}")
        print()
        print("Look again: take the fix if one exists now, or move the date")
        print("and say why it is still acceptable.")
        print()

    if verdict.unexpected:
        print(f"{len(verdict.unexpected)} vulnerability(ies) with no decision "
              f"recorded:")
        for finding in verdict.unexpected:
            print(f"  {finding}")
        print()
        print("Upgrade the package, or — if there is no fix and the vulnerable")
        print("code path is not reachable from here — add it to")
        print(f"{ALLOWLIST.relative_to(ROOT)} with a reason and a review date.")

    if not verdict.ok:
        return 1

    print(f"No unaccepted vulnerabilities. "
          f"({len(findings)} finding(s), {len(verdict.tolerated)} accepted.)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
