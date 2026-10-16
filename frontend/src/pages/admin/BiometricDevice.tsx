import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  FiAlertTriangle, FiCheck, FiRefreshCw, FiSlash, FiUser, FiUserX,
} from 'react-icons/fi';
import {
  biometricService,
  type BiometricIdentity,
  type BiometricScan,
} from '../../services/biometricService';
import { employeeService } from '../../services/employeeService';
import './BiometricDevice.css';

/**
 * The office fingerprint device.
 *
 * Scans reach attendance on their own through the `biometric_sync` sweep, so
 * nothing here is needed for the clock to work. This screen answers the two
 * questions the sweep cannot: which device names nobody has claimed — every
 * one of those is somebody's attendance going nowhere — and what the device
 * actually recorded on a day somebody disputes.
 *
 * Admin/HR only, matching `require_admin_or_hr` on every route behind it.
 */

const today = () => new Date().toISOString().slice(0, 10);

const detailOf = (err: unknown, fallback: string): string => {
  const detail = (err as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail;
  return typeof detail === 'string' && detail ? detail : fallback;
};

interface EmployeeOption { id: number; name: string }

const BiometricDevice = () => {
  const [tab, setTab] = useState<'identities' | 'scans'>('identities');
  const [identities, setIdentities] = useState<BiometricIdentity[]>([]);
  const [scans, setScans] = useState<BiometricScan[]>([]);
  const [people, setPeople] = useState<EmployeeOption[]>([]);
  // Two values, deliberately. `dateInput` is what the box shows; `scanDate`
  // is what gets loaded.
  //
  // The box is seeded with today, so every segment is already filled — which
  // means typing a year produces complete, valid, absurd dates on the way to
  // the real one: 0002-10-14, 0020-10-14, 0202-10-14. Each one fired a fetch,
  // and re-rendering the controlled input mid-keystroke threw away the
  // segment being typed, so the year kept resetting and the date could not be
  // typed at all. The calendar picker worked because it commits once.
  const [dateInput, setDateInput] = useState(today());
  const [scanDate, setScanDate] = useState(today());

  useEffect(() => {
    if (!dateInput) return undefined;
    const typed = new Date(dateInput);
    // A year below 2000 or a date in the future is somebody mid-keystroke,
    // not a day they want to look at.
    if (Number.isNaN(typed.getTime()) || dateInput < '2000-01-01' || dateInput > today()) {
      return undefined;
    }
    const settle = setTimeout(() => setScanDate(dateInput), 350);
    return () => clearTimeout(settle);
  }, [dateInput]);
  // Three states, not a tick box. "Needs a decision" counted an ignored name
  // as decided, so this screen reported that every name was accounted for
  // while thirty-five ignored names were quietly discarding their punches —
  // eleven of them belonging to current employees. Ignoring a name is a
  // decision somebody has to be able to find again.
  const [nameState, setNameState] = useState<'undecided' | 'ignored' | 'mapped' | 'all'>('undecided');

  // Punches that reach nobody. Counted from whatever is on screen, so the
  // warning follows the filter rather than claiming a number it cannot see.
  const discarded = identities.reduce((n, i) => n + (i.unattached_scans || 0), 0);
  const discardedNames = identities.filter((i) => (i.unattached_scans || 0) > 0).length;
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const loadIdentities = useCallback(async () => {
    const rows = await biometricService.listIdentities(nameState);
    setIdentities(rows);
  }, [nameState]);

  const loadScans = useCallback(async () => {
    const rows = await biometricService.listScans({ on: scanDate });
    setScans(rows);
  }, [scanDate]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      setLoading(true); setError(null);
      try {
        // Everyone, including admins — an admin has a fingerprint too, so
        // getEmployeesForAssignment (which excludes them) would leave their
        // own device name unmappable.
        const [, , staff] = await Promise.all([
          loadIdentities(), loadScans(), employeeService.getEmployees(),
        ]);
        if (cancelled) return;
        const list = Array.isArray(staff) ? staff : [];
        setPeople(
          list
            .map((u) => ({ id: Number(u.id), name: u.name || `#${u.id}` }))
            .filter((u) => Number.isFinite(u.id))
            .sort((a, b) => a.name.localeCompare(b.name)),
        );
      } catch (err) {
        if (!cancelled) setError(detailOf(err, 'Could not reach the device records.'));
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [loadIdentities, loadScans]);

  const unclaimed = useMemo(
    () => identities.filter((i) => !i.is_resolved).length, [identities],
  );

  const act = async (key: string, run: () => Promise<void>, done?: string) => {
    setBusy(key); setError(null); setNotice(null);
    try {
      await run();
      if (done) setNotice(done);
    } catch (err) {
      setError(detailOf(err, 'That did not work.'));
    } finally {
      setBusy(null);
    }
  };

  const assign = (identity: BiometricIdentity, employeeId: string) =>
    act(`assign-${identity.id}`, async () => {
      await biometricService.updateIdentity(identity.id, {
        employee_id: employeeId ? Number(employeeId) : null,
        ignored: false,
      });
      await loadIdentities();
    }, employeeId ? 'Mapped. Their scans will attach from the next sync.' : 'Mapping cleared.');

  const ignore = (identity: BiometricIdentity) =>
    act(`ignore-${identity.id}`, async () => {
      await biometricService.updateIdentity(identity.id, { ignored: !identity.ignored });
      await loadIdentities();
    });

  return (
    <div className="biometric-page">
      <header className="biometric-head">
        <div>
          <h1>Fingerprint device</h1>
          <p>
            Scans sync automatically. This is where an unclaimed name gets
            attached to a person — until it is, their attendance goes nowhere.
          </p>
        </div>
        <div className="biometric-actions">
          <button
            type="button"
            className="btn-secondary"
            disabled={busy === 'sync'}
            onClick={() => act('sync', async () => {
              await biometricService.sync();
              await Promise.all([loadIdentities(), loadScans()]);
            }, 'Pulled the latest log from the device.')}
          >
            <FiRefreshCw className={busy === 'sync' ? 'spin' : ''} /> Sync now
          </button>
        </div>
      </header>

      {unclaimed > 0 && (
        <div className="biometric-callout">
          <FiAlertTriangle />
          <span>
            <strong>{unclaimed}</strong> device {unclaimed === 1 ? 'name is' : 'names are'}
            {' '}not attached to anybody. Their scans are being recorded and discarded.
          </span>
        </div>
      )}
      {error && <div className="biometric-alert error" role="alert">{error}</div>}
      {notice && <div className="biometric-alert ok">{notice}</div>}

      <nav className="biometric-tabs">
        <button type="button" className={tab === 'identities' ? 'on' : ''}
                onClick={() => setTab('identities')}>
          Device names
        </button>
        <button type="button" className={tab === 'scans' ? 'on' : ''}
                onClick={() => setTab('scans')}>
          Scans
        </button>
      </nav>

      {loading ? <p className="biometric-empty">Loading…</p> : tab === 'identities' ? (
        <section>
          <div className="biometric-filter" role="group" aria-label="Which device names to show">
            {([
              ['undecided', 'Needs a decision'],
              ['ignored', 'Ignored'],
              ['mapped', 'Mapped to someone'],
              ['all', 'All'],
            ] as const).map(([value, label]) => (
              <button key={value} type="button"
                className={nameState === value ? 'is-active' : ''}
                aria-pressed={nameState === value}
                onClick={() => setNameState(value)}>
                {label}
              </button>
            ))}
          </div>

          {discarded > 0 && (
            <p className="biometric-callout" role="status">
              <FiAlertTriangle aria-hidden="true" />
              <span>
                {discarded.toLocaleString()} scan{discarded === 1 ? '' : 's'} from
                {' '}{discardedNames} ignored name{discardedNames === 1 ? '' : 's'} are
                not reaching anyone&rsquo;s attendance. If one of these belongs to an
                employee, map it here and their punches are attached.
              </span>
            </p>
          )}

          {identities.length === 0 ? (
            <p className="biometric-empty">
              {nameState === 'undecided'
                ? 'Every device name has been decided — mapped to someone or ignored. Check the Ignored tab for names that should be mapped.'
                : nameState === 'ignored'
                  ? 'No names are being ignored.'
                  : nameState === 'mapped'
                    ? 'No device names are mapped to anyone yet.'
                    : 'No device names yet.'}
            </p>
          ) : (
            <div className="biometric-scroll">
              <table className="biometric-table">
                <thead>
                  <tr>
                    <th>Device name</th><th>Scans</th><th>First seen</th><th>Last seen</th>
                    <th>Belongs to</th><th aria-label="Actions" />
                  </tr>
                </thead>
                <tbody>
                  {identities.map((i) => (
                    <tr key={i.id} className={i.ignored ? 'muted' : ''}>
                      <td className="mono">{i.device_name}</td>
                      <td className="num">
                        {i.scan_count}
                        {i.unattached_scans > 0 && (
                          <small className="biometric-orphans"
                            title={`${i.unattached_scans} of these are attached to nobody`}>
                            {i.unattached_scans} loose
                          </small>
                        )}
                      </td>
                      <td className="num dim">{i.first_seen || '—'}</td>
                      <td className="num dim">{i.last_seen || '—'}</td>
                      <td>
                        <select
                          value={i.employee_id ?? ''}
                          disabled={i.ignored || busy === `assign-${i.id}`}
                          onChange={(e) => assign(i, e.target.value)}
                        >
                          <option value="">— nobody —</option>
                          {people.map((p) => (
                            <option key={p.id} value={p.id}>{p.name}</option>
                          ))}
                        </select>
                      </td>
                      <td className="right">
                        {i.is_resolved && !i.ignored && <FiCheck className="ok-tick" title="Attached" />}
                        <button
                          type="button"
                          className="btn-link"
                          disabled={busy === `ignore-${i.id}`}
                          onClick={() => ignore(i)}
                          title={i.ignored ? 'Stop ignoring this name' : 'Not a member of staff'}
                        >
                          {i.ignored ? <><FiUser /> Unignore</> : <><FiSlash /> Ignore</>}
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
      ) : (
        <section>
          <label className="biometric-filter">
            Day
            <input type="date" value={dateInput} max={today()} min="2000-01-01"
                   onChange={(e) => setDateInput(e.target.value)} />
          </label>

          {scans.length === 0 ? (
            <p className="biometric-empty">Nothing recorded on {scanDate}.</p>
          ) : (
            <div className="biometric-scroll">
              <table className="biometric-table">
                <thead>
                  <tr>
                    <th>Device name</th><th>Employee</th><th>First</th><th>Last</th><th />
                  </tr>
                </thead>
                <tbody>
                  {scans.map((s, n) => (
                    <tr key={`${s.device_name}-${s.scan_date}-${n}`}>
                      <td className="mono">{s.device_name}</td>
                      <td>
                        {s.employee_name || (
                          <span className="unclaimed"><FiUserX /> not attached</span>
                        )}
                      </td>
                      <td className="num">{s.first_scan_local}</td>
                      <td className="num">{s.single_scan ? '—' : s.last_scan_local}</td>
                      <td className="dim">
                        {s.single_scan ? 'touched the device once; departure unknown' : ''}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
      )}
    </div>
  );
};

export default BiometricDevice;
