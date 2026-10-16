import { useCallback, useEffect, useState, type FormEvent } from 'react';
import {
  FiAlertTriangle, FiCheckCircle, FiClock, FiPlus, FiStar, FiUsers, FiX,
} from 'react-icons/fi';
import {
  shiftService, DAY_INITIALS, clock,
  type Shift, type ShiftAssignment,
} from '../../services/shiftService';
import { employeeService } from '../../services/employeeService';
import type { Employee } from '../../types';
import './Shifts.css';

const detailOf = (err: unknown, fallback: string): string => {
  const detail = (err as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail;
  if (typeof detail === 'string' && detail) return detail;
  return (err as { message?: string })?.message || fallback;
};

const shortDate = (v?: string | null) => {
  if (!v) return '—';
  const d = new Date(v);
  return Number.isNaN(d.getTime()) ? v : d.toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' });
};

/** Monday first, matching the backend's seven-character string. */
const DayDots = ({ pattern }: { pattern: string }) => (
  <span className="sh-days" aria-label={pattern}>
    {DAY_INITIALS.map((letter, i) => (
      <i key={i} className={pattern[i] === '1' ? 'is-on' : ''}>{letter}</i>
    ))}
  </span>
);

const Shifts = () => {
  const [shifts, setShifts] = useState<Shift[]>([]);
  const [assignments, setAssignments] = useState<ShiftAssignment[]>([]);
  const [employees, setEmployees] = useState<Employee[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [creating, setCreating] = useState(false);
  const [includeInactive, setIncludeInactive] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      // Exactly one shift can be default and setting a second clears the
      // first, so both lists are re-read together after any save.
      const [list, assigned] = await Promise.all([
        shiftService.list(includeInactive),
        shiftService.assignments(true),
      ]);
      setShifts(list);
      setAssignments(assigned);
    } catch (err) {
      setError(detailOf(err, 'Could not load shifts.'));
    } finally {
      setLoading(false);
    }
  }, [includeInactive]);

  useEffect(() => { void load(); }, [load]);
  useEffect(() => {
    employeeService.getEmployees().then(setEmployees).catch(() => setEmployees([]));
  }, []);

  const done = (message: string) => { setNotice(message); setCreating(false); void load(); };

  const assign = async (employeeId: number, shiftId: number) => {
    setError('');
    try {
      await shiftService.assign({ employee_id: employeeId, shift_id: shiftId });
      done('Shift assigned. Any running assignment was closed the day before.');
    } catch (err) {
      setError(detailOf(err, 'That assignment did not save.'));
    }
  };

  const currentFor = (employeeId: number) =>
    assignments.find((a) => a.employee_id === employeeId && !a.effective_to);

  return (
    <div className="shifts-page">
      <header className="sh-head">
        <div>
          <h1>Shifts</h1>
          <p>
            Which hours, not just how many. An assigned shift drives the automatic
            check-out instead of the flat standard hours.
          </p>
        </div>
        <div className="sh-controls">
          <label className="sh-check">
            <input type="checkbox" checked={includeInactive}
              onChange={(e) => setIncludeInactive(e.target.checked)} />
            Show retired
          </label>
          <button type="button" className="sh-btn sh-btn-primary" onClick={() => setCreating(true)}>
            <FiPlus /> New shift
          </button>
        </div>
      </header>

      {error && <div className="sh-alert sh-alert-error"><FiAlertTriangle /> {error}</div>}
      {notice && <div className="sh-alert sh-alert-ok"><FiCheckCircle /> {notice}</div>}

      {loading ? <div className="sh-empty">Loading…</div> : (
        <>
          <section>
            <h2>Patterns</h2>
            {shifts.length === 0 ? (
              <div className="sh-empty">No shifts defined. Everyone falls back to their standard hours.</div>
            ) : (
              <div className="sh-grid">
                {shifts.map((shift) => (
                  <article key={shift.id} className={`sh-card${shift.is_active ? '' : ' is-retired'}`}>
                    <header>
                      <h3>{shift.name}{shift.is_default && <FiStar title="Default for new employees" />}</h3>
                      {!shift.is_active && <span className="sh-retired">Retired</span>}
                    </header>
                    <p className="sh-times">
                      <FiClock /> {clock(shift.start_time)} – {clock(shift.end_time)}
                      <span>{shift.hours}h{shift.break_minutes ? ` · ${shift.break_minutes} min break` : ''}</span>
                    </p>
                    <DayDots pattern={shift.days_of_week} />
                    <p className="sh-meta">
                      <FiUsers /> {shift.assigned_count} assigned
                      {shift.grace_minutes ? ` · ${shift.grace_minutes} min grace` : ''}
                    </p>
                    {shift.is_active && (
                      <button type="button" className="sh-btn sh-btn-ghost"
                        onClick={async () => {
                          try {
                            await shiftService.deactivate(shift.id);
                            done(`${shift.name} retired. Past attendance keeps being judged against it.`);
                          } catch (err) { setError(detailOf(err, 'That shift could not be retired.')); }
                        }}>
                        Retire
                      </button>
                    )}
                  </article>
                ))}
              </div>
            )}
          </section>

          {shifts.some((s) => s.is_active) && employees.length > 0 && (
            <section className="sh-assign">
              <h2>Who is on what</h2>
              <p className="sh-sub">
                Reassigning closes the running assignment the day before, so nobody
                is ever on two patterns at once and last March's attendance is still
                judged against last March's shift.
              </p>
              <div className="sh-table-wrap">
                <table className="sh-table">
                  <thead><tr><th>Employee</th><th>Current shift</th><th>Since</th><th>Change to</th></tr></thead>
                  <tbody>
                    {employees.map((employee) => {
                      const current = currentFor(Number(employee.id));
                      return (
                        <tr key={employee.id}>
                          <td><strong>{employee.name}</strong><small>{employee.designation || '—'}</small></td>
                          <td>{current?.shift_name || <span className="sh-muted">Standard hours</span>}</td>
                          <td>{current ? shortDate(current.effective_from) : '—'}</td>
                          <td>
                            <select value="" onChange={(e) => {
                              if (e.target.value) void assign(Number(employee.id), Number(e.target.value));
                            }}>
                              <option value="">Choose a shift…</option>
                              {shifts.filter((s) => s.is_active && s.id !== current?.shift_id).map((s) => (
                                <option key={s.id} value={s.id}>{s.name}</option>
                              ))}
                            </select>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </section>
          )}
        </>
      )}

      {creating && <ShiftDialog onClose={() => setCreating(false)} onDone={done} onError={setError} />}
    </div>
  );
};

const ShiftDialog = ({ onClose, onDone, onError }: {
  onClose: () => void; onDone: (m: string) => void; onError: (m: string) => void;
}) => {
  const [name, setName] = useState('');
  const [start, setStart] = useState('09:00');
  const [end, setEnd] = useState('18:00');
  const [breakMinutes, setBreakMinutes] = useState('60');
  const [grace, setGrace] = useState('15');
  const [days, setDays] = useState('1111100');
  const [isDefault, setIsDefault] = useState(false);
  const [saving, setSaving] = useState(false);

  const toggleDay = (index: number) => {
    const next = days.split('');
    next[index] = next[index] === '1' ? '0' : '1';
    setDays(next.join(''));
  };

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setSaving(true);
    try {
      await shiftService.create({
        name: name.trim(),
        start_time: `${start}:00`,
        end_time: `${end}:00`,
        break_minutes: Number(breakMinutes) || 0,
        grace_minutes: Number(grace) || 0,
        days_of_week: days,
        is_default: isDefault,
      });
      onDone(`${name.trim()} created.`);
    } catch (err) {
      onError(detailOf(err, 'That shift could not be created.'));
      onClose();
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="sh-overlay" role="dialog" aria-modal="true" aria-label="New shift">
      <form className="sh-dialog" onSubmit={submit}>
        <header><h2>New shift</h2><button type="button" onClick={onClose} aria-label="Close"><FiX /></button></header>
        <label className="sh-field">Name
          <input required value={name} placeholder="Half day — mornings"
            onChange={(e) => setName(e.target.value)} />
        </label>
        <div className="sh-grid-fields">
          <label>Starts<input type="time" required value={start} onChange={(e) => setStart(e.target.value)} /></label>
          <label>Ends<input type="time" required value={end} onChange={(e) => setEnd(e.target.value)} /></label>
          <label>Break (min)<input type="number" min="0" max="480" value={breakMinutes}
            onChange={(e) => setBreakMinutes(e.target.value)} /></label>
          <label>Grace (min)<input type="number" min="0" max="240" value={grace}
            onChange={(e) => setGrace(e.target.value)} />
            <small>How late an arrival still counts as on time.</small></label>
        </div>
        <div className="sh-field">
          <span>Working days</span>
          <div className="sh-daypicker">
            {DAY_INITIALS.map((letter, i) => (
              <button type="button" key={i} className={days[i] === '1' ? 'is-on' : ''}
                onClick={() => toggleDay(i)} aria-pressed={days[i] === '1'}>{letter}</button>
            ))}
          </div>
          <small>Monday first.</small>
        </div>
        <label className="sh-checkline">
          <input type="checkbox" checked={isDefault} onChange={(e) => setIsDefault(e.target.checked)} />
          <span>
            <strong>Make this the default</strong>
            <small>Only one shift can be. Setting this clears whichever holds it now.</small>
          </span>
        </label>
        <footer>
          <button type="button" onClick={onClose}>Cancel</button>
          <button type="submit" className="sh-btn-primary" disabled={saving || !days.includes('1')}>
            {saving ? 'Saving…' : 'Create shift'}
          </button>
        </footer>
      </form>
    </div>
  );
};

export default Shifts;
