import { useCallback, useEffect, useState, type FormEvent } from 'react';
import {
  FiAlertCircle, FiCheck, FiClock, FiDollarSign, FiInbox, FiPlus, FiTrash2, FiX,
} from 'react-icons/fi';
import {
  timeLogService, isOpen, TIME_LOG_STATUS_LABEL,
  type TimeLog, type TimeLogStatus,
} from '../../services/timeLogService';
import './TimeLogs.css';

type Tab = 'claim' | 'mine' | 'approvals';
type Kind = 'attendance' | 'overtime';

/** A claim filed on or before this day of the month is paid with that month's
 *  salary; after it, the next month's payroll picks it up. */
const FILING_CUTOFF_DAY = 25;

const pad2 = (n: number) => String(n).padStart(2, '0');
const todayISO = () => {
  const d = new Date();
  return `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}`;
};

/** A date and a wall-clock time as the UTC instant the API stores. */
const toUtc = (date: string, time: string) => new Date(`${date}T${time}`).toISOString();

const shortDate = (value?: string | null) => {
  if (!value) return '—';
  const d = new Date(value);
  return Number.isNaN(d.getTime())
    ? value
    : d.toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' });
};

const clockTime = (value?: string | null) => {
  if (!value) return '—';
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? '—' : d.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' });
};

const money = (value?: number | null) =>
  typeof value === 'number' ? value.toLocaleString('en-PK', { maximumFractionDigits: 0 }) : '—';

const STATUS_TONE: Record<TimeLogStatus, string> = {
  pending: 'wait',
  manager_approved: 'wait',
  approved: 'ok',
  rejected: 'bad',
  cancelled: 'muted',
};

/**
 * Manually logged time.
 *
 * Two things are claimed here: a session that was never clocked, and overtime
 * worked. Both take two signatures — the line manager who saw the work, then
 * HR who pays for it — except for someone with no line manager, whose request
 * starts already past the first stage.
 *
 * The client never decides who may sign. Every response carries `can_approve`
 * for this viewer and `next_approver` for everyone else, so the rules the
 * server enforces are not restated here where they could drift.
 */
