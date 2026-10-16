import { useCallback, useEffect, useState } from 'react';
import {
  FiAlertTriangle, FiCheck, FiCheckCircle, FiEdit2, FiLock, FiRefreshCw, FiRotateCcw,
} from 'react-icons/fi';
import {
  settlementService, SETTLEMENT_STATUS_LABEL,
  type Settlement, type SettlementEdit, type SettlementLine,
} from '../../services/settlementService';
import './SettlementPanel.css';

const money = (value: number) =>
  `PKR ${value.toLocaleString('en-PK', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

const detailOf = (err: unknown, fallback: string): string => {
  const detail = (err as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail;
  const message = (err as { message?: string })?.message;
  if (typeof detail === 'string' && detail) return detail;
  return message || fallback;
};

/**
 * Which editable field each rendered line maps to. The server renders the
 * lines; this is only needed so an override control can sit on the right row.
 */
const FIELD_FOR_LINE: Record<string, keyof SettlementEdit> = {
  final_month: 'final_month_amount',
  leave_encashment: 'leave_encashment_amount',
  gratuity: 'gratuity_amount',
  reimbursements: 'pending_reimbursements',
  other_earning: 'other_earning',
  notice_shortfall: 'notice_shortfall_amount',
  loan_outstanding: 'loan_outstanding',
  asset_recovery: 'asset_recovery',
  tax: 'tax_deduction',
  other_deduction: 'other_deduction',
};

/** `manual_fields` names the underlying column, not the line key. */
const isOverridden = (line: SettlementLine, manual: string[]) => {
  const field = FIELD_FOR_LINE[line.key];
  return Boolean(field && manual.includes(field));
};

const POLICY_LABEL: Record<string, string> = {
  gratuity_enabled: 'Gratuity',
  leave_encashment_basis: 'Leave encashed at',
  recover_notice_shortfall: 'Notice shortfall',
};

const policyText = (value: unknown): string => {
  if (typeof value === 'boolean') return value ? 'applied' : 'not applied';
  return String(value).replace(/_/g, ' ');
};

const SettlementPanel = ({ resignationId, status, onSettlementChange }: {
  resignationId: number;
  /** The resignation's own status, so the panel can say why it is early. */
  status: string;
  /** Lets the parent disable Complete until this reads approved. */
  onSettlementChange?: (settlement: Settlement | null) => void;
}) => {
  const [settlement, setSettlement] = useState<Settlement | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<SettlementEdit>({});
  const [notes, setNotes] = useState('');
  const [askingNotes, setAskingNotes] = useState(false);

  const apply = useCallback((next: Settlement | null) => {
    setSettlement(next);
    setNotes(next?.notes || '');
    onSettlementChange?.(next);
  }, [onSettlementChange]);

  const load = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      apply(await settlementService.forResignation(resignationId));
    } catch (err) {
      setError(detailOf(err, 'Could not read the settlement.'));
    } finally {
      setLoading(false);
    }
  }, [resignationId, apply]);

  useEffect(() => { void load(); }, [load]);

  const run = async (fn: () => Promise<Settlement>, onDone?: () => void) => {
    setBusy(true);
    setError('');
    try {
      apply(await fn());
      onDone?.();
    } catch (err) {
      setError(detailOf(err, 'That did not work.'));
    } finally {
      setBusy(false);
    }
  };

  const approve = async () => {
    if (!settlement) return;
    // A negative net is legitimate — someone leaving without notice can owe
    // the company — but the server refuses until notes explain the recovery.
    // Ask here rather than letting them hit the wall.
    if (settlement.net_payable < 0 && !notes.trim()) {
      setAskingNotes(true);
      return;
    }
    if (notes.trim() !== (settlement.notes || '')) {
      await run(() => settlementService.update(settlement.id, { notes: notes.trim() }));
    }
    await run(() => settlementService.approve(settlement.id), () => setAskingNotes(false));
  };

  if (loading) return <section className="st-panel st-loading">Loading settlement…</section>;

  if (!settlement) {
    return (
      <section className="st-panel st-none">
        <div>
          <h4>Final settlement</h4>
          <p>
            Nothing drafted yet. The exit cannot be completed until a settlement
            is drafted and approved.
          </p>
        </div>
        <button type="button" className="st-btn st-btn-primary" disabled={busy}
          onClick={() => void run(() => settlementService.compute(resignationId))}>
          <FiRefreshCw /> {busy ? 'Computing…' : 'Compute draft'}
        </button>
      </section>
    );
  }

  const isDraft = settlement.status === 'draft';
  const isPaid = settlement.status === 'paid';
  const negative = settlement.net_payable < 0;

  const renderLines = (lines: SettlementLine[]) => lines.map((line) => {
    const field = FIELD_FOR_LINE[line.key];
    const overridden = isOverridden(line, settlement.manual_fields);
    return (
      <li key={line.key} className={overridden ? 'is-manual' : ''}>
        <span className="st-line-label">
          {line.label}
          {overridden && <FiEdit2 title="Overridden by HR — a recompute leaves this alone" />}
          {line.detail && <small>{line.detail}</small>}
        </span>
        {editing && field ? (
          <input type="number" min="0" step="0.01"
            value={(draft[field] as number | undefined) ?? line.amount}
            onChange={(e) => setDraft({ ...draft, [field]: Number(e.target.value) })} />
        ) : (
          <span className="st-line-amount">{money(line.amount)}</span>
        )}
      </li>
    );
  });

  return (
    <section className="st-panel">
      <header className="st-head">
        <div>
          <h4>Final settlement</h4>
          <p>
            {settlement.employee_name} · last working day{' '}
            {new Date(settlement.last_working_day).toLocaleDateString('en-GB', {
              day: 'numeric', month: 'short', year: 'numeric',
            })}
          </p>
        </div>
        <span className={`st-status st-status-${settlement.status}`}>
          {SETTLEMENT_STATUS_LABEL[settlement.status]}
        </span>
      </header>

      {error && <p className="st-error"><FiAlertTriangle /> {error}</p>}

      {status !== 'cleared' && isDraft && (
        <p className="st-hint">
          <FiLock /> Clearance is not finished. You can draft and adjust the figures
          now; the exit itself cannot be completed until clearance is done and this
          settlement is approved.
        </p>
      )}

      <div className="st-columns">
        <div>
          <h5>Earnings</h5>
          <ul className="st-lines">{renderLines(settlement.earnings)}</ul>
          <p className="st-subtotal"><span>Gross payable</span><b>{money(settlement.gross_payable)}</b></p>
        </div>
        <div>
          <h5>Deductions</h5>
          {settlement.deductions.length
            ? <ul className="st-lines">{renderLines(settlement.deductions)}</ul>
            : <p className="st-nothing">None.</p>}
          <p className="st-subtotal"><span>Total deductions</span><b>{money(settlement.total_deductions)}</b></p>
        </div>
      </div>

      <p className={`st-net${negative ? ' is-negative' : ''}`}>
        <span>{negative ? 'Recoverable from the employee' : 'Net payable'}</span>
        <b>{money(Math.abs(settlement.net_payable))}</b>
      </p>

      {negative && (
        <p className="st-warn">
          <FiAlertTriangle /> This settlement owes the company rather than the
          employee. Approval needs a note saying how the balance will be recovered.
        </p>
      )}

      {(askingNotes || (isDraft && negative)) && (
        <label className="st-field">
          <span>How the balance will be recovered</span>
          <textarea rows={2} value={notes} onChange={(e) => setNotes(e.target.value)}
            placeholder="Required before this can be approved." />
        </label>
      )}

      {Object.keys(settlement.policy).length > 0 && (
        <p className="st-policy">
          {Object.entries(settlement.policy).map(([key, value]) => (
            <span key={key}>{POLICY_LABEL[key] || key.replace(/_/g, ' ')}: {policyText(value)}</span>
          ))}
        </p>
      )}

      <footer className="st-actions">
        {isDraft && (
          <>
            <button type="button" className="st-btn" disabled={busy}
              onClick={() => void run(() => settlementService.compute(resignationId))}>
              <FiRefreshCw /> Recompute
            </button>
            {editing ? (
              <>
                <button type="button" className="st-btn" onClick={() => { setEditing(false); setDraft({}); }}>Cancel</button>
                <button type="button" className="st-btn st-btn-primary" disabled={busy}
                  onClick={() => void run(
                    () => settlementService.update(settlement.id, draft),
                    () => { setEditing(false); setDraft({}); },
                  )}>
                  <FiCheck /> Save overrides
                </button>
              </>
            ) : (
              <button type="button" className="st-btn" onClick={() => setEditing(true)}>
                <FiEdit2 /> Override a figure
              </button>
            )}
            <button type="button" className="st-btn st-btn-primary" disabled={busy || editing}
              onClick={() => void approve()}>
              <FiCheckCircle /> Approve
            </button>
          </>
        )}

        {settlement.status === 'approved' && (
          <>
            <button type="button" className="st-btn" disabled={busy}
              onClick={() => void run(() => settlementService.reopen(settlement.id))}>
              <FiRotateCcw /> Reopen
            </button>
            <button type="button" className="st-btn st-btn-primary" disabled={busy}
              onClick={() => void run(() => settlementService.markPaid(settlement.id, {}))}>
              Mark paid
            </button>
          </>
        )}

        {isPaid && (
          <p className="st-paid">
            <FiCheckCircle /> Paid
            {settlement.payment_reference ? ` · ref ${settlement.payment_reference}` : ''}
            {settlement.paid_at ? ` · ${new Date(settlement.paid_at).toLocaleDateString('en-GB')}` : ''}
          </p>
        )}
      </footer>
    </section>
  );
};

export default SettlementPanel;
