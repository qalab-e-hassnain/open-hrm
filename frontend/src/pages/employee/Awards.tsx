import { useCallback, useEffect, useState, type FormEvent } from 'react';
import {
  FiAlertTriangle, FiAward, FiCheckCircle, FiEyeOff, FiPlus, FiStar, FiX,
} from 'react-icons/fi';
import {
  awardService, type Award, type AwardSignal, type AwardSignals,
} from '../../services/performanceService';
import { employeeService } from '../../services/employeeService';
import { useAuthStore } from '../../store/authStore';
import type { Employee } from '../../types';
import './Awards.css';

const MONTHS = ['January', 'February', 'March', 'April', 'May', 'June',
  'July', 'August', 'September', 'October', 'November', 'December'];

const monthName = (m: number, y: number) => `${MONTHS[m - 1] || m} ${y}`;

const detailOf = (err: unknown, fallback: string): string => {
  const detail = (err as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail;
  if (typeof detail === 'string' && detail) return detail;
  return (err as { message?: string })?.message || fallback;
};

const MIN_REASON = 15;

const Awards = () => {
  const { user } = useAuthStore();
  // Deciding is a true admin's call, narrower than the rest of the admin
  // area. HR runs the process and may nominate like anyone else, so an HR
  // manager deciding would be a nominator picking the winner — and the
  // server refuses it (require_admin). Offering the button anyway would
  // produce a 403 at the last step, after the citation had been written.
  const canDecide = user?.role === 'admin';
  const [current, setCurrent] = useState<Award | null>(null);
  const [past, setPast] = useState<Award[]>([]);
  const [employees, setEmployees] = useState<Employee[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [deciding, setDeciding] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const [now, all] = await Promise.all([
        awardService.current().catch(() => null),
        awardService.list().catch(() => []),
      ]);
      setCurrent(now);
      setPast(all.filter((a) => a.status !== 'open'));
    } catch (err) {
      setError(detailOf(err, 'Could not load the award.'));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void load(); }, [load]);
  useEffect(() => {
    employeeService.getDirectory().then(setEmployees).catch(() => setEmployees([]));
  }, []);

  const done = (message: string) => { setNotice(message); setDeciding(false); void load(); };

  const openThisMonth = async () => {
    const now = new Date();
    try {
      await awardService.open({ month: now.getMonth() + 1, year: now.getFullYear() });
      done(`Nominations open for ${monthName(now.getMonth() + 1, now.getFullYear())}.`);
    } catch (err) {
      setError(detailOf(err, 'That month could not be opened.'));
    }
  };

  return (
    <div className="aw-page">
      <header className="aw-head">
        <div>
          <h1>Employee of the month</h1>
          <p>Anyone can nominate a colleague with a reason. An admin decides.</p>
        </div>
        {canDecide && !current && (
          <button type="button" className="aw-btn aw-btn-primary" onClick={() => void openThisMonth()}>
            <FiPlus /> Open this month
          </button>
        )}
      </header>

      {error && <div className="aw-alert aw-alert-error"><FiAlertTriangle /> {error}</div>}
      {notice && <div className="aw-alert aw-alert-ok"><FiCheckCircle /> {notice}</div>}

      {loading ? <div className="aw-empty">Loading…</div> : (
        <>
          {current ? (
            <OpenAward award={current} employees={employees}
              meId={Number(user?.id)} onDone={done} onError={setError}
              canDecide={canDecide}
              onDecide={() => setDeciding(true)} />
          ) : (
            <div className="aw-empty">
              <FiAward />
              <p>No month is open for nominations.</p>
            </div>
          )}

          {past.length > 0 && (
            <section className="aw-past">
              <h2>Previous months</h2>
              <div className="aw-past-list">
                {past.map((award) => (
                  <article key={award.id} className={`aw-winner${award.winner_id ? '' : ' is-none'}`}>
                    <div className="aw-winner-head">
                      <span className="aw-month">{monthName(award.month, award.year)}</span>
                      {award.winner_name
                        ? <strong><FiStar /> {award.winner_name}</strong>
                        /* A month nobody won is recorded, so an award that
                           quietly stopped being given is distinguishable. */
                        : <strong className="aw-noaward">No award this month</strong>}
                    </div>
                    {award.citation && <p>{award.citation}</p>}
                  </article>
                ))}
              </div>
            </section>
          )}
        </>
      )}

      {deciding && current && (
        <DecideDialog award={current} onClose={() => setDeciding(false)}
          onDone={done} onError={setError} />
      )}
    </div>
  );
};