const TimeLogs = () => {
  const [tab, setTab] = useState<Tab>('claim');
  const [kind, setKind] = useState<Kind>('attendance');
  const [mine, setMine] = useState<TimeLog[]>([]);
  const [queue, setQueue] = useState<TimeLog[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [notice, setNotice] = useState<{ tone: 'ok' | 'bad'; text: string } | null>(null);

  const load = useCallback(async () => {
    setIsLoading(true);
    const [mineResult, queueResult] = await Promise.allSettled([
      timeLogService.myLogs(),
      // Everyone asks: the server returns an empty queue to anyone with
      // nothing waiting on them, so there is no role check to make here.
      timeLogService.pending(),
    ]);
    if (mineResult.status === 'fulfilled') setMine(mineResult.value);
    if (queueResult.status === 'fulfilled') setQueue(queueResult.value);
    setIsLoading(false);
  }, []);

  useEffect(() => { void load(); }, [load]);

  return (
    <div className="tl-page">
      <header className="tl-head">
        <div>
          <h1>Time logs</h1>
          <p>Claim a session you forgot to clock, or overtime you worked.</p>
        </div>
        <nav className="tl-tabs">
          <button className={tab === 'claim' ? 'is-active' : ''} onClick={() => setTab('claim')}>
            <FiPlus size={15} /> New claim
          </button>
          <button className={tab === 'mine' ? 'is-active' : ''} onClick={() => setTab('mine')}>
            <FiClock size={15} /> My logs {mine.length > 0 && <span className="tl-count">{mine.length}</span>}
          </button>
          {queue.length > 0 && (
            <button className={tab === 'approvals' ? 'is-active' : ''} onClick={() => setTab('approvals')}>
              <FiInbox size={15} /> Approvals <span className="tl-count tl-count-alert">{queue.length}</span>
            </button>
          )}
        </nav>
      </header>

      {notice && (
        <div className={`tl-notice tl-notice-${notice.tone}`} role="status">
          <span>{notice.text}</span>
          <button onClick={() => setNotice(null)} aria-label="Dismiss"><FiX size={15} /></button>
        </div>
      )}

      {tab === 'claim' && (
        <ClaimForm
          kind={kind}
          onKind={setKind}
          onDone={(text) => { setNotice({ tone: 'ok', text }); setTab('mine'); void load(); }}
          onError={(text) => setNotice({ tone: 'bad', text })}
        />
      )}

      {tab === 'mine' && (
        <MyLogs
          logs={mine}
          isLoading={isLoading}
          onWithdraw={async (id) => {
            try {
              await timeLogService.withdraw(id);
              setNotice({ tone: 'ok', text: 'Request withdrawn.' });
              void load();
            } catch (err: any) {
              setNotice({ tone: 'bad', text: err?.message || 'Could not withdraw this request.' });
            }
          }}
        />
      )}

      {tab === 'approvals' && (
        <ApprovalQueue
          logs={queue}
          onDecided={(text) => { setNotice({ tone: 'ok', text }); void load(); }}
          onError={(text) => setNotice({ tone: 'bad', text })}
        />
      )}
    </div>
  );
};

/* ----------------------------- New claim ------------------------------ */

const ClaimForm = ({ kind, onKind, onDone, onError }: {
  kind: Kind;
  onKind: (k: Kind) => void;
  onDone: (text: string) => void;
  onError: (text: string) => void;
}) => {
  const [workDate, setWorkDate] = useState(todayISO());
  const [checkIn, setCheckIn] = useState('09:00');
  const [checkOut, setCheckOut] = useState('17:00');
  const [hours, setHours] = useState('');
  const [reason, setReason] = useState('');
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [isSaving, setIsSaving] = useState(false);
  // Which payroll run a claim lands in is decided server-side off the filing
  // date; this only tells the employee which side of the line they are on.
  const pastCutoff = new Date().getDate() > FILING_CUTOFF_DAY;

  const validate = () => {
    const next: Record<string, string> = {};
    if (!workDate) next.workDate = 'Pick the date you worked.';
    else if (workDate > todayISO()) next.workDate = 'The work date cannot be in the future.';
    if (reason.trim().length < 3) next.reason = 'Give a brief reason — at least 3 characters.';

    if (kind === 'attendance') {
      if (!checkIn || !checkOut) next.times = 'Both times are required.';
      else {
        // Checked here rather than left to the server's 422: the fix is in
        // this form, so the message belongs beside the fields.
        const from = new Date(`${workDate}T${checkIn}`);
        const to = new Date(`${workDate}T${checkOut}`);
        const span = (to.getTime() - from.getTime()) / 36e5;
        if (span <= 0) next.times = 'Check-out must be after check-in.';
        else if (span > 24) next.times = 'A single session cannot exceed 24 hours.';
      }
    } else {
      const value = Number(hours);
      if (!Number.isFinite(value) || value <= 0) next.hours = 'Enter the hours worked.';
      else if (value > 24) next.hours = 'A single day cannot exceed 24 hours.';
    }
    setErrors(next);
    return Object.keys(next).length === 0;
  };

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!validate()) return;
    setIsSaving(true);
    try {
      if (kind === 'attendance') {
        await timeLogService.claimAttendance({
          work_date: toUtc(workDate, '00:00'),
          check_in_time: toUtc(workDate, checkIn),
          check_out_time: toUtc(workDate, checkOut),
          reason: reason.trim(),
        });
        onDone('Attendance claim submitted for approval.');
      } else {
        await timeLogService.claimOvertime({
          work_date: toUtc(workDate, '00:00'),
          hours: Number(hours),
          reason: reason.trim(),
        });
        onDone('Overtime claim submitted for approval.');
      }
      setReason(''); setHours('');
    } catch (err: any) {
      // The server's conflict message names the clashing session and time —
      // far more use than anything generic this form could say.
      onError(err?.message || 'Could not submit this claim.');
    } finally {
      setIsSaving(false);
    }
  };

  return (
    <form className="tl-card tl-form" onSubmit={submit}>
      <div className="tl-kind">
        <button type="button" className={kind === 'attendance' ? 'is-active' : ''} onClick={() => onKind('attendance')}>
          <FiClock size={15} /> Forgotten session
        </button>
        <button type="button" className={kind === 'overtime' ? 'is-active' : ''} onClick={() => onKind('overtime')}>
          <FiDollarSign size={15} /> Overtime
        </button>
      </div>

      <label className="tl-field">
        <span>Date worked</span>
        <input type="date" value={workDate} max={todayISO()} onChange={(e) => setWorkDate(e.target.value)} />
        {errors.workDate && <small className="tl-error">{errors.workDate}</small>}
      </label>

      {kind === 'attendance' ? (
        <>
          <div className="tl-row">
            <label className="tl-field">
              <span>Check in</span>
              <input type="time" value={checkIn} onChange={(e) => setCheckIn(e.target.value)} />
            </label>
            <label className="tl-field">
              <span>Check out</span>
              <input type="time" value={checkOut} onChange={(e) => setCheckOut(e.target.value)} />
            </label>
          </div>
          {errors.times && <small className="tl-error">{errors.times}</small>}
          <p className="tl-hint">Hours are worked out from the two times.</p>
        </>
      ) : (
        <>
          <label className="tl-field">
            <span>Hours worked</span>
            <input type="number" step="0.5" min="0" max="24" value={hours}
              onChange={(e) => setHours(e.target.value)} placeholder="e.g. 3" />
            {errors.hours && <small className="tl-error">{errors.hours}</small>}
          </label>

          {/* No amount here, by design. The figure follows from the hours and
              this employee's own salary, the server derives it, and an amount
              sent by a client is ignored. It appears on the filed claim under
              My logs, where it is a fact rather than a suggestion to argue
              with while typing. */}
          <p className="tl-hint">
            Payment is worked out pro rata from your basic salary and shown on the claim once
            it is filed.
          </p>

          {/* The cut-off runs on the FILING date — not the work date, and not the
              approval date, so a slow approval never costs the employee a month.
              That makes it the employee's own deadline to miss, which is why it
              belongs on their form rather than on an approver's screen. */}
          <p className={`tl-hint${pastCutoff ? ' tl-error' : ''}`}>
            {pastCutoff
              ? `The ${FILING_CUTOFF_DAY}th has passed, so a claim filed now is paid with next month’s salary. Filing today still fixes the date — a slow approval will not push it back another month.`
              : `File by the ${FILING_CUTOFF_DAY}th and this is paid with this month’s salary. After that it goes onto next month’s payroll.`}
          </p>
        </>
      )}

      <label className="tl-field">
        <span>Reason</span>
        <textarea rows={3} value={reason} onChange={(e) => setReason(e.target.value)}
          placeholder={kind === 'attendance' ? 'e.g. Phone died, could not check in' : 'e.g. Release night, agreed in advance'} />
        {errors.reason && <small className="tl-error">{errors.reason}</small>}
      </label>

      <div className="tl-form-actions">
        <button type="submit" className="tl-btn tl-btn-primary" disabled={isSaving}>
          {isSaving ? 'Submitting…' : 'Submit for approval'}
        </button>
      </div>
    </form>
  );
};

