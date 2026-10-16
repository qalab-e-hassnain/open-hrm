import { useCallback, useEffect, useMemo, useState, type FormEvent } from 'react';
import {
  FiAlertTriangle, FiCheck, FiCheckCircle, FiClock, FiInbox, FiUserCheck, FiX,
} from 'react-icons/fi';
import {
  probationService, PROBATION_STATUS_LABEL, DECISION_LABEL,
  type Probation, type ProbationDecision, type ProbationStatus,
} from '../../services/probationService';
import './Probation.css';

const shortDate = (value?: string | null) => {
  if (!value) return '—';
  const d = new Date(value);
  return Number.isNaN(d.getTime())
    ? value
    : d.toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' });
};

const STATUS_TONE: Record<ProbationStatus, string> = {
  in_probation: 'tone-open',
  extended: 'tone-warn',
  confirmed: 'tone-done',
  not_applicable: 'tone-muted',
};

/**
 * How long past its end date a probation has to be before it reads as a
 * historical record rather than a decision somebody is late making. Matches
 * the backend's bulk-confirm default of a year.
 */
const BACKLOG_DAYS = 365;

const daysSince = (value?: string | null): number | null => {
  if (!value) return null;
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return null;
  return Math.floor((Date.now() - d.getTime()) / 86_400_000);
};

