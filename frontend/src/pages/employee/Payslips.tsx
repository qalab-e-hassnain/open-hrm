import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import {
  FiAlertCircle, FiAlertTriangle, FiCheck, FiChevronLeft, FiChevronRight,
  FiDownload, FiEye, FiEyeOff, FiMaximize2, FiPrinter, FiRefreshCw, FiX,
} from 'react-icons/fi';
import { useAuthStore } from '../../store/authStore';
import { employeeService } from '../../services/employeeService';
import {
  payslipService, readApiError, MONTH_LABELS, DISPUTE_CATEGORIES,
  type PayslipDocument as PayslipDoc, type PayslipSummary, type PayslipYTD,
  type DisputeCategory, type PayslipDispute,
} from '../../services/payslipService';
import { ROUTES } from '../../config/constants';
import { isPrivileged as hasPrivilegedAccess } from '../../utils/permissions';
import PayslipDocument from './PayslipDocument';
import './Payslips.css';

const STATUS_META: Record<string, { label: string; tone: string; hint?: string }> = {
  draft: { label: 'Draft', tone: 'neutral', hint: 'Not final — figures may still change.' },
  processing: { label: 'Processing', tone: 'info', hint: 'Payroll is being processed. The final payslip will be available soon.' },
  processed: { label: 'Finalized', tone: 'success' },
  paid: { label: 'Paid', tone: 'success' },
  corrected: { label: 'Superseded', tone: 'warning', hint: 'A revised payslip has replaced this version.' },
  cancelled: { label: 'Cancelled', tone: 'error', hint: 'This payslip has been cancelled.' },
};

const DISPUTE_STATUS_META: Record<string, { label: string; tone: string }> = {
  open: { label: 'Open', tone: 'warning' },
  in_review: { label: 'In review', tone: 'info' },
  resolved: { label: 'Resolved', tone: 'success' },
  rejected: { label: 'Rejected', tone: 'error' },
};

const disputeCategoryLabel = (category: string) =>
  DISPUTE_CATEGORIES.find((c) => c.value === category)?.label || category;

const now = new Date();

