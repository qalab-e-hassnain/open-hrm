import { useCallback, useEffect, useState, type FormEvent } from 'react';
import {
  FiAlertTriangle, FiCheck, FiCheckCircle, FiInbox, FiPlus, FiTrash2, FiX,
} from 'react-icons/fi';
import {
  loanService, LOAN_KIND_LABEL, LOAN_STATUS_LABEL, INSTALMENT_STATUS_LABEL,
  type Loan, type LoanKind, type LoanStatus,
} from '../../services/loanService';
import { useAuthStore } from '../../store/authStore';
import { isPrivileged } from '../../utils/permissions';
import './Advances.css';

const money = (v?: number | null) =>
  typeof v === 'number' ? `PKR ${v.toLocaleString('en-PK', { maximumFractionDigits: 0 })}` : '—';

const shortDate = (v?: string | null) => {
  if (!v) return '—';
  const d = new Date(v);
  return Number.isNaN(d.getTime()) ? v : d.toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' });
};

const monthLabel = (key?: string | null) => {
  if (!key) return '—';
  const [y, m] = key.split('-');
  const d = new Date(Number(y), Number(m) - 1, 1);
  return Number.isNaN(d.getTime()) ? key : d.toLocaleDateString('en-GB', { month: 'short', year: 'numeric' });
};

const STATUS_TONE: Record<LoanStatus, string> = {
  requested: 'tone-wait', approved: 'tone-info', disbursed: 'tone-live',
  settled: 'tone-done', rejected: 'tone-bad', cancelled: 'tone-muted',
};

const detailOf = (err: unknown, fallback: string): string => {
  const detail = (err as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail;
  if (typeof detail === 'string' && detail) return detail;
  return (err as { message?: string })?.message || fallback;
};

type Tab = 'mine' | 'queue';

const Advances = () => {
  const { user } = useAuthStore();
  const privileged = isPrivileged(user);
  const [tab, setTab] = useState<Tab>('mine');
  const [mine, setMine] = useState<Loan[]>([]);
  const [queue, setQueue] = useState<Loan[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [asking, setAsking] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      setMine(await loanService.mine());
      if (privileged) setQueue(await loanService.list());
    } catch (err) {
      setError(detailOf(err, 'Could not load advances.'));
    } finally {
      setLoading(false);
    }
  }, [privileged]);

  useEffect(() => { void load(); }, [load]);

  const done = (message: string) => { setNotice(message); setAsking(false); void load(); };
  const rows = tab === 'mine' ? mine : queue;

  return (
    <div className="advances-page">
      <header className="adv-head">
        <div>
          <h1>Advances and loans</h1>
          <p>Money paid ahead of a payslip, and the schedule that recovers it.</p>
        </div>
        <button type="button" className="adv-btn adv-btn-primary" onClick={() => setAsking(true)}>
          <FiPlus /> Request
        </button>
      </header>

      {privileged && (
        <div className="adv-tabs">
          <button type="button" className={tab === 'mine' ? 'is-active' : ''} onClick={() => setTab('mine')}>Mine</button>
          <button type="button" className={tab === 'queue' ? 'is-active' : ''} onClick={() => setTab('queue')}>
            Everyone {queue.filter((l) => l.status === 'requested').length > 0 && (
              <span className="adv-count">{queue.filter((l) => l.status === 'requested').length}</span>
            )}
          </button>
        </div>
      )}

      {error && <div className="adv-alert adv-alert-error"><FiAlertTriangle /> {error}</div>}
      {notice && <div className="adv-alert adv-alert-ok"><FiCheckCircle /> {notice}</div>}

      {loading ? (
        <div className="adv-empty">Loading…</div>
      ) : rows.length === 0 ? (
        <div className="adv-empty">
          <FiInbox />
          <p>{tab === 'mine' ? 'You have not requested an advance.' : 'Nothing to decide.'}</p>
        </div>
      ) : (
        <div className="adv-list">
          {rows.map((loan) => (
            <LoanCard key={loan.id} loan={loan} privileged={privileged}
              isMine={loan.employee_id === Number(user?.id)}
              onDone={done} onError={setError} />
          ))}
        </div>
      )}

      {asking && (
        <RequestDialog onClose={() => setAsking(false)} onDone={done} onError={setError} />
      )}
    </div>
  );
};

