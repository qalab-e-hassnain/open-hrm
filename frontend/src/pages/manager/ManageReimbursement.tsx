import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  FiAlertTriangle, FiCheck, FiCheckCircle, FiChevronRight, FiClock, FiCreditCard,
  FiDownload, FiFile, FiPaperclip, FiRotateCcw, FiSearch, FiX,
  FiDollarSign,
} from 'react-icons/fi';
import { reimbursementService, isPaidOut, paidSummary, REIMBURSEMENT_TYPES } from '../../services/reimbursementService';
import { employeeService } from '../../services/employeeService';
import { useAuthStore } from '../../store/authStore';
import { type Employee, type ReimbursementRequest } from '../../types';
// The shared workspace styles are imported explicitly, not inherited from
// whichever page stylesheet happens to be in the bundle first.
import './ManageWorkspace.css';
import './ManageReimbursement.css';
import AttachmentViewer from '../../components/common/AttachmentViewer';

// The real chain from api/v1/reimbursements.approve_or_reject_reimbursement:
// a claim is submitted, an approver decides, and payroll pays it out on the next
// payslip. Mirrors the backend rather than inventing extra stages.
const APPROVAL_STEPS = [
  { key: 'pending', label: 'Approval', detail: 'Manager or admin reviews the claim' },
  { key: 'approved', label: 'Payout', detail: 'Swept into the month’s payslip, or handed over separately' },
  { key: 'paid', label: 'Recorded as paid', detail: 'The route is on the record, so nobody pays it twice' },
] as const;

type DateRangeMode = 'all' | 'this_month' | 'last_month' | 'custom';

const iso = (date: Date) => date.toISOString().split('T')[0];
const shortDate = (value?: string) =>
  value ? new Date(value).toLocaleDateString('en-GB', { day: '2-digit', month: 'short', year: 'numeric' }) : '—';
const money = (amount?: number) => `Rs. ${Number(amount || 0).toLocaleString('en-PK')}`;