const OpenAward = ({ award, employees, canDecide, meId, onDone, onError, onDecide }: {
  award: Award; employees: Employee[]; meId: number;
  canDecide: boolean;
  onDone: (m: string) => void; onError: (m: string) => void; onDecide: () => void;
}) => {
  const [nomineeId, setNomineeId] = useState('');
  const [reason, setReason] = useState('');
  const [busy, setBusy] = useState(false);

  const mine = award.nominations.filter((n) => n.nominated_by === meId);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    try {
      await awardService.nominate(award.id, Number(nomineeId), reason.trim());
      setNomineeId(''); setReason('');
      onDone('Nomination recorded.');
    } catch (err) {
      onError(detailOf(err, 'That nomination was not accepted.'));
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="aw-open">
      <header className="aw-open-head">
        <div>
          <h2>{monthName(award.month, award.year)}</h2>
          <p>
            Nominations open
            {award.nominations_close_on && ` until ${new Date(award.nominations_close_on).toLocaleDateString('en-GB', { day: 'numeric', month: 'short' })}`}
            {' · '}{award.nomination_count} so far
          </p>
        </div>
        {canDecide && (
          <button type="button" className="aw-btn aw-btn-primary" onClick={onDecide}>
            <FiAward /> Decide
          </button>
        )}
      </header>

      {/* The tally is deliberately hidden while nominations are open: a
          visible running count turns nominating into voting for whoever is
          already ahead. */}
      {!canDecide && (
        <p className="aw-hidden">
          <FiEyeOff /> The count stays hidden until a winner is announced, so nobody
          nominates whoever is already ahead.
        </p>
      )}

      <form className="aw-form" onSubmit={submit}>
        <label>
          Nominate a colleague
          <select required value={nomineeId} onChange={(e) => setNomineeId(e.target.value)}>
            <option value="">Choose someone…</option>
            {employees.filter((e) => Number(e.id) !== meId).map((e) => (
              <option key={e.id} value={e.id}>{e.name}{e.designation ? ` — ${e.designation}` : ''}</option>
            ))}
          </select>
          <small>You cannot nominate yourself.</small>
        </label>
        <label>
          Why
          <textarea rows={3} required minLength={MIN_REASON} maxLength={1000} value={reason}
            onChange={(e) => setReason(e.target.value)}
            placeholder="Something specific they did this month, and what difference it made." />
          <small className={reason.trim().length && reason.trim().length < MIN_REASON ? 'is-short' : ''}>
            {reason.trim().length < MIN_REASON
              ? `At least ${MIN_REASON} characters — a nomination with nothing in it gives whoever decides nothing to weigh.`
              : `${reason.trim().length} characters.`}
          </small>
        </label>
        <button type="submit" className="aw-btn aw-btn-primary"
          disabled={busy || !nomineeId || reason.trim().length < MIN_REASON}>
          {busy ? 'Sending…' : award.you_have_nominated ? 'Update my nomination' : 'Nominate'}
        </button>
      </form>

      {mine.length > 0 && (
        <div className="aw-mine">
          <h3>Yours</h3>
          {mine.map((n) => (
            <div key={n.id} className="aw-mine-row">
              <div>
                <strong>{n.nominee_name}</strong>
                <p>{n.reason}</p>
              </div>
              <button type="button" onClick={async () => {
                try {
                  await awardService.withdraw(award.id, n.nominee_id);
                  onDone('Nomination withdrawn.');
                } catch (err) { onError(detailOf(err, 'That could not be withdrawn.')); }
              }} aria-label={`Withdraw nomination for ${n.nominee_name}`}><FiX /></button>
            </div>
          ))}
        </div>
      )}
    </section>
  );
};

