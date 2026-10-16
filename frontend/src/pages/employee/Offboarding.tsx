import { useCallback, useEffect, useState, type FormEvent } from 'react';
import {
  FiAlertTriangle, FiCheck, FiClock, FiExternalLink, FiInbox, FiLogOut, FiX,
} from 'react-icons/fi';
import { Link } from 'react-router-dom';
import { ROUTES } from '../../config/constants';
import {
  lifecycleService, isResignationOpen, isAdminStage, RESIGNATION_STATUS_LABEL,
  type ClearanceItem, type Resignation, type ResignationStatus, type TaskStatus,
} from '../../services/lifecycleService';
import SettlementPanel from './SettlementPanel';
import type { Settlement } from '../../services/settlementService';
import './Offboarding.css';

const pad2 = (n: number) => String(n).padStart(2, '0');
const isoDate = (d: Date) => `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}`;

/** The notice period is a policy floor, so the picker starts there. */
const NOTICE_DAYS = 30;
const defaultLastDay = () => {
  const d = new Date();
  d.setDate(d.getDate() + NOTICE_DAYS);
  return isoDate(d);
};

const longDate = (value?: string | null) => {
  if (!value) return '—';
  const d = new Date(value);
  return Number.isNaN(d.getTime())
    ? value
    : d.toLocaleDateString('en-GB', { day: 'numeric', month: 'long', year: 'numeric' });
};

const STATUS_TONE: Record<ResignationStatus, string> = {
  submitted: 'wait', manager_approved: 'wait', ceo_approved: 'wait',
  cleared: 'ok', completed: 'ok', rejected: 'bad', withdrawn: 'muted',
};

/**
 * Resignations, their three signatures, and HR clearance.
 *
 * Who may act is never worked out here: every resignation carries `can_decide`
 * for this viewer and `next_approver` for everyone else, and the server has
 * already refused wrong-stage, wrong-role and self-approval.
 */