const LoanCard = ({ loan, privileged, isMine, onDone, onError }: {
  loan: Loan; privileged: boolean; isMine: boolean;
  onDone: (m: string) => void; onError: (m: string) => void;
}) => {
  const [busy, setBusy] = useState(false);
  const [disbursing, setDisbursing] = useState(false);
  const [firstMonth, setFirstMonth] = useState('');
  const [note, setNote] = useState('');

  const run = async (fn: () => Promise<unknown>, message: string) => {
    setBusy(true);
    try { await fn(); onDone(message); }
    catch (err) { onError(detailOf(err, 'That did not work.')); }
    finally { setBusy(false); }
  };

  // Nobody decides their own request, and the server refuses it anyway.
  const canDecide = privileged && !isMine && loan.status === 'requested';
  const canDisburse = privileged && loan.status === 'approved';
  const canWithdraw = (isMine || privileged) && (loan.status === 'requested' || loan.status === 'approved');

  return (
    <article className={`adv-card ${STATUS_TONE[loan.status]}`}>
      <header>
        <div>
          <h2>{money(loan.principal)} <small>{LOAN_KIND_LABEL[loan.kind]}</small></h2>
          <p>
            {loan.employee_name && !isMine ? `${loan.employee_name} · ` : ''}
            requested {shortDate(loan.requested_at)}
            {loan.instalment_count ? ` · ${loan.instalment_count} instalment${loan.instalment_count === 1 ? '' : 's'}` : ''}
          </p>
        </div>
        <span className={`adv-badge ${STATUS_TONE[loan.status]}`}>{LOAN_STATUS_LABEL[loan.status]}</span>
      </header>

      <p className="adv-reason">{loan.reason}</p>

      {loan.decision_note && <p className="adv-note">“{loan.decision_note}”</p>}

      {/* Approved is not paid: outstanding stays zero and no payslip is
          touched until the money actually leaves. */}
      {loan.status === 'approved' && (
        <p className="adv-hint">
          Agreed but not yet paid out. Nothing is deducted from any payslip until it is disbursed.
        </p>
      )}

      {loan.instalments.length > 0 && (
        <div className="adv-schedule">
          <div className="adv-schedule-head">
            <span>Recovered {money(loan.recovered)}</span>
            <span>Outstanding <b>{money(loan.outstanding)}</b></span>
          </div>
          <ol className="adv-instalments">
            {loan.instalments.map((inst) => (
              <li key={inst.id} className={`is-${inst.status}`}>
                <span className="adv-inst-month">{monthLabel(inst.due_month)}</span>
                <span className="adv-inst-amount">{money(inst.amount)}</span>
                <span className="adv-inst-state">{INSTALMENT_STATUS_LABEL[inst.status]}</span>
                {privileged && inst.status === 'pending' && (
                  <button type="button" disabled={busy} onClick={() => {
                    const reason = window.prompt('Why is this instalment being waived?');
                    if (reason && reason.trim().length >= 3) {
                      void run(() => loanService.waive(loan.id, inst.id, reason.trim()), 'Instalment waived.');
                    }
                  }}>Waive</button>
                )}
              </li>
            ))}
          </ol>
        </div>
      )}

      {(canDecide || canDisburse || canWithdraw) && (
        <footer className="adv-actions">
          {canDecide && (
            <>
              <input className="adv-note-input" value={note} placeholder="Note (optional)"
                onChange={(e) => setNote(e.target.value)} />
              <button type="button" className="adv-btn" disabled={busy}
                onClick={() => void run(
                  () => loanService.decide(loan.id, { approved: false, note: note.trim() || undefined }),
                  'Rejected.')}>
                <FiX /> Reject
              </button>
              <button type="button" className="adv-btn adv-btn-primary" disabled={busy}
                onClick={() => void run(
                  () => loanService.decide(loan.id, { approved: true, note: note.trim() || undefined }),
                  'Approved. It is not paid until you disburse it.')}>
                <FiCheck /> Approve
              </button>
            </>
          )}

          {canDisburse && (disbursing ? (
            <>
              <label className="adv-month">
                First recovery month
                <input type="month" value={firstMonth} onChange={(e) => setFirstMonth(e.target.value)} />
              </label>
              <button type="button" className="adv-btn" onClick={() => setDisbursing(false)}>Cancel</button>
              <button type="button" className="adv-btn adv-btn-primary" disabled={busy}
                onClick={() => void run(
                  () => loanService.disburse(loan.id, firstMonth ? { first_recovery_month: firstMonth } : {}),
                  'Disbursed. The recovery schedule is running.')}>
                Confirm disbursement
              </button>
            </>
          ) : (
            <button type="button" className="adv-btn adv-btn-primary" onClick={() => setDisbursing(true)}>
              Disburse
            </button>
          ))}

          {canWithdraw && !disbursing && (
            <button type="button" className="adv-btn adv-btn-ghost" disabled={busy}
              onClick={() => void run(() => loanService.withdraw(loan.id), 'Withdrawn.')}>
              <FiTrash2 /> Withdraw
            </button>
          )}
        </footer>
      )}
    </article>
  );
};