const DecideDialog = ({ award, onClose, onDone, onError }: {
  award: Award; onClose: () => void; onDone: (m: string) => void; onError: (m: string) => void;
}) => {
  const [signals, setSignals] = useState<AwardSignals | null>(null);
  // An empty table and a failed request looked identical: headers, no rows,
  // no explanation. Whoever is deciding could not tell "nobody is eligible"
  // from "the request died", and had nothing to retry with.
  const [signalsState, setSignalsState] = useState<'loading' | 'ready' | 'failed'>('loading');
  const [winner, setWinner] = useState<number | null>(null);
  const [citation, setCitation] = useState('');
  const [noAward, setNoAward] = useState(false);
  const [busy, setBusy] = useState(false);

  const loadSignals = useCallback(() => {
    setSignalsState('loading');
    awardService.signals(award.id)
      .then((data) => { setSignals(data); setSignalsState('ready'); })
      .catch(() => { setSignals(null); setSignalsState('failed'); });
  }, [award.id]);

  useEffect(() => { loadSignals(); }, [loadSignals]);

  const submit = async () => {
    setBusy(true);
    try {
      await awardService.decide(award.id, noAward
        ? { no_award: true, ...(citation.trim() ? { citation: citation.trim() } : {}) }
        : { winner_id: winner ?? undefined, citation: citation.trim(), announce: true });
      onDone(noAward ? 'Recorded as no award this month.' : 'Winner announced.');
    } catch (err) {
      onError(detailOf(err, 'That decision was not recorded.'));
      setBusy(false);
    }
  };

  const nominationsFor = (id: number) => award.nominations.filter((n) => n.nominee_id === id);

  // Every nominee who received anything, most-nominated first — the same
  // order the signals table uses, so the two read together.
  const nomineeOrder = Array.from(new Set(award.nominations.map((n) => n.nominee_id)))
    .sort((a, b) => nominationsFor(b).length - nominationsFor(a).length);

  return (
    <div className="aw-overlay" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="aw-dialog" role="dialog" aria-modal="true">
        <header>
          <div>
            <h2>Decide {monthName(award.month, award.year)}</h2>
            <p>{signals?.period ? `The month's record, ${signals.period}` : 'The month’s record beside each name.'}</p>
          </div>
          <button type="button" onClick={onClose} aria-label="Close"><FiX /></button>
        </header>
        {/* The dialog is taller than the viewport on a full team, so the
            body scrolls and the header and footer do not — the title and
            the Announce button were being carried off-screen. */}
        <div className="aw-dialog-body">

        {/* The record, not a formula. Whoever decides still decides — but
            they should see that the most-nominated person was on leave. */}
        <div className="aw-table-wrap">
          <table className="aw-table">
            <thead>
              <tr>
                <th aria-label="Pick" /><th>Person</th><th>Noms</th><th>Attended</th>
                <th>Hours</th><th>Late</th><th>Overtime</th><th>Leave</th><th>Last score</th>
              </tr>
            </thead>
            <tbody>
              {signalsState !== 'ready' || (signals?.candidates || []).length === 0 ? (
                <tr>
                  <td colSpan={9} className="aw-table-state">
                    {signalsState === 'loading' && 'Loading the month’s record…'}
                    {signalsState === 'failed' && (
                      <>
                        The month’s record could not be loaded, so the columns
                        below are blank. You can still decide from the
                        nominations underneath.
                        {' '}
                        <button type="button" className="aw-retry" onClick={loadSignals}>
                          Try again
                        </button>
                      </>
                    )}
                    {signalsState === 'ready' && 'Nobody is eligible this month.'}
                  </td>
                </tr>
              ) : null}
              {(signals?.candidates || []).map((c: AwardSignal) => (
                <tr key={c.employee_id} className={winner === c.employee_id ? 'is-picked' : ''}>
                  <td>
                    <input type="radio" name="winner" checked={winner === c.employee_id}
                      disabled={noAward}
                      onChange={() => setWinner(c.employee_id)}
                      aria-label={`Choose ${c.employee_name}`} />
                  </td>
                  <td>
                    <strong>{c.employee_name}</strong>
                    <small>{c.designation || '—'}</small>
                  </td>
                  <td className={c.nominations > 0 ? 'aw-strong' : ''}>{c.nominations}</td>
                  <td>{c.attendance_exempt ? '—' : `${c.attendance_days}/${c.working_days_in_month}`}</td>
                  <td>{c.hours_worked ? c.hours_worked.toFixed(0) : '—'}</td>
                  <td className={c.late_days > 0 ? 'aw-warn' : ''}>{c.attendance_exempt ? '—' : c.late_days}</td>
                  <td>{c.approved_overtime_hours || '—'}</td>
                  <td className={c.leave_days > 4 ? 'aw-warn' : ''}>{c.leave_days || '—'}</td>
                  <td>{typeof c.last_review_score === 'number' ? c.last_review_score.toFixed(2) : '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        {/* Who nominated whom, before a winner is picked rather than after.
            The table above gives a count; a count does not say whether three
            nominations came from three teams or from one person's own. */}
        {award.nominations.length > 0 && (
          <div className="aw-reasons">
            <h3>Who nominated whom</h3>
            {nomineeOrder.map((nomineeId) => {
              const forThem = nominationsFor(nomineeId);
              return (
                <div key={nomineeId}
                     className={`aw-nominee${winner === nomineeId ? ' is-picked' : ''}`}>
                  <h4>
                    {forThem[0]?.nominee_name || `#${nomineeId}`}
                    <span>{forThem.length} {forThem.length === 1 ? 'nomination' : 'nominations'}</span>
                  </h4>
                  {forThem.map((n) => (
                    <p key={n.id}>
                      <span className="aw-by">{n.nominator_name || 'Someone'}</span>
                      {n.reason ? <>: “{n.reason}”</> : null}
                    </p>
                  ))}
                </div>
              );
            })}
          </div>
        )}

        <label className="aw-field">
          Citation{!noAward && <em>required</em>}
          <textarea rows={3} value={citation} onChange={(e) => setCitation(e.target.value)}
            placeholder="What they did and why it mattered. The other fifteen people read this." />
          <small>An award with no stated reason tells everybody else nothing about what the company values.</small>
        </label>

        <label className="aw-check">
          <input type="checkbox" checked={noAward} onChange={(e) => { setNoAward(e.target.checked); setWinner(null); }} />
          <span>
            <strong>No award this month</strong>
            <small>Recorded as an outcome, so an award that stops being given can be told apart from one nobody earned.</small>
          </span>
        </label>

        </div>
        <footer>
          <button type="button" onClick={onClose}>Cancel</button>
          <button type="button" className="aw-btn-primary" disabled={busy || (!noAward && (!winner || !citation.trim()))}
            onClick={() => void submit()}>
            {busy ? 'Saving…' : noAward ? 'Record no award' : 'Announce winner'}
          </button>
        </footer>
      </div>
    </div>
  );
};

export default Awards;