const ManageReimbursement = () => {
  const { user } = useAuthStore();
  const [requests, setRequests] = useState<ReimbursementRequest[]>([]);
  const [allClaims, setAllClaims] = useState<ReimbursementRequest[]>([]);
  const [employees, setEmployees] = useState<Employee[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState('');
  const [processingId, setProcessingId] = useState<string | null>(null);
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [notes, setNotes] = useState<Record<string, string>>({});
  const [notice, setNotice] = useState<{ type: 'success' | 'error'; text: string } | null>(null);
  const [attachment, setAttachment] = useState<{ url: string; label: string } | null>(null);
  // Claims ticked for a bulk payout. The bulk route is all-or-nothing, so this
  // set either settles entirely or not at all.
  const [payingIds, setPayingIds] = useState<Set<string>>(new Set());
  const [payingNow, setPayingNow] = useState(false);

  const [search, setSearch] = useState('');
  const [typeFilter, setTypeFilter] = useState('all');
  const [departmentFilter, setDepartmentFilter] = useState('all');
  const [rangeMode, setRangeMode] = useState<DateRangeMode>('all');
  const [customRange, setCustomRange] = useState({ start: '', end: '' });

  const employeeById = useMemo(
    () => new Map(employees.map((employee) => [String(employee.id), employee])),
    [employees],
  );

  // `silent` skips the loading state so a background refresh after a decision
  // does not replace the page with the spinner.
  const loadData = useCallback(async (silent = false) => {
    if (!silent) setIsLoading(true);
    setError('');
    const [queueResult, historyResult, employeeResult] = await Promise.allSettled([
      reimbursementService.getReimbursementRequests(),
      reimbursementService.getReimbursementHistory(undefined, true),
      employeeService.getEmployees(),
    ]);

    if (queueResult.status === 'fulfilled' && Array.isArray(queueResult.value)) {
      setRequests(queueResult.value.filter((claim) => claim.status === 'pending'));
    } else {
      setRequests([]);
      setError('The claim queue could not be loaded. Check your connection and try again.');
    }
    if (historyResult.status === 'fulfilled' && Array.isArray(historyResult.value)) {
      setAllClaims(historyResult.value);
    }
    if (employeeResult.status === 'fulfilled') {
      setEmployees(employeeResult.value ?? []);
    }
    if (!silent) setIsLoading(false);
  }, []);

  useEffect(() => { void loadData(); }, [loadData]);

  const canDecide = (claim: ReimbursementRequest) =>
    claim.status === 'pending' && String(claim.employeeId) !== String(user?.id);

  // ---- Counters, all derived from loaded records ------------------------------

  const today = iso(new Date());

  const stats = useMemo(() => {
    const now = new Date();
    const month = now.getMonth();
    const year = now.getFullYear();

    const approvedThisMonth = allClaims.filter((claim) => {
      if (claim.status !== 'approved') return false;
      const reference = claim.approvedAt || claim.dateUsed;
      if (!reference) return false;
      const date = new Date(reference);
      return date.getMonth() === month && date.getFullYear() === year;
    });

    const pendingValue = requests.reduce((total, claim) => total + Number(claim.amount || 0), 0);
    const approvedValue = approvedThisMonth.reduce((total, claim) => total + Number(claim.amount || 0), 0);

    // A claim dated more than 60 days ago is worth a second look before it is
    // paid — an age flag computed from the expense date, nothing inferred.
    const staleCutoff = new Date();
    staleCutoff.setDate(staleCutoff.getDate() - 60);
    const stale = requests.filter((claim) => claim.dateUsed && new Date(claim.dateUsed) < staleCutoff).length;

    return {
      pending: requests.length,
      pendingValue,
      approvedThisMonth: approvedThisMonth.length,
      approvedValue,
      stale,
    };
  }, [allClaims, requests]);

  const staleIds = useMemo(() => {
    const cutoff = new Date();
    cutoff.setDate(cutoff.getDate() - 60);
    return new Set(
      requests.filter((claim) => claim.dateUsed && new Date(claim.dateUsed) < cutoff).map((claim) => String(claim.id)),
    );
  }, [requests]);

  // ---- Filtering --------------------------------------------------------------

  // Every type the API defines, not only the ones among the claims that
  // happen to be pending — that offered "equipment" alone, so the filter
  // could only find what was already on screen. Fixed, so no memo.
  const claimTypes = REIMBURSEMENT_TYPES.map((t) => t.value) as unknown as string[];
  const departments = useMemo(
    () => [...new Set(employees.map((employee) => employee.department).filter(Boolean))].sort() as string[],
    [employees],
  );

  const activeFilters = [
    search.trim() !== '',
    typeFilter !== 'all',
    departmentFilter !== 'all',
    rangeMode !== 'all',
  ].filter(Boolean).length;

  const resetFilters = () => {
    setSearch('');
    setTypeFilter('all');
    setDepartmentFilter('all');
    setRangeMode('all');
    setCustomRange({ start: '', end: '' });
  };

  // One predicate, used by both lists on this screen. The filters used to
  // apply to the pending table only, so narrowing to a department or a month
  // left "Approved, awaiting payment" showing everything — the two halves of
  // the same screen answered different questions and nothing said so.
  const matchesFilters = useCallback((claim: ReimbursementRequest) => {
    const term = search.trim().toLowerCase();
    const now = new Date();
    let rangeStart = '';
    let rangeEnd = '';
    if (rangeMode === 'this_month') {
      rangeStart = iso(new Date(now.getFullYear(), now.getMonth(), 1));
      rangeEnd = iso(new Date(now.getFullYear(), now.getMonth() + 1, 0));
    } else if (rangeMode === 'last_month') {
      rangeStart = iso(new Date(now.getFullYear(), now.getMonth() - 1, 1));
      rangeEnd = iso(new Date(now.getFullYear(), now.getMonth(), 0));
    } else if (rangeMode === 'custom') {
      rangeStart = customRange.start;
      rangeEnd = customRange.end;
    }

    if (term && !(claim.employeeName || '').toLowerCase().includes(term)) return false;
    if (typeFilter !== 'all' && claim.reimbursementType !== typeFilter) return false;
    if (departmentFilter !== 'all') {
      const department = employeeById.get(String(claim.employeeId))?.department;
      if (department !== departmentFilter) return false;
    }
    const expense = (claim.dateUsed || '').split('T')[0];
    if (rangeStart && expense && expense < rangeStart) return false;
    if (rangeEnd && expense && expense > rangeEnd) return false;
    return true;
  }, [customRange, departmentFilter, employeeById, rangeMode, search, typeFilter]);

  const visibleClaims = useMemo(
    () => requests.filter(matchesFilters),
    [requests, matchesFilters],
  );

  // Approved and the money has not gone out yet. This is the list somebody
  // works through on payday, and the one that quietly gets paid twice when the
  // screen only ever says "Approved".
  const awaitingPayment = useMemo(
    () => allClaims
      .filter((claim) => claim.status === 'approved' && !isPaidOut(claim))
      .filter(matchesFilters)
      .sort((a, b) => (a.approvedAt || a.dateUsed || '').localeCompare(b.approvedAt || b.dateUsed || '')),
    [allClaims, matchesFilters],
  );

  const awaitingValue = useMemo(
    () => awaitingPayment.reduce((total, claim) => total + Number(claim.amount || 0), 0),
    [awaitingPayment],
  );

  // Ticks for rows that have since been paid elsewhere must not linger.
  const selectedForPayment = useMemo(
    () => awaitingPayment.filter((claim) => payingIds.has(String(claim.id))),
    [awaitingPayment, payingIds],
  );

  const recentlyApproved = useMemo(
    () => allClaims
      .filter((claim) => claim.status === 'approved')
      .sort((a, b) => (b.approvedAt || b.dateUsed || '').localeCompare(a.approvedAt || a.dateUsed || ''))
      .slice(0, 5),
    [allClaims],
  );

  // ---- Actions ---------------------------------------------------------------

  const handleAction = async (claim: ReimbursementRequest, action: 'approve' | 'reject') => {
    const note = (notes[claim.id] || '').trim();
    if (action === 'reject' && !note) {
      setNotice({ type: 'error', text: 'A note is required when rejecting — the employee is told why.' });
      setExpandedId(claim.id);
      return;
    }

    setProcessingId(claim.id);
    setNotice(null);
    try {
      if (action === 'approve') {
        await reimbursementService.approveReimbursement(claim.id, note || undefined);
        setNotice({ type: 'success', text: `${money(claim.amount)} claim for ${claim.employeeName || 'the employee'} approved.` });
      } else {
        await reimbursementService.rejectReimbursement(claim.id, note);
        setNotice({ type: 'success', text: `${money(claim.amount)} claim for ${claim.employeeName || 'the employee'} rejected.` });
      }
      setNotes((current) => ({ ...current, [claim.id]: '' }));
      setExpandedId(null);
      // The decided claim leaves the queue immediately; the counters and side
      // panels catch up from a background refresh that does not show the spinner.
      setRequests((current) => current.filter((item) => item.id !== claim.id));
      void loadData(true);
    } catch (actionError) {
      setNotice({
        type: 'error',
        text: actionError instanceof Error ? actionError.message : 'The claim could not be updated. Please try again.',
      });
    } finally {
      setProcessingId(null);
    }
  };

  const toggleForPayment = (id: string) => setPayingIds((current) => {
    const next = new Set(current);
    if (next.has(id)) next.delete(id); else next.add(id);
    return next;
  });

  /**
   * Record that the money went out.
   *
   * The backend refuses this when a payslip already out has carried the claim,
   * and its message names the payslip and the month. That message is shown
   * exactly as sent: it is the last check before cash leaves the drawer, and a
   * generic "could not be updated" here is how somebody pays twice.
   */
  const markPaid = async (claims: ReimbursementRequest[]) => {
    if (claims.length === 0) return;
    setPayingNow(true);
    setNotice(null);
    try {
      if (claims.length === 1) {
        await reimbursementService.markPaid(claims[0].id);
      } else {
        // One call, all or nothing — a part-settled batch cannot happen.
        await reimbursementService.markManyPaid(claims.map((claim) => claim.id));
      }
      const total = claims.reduce((sum, claim) => sum + Number(claim.amount || 0), 0);
      setNotice({
        type: 'success',
        text: claims.length === 1
          ? `${money(claims[0].amount)} to ${claims[0].employeeName || 'the employee'} recorded as paid.`
          : `${claims.length} claims recorded as paid — ${money(total)} in total.`,
      });
      setPayingIds(new Set());
      await loadData(true);
    } catch (payError) {
      setNotice({
        type: 'error',
        text: payError instanceof Error && payError.message
          ? payError.message
          : 'That payment could not be recorded.',
      });
    } finally {
      setPayingNow(false);
    }
  };

  const exportCsv = () => {
    const headings = ['Employee', 'Type', 'Amount (Rs)', 'Expense date', 'Applied on', 'Status', 'Payment'];
    const rows = visibleClaims.map((claim) => [
      claim.employeeName || 'Unknown',
      claim.reimbursementType || 'Other',
      String(claim.amount ?? ''),
      claim.dateUsed,
      claim.appliedDate,
      claim.status,
      paidSummary(claim) ?? 'Not paid',
    ]);
    const csv = [headings, ...rows]
      .map((row) => row.map((cell) => `"${String(cell ?? '').replace(/"/g, '""')}"`).join(','))
      .join('\n');
    const link = document.createElement('a');
    link.href = URL.createObjectURL(new Blob([csv], { type: 'text/csv;charset=utf-8' }));
    link.download = `reimbursement-claims-${today}.csv`;
    link.click();
    URL.revokeObjectURL(link.href);
  };

  const receiptUrl = (claim: ReimbursementRequest) => {
    if (claim.receiptUrl && claim.receiptUrl.trim()) return claim.receiptUrl;
    return typeof claim.document === 'string' && claim.document.trim() ? claim.document : '';
  };

  if (isLoading) {
    return (
      <div className="manage-reimbursement-loading">
        <div className="spinner" />
      </div>
    );
  }

  return (
    <div className="manage-reimbursement-page">
      <div className="ml-toolbar">
        <button type="button" className="ml-toolbar-btn" onClick={exportCsv} disabled={!visibleClaims.length}>
          <FiDownload /> Export
        </button>
      </div>

      {notice && (
        <div className={`ml-notice ml-notice-${notice.type}`} role="status">
          <span>{notice.text}</span>
          <button type="button" onClick={() => setNotice(null)} aria-label="Dismiss"><FiX /></button>
        </div>
      )}

      {error && (
        <div className="ml-notice ml-notice-error" role="alert">
          <span>{error}</span>
          <button type="button" onClick={() => void loadData()}>Try again</button>
        </div>
      )}

      <section className="ml-kpi-grid" aria-label="Reimbursement summary">
        <article className="ml-kpi">
          <span className="ml-kpi-icon ml-kpi-red"><FiCreditCard /></span>
          <div>
            <span className="ml-kpi-label">Pending claims</span>
            <strong>{stats.pending}</strong>
            <small>Awaiting your approval</small>
          </div>
        </article>
        <article className="ml-kpi">
          <span className="ml-kpi-icon ml-kpi-amber"><FiClock /></span>
          <div>
            <span className="ml-kpi-label">Pending value</span>
            <strong className="ml-kpi-money">{money(stats.pendingValue)}</strong>
            <small>Not yet approved or paid</small>
          </div>
        </article>
        <article className="ml-kpi">
          <span className="ml-kpi-icon ml-kpi-green"><FiCheckCircle /></span>
          <div>
            <span className="ml-kpi-label">Approved this month</span>
            <strong>{stats.approvedThisMonth}</strong>
            <small>{money(stats.approvedValue)} approved</small>
          </div>
        </article>
        <article className="ml-kpi">
          <span className="ml-kpi-icon ml-kpi-blue"><FiAlertTriangle /></span>
          <div>
            <span className="ml-kpi-label">Need attention</span>
            <strong>{stats.stale}</strong>
            <small>Expense older than 60 days</small>
          </div>
        </article>
      </section>

      <div className="ml-layout">
        <div className="ml-main">
          <section className="ml-filters" aria-label="Filter claims">
            <div className="ml-search">
              <FiSearch aria-hidden="true" />
              <input
                value={search}
                onChange={(event) => setSearch(event.target.value)}
                placeholder="Search by employee name..."
                aria-label="Search by employee name"
              />
              {search && <button type="button" onClick={() => setSearch('')} aria-label="Clear search"><FiX /></button>}
            </div>

            <label className="ml-field">
              <span>Claim type</span>
              <select value={typeFilter} onChange={(event) => setTypeFilter(event.target.value)}>
                <option value="all">All types</option>
                {claimTypes.map((type) => <option key={type} value={type}>{type}</option>)}
              </select>
            </label>

            <label className="ml-field">
              <span>Department</span>
              <select value={departmentFilter} onChange={(event) => setDepartmentFilter(event.target.value)}>
                <option value="all">All departments</option>
                {departments.map((department) => <option key={department} value={department}>{department}</option>)}
              </select>
            </label>

            <label className="ml-field">
              <span>Expense date</span>
              <select value={rangeMode} onChange={(event) => setRangeMode(event.target.value as DateRangeMode)}>
                <option value="all">Any date</option>
                <option value="this_month">This month</option>
                <option value="last_month">Last month</option>
                <option value="custom">Custom range</option>
              </select>
            </label>

            <button type="button" className="ml-reset" onClick={resetFilters} disabled={!activeFilters}>
              <FiRotateCcw /> Reset
            </button>

            {rangeMode === 'custom' && (
              <div className="ml-custom-range">
                <label>
                  <span>From</span>
                  <input type="date" value={customRange.start} onChange={(event) => setCustomRange({ ...customRange, start: event.target.value })} />
                </label>
                <label>
                  <span>To</span>
                  <input type="date" value={customRange.end} onChange={(event) => setCustomRange({ ...customRange, end: event.target.value })} />
                </label>
              </div>
            )}
          </section>

          <section className="ml-table-wrap mr-table">
            <div className="ml-table-head">
              <span>Employee / claim type</span>
              <span>Amount</span>
              <span>Expense date</span>
              <span>Applied on</span>
              <span>Receipt</span>
              <span>Status</span>
              <span className="ml-actions-col">Actions</span>
            </div>

            {visibleClaims.length === 0 ? (
              <div className="ml-empty">
                <FiCreditCard size={26} />
                <strong>{requests.length === 0 ? 'Nothing awaiting approval' : 'No claims match these filters'}</strong>
                <span>
                  {requests.length === 0
                    ? 'Claims appear here as soon as someone submits an expense.'
                    : 'Clear a filter or widen the date range.'}
                </span>
                {requests.length > 0 && activeFilters > 0 && (
                  <button type="button" onClick={resetFilters}>Clear filters</button>
                )}
              </div>
            ) : (
              visibleClaims.map((claim) => {
                const isExpanded = expandedId === claim.id;
                const receipt = receiptUrl(claim);
                const flagged = staleIds.has(String(claim.id));
                const employee = employeeById.get(String(claim.employeeId));
                return (
                  <div className={`ml-row-group${isExpanded ? ' is-expanded' : ''}${flagged ? ' is-flagged' : ''}`} key={claim.id}>
                    <div className="ml-row">
                      <span className="ml-cell-employee">
                        <i className="ml-avatar">{(claim.employeeName || 'U').slice(0, 1).toUpperCase()}</i>
                        <span>
                          <strong>{claim.employeeName || 'Unknown employee'}</strong>
                          <small>{employee?.designation || employee?.department || 'Employee'}</small>
                          <em className="ml-type-badge">{claim.reimbursementType || 'Other'}</em>
                        </span>
                      </span>

                      <span className="ml-cell">
                        <strong className="mr-amount">{money(claim.amount)}</strong>
                      </span>

                      <span className="ml-cell">
                        <strong>{shortDate(claim.dateUsed)}</strong>
                        {flagged && <small className="ml-flag"><FiAlertTriangle /> Over 60 days old</small>}
                      </span>

                      <span className="ml-cell"><strong>{shortDate(claim.appliedDate)}</strong></span>

                      <span className="ml-cell">
                        {receipt
                          ? <strong className="mr-has-receipt">Attached</strong>
                          : <small>Not attached</small>}
                      </span>

                      <span className="ml-cell">
                        <em className="ml-badge ml-badge-pending">Pending</em>
                      </span>

                      <span className="ml-cell ml-actions-col">
                        {receipt && (
                          <button
                            type="button"
                            className="ml-icon-btn"
                            title="View receipt"
                            aria-label="View receipt"
                            onClick={() => setAttachment({ url: receipt, label: `${claim.employeeName || 'Employee'} — ${money(claim.amount)}` })}
                          >
                            <FiPaperclip />
                          </button>
                        )}
                        {canDecide(claim) && (
                          <>
                            <button
                              type="button"
                              className="ml-icon-btn ml-icon-approve"
                              title="Approve"
                              aria-label="Approve"
                              disabled={processingId === claim.id}
                              onClick={() => void handleAction(claim, 'approve')}
                            >
                              <FiCheck />
                            </button>
                            <button
                              type="button"
                              className="ml-icon-btn ml-icon-reject"
                              title="Reject"
                              aria-label="Reject"
                              disabled={processingId === claim.id}
                              onClick={() => { setExpandedId(claim.id); setNotice({ type: 'error', text: 'Add a note explaining the rejection, then confirm.' }); }}
                            >
                              <FiX />
                            </button>
                          </>
                        )}
                        {/* Sole details toggle now that the eye button, which did
                            the same thing, is gone — so it carries its wording. */}
                        <button
                          type="button"
                          className={`ml-icon-btn ml-chevron${isExpanded ? ' is-open' : ''}`}
                          title={isExpanded ? 'Hide details' : 'View details'}
                          aria-label={isExpanded ? 'Hide details' : 'View details'}
                          aria-expanded={isExpanded}
                          onClick={() => setExpandedId(isExpanded ? null : claim.id)}
                        >
                          <FiChevronRight />
                        </button>
                      </span>
                    </div>

                    {isExpanded && (
                      <div className="ml-detail">
                        <div className="ml-detail-left">
                          <div className="ml-detail-block">
                            <span className="ml-detail-label">Description</span>
                            <p>{claim.reason || 'No description given'}</p>
                          </div>

                          <div className="ml-detail-block">
                            <span className="ml-detail-label">Claim</span>
                            <div className="ml-balance-chips">
                              <span className="ml-chip ml-chip-active">{money(claim.amount)}</span>
                              <span className="ml-chip">{claim.reimbursementType || 'Other'}</span>
                              <span className="ml-chip">Expense: {shortDate(claim.dateUsed)}</span>
                            </div>
                          </div>

                          {claim.employeeEmail && (
                            <div className="ml-detail-block">
                              <span className="ml-detail-label">Employee email</span>
                              <p>{claim.employeeEmail}</p>
                            </div>
                          )}

                          <div className="ml-detail-block">
                            <span className="ml-detail-label">Receipt</span>
                            {receipt ? (
                              <button
                                type="button"
                                className="ml-doc-btn"
                                onClick={() => setAttachment({ url: receipt, label: `${claim.employeeName || 'Employee'} — ${money(claim.amount)}` })}
                              >
                                <FiFile /> View receipt
                              </button>
                            ) : (
                              <p className="ml-muted">None attached</p>
                            )}
                          </div>

                          {flagged && (
                            <p className="ml-warning">
                              <FiAlertTriangle /> This expense is more than 60 days old — worth confirming before payout.
                            </p>
                          )}
                        </div>

                        <div className="ml-detail-right">
                          <label className="ml-note-field">
                            <span className="ml-detail-label">Note to employee <em>(required to reject)</em></span>
                            <textarea
                              value={notes[claim.id] || ''}
                              onChange={(event) => setNotes({ ...notes, [claim.id]: event.target.value })}
                              placeholder="Add a note for the employee..."
                              rows={4}
                            />
                          </label>
                          <div className="ml-detail-actions">
                            <button type="button" onClick={() => setExpandedId(null)} disabled={processingId === claim.id}>
                              Close
                            </button>
                            {canDecide(claim) && (
                              <>
                                <button
                                  type="button"
                                  className="ml-approve"
                                  onClick={() => void handleAction(claim, 'approve')}
                                  disabled={processingId === claim.id}
                                >
                                  {processingId === claim.id ? 'Working…' : 'Approve'}
                                </button>
                                <button
                                  type="button"
                                  className="ml-reject"
                                  onClick={() => void handleAction(claim, 'reject')}
                                  disabled={processingId === claim.id}
                                >
                                  {processingId === claim.id ? 'Working…' : 'Reject'}
                                </button>
                              </>
                            )}
                          </div>
                        </div>
                      </div>
                    )}
                  </div>
                );
              })
            )}
          </section>

          {/* Approved is not paid. Until somebody records how the money left,
              the next person looking at this row has no way to tell. */}
          {awaitingPayment.length > 0 && (
            <section className="ml-table-wrap mr-pay" aria-label="Approved claims awaiting payment">
              <header className="mr-pay-head">
                <div>
                  <h3>Approved, awaiting payment</h3>
                  <p>{awaitingPayment.length} claim{awaitingPayment.length === 1 ? '' : 's'} · {money(awaitingValue)}</p>
                </div>
                <div className="mr-pay-tools">
                  <label className="mr-pay-all">
                    <input
                      type="checkbox"
                      checked={selectedForPayment.length === awaitingPayment.length}
                      onChange={(event) => setPayingIds(event.target.checked
                        ? new Set(awaitingPayment.map((claim) => String(claim.id)))
                        : new Set())}
                    />
                    Select all
                  </label>
                  <button
                    type="button"
                    className="mr-pay-bulk"
                    disabled={payingNow || selectedForPayment.length === 0}
                    onClick={() => void markPaid(selectedForPayment)}
                  >
                    <FiDollarSign />
                    {payingNow && selectedForPayment.length > 1 ? 'Recording…'
                      : selectedForPayment.length > 0 ? `Mark ${selectedForPayment.length} as paid`
                      : 'Mark selected as paid'}
                  </button>
                </div>
              </header>

              {awaitingPayment.map((claim) => {
                const id = String(claim.id);
                return (
                  <div className={`mr-pay-row${payingIds.has(id) ? ' is-picked' : ''}`} key={id}>
                    <label className="mr-pay-pick">
                      <input
                        type="checkbox"
                        checked={payingIds.has(id)}
                        onChange={() => toggleForPayment(id)}
                        aria-label={`Select ${claim.employeeName || 'claim'} for payment`}
                      />
                    </label>
                    <span className="mr-pay-who">
                      <strong>{claim.employeeName || 'Unknown employee'}</strong>
                      <small>{claim.reimbursementType || 'Other'} · {shortDate(claim.dateUsed)}</small>
                    </span>
                    <strong className="mr-amount">{money(claim.amount)}</strong>
                    <small className="mr-pay-when">Approved {shortDate(claim.approvedAt)}</small>
                    <button
                      type="button"
                      className="mr-pay-one"
                      disabled={payingNow}
                      onClick={() => void markPaid([claim])}
                    >
                      Mark as paid
                    </button>
                  </div>
                );
              })}
            </section>
          )}
        </div>

        <aside className="ml-side">
          <section className="ml-panel">
            <h3>Recently approved</h3>
            {recentlyApproved.length === 0 ? (
              <p className="ml-muted">No approved claims yet.</p>
            ) : (
              <ul className="ml-upcoming">
                {recentlyApproved.map((claim) => (
                  <li key={claim.id}>
                    <i>{(claim.employeeName || 'U').slice(0, 1).toUpperCase()}</i>
                    <span>
                      <strong>{claim.employeeName || 'Employee'}</strong>
                      <small>{money(claim.amount)} · {shortDate(claim.approvedAt || claim.dateUsed)}</small>
                      <em>{paidSummary(claim) ?? 'Awaiting payment'}</em>
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </section>

          <section className="ml-panel">
            <h3>Payout flow</h3>
            <ol className="ml-flow">
              {APPROVAL_STEPS.map((step, index) => {
                const expanded = visibleClaims.find((claim) => claim.id === expandedId);
                // Paid is not a status the backend sends — it is `paid_via`
                // being filled in — so the last step is read from that.
                const currentKey = expanded
                  ? (isPaidOut(expanded) ? 'paid' : expanded.status)
                  : 'pending';
                const isCurrent = step.key === currentKey;
                const isDone = APPROVAL_STEPS.findIndex((s) => s.key === currentKey) > index;
                return (
                  <li key={step.key} className={`${isCurrent ? 'is-current' : ''}${isDone ? ' is-done' : ''}`}>
                    <i>{isDone ? <FiCheck /> : index + 1}</i>
                    <span>
                      <strong>{step.label}</strong>
                      <small>{step.detail}</small>
                    </span>
                    {isCurrent && <em>Current step</em>}
                  </li>
                );
              })}
            </ol>
            <p className="ml-flow-note">
              <FiClock /> An approved claim is picked up by the payroll run for the month the
              expense falls in, and cannot be deleted once it is on a payslip.
            </p>
          </section>
        </aside>
      </div>

      {attachment && (
        <AttachmentViewer
          url={attachment.url}
          label={attachment.label}
          title="Receipt"
          prefix="ml"
          onClose={() => setAttachment(null)}
        />
      )}
    </div>
  );
};

export default ManageReimbursement;