const RequestDialog = ({ onClose, onDone, onError }: {
  onClose: () => void; onDone: (m: string) => void; onError: (m: string) => void;
}) => {
  const [kind, setKind] = useState<LoanKind>('advance');
  const [principal, setPrincipal] = useState('');
  const [instalments, setInstalments] = useState('1');
  const [reason, setReason] = useState('');
  const [saving, setSaving] = useState(false);

  const perMonth = Number(principal) > 0 && Number(instalments) > 0
    ? Number(principal) / Number(instalments) : 0;

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setSaving(true);
    try {
      await loanService.request({
        kind, principal: Number(principal),
        instalment_count: Number(instalments), reason: reason.trim(),
      });
      onDone('Requested. HR will decide it.');
    } catch (err) {
      onError(detailOf(err, 'That request could not be filed.'));
      onClose();
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="adv-overlay" role="dialog" aria-modal="true" aria-label="Request an advance">
      <form className="adv-dialog" onSubmit={submit}>
        <header><h2>Request an advance</h2><button type="button" onClick={onClose} aria-label="Close"><FiX /></button></header>

        <fieldset className="adv-choices">
          <legend>What you need</legend>
          {(['advance', 'loan'] as LoanKind[]).map((k) => (
            <label key={k} className={kind === k ? 'is-chosen' : ''}>
              <input type="radio" name="kind" checked={kind === k} onChange={() => setKind(k)} />
              <span>{LOAN_KIND_LABEL[k]}</span>
            </label>
          ))}
        </fieldset>

        <div className="adv-grid">
          <label>Amount (PKR)
            <input type="number" required min="1" value={principal} onChange={(e) => setPrincipal(e.target.value)} />
          </label>
          <label>Recovered over
            <input type="number" required min="1" max="36" value={instalments}
              onChange={(e) => setInstalments(e.target.value)} />
            <small>Months. An advance is usually one.</small>
          </label>
        </div>

        {perMonth > 0 && (
          <p className="adv-preview">
            About <b>{money(Math.round(perMonth))}</b> a month.
            {' '}Any rounding lands on the first instalment, so the last is never a stray rupee.
          </p>
        )}

        <label className="adv-field">Reason
          <textarea rows={3} required minLength={5} maxLength={2000} value={reason}
            onChange={(e) => setReason(e.target.value)} placeholder="What the money is for." />
        </label>

        <footer>
          <button type="button" onClick={onClose}>Cancel</button>
          <button type="submit" className="adv-btn-primary" disabled={saving}>{saving ? 'Filing…' : 'Send request'}</button>
        </footer>
      </form>
    </div>
  );
};

export default Advances;