const Payslips = () => {
  const { user } = useAuthStore();
  const isPrivileged = hasPrivilegedAccess(user);

  const [params, setParams] = useSearchParams();
  const printRef = useRef<HTMLDivElement>(null);

  // The period lives in the URL so a payslip can be linked to and survives a
  // reload — one of the few bits of page state worth persisting.
  const year = Number(params.get('year')) || now.getFullYear();
  const month = Number(params.get('month')) || now.getMonth() + 1;
  const viewedEmployee = Number(params.get('employee')) || undefined;

  const [doc, setDoc] = useState<PayslipDoc | null>(null);
  const [periods, setPeriods] = useState<PayslipSummary[]>([]);
  const [ytd, setYtd] = useState<PayslipYTD | null>(null);
  const [employees, setEmployees] = useState<{ id: string; name: string }[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState<{ tone: 'success' | 'error'; text: string } | null>(null);
  const [hideAmounts, setHideAmounts] = useState(false);
  const [showDispute, setShowDispute] = useState(false);
  const [busy, setBusy] = useState('');
  const [isExportingPdf, setIsExportingPdf] = useState(false);
  const [myDisputes, setMyDisputes] = useState<PayslipDispute[]>([]);

  // "Track the response here" (below) was a promise the UI made with nothing
  // behind it — raising a query filed it straight into the admin-only queue
  // with no way for the person who raised it to ever see it again short of
  // catching the one resolution notification. listDisputes() returns every
  // dispute in the company for a privileged caller (admin/HR), so this is
  // filtered to the logged-in user's own regardless of role — an admin
  // looking at someone else's payslip (?employee=) has the real admin queue
  // for that; this section is deliberately "my own queries", not "queries on
  // the payslip currently on screen".
  const loadMyDisputes = useCallback(async () => {
    try {
      const all = await payslipService.listDisputes();
      setMyDisputes(user ? all.filter((d) => String(d.employee_id) === user.id) : []);
    } catch {
      setMyDisputes([]);
    }
  }, [user]);

  useEffect(() => { loadMyDisputes(); }, [loadMyDisputes]);

  // Genuinely distinct from Print (window.print(), still on its own button
  // below): downloads an actual .pdf file in one click, no OS print dialog.
  // The single "Print / PDF" button here used to just be window.print() —
  // same bug already fixed on Tax Certificate.
  const handleSavePdf = async () => {
    if (!printRef.current || !doc) return;
    setIsExportingPdf(true);
    // The preview lives in a narrow side panel, and html2canvas captures the
    // DOM at its on-screen width. Scaling a ~500px-wide capture up to a
    // 194mm page magnified everything and pushed the payslip onto a second
    // sheet mid-table. Widen the document to a page for the capture, and
    // export the document itself rather than the scrollable surface holding
    // it, so the card chrome and canvas backdrop stay out of the file.
    const psDoc = printRef.current.querySelector<HTMLElement>('.ps-doc');
    if (!psDoc) {
      setIsExportingPdf(false);
      return;
    }
    psDoc.classList.add('is-exporting');
    // The exporter only relaxes clipping on the element it is given and its
    // descendants. Now that the document itself is the root, the scrollable
    // surface around it is an ancestor and would still clip the capture.
    printRef.current.classList.add('is-exporting-host');
    try {
      const { exportElementToPdf } = await import('../../utils/pdfExport');
      await exportElementToPdf(psDoc, `Payslip-${doc.employee.name}-${doc.period_label}.pdf`);
    } catch (err) {
      console.error('Payslip PDF export failed:', err);
    } finally {
      psDoc.classList.remove('is-exporting');
      printRef.current?.classList.remove('is-exporting-host');
      setIsExportingPdf(false);
    }
  };

  const setPeriod = (nextYear: number, nextMonth: number) => {
    const next = new URLSearchParams(params);
    next.set('year', String(nextYear));
    next.set('month', String(nextMonth));
    setParams(next, { replace: true });
  };

  const setEmployee = (id: string) => {
    const next = new URLSearchParams(params);
    if (id) next.set('employee', id); else next.delete('employee');
    setParams(next, { replace: true });
  };

  const load = useCallback(async () => {
    setIsLoading(true);
    setError('');
    setNotice(null);
    try {
      // The period list and YTD are useful even when the month itself has no
      // payslip, so they are fetched independently of the document.
      const [list, totals] = await Promise.all([
        payslipService.listPayslips(undefined, viewedEmployee).catch(() => []),
        payslipService.getYearToDate(year, month, viewedEmployee).catch(() => null),
      ]);
      setPeriods(list);
      setYtd(totals);
      setDoc(await payslipService.getPayslipForPeriod(year, month, viewedEmployee));
    } catch (err: unknown) {
      setDoc(null);
      // "There is no payslip for this month" is an answer, not a failure.
      // The endpoint says so with a 404, and treating that as an error put a
      // red message and a Try again button in front of a perfectly normal
      // empty month, which reads as a broken page.
      const status = (err as { response?: { status?: number } })?.response?.status;
      if (status === 404) {
        setError('');
      } else {
        setError(readApiError(err, 'We could not load this payslip. Please try again.'));
      }
    } finally {
      setIsLoading(false);
    }
  }, [year, month, viewedEmployee]);

  useEffect(() => { load(); }, [load]);

  useEffect(() => {
    if (!isPrivileged) return;
    employeeService.getDirectory()
      .then((list) => setEmployees(list.map((e) => ({ id: String(e.id), name: e.name }))))
      .catch(() => setEmployees([]));
  }, [isPrivileged]);

  const currency = doc?.currency || 'PKR';

  // Amounts can be blurred for viewing on a shared screen; the formatter is the
  // single place that happens so nothing leaks through a card that forgot.
  const money = useCallback((amount: number) => {
    if (hideAmounts) return '•••••';
    return amount.toLocaleString('en-PK', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }, [hideAmounts]);

  const years = useMemo(() => {
    const found = new Set(periods.map((p) => p.year));
    found.add(now.getFullYear());
    found.add(year);
    return [...found].sort((a, b) => b - a);
  }, [periods, year]);

  const latest = periods[0];
  const isLatest = latest && latest.year === year && latest.month === month;

  // Nobody has been paid for a month that has not happened. Stepping past the
  // current one walked into 2027 showing "no payslip", which reads as missing
  // data rather than a month that does not exist yet.
  const atCurrentMonth = year === now.getFullYear() && month === now.getMonth() + 1;
  const isFuture = (y: number, m: number) =>
    y > now.getFullYear() || (y === now.getFullYear() && m > now.getMonth() + 1);

  const step = (delta: number) => {
    const target = new Date(year, month - 1 + delta, 1);
    const nextYear = target.getFullYear();
    const nextMonth = target.getMonth() + 1;
    if (isFuture(nextYear, nextMonth)) return;
    setPeriod(nextYear, nextMonth);
  };

  const run = async (label: string, action: () => Promise<unknown>, success: string) => {
    setBusy(label);
    setNotice(null);
    try {
      await action();
      setNotice({ tone: 'success', text: success });
      await load();
    } catch (err: unknown) {
      setNotice({ tone: 'error', text: readApiError(err, 'That action could not be completed.') });
    } finally {
      setBusy('');
    }
  };

  const askReason = (title: string) => {
    const reason = window.prompt(title);
    return reason && reason.trim() ? reason.trim() : null;
  };

  const statusMeta = doc ? STATUS_META[doc.status] : null;

  return (
    <div className="payslips-page">
      <nav className="ps-breadcrumb" aria-label="Breadcrumb">
        <Link to={ROUTES.DASHBOARD}>Home</Link>
        <span aria-hidden="true">/</span>
        <span aria-current="page">Payslips</span>
      </nav>

      <header className="ps-page-header">
        <div>
          <h1 className="ps-page-title">Payslips</h1>
          <p>View and download your payslips and payment details.</p>
        </div>
        <div className="ps-header-actions">
          <button type="button" onClick={() => setHideAmounts((v) => !v)}
            aria-pressed={hideAmounts}>
            {hideAmounts ? <FiEye /> : <FiEyeOff />} {hideAmounts ? 'Show amounts' : 'Hide amounts'}
          </button>
        </div>
      </header>

      {/* ---- Filters ---- */}
      <section className="ps-filters" aria-label="Select payslip period">
        <label className="ps-field">
          <span>Year</span>
          <select value={year} onChange={(e) => setPeriod(Number(e.target.value), month)}>
            {years.map((y) => <option key={y} value={y}>{y}</option>)}
          </select>
        </label>

        <label className="ps-field">
          <span>Month</span>
          <select value={month} onChange={(e) => setPeriod(year, Number(e.target.value))}>
            {MONTH_LABELS.map((label, index) => (
              <option key={label} value={index + 1}>{label}</option>
            ))}
          </select>
        </label>

        {/* Only an admin may look at someone else's payslip, so employees never
            see this control at all. */}
        {isPrivileged && (
          <label className="ps-field">
            <span>Employee</span>
            <select value={viewedEmployee ? String(viewedEmployee) : ''}
              onChange={(e) => setEmployee(e.target.value)}>
              <option value="">{user?.name || 'Myself'}</option>
              {employees.map((e) => <option key={e.id} value={e.id}>{e.name}</option>)}
            </select>
          </label>
        )}

        <div className="ps-period-nav">
          <button type="button" onClick={() => step(-1)} aria-label="Previous month"><FiChevronLeft /></button>
          <button type="button" onClick={() => step(1)} aria-label="Next month"
            disabled={atCurrentMonth}
            title={atCurrentMonth ? 'This is the current month' : undefined}>
            <FiChevronRight />
          </button>
          <button type="button" className="ps-latest" disabled={!latest || isLatest}
            onClick={() => latest && setPeriod(latest.year, latest.month)}>
            Latest
          </button>
        </div>
      </section>

      {notice && (
        <div className={`ps-notice ps-notice-${notice.tone}`} role={notice.tone === 'error' ? 'alert' : 'status'}>
          {notice.tone === 'success' ? <FiCheck /> : <FiAlertCircle />}<span>{notice.text}</span>
        </div>
      )}

      {isLoading ? (
        <div className="ps-layout">
          <div className="ps-column">
            <div className="ps-skeleton ps-skeleton-summary" />
            <div className="ps-skeleton ps-skeleton-cards" />
            <div className="ps-skeleton ps-skeleton-ytd" />
          </div>
          <div className="ps-skeleton ps-skeleton-doc" />
        </div>
      ) : error || !doc ? (
        <div className="ps-empty" role="status">
          <FiAlertCircle />
          <h2>No payslip for {MONTH_LABELS[month - 1]} {year}</h2>
          <p>{error || 'No payslip has been generated for this period yet.'}</p>
          {/* Retrying only makes sense if something actually went wrong.
              Offering it for a month that simply has no payslip invites
              people to press it over and over to no effect. */}
          {error && (
            <button type="button" onClick={load}><FiRefreshCw /> Try again</button>
          )}
        </div>
      ) : (
        <div className="ps-layout">
          <div className="ps-column">
            {/* Never let a draft or superseded document read as final. */}
            {statusMeta?.hint && (
              <div className={`ps-callout ps-tone-${statusMeta.tone}`} role="status">
                <FiAlertTriangle /><span>{statusMeta.hint}</span>
              </div>
            )}

            {!doc.reconciliation.balanced && (
              <div className="ps-callout ps-tone-error" role="alert">
                <FiAlertTriangle />
                <span>
                  These figures do not reconcile: {money(doc.reconciliation.gross_earnings)} −{' '}
                  {money(doc.reconciliation.total_deductions)} ={' '}
                  {money(doc.reconciliation.expected_net)}, but net pay reads{' '}
                  {money(doc.reconciliation.net_salary)}. Please report this.
                </span>
              </div>
            )}

            {/* ---- Payment summary ---- */}
            <section className="ps-summary" aria-label="Payment summary">
              <div className="ps-summary-net">
                <span>Net pay</span>
                <strong>{currency} {money(doc.net_salary)}</strong>
                {statusMeta && (
                  <em className={`ps-badge ps-tone-${statusMeta.tone}`}>{statusMeta.label}</em>
                )}
              </div>
              {/* "Not set" and "Not recorded" on every payslip said nothing. The
                  bank account moves here, since it is the one payment fact
                  actually on file. */}
              <div className="ps-summary-item">
                <span>Pay period</span>
                <strong>{MONTH_LABELS[doc.month - 1]} {doc.year}</strong>
                <small>{doc.period_label}</small>
              </div>
              <div className="ps-summary-item">
                <span>Paid into</span>
                <strong>{doc.employee.bank_account_masked || 'No account on file'}</strong>
                {doc.payment_reference && <small>Ref {doc.payment_reference}</small>}
              </div>
            </section>

            {/* ---- Earnings & deductions ---- */}
            <div className="ps-breakdown">
              <section className="ps-card" aria-label="Earnings">
                <header><h2>Earnings</h2><span>Amount ({currency})</span></header>
                <ul>
                  {doc.earnings.map((line) => (
                    <li key={line.key}>
                      <span>
                        {line.label}
                        {line.taxable && <b className="ps-tag">Taxable</b>}
                        {(line.formula || line.note) && <small>{line.formula || line.note}</small>}
                      </span>
                      <data value={line.amount}>{money(line.amount)}</data>
                    </li>
                  ))}
                </ul>
                <footer className="ps-total-earn">
                  <span>Total earnings</span><data value={doc.gross_earnings}>{money(doc.gross_earnings)}</data>
                </footer>
              </section>

              <section className="ps-card" aria-label="Deductions">
                <header><h2>Deductions</h2><span>Amount ({currency})</span></header>
                <ul>
                  {/* Every standard deduction is sent, zeros included, so the
                      "no deductions" placeholder can no longer occur. */}
                  {doc.deductions.map((line) => (
                    <li key={line.key}>
                      <span>{line.label}{line.note && <small>{line.note}</small>}</span>
                      <data value={line.amount}>{money(line.amount)}</data>
                    </li>
                  ))}
                </ul>
                <footer className="ps-total-deduct">
                  <span>Total deductions</span><data value={doc.total_deductions}>{money(doc.total_deductions)}</data>
                </footer>
              </section>
            </div>

            {/* States the base income tax was charged on, so the tax line above
                can be checked rather than taken on trust. */}
            <section className="ps-taxable-row" aria-label="Taxable income">
              <span>
                Taxable income
                <small>Basic pay less unpaid leave — allowances, bonus, arrears, overtime and reimbursements are exempt</small>
              </span>
              <data value={doc.taxable_income}>{currency} {money(doc.taxable_income)}</data>
            </section>

            {/* ---- Year to date ---- */}
            {ytd && (
              <section className="ps-ytd" aria-label="Year to date">
                <header>
                  <h2>Year to date</h2>
                  <span>{ytd.year} · {ytd.months_counted} {ytd.months_counted === 1 ? 'month' : 'months'}</span>
                </header>
                <dl>
                  <div><dt>Gross earnings</dt><dd>{money(ytd.gross_earnings)}</dd></div>
                  <div><dt>Deductions</dt><dd>{money(ytd.total_deductions)}</dd></div>
                  <div><dt>Taxable income</dt><dd>{money(ytd.taxable_income)}</dd></div>
                  <div><dt>Tax paid</dt><dd>{money(ytd.tax_paid)}</dd></div>
                </dl>
                {ytd.previous_month_net != null && (
                  <p className="ps-ytd-compare">
                    Previous month net pay: {currency} {money(ytd.previous_month_net)}
                  </p>
                )}
              </section>
            )}

            {/* ---- Support ---- */}
            <section className="ps-support">
              <div>
                <strong>Something look wrong?</strong>
                <span>Raise a query with Payroll and track the response here.</span>
                {doc.open_dispute_count > 0 && (
                  <em>{doc.open_dispute_count} open {doc.open_dispute_count === 1 ? 'query' : 'queries'} on this payslip</em>
                )}
              </div>
              <button type="button" onClick={() => setShowDispute(true)}>Report an issue</button>
            </section>

            {/* ---- Your queries ----
                An admin viewing someone else's payslip (?employee=) has the
                real admin queue (Payroll → Payroll queries) for that person's
                disputes; this is deliberately just the logged-in user's own,
                so it's hidden while looking at another employee's payslip. */}
            {!viewedEmployee && myDisputes.length > 0 && (
              <section className="ps-my-disputes" aria-label="Your payslip queries">
                <header><h2>Your queries</h2><span>{myDisputes.length} total</span></header>
                <ul>
                  {myDisputes.map((dispute) => {
                    const meta = DISPUTE_STATUS_META[dispute.status] || { label: dispute.status, tone: 'neutral' };
                    return (
                      <li key={dispute.id}>
                        <div className="ps-my-dispute-head">
                          <span>
                            <strong>{disputeCategoryLabel(dispute.category)}</strong>
                            <small>
                              {dispute.period_label || 'Unknown period'} · {new Date(dispute.created_at).toLocaleDateString('en-GB', { day: '2-digit', month: 'short', year: 'numeric' })}
                            </small>
                          </span>
                          <em className={`ps-badge ps-tone-${meta.tone}`}>{meta.label}</em>
                        </div>
                        <p>{dispute.comment}</p>
                        {dispute.response && (
                          <div className="ps-my-dispute-response">
                            <strong>{dispute.responder_name ? `${dispute.responder_name} replied` : 'Payroll replied'}</strong>
                            <p>{dispute.response}</p>
                          </div>
                        )}
                      </li>
                    );
                  })}
                </ul>
              </section>
            )}

            {/* ---- Admin only ---- */}
            {isPrivileged && (
              <section className="ps-admin" aria-label="Payroll actions">
                <header><h2>Payroll actions</h2><span>Visible to administrators only</span></header>
                <div>
                  {doc.status === 'draft' && (
                    <button type="button" disabled={busy !== ''}
                      onClick={() => run('publish', () => payslipService.publish(doc.id), 'Payslip published.')}>
                      {busy === 'publish' ? 'Publishing…' : 'Publish payslip'}
                    </button>
                  )}
                  <button type="button" disabled={busy !== '' || doc.status === 'cancelled'}
                    onClick={() => {
                      const reason = askReason('Reason for correcting this payslip:');
                      if (reason) run('correct', () => payslipService.correct(doc.id, reason), 'Correction created as a new version.');
                    }}>
                    {busy === 'correct' ? 'Correcting…' : 'Correct payslip'}
                  </button>
                  <button type="button" className="ps-danger" disabled={busy !== '' || doc.status === 'paid'}
                    onClick={() => {
                      const reason = askReason('Reason for cancelling this payslip:');
                      if (reason) run('cancel', () => payslipService.cancel(doc.id, reason), 'Payslip cancelled.');
                    }}>
                    {busy === 'cancel' ? 'Cancelling…' : 'Cancel payslip'}
                  </button>
                </div>
              </section>
            )}
          </div>

          {/* ---- Document preview ---- */}
          <div className="ps-preview">
            <div className="ps-preview-toolbar">
              <strong>Payslip preview</strong>
              <button type="button" onClick={() => window.print()} title="Print this payslip">
                <FiPrinter /> Print
              </button>
              <button type="button" onClick={handleSavePdf} disabled={isExportingPdf} title="Download this payslip as a PDF file">
                <FiDownload /> {isExportingPdf ? 'Saving…' : 'Save PDF'}
              </button>
              <button type="button" onClick={() => printRef.current?.requestFullscreen?.()}
                title="View full screen">
                <FiMaximize2 /> Full screen
              </button>
            </div>
            <div className="ps-preview-surface" ref={printRef}>
              <PayslipDocument doc={doc} money={money} />
            </div>
          </div>
        </div>
      )}

      {showDispute && doc && (
        <DisputeDialog
          payslipId={doc.id}
          onClose={() => setShowDispute(false)}
          onDone={(text) => { setShowDispute(false); setNotice({ tone: 'success', text }); load(); loadMyDisputes(); }}
        />
      )}
    </div>
  );
};

/** Report-an-issue form. Kept in this file because it exists only for this page. */
const DisputeDialog = ({
  payslipId, onClose, onDone,
}: {
  payslipId: number;
  onClose: () => void;
  onDone: (message: string) => void;
}) => {
  const [category, setCategory] = useState<DisputeCategory>('incorrect_salary');
  const [comment, setComment] = useState('');
  const [attachment, setAttachment] = useState('');
  const [isSaving, setIsSaving] = useState(false);
  const [failure, setFailure] = useState('');

  const submit = async () => {
    if (comment.trim().length < 5) {
      setFailure('Describe the issue in at least a few words.');
      return;
    }
    setIsSaving(true);
    setFailure('');
    try {
      await payslipService.raiseDispute(payslipId, {
        category, comment: comment.trim(), attachment_url: attachment || undefined,
      });
      onDone('Your query has been sent to Payroll.');
    } catch (err: unknown) {
      setFailure(readApiError(err, 'We could not submit your query. Please try again.'));
    } finally {
      setIsSaving(false);
    }
  };

  return (
    <div className="ps-dialog-backdrop" role="presentation" onClick={onClose}>
      <div className="ps-dialog" role="dialog" aria-modal="true" aria-labelledby="ps-dialog-title"
        onClick={(event) => event.stopPropagation()}>
        <header>
          <h2 id="ps-dialog-title">Report an issue</h2>
          <button type="button" onClick={onClose} aria-label="Close"><FiX /></button>
        </header>

        <label className="ps-field">
          <span>What is the issue?</span>
          <select value={category} onChange={(e) => setCategory(e.target.value as DisputeCategory)}>
            {DISPUTE_CATEGORIES.map((c) => <option key={c.value} value={c.value}>{c.label}</option>)}
          </select>
        </label>

        <label className="ps-field">
          <span>Details</span>
          <textarea rows={4} value={comment} maxLength={1000}
            placeholder="Tell Payroll what looks wrong and what you expected."
            onChange={(e) => setComment(e.target.value)} />
        </label>

        <label className="ps-field">
          <span>Supporting document link <small>Optional</small></span>
          <input value={attachment} onChange={(e) => setAttachment(e.target.value)}
            placeholder="https://…" />
        </label>

        {failure && <p className="ps-dialog-error" role="alert">{failure}</p>}

        <footer>
          <button type="button" onClick={onClose}>Cancel</button>
          <button type="button" className="ps-primary" onClick={submit} disabled={isSaving}>
            {isSaving ? 'Sending…' : 'Submit to Payroll'}
          </button>
        </footer>
      </div>
    </div>
  );
};

export default Payslips;