/* ------------------------------ My logs ------------------------------- */

const MyLogs = ({ logs, isLoading, onWithdraw }: {
  logs: TimeLog[]; isLoading: boolean; onWithdraw: (id: number) => void;
}) => {
  if (isLoading) return <div className="tl-card tl-empty">Loading your time logs…</div>;
  if (!logs.length) return <div className="tl-card tl-empty">You have not logged any time yet.</div>;

  return (
    <div className="tl-list">
      {logs.map((log) => (
        <article key={log.id} className="tl-card tl-item">
          <div className="tl-item-head">
            <div>
              <h3>{log.kind === 'overtime' ? 'Overtime' : 'Forgotten session'} · {shortDate(log.work_date)}</h3>
              <p className="tl-meta">
                {log.hours.toFixed(2)} hours
                {log.kind === 'attendance' && log.check_in_time && (
                  <> · {clockTime(log.check_in_time)} – {clockTime(log.check_out_time)}</>
                )}
                {log.kind === 'overtime' && <> · <strong>PKR {money(log.amount)}</strong> payable</>}
              </p>
            </div>
            <span className={`tl-status tl-status-${STATUS_TONE[log.status]}`}>
              {TIME_LOG_STATUS_LABEL[log.status]}
            </span>
          </div>

          <p className="tl-reason">{log.reason}</p>

          {log.status === 'rejected' && log.rejection_reason && (
            <p className="tl-rejected"><FiAlertCircle size={14} /> {log.rejection_reason}</p>
          )}

          {/* Who signed, and at which stage. A claim that just says "approved"
              leaves the employee with nobody to ask about the figure. */}
          {(log.manager_approver_name || log.approver_name) && (
            <p className="tl-signatures">
              {log.manager_approver_name && (
                <span><FiCheck size={13} /> Line manager: {log.manager_approver_name}</span>
              )}
              {log.approver_name && (
                <span><FiCheck size={13} /> HR: {log.approver_name}</span>
              )}
            </p>
          )}

          {isOpen(log.status) && (
            <div className="tl-item-actions">
              {log.next_approver && <span className="tl-meta">Waiting on {log.next_approver}</span>}
              <button className="tl-btn tl-btn-ghost" onClick={() => onWithdraw(log.id)}>
                <FiTrash2 size={14} /> Withdraw
              </button>
            </div>
          )}
        </article>
      ))}
    </div>
  );
};

/* --------------------------- Approval queue --------------------------- */