/** The error the API sends is written for a person: show it, don't remap it. */
const detailOf = (err: unknown, fallback: string): string => {
  const detail = (err as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail;
  return typeof detail === 'string' && detail ? detail : fallback;
};

const ProbationPage = () => {
  const [rows, setRows] = useState<Probation[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [withinDays, setWithinDays] = useState(14);
  const [deciding, setDeciding] = useState<Probation | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      setRows(await probationService.getDue(withinDays));
    } catch (err) {
      setError(detailOf(err, 'Could not load the probation queue.'));
    } finally {
      setLoading(false);
    }
  }, [withinDays]);

  useEffect(() => { void load(); }, [load]);

  // Two different things share this queue: people genuinely coming up for a
  // decision, and everyone who was never formally confirmed because the
  // feature did not exist until now. Only the first group is a task.
  const { backlog, live } = useMemo(() => {
    const backlogRows: Probation[] = [];
    const liveRows: Probation[] = [];
    for (const row of rows) {
      const age = daysSince(row.probation_end_date);
      (age !== null && age > BACKLOG_DAYS ? backlogRows : liveRows).push(row);
    }
    return { backlog: backlogRows, live: liveRows };
  }, [rows]);

  const runBulkConfirm = async () => {
    setBusy(true);
    setError('');
    setNotice('');
    try {
      const confirmed = await probationService.bulkConfirm({});
      setNotice(
        confirmed.length
          ? `Confirmed ${confirmed.length} ${confirmed.length === 1 ? 'person' : 'people'} whose probation ended over a year ago. No letters were issued.`
          : 'Nothing to clear — no probation ended more than a year ago.',
      );
      await load();
    } catch (err) {
      setError(detailOf(err, 'Bulk confirmation failed.'));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="probation-page">
      <header className="pb-head">
        <div>
          <h1>Probation</h1>
          <p>Probation ends because somebody decides it does. This is the queue of those decisions.</p>
        </div>
        <label className="pb-window">
          Ending within
          <select value={withinDays} onChange={(e) => setWithinDays(Number(e.target.value))}>
            <option value={7}>7 days</option>
            <option value={14}>14 days</option>
            <option value={30}>30 days</option>
            <option value={90}>90 days</option>
          </select>
        </label>
      </header>

      {error && <div className="pb-alert pb-alert-error"><FiAlertTriangle /> {error}</div>}
      {notice && <div className="pb-alert pb-alert-ok"><FiCheckCircle /> {notice}</div>}

      {backlog.length > 0 && (
        <section className="pb-backlog">
          <div className="pb-backlog-text">
            <h2><FiUserCheck /> {backlog.length} {backlog.length === 1 ? 'person was' : 'people were'} never formally confirmed</h2>
            <p>
              Their probation ended over a year ago — this is paperwork the portal
              never had, not a decision anybody needs to make one at a time.
              Confirming them clears the queue and issues no letters, because a
              confirmation dated today for somebody confirmed in practice years ago
              would be a fiction. Anyone genuinely still on probation is left alone.
            </p>
            <ul className="pb-backlog-names">
              {backlog.map((row) => (
                <li key={row.employee_id}>
                  <strong>{row.employee_name}</strong>
                  <span>{row.designation || '—'} · ended {shortDate(row.probation_end_date)}</span>
                </li>
              ))}
            </ul>
          </div>
          <button type="button" className="pb-primary" onClick={runBulkConfirm} disabled={busy}>
            {busy ? 'Confirming…' : `Confirm all ${backlog.length}`}
          </button>
        </section>
      )}

      <section className="pb-queue">
        <h2>Coming up for decision</h2>
        {loading ? (
          <div className="pb-empty">Loading…</div>
        ) : live.length === 0 ? (
          <div className="pb-empty">
            <FiInbox />
            <p>Nobody{withinDays === 14 ? '' : ' in this window'} is due a probation decision.</p>
          </div>
        ) : (
          <div className="pb-table-wrap">
            <table className="pb-table">
              <thead>
                <tr>
                  <th>Employee</th><th>Manager</th><th>Ends</th>
                  <th>Status</th><th aria-label="Action" />
                </tr>
              </thead>
              <tbody>
                {live.map((row) => (
                  <tr key={row.employee_id} className={row.is_overdue ? 'is-overdue' : ''}>
                    <td>
                      <strong>{row.employee_name}</strong>
                      <small>{[row.designation, row.department].filter(Boolean).join(' · ') || '—'}</small>
                    </td>
                    <td>{row.manager_name || <span className="pb-muted">No line manager</span>}</td>
                    <td>
                      {shortDate(row.probation_end_date)}
                      {row.is_overdue
                        ? <small className="pb-overdue"><FiClock /> Overdue</small>
                        : typeof row.days_remaining === 'number'
                          ? <small>{row.days_remaining} days left</small>
                          : null}
                    </td>
                    <td><span className={`pb-badge ${STATUS_TONE[row.status]}`}>{PROBATION_STATUS_LABEL[row.status]}</span></td>
                    <td className="pb-action">
                      {/* can_decide is the server's answer for this caller. */}
                      {row.can_decide
                        ? <button type="button" onClick={() => { setDeciding(row); setNotice(''); }}>Decide</button>
                        : <span className="pb-muted">{row.manager_name ? `${row.manager_name} decides` : 'Not yours to decide'}</span>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {deciding && (
        <DecisionDialog
          row={deciding}
          onClose={() => setDeciding(null)}
          onDone={(message) => { setDeciding(null); setNotice(message); void load(); }}
          onError={(message) => setError(message)}
        />
      )}
    </div>
  );
};

const DecisionDialog = ({ row, onClose, onDone, onError }: {
  row: Probation;
  onClose: () => void;
  onDone: (message: string) => void;
  onError: (message: string) => void;
}) => {
  const [decision, setDecision] = useState<ProbationDecision>('confirm');
  const [comments, setComments] = useState('');
  const [newEndDate, setNewEndDate] = useState('');
  const [effectiveDate, setEffectiveDate] = useState('');
  const [issueLetter, setIssueLetter] = useState(true);
  const [saving, setSaving] = useState(false);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setSaving(true);
    try {
      await probationService.decide(row.employee_id, {
        decision,
        comments: comments.trim() || undefined,
        new_end_date: decision === 'extend' ? newEndDate : undefined,
        effective_date: decision === 'confirm' && effectiveDate ? effectiveDate : undefined,
        issue_letter: decision === 'confirm' ? issueLetter : false,
      });
      onDone(`${row.employee_name}: ${DECISION_LABEL[decision].toLowerCase()} recorded.`);
    } catch (err) {
      onError(detailOf(err, 'The decision was not recorded.'));
      onClose();
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="pb-overlay" role="dialog" aria-modal="true" aria-label={`Probation decision for ${row.employee_name}`}>
      <form className="pb-dialog" onSubmit={submit}>
        <header>
          <div>
            <h2>{row.employee_name}</h2>
            <p>{[row.designation, row.department].filter(Boolean).join(' · ') || '—'} · probation ends {shortDate(row.probation_end_date)}</p>
          </div>
          <button type="button" onClick={onClose} aria-label="Close"><FiX /></button>
        </header>

        <fieldset className="pb-choices">
          <legend>Decision</legend>
          {(['confirm', 'extend', 'not_confirmed'] as ProbationDecision[]).map((value) => (
            <label key={value} className={decision === value ? 'is-chosen' : ''}>
              <input type="radio" name="decision" value={value}
                checked={decision === value} onChange={() => setDecision(value)} />
              <span>{DECISION_LABEL[value]}</span>
            </label>
          ))}
        </fieldset>

        {decision === 'extend' && (
          <label className="pb-field">
            New end date
            <input type="date" required value={newEndDate} min={row.probation_end_date || undefined}
              onChange={(e) => setNewEndDate(e.target.value)} />
            <small>Must be later than the current end date.</small>
          </label>
        )}

        {decision === 'confirm' && (
          <>
            <label className="pb-field">
              Effective from
              <input type="date" value={effectiveDate} onChange={(e) => setEffectiveDate(e.target.value)} />
              <small>Leave blank to take effect on the probation end date.</small>
            </label>
            <label className="pb-check">
              <input type="checkbox" checked={issueLetter} onChange={(e) => setIssueLetter(e.target.checked)} />
              <span>
                <strong>Issue a confirmation letter</strong>
                <small>Appears under their documents, needs no signature, and carries a verification code.</small>
              </span>
            </label>
          </>
        )}

        <label className="pb-field">
          Comments
          <textarea rows={3} maxLength={2000} value={comments} onChange={(e) => setComments(e.target.value)}
            placeholder={decision === 'not_confirmed' ? 'Why probation was not confirmed.' : 'Optional.'} />
        </label>

        <footer>
          <button type="button" onClick={onClose}>Cancel</button>
          <button type="submit" className="pb-primary" disabled={saving || (decision === 'extend' && !newEndDate)}>
            {saving ? 'Saving…' : <><FiCheck /> Record decision</>}
          </button>
        </footer>
      </form>
    </div>
  );
};

export default ProbationPage;