const Offboarding = () => {
  const [mine, setMine] = useState<Resignation | null>(null);
  const [queue, setQueue] = useState<Resignation[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [notice, setNotice] = useState<{ tone: 'ok' | 'bad'; text: string } | null>(null);

  const load = useCallback(async () => {
    setIsLoading(true);
    const [mineResult, allResult] = await Promise.allSettled([
      lifecycleService.myResignation(),
      lifecycleService.resignations(),
    ]);
    if (mineResult.status === 'fulfilled') setMine(mineResult.value);
    // The list is already scoped by the server; anyone with nothing to see
    // gets an empty one, so there is no role check to make here.
    if (allResult.status === 'fulfilled') setQueue(allResult.value);
    setIsLoading(false);
  }, []);

  useEffect(() => { void load(); }, [load]);

  const others = queue.filter((r) => !mine || r.id !== mine.id);

  return (
    <div className="off-page">
      <header className="off-head">
        <div>
          <h1>Resignation</h1>
          <p>Give notice, and follow it through its approvals.</p>
        </div>
      </header>

      {notice && (
        <div className={`off-notice off-notice-${notice.tone}`} role="status">
          <span>{notice.text}</span>
          <button onClick={() => setNotice(null)} aria-label="Dismiss"><FiX size={15} /></button>
        </div>
      )}

      {isLoading ? (
        <div className="off-card off-empty">Loading…</div>
      ) : (
        <>
          {mine ? (
            <MyResignation
              resignation={mine}
              onChanged={(r, text) => { setMine(r); setNotice({ tone: 'ok', text }); void load(); }}
              onError={(text) => setNotice({ tone: 'bad', text })}
            />
          ) : (
            <ResignForm
              onDone={(text) => { setNotice({ tone: 'ok', text }); void load(); }}
              onError={(text) => setNotice({ tone: 'bad', text })}
            />
          )}

          {others.length > 0 && (
            <section className="off-section">
              <h2><FiInbox size={16} /> Resignations you can see</h2>
              {others.map((r) => (
                <ResignationCard
                  key={r.id}
                  resignation={r}
                  onChanged={(text) => { setNotice({ tone: 'ok', text }); void load(); }}
                  onError={(text) => setNotice({ tone: 'bad', text })}
                />
              ))}
            </section>
          )}
        </>
      )}
    </div>
  );
};

/* ------------------------------ Resigning ------------------------------ */

const ResignForm = ({ onDone, onError }: {
  onDone: (text: string) => void; onError: (text: string) => void;
}) => {
  const [reason, setReason] = useState('');
  const [lastDay, setLastDay] = useState(defaultLastDay());
  const [isSaving, setIsSaving] = useState(false);
  const [confirming, setConfirming] = useState(false);

  const earlier = lastDay < defaultLastDay();

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (reason.trim().length < 3) return;
    setConfirming(true);
  };

  const send = async () => {
    setIsSaving(true);
    try {
      await lifecycleService.resign({
        reason: reason.trim(),
        proposed_last_working_day: lastDay,
      });
      setConfirming(false);
      onDone('Your resignation has been submitted.');
    } catch (err: any) {
      setConfirming(false);
      onError(err?.message || 'Your resignation could not be submitted.');
    } finally {
      setIsSaving(false);
    }
  };

  return (
    <>
      <form className="off-card off-form" onSubmit={submit}>
        <h2>Give notice</h2>
        <p className="off-meta">
          This starts a three-stage approval: your line manager, then an administrator,
          then HR clearance.
        </p>

        <label className="off-field">
          <span>Reason</span>
          <textarea rows={4} value={reason} onChange={(e) => setReason(e.target.value)}
            placeholder="Why you are leaving" />
        </label>

        <label className="off-field">
          <span>Proposed last working day</span>
          <input type="date" value={lastDay} onChange={(e) => setLastDay(e.target.value)} />
          {/* The notice period is a floor, not a rule the form enforces: an
              earlier date is allowed, but it is a request an administrator has
              to agree, and saying so is fairer than a silent rejection. */}
          <small className={earlier ? 'off-flag' : ''}>
            {earlier
              ? `That is inside the ${NOTICE_DAYS}-day notice period. You may ask for it, but an administrator has to agree the date.`
              : `Defaults to ${NOTICE_DAYS} days from today, the standard notice period.`}
          </small>
        </label>

        <div className="off-actions">
          <button type="submit" className="off-btn off-btn-danger" disabled={reason.trim().length < 3}>
            <FiLogOut size={15} /> Submit resignation
          </button>
        </div>
      </form>

      {confirming && (
        <div className="off-backdrop" onMouseDown={(e) => { if (e.target === e.currentTarget) setConfirming(false); }}>
          <div className="off-dialog" role="dialog" aria-modal="true" aria-labelledby="off-confirm-title">
            <h2 id="off-confirm-title">Submit your resignation?</h2>
            <p>
              Your line manager will be asked to approve it, then an administrator, then HR.
              Your proposed last working day is <strong>{longDate(lastDay)}</strong>.
            </p>
            <p className="off-meta">You can withdraw it while it is still open.</p>
            <div className="off-actions">
              <button className="off-btn off-btn-ghost" onClick={() => setConfirming(false)}>Cancel</button>
              <button className="off-btn off-btn-danger" disabled={isSaving} onClick={send}>
                {isSaving ? 'Submitting…' : 'Submit resignation'}
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
};

/* ---------------------------- My resignation --------------------------- */

const MyResignation = ({ resignation, onChanged, onError }: {
  resignation: Resignation;
  onChanged: (r: Resignation, text: string) => void;
  onError: (text: string) => void;
}) => {
  const [isSaving, setIsSaving] = useState(false);

  const withdraw = async () => {
    setIsSaving(true);
    try {
      onChanged(await lifecycleService.withdraw(resignation.id), 'Your resignation has been withdrawn.');
    } catch (err: any) {
      onError(err?.message || 'It could not be withdrawn.');
    } finally {
      setIsSaving(false);
    }
  };

  return (
    <section className="off-card off-mine">
      <div className="off-mine-head">
        <div>
          <h2>Your resignation</h2>
          <p className="off-meta">Submitted {longDate(resignation.submitted_at)}</p>
        </div>
        <span className={`off-status off-status-${STATUS_TONE[resignation.status]}`}>
          {RESIGNATION_STATUS_LABEL[resignation.status]}
        </span>
      </div>

      <dl className="off-facts">
        <div>
          <dt>Last working day</dt>
          <dd>
            {longDate(resignation.agreed_last_working_day || resignation.proposed_last_working_day)}
            {resignation.agreed_last_working_day ? <small>Agreed</small> : <small>Proposed</small>}
          </dd>
        </div>
        {typeof resignation.days_remaining === 'number' && (
          <div><dt>Days remaining</dt><dd>{resignation.days_remaining}</dd></div>
        )}
        {resignation.next_approver && (
          <div><dt>Waiting on</dt><dd>{resignation.next_approver}</dd></div>
        )}
      </dl>

      <p className="off-reason">{resignation.reason}</p>

      {resignation.rejection_reason && (
        <p className="off-rejected"><FiAlertTriangle size={14} /> {resignation.rejection_reason}</p>
      )}

      {resignation.clearance_items.length > 0 && (
        <ClearanceList items={resignation.clearance_items} readOnly />
      )}

      {isResignationOpen(resignation.status) && (
        <div className="off-actions">
          <button className="off-btn off-btn-ghost" disabled={isSaving} onClick={withdraw}>
            Withdraw resignation
          </button>
        </div>
      )}
    </section>
  );
};

/* ------------------------- Approving and clearing ---------------------- */

const ResignationCard = ({ resignation, onChanged, onError }: {
  resignation: Resignation;
  onChanged: (text: string) => void;
  onError: (text: string) => void;
}) => {
  const [note, setNote] = useState('');
  const [agreedDay, setAgreedDay] = useState(
    resignation.agreed_last_working_day || resignation.proposed_last_working_day,
  );
  const [rejecting, setRejecting] = useState(false);
  const [reason, setReason] = useState('');
  const [completing, setCompleting] = useState(false);
  const [isSaving, setIsSaving] = useState(false);
  // The exit cannot be completed until a settlement is approved, so the
  // panel reports upward rather than the button guessing.
  const [settlement, setSettlement] = useState<Settlement | null>(null);

  // Only an administrator sets the leaving date. Offering the field at the
  // manager stage would imply an authority they do not have, and the server
  // ignores it there anyway.
  const canSetDate = isAdminStage(resignation.status);
  const outstanding = resignation.clearance_outstanding;

  const decide = async (approved: boolean) => {
    setIsSaving(true);
    try {
      await lifecycleService.decide(resignation.id, {
        approved,
        ...(note.trim() ? { note: note.trim() } : {}),
        ...(approved && canSetDate ? { agreed_last_working_day: agreedDay } : {}),
        ...(approved ? {} : { rejection_reason: reason.trim() }),
      });
      setRejecting(false);
      setReason('');
      onChanged(approved ? 'Approved.' : 'Rejected.');
    } catch (err: any) {
      onError(err?.message || 'That decision could not be recorded.');
    } finally {
      setIsSaving(false);
    }
  };

  const complete = async () => {
    setIsSaving(true);
    try {
      await lifecycleService.completeExit(resignation.id);
      setCompleting(false);
      onChanged('Exit completed. The NOC has been issued and the account closed.');
    } catch (err: any) {
      onError(err?.message || 'The exit could not be completed.');
    } finally {
      setIsSaving(false);
    }
  };

  return (
    <article className="off-card off-item">
      <div className="off-mine-head">
        <div>
          <h3>{resignation.employee_name || `Employee #${resignation.employee_id}`}</h3>
          <p className="off-meta">
            Submitted {longDate(resignation.submitted_at)} · last day{' '}
            {longDate(resignation.agreed_last_working_day || resignation.proposed_last_working_day)}
          </p>
        </div>
        <span className={`off-status off-status-${STATUS_TONE[resignation.status]}`}>
          {RESIGNATION_STATUS_LABEL[resignation.status]}
        </span>
      </div>

      <p className="off-reason">{resignation.reason}</p>

      {resignation.clearance_items.length > 0 && (
        <ClearanceList
          items={resignation.clearance_items}
          onUpdate={async (itemId, body) => {
            try {
              await lifecycleService.updateClearance(itemId, body);
              onChanged('Clearance updated.');
            } catch (err: any) {
              onError(err?.message || 'That clearance item could not be updated.');
            }
          }}
        />
      )}

      <SettlementPanel
        resignationId={resignation.id}
        status={resignation.status}
        onSettlementChange={setSettlement}
      />

      {resignation.can_decide ? (
        <div className="off-decide">
          {canSetDate && (
            <label className="off-field">
              <span>Agreed last working day</span>
              <input type="date" value={agreedDay} onChange={(e) => setAgreedDay(e.target.value)} />
              <small>Only an administrator sets this. It becomes the leaving date.</small>
            </label>
          )}

          <label className="off-field">
            <span>Note (optional)</span>
            <input value={note} onChange={(e) => setNote(e.target.value)} placeholder="Recorded with your decision" />
          </label>

          {/* The server refuses with a 400 naming the count, but an approver
              should see why before clicking, not after. */}
          {resignation.status === 'ceo_approved' && outstanding > 0 && (
            <p className="off-blocked">
              <FiClock size={14} /> {outstanding} clearance item{outstanding === 1 ? '' : 's'} still
              outstanding. Clear them before signing this off.
            </p>
          )}

          <div className="off-actions">
            <button className="off-btn off-btn-danger-ghost" disabled={isSaving}
              onClick={() => setRejecting(true)}>
              <FiX size={14} /> Reject
            </button>
            <button className="off-btn off-btn-primary"
              disabled={isSaving || (resignation.status === 'ceo_approved' && outstanding > 0)}
              onClick={() => decide(true)}>
              <FiCheck size={14} /> Approve
            </button>
          </div>
        </div>
      ) : (
        <p className="off-meta">
          {resignation.next_approver
            ? `Waiting on ${resignation.next_approver}`
            : 'You cannot act on this resignation.'}
        </p>
      )}

      {resignation.status === 'cleared' && (
        <div className="off-actions off-complete">
          {/* The server returns 400 with "No final settlement has been
              drafted" or "still a draft". Say so before the click, not after. */}
          {settlement?.status !== 'approved' && settlement?.status !== 'paid' && (
            <p className="off-blocked">
              <FiClock size={14} /> {settlement
                ? 'The final settlement is still a draft. Approve it above to complete this exit.'
                : 'No final settlement has been drafted. Draft and approve one above to complete this exit.'}
            </p>
          )}
          <button className="off-btn off-btn-danger"
            disabled={settlement?.status !== 'approved' && settlement?.status !== 'paid'}
            onClick={() => setCompleting(true)}>
            Complete exit
          </button>
        </div>
      )}

      {rejecting && (
        <div className="off-backdrop" onMouseDown={(e) => { if (e.target === e.currentTarget) setRejecting(false); }}>
          <div className="off-dialog" role="dialog" aria-modal="true">
            <h2>Reject this resignation</h2>
            <label className="off-field">
              <span>Reason (required)</span>
              <textarea rows={3} value={reason} autoFocus onChange={(e) => setReason(e.target.value)} />
            </label>
            <div className="off-actions">
              <button className="off-btn off-btn-ghost" onClick={() => setRejecting(false)}>Cancel</button>
              <button className="off-btn off-btn-danger" disabled={reason.trim().length < 3 || isSaving}
                onClick={() => decide(false)}>
                Reject
              </button>
            </div>
          </div>
        </div>
      )}

      {completing && (
        <div className="off-backdrop" onMouseDown={(e) => { if (e.target === e.currentTarget) setCompleting(false); }}>
          <div className="off-dialog" role="dialog" aria-modal="true">
            <h2><FiAlertTriangle /> Complete this exit?</h2>
            {/* Named plainly, because none of it can be undone. */}
            <p>
              This issues the NOC for{' '}
              <strong>{resignation.employee_name || `employee #${resignation.employee_id}`}</strong>,
              emails them the exit pack, and <strong>deactivates their account</strong> so they can
              no longer sign in.
            </p>
            <p className="off-meta">There is no undo.</p>
            <div className="off-actions">
              <button className="off-btn off-btn-ghost" onClick={() => setCompleting(false)}>Cancel</button>
              <button className="off-btn off-btn-danger" disabled={isSaving} onClick={complete}>
                {isSaving ? 'Completing…' : 'Complete exit and close the account'}
              </button>
            </div>
          </div>
        </div>
      )}
    </article>
  );
};

/* ------------------------------ Clearance ------------------------------ */

const ClearanceList = ({ items, readOnly, onUpdate }: {
  items: ClearanceItem[];
  readOnly?: boolean;
  onUpdate?: (id: number, body: { status: TaskStatus; notes?: string; reference?: string }) => void;
}) => {
  // Grouped by category — assets, handover, access, finance — because that is
  // how the list is worked through, not one flat run of nine.
  const grouped = items.reduce<Record<string, ClearanceItem[]>>((acc, item) => {
    acc[item.category] = [...(acc[item.category] ?? []), item];
    return acc;
  }, {});

  return (
    <div className="off-clearance">
      <h4>Clearance</h4>
      {Object.entries(grouped).map(([category, list]) => (
        <div key={category} className="off-clearance-group">
          <h5>{category}</h5>
          {list.map((item) => (
            <ClearanceRow key={item.id} item={item} readOnly={readOnly} onUpdate={onUpdate} />
          ))}
        </div>
      ))}
    </div>
  );
};

const ClearanceRow = ({ item, readOnly, onUpdate }: {
  item: ClearanceItem;
  readOnly?: boolean;
  onUpdate?: (id: number, body: { status: TaskStatus; notes?: string; reference?: string }) => void;
}) => {
  const [reference, setReference] = useState(item.reference || '');
  const rawId = Number(item.reference);
  const assetId = item.category === 'assets' && Number.isInteger(rawId) && rawId > 0 ? rawId : null;
  const done = item.status === 'completed';
  const waived = item.status === 'waived';

  return (
    <div className={`off-clearance-row${done ? ' is-done' : ''}${waived ? ' is-waived' : ''}`}>
      <span className="off-tick" aria-hidden="true">
        {done ? <FiCheck size={13} /> : waived ? '—' : <FiClock size={12} />}
      </span>
      <span className="off-clearance-label">
        {item.label}
        {/* Clearance rows for equipment carry the item id in `reference`.
            The register refuses to clear while anything is still issued, so
            link to the row rather than let HR tick the box and hit a 400
            they cannot act on from here. */}
        {assetId !== null && (
          <Link className="off-clearance-link" to={ROUTES.INVENTORY_ITEM(assetId)}>
            <FiExternalLink size={11} /> Close this out
          </Link>
        )}
      </span>

      {readOnly ? (
        <span className="off-clearance-state">
          {done ? 'Cleared' : waived ? 'Waived' : 'Outstanding'}
        </span>
      ) : (
        <>
          {/* Free text today; where an asset id goes once there is a register. */}
          <input
            className="off-clearance-ref"
            value={reference}
            placeholder="Reference"
            onChange={(e) => setReference(e.target.value)}
          />
          <select
            value={item.status}
            onChange={(e) => onUpdate?.(item.id, {
              status: e.target.value as TaskStatus,
              reference: reference.trim() || undefined,
            })}
          >
            <option value="pending">Outstanding</option>
            <option value="completed">Cleared</option>
            <option value="waived">Waived</option>
          </select>
        </>
      )}
    </div>
  );
};

export default Offboarding;