const ApprovalQueue = ({ logs, onDecided, onError }: {
  logs: TimeLog[]; onDecided: (text: string) => void; onError: (text: string) => void;
}) => {
  const [rejecting, setRejecting] = useState<TimeLog | null>(null);
  const [reason, setReason] = useState('');
  const [amounts, setAmounts] = useState<Record<number, string>>({});
  const [busyId, setBusyId] = useState<number | null>(null);

  const approve = async (log: TimeLog) => {
    setBusyId(log.id);
    try {
      const corrected = amounts[log.id];
      await timeLogService.decide(log.id, {
        approved: true,
        // Only sent when the approver actually changed it.
        ...(corrected !== undefined && corrected !== '' && Number(corrected) !== log.amount
          ? { amount: Number(corrected) }
          : {}),
      });
      onDecided('Approved.');
    } catch (err: any) {
      onError(err?.message || 'Could not approve this request.');
    } finally {
      setBusyId(null);
    }
  };

  const reject = async () => {
    if (!rejecting) return;
    // Required here rather than letting the server answer 422: the approver is
    // already in the dialog, and that is where the fix belongs.
    if (!reason.trim()) return;
    setBusyId(rejecting.id);
    try {
      await timeLogService.decide(rejecting.id, { approved: false, rejection_reason: reason.trim() });
      setRejecting(null);
      setReason('');
      onDecided('Rejected.');
    } catch (err: any) {
      onError(err?.message || 'Could not reject this request.');
    } finally {
      setBusyId(null);
    }
  };

  if (!logs.length) return <div className="tl-card tl-empty">Nothing is waiting on you.</div>;

  return (
    <>
      <div className="tl-list">
        {logs.map((log) => {
          const claimed = log.amount ?? null;
          const differs =
            log.kind === 'overtime' &&
            typeof claimed === 'number' &&
            typeof log.suggested_amount === 'number' &&
            claimed !== log.suggested_amount;

          return (
            <article key={log.id} className="tl-card tl-item">
              <div className="tl-item-head">
                <div>
                  <h3>{log.employee_name || `Employee #${log.employee_id}`}</h3>
                  <p className="tl-meta">
                    {log.kind === 'overtime' ? 'Overtime' : 'Forgotten session'} · {shortDate(log.work_date)}
                    {' · '}{log.hours.toFixed(2)} hours
                    {log.kind === 'attendance' && log.check_in_time && (
                      <> · {clockTime(log.check_in_time)} – {clockTime(log.check_out_time)}</>
                    )}
                  </p>
                </div>
                <span className={`tl-status tl-status-${STATUS_TONE[log.status]}`}>
                  {TIME_LOG_STATUS_LABEL[log.status]}
                </span>
              </div>

              <p className="tl-reason">{log.reason}</p>

              {differs && (
                <p className="tl-flag">
                  <FiAlertCircle size={14} />
                  Claimed <strong>PKR {money(claimed)}</strong>, formula suggests{' '}
                  <strong>PKR {money(log.suggested_amount)}</strong>.
                </p>
              )}

              {log.can_approve ? (
                <div className="tl-item-actions">
                  {log.kind === 'overtime' && (
                    <label className="tl-inline-field">
                      <span>Amount to pay</span>
                      <input
                        type="number" min="0"
                        value={amounts[log.id] ?? (claimed !== null ? String(claimed) : '')}
                        onChange={(e) => setAmounts((cur) => ({ ...cur, [log.id]: e.target.value }))}
                      />
                    </label>
                  )}
                  <button className="tl-btn tl-btn-primary" disabled={busyId === log.id} onClick={() => approve(log)}>
                    <FiCheck size={14} /> Approve
                  </button>
                  <button className="tl-btn tl-btn-danger" disabled={busyId === log.id}
                    onClick={() => { setRejecting(log); setReason(''); }}>
                    <FiX size={14} /> Reject
                  </button>
                </div>
              ) : (
                <p className="tl-meta">
                  {log.next_approver ? `Waiting on ${log.next_approver}` : 'You cannot act on this request.'}
                </p>
              )}
            </article>
          );
        })}
      </div>

      {rejecting && (
        <div className="tl-backdrop" onMouseDown={(e) => { if (e.target === e.currentTarget) setRejecting(null); }}>
          <div className="tl-dialog" role="dialog" aria-modal="true" aria-labelledby="tl-reject-title">
            <h2 id="tl-reject-title">Reject this claim</h2>
            <p className="tl-meta">
              {rejecting.employee_name} · {shortDate(rejecting.work_date)} · {rejecting.hours.toFixed(2)} hours
            </p>
            <label className="tl-field">
              <span>Reason (required)</span>
              <textarea rows={3} value={reason} autoFocus
                onChange={(e) => setReason(e.target.value)}
                placeholder="e.g. Not pre-agreed" />
            </label>
            <div className="tl-form-actions">
              <button className="tl-btn tl-btn-ghost" onClick={() => setRejecting(null)}>Cancel</button>
              <button className="tl-btn tl-btn-danger" disabled={!reason.trim() || busyId === rejecting.id}
                onClick={reject}>
                {busyId === rejecting.id ? 'Rejecting…' : 'Reject claim'}
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
};

export default TimeLogs;
