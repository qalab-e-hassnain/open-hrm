import { useState, useEffect, useMemo, useRef } from 'react';
import {
  FiAlertTriangle, FiCalendar, FiCheck, FiCheckCircle, FiClock, FiDollarSign,
  FiEdit2, FiFile, FiFileText, FiMessageSquare, FiShield, FiTrash2, FiUser, FiUsers, FiX, FiXCircle,
} from 'react-icons/fi';
import { reimbursementService, isPaidOut, paidSummary, REIMBURSEMENT_STATUSES } from '../../services/reimbursementService';
import { employeeService } from '../../services/employeeService';
import { useAuthStore } from '../../store/authStore';
import Card from '../../components/common/Card';
import Input from '../../components/common/Input';
import Select from '../../components/common/Select';
import { type ReimbursementRequest, type Employee } from '../../types';
import './ReimbursementHistory.css';
import AttachmentViewer from '../../components/common/AttachmentViewer';

// Mirrors employee/Reimbursement.tsx's list (the Apply form) so edited claims
// use the same categories.
const REIMBURSEMENT_TYPES = [
  { value: 'travel', label: 'Travel' },
  { value: 'food', label: 'Food and meals' },
  { value: 'medical', label: 'Medical' },
  { value: 'equipment', label: 'Equipment and supplies' },
  { value: 'other', label: 'Other business expense' },
];
const ACCEPTED_RECEIPT_TYPES = ['application/pdf', 'image/jpeg', 'image/png', 'image/gif', 'image/webp'];
const MAX_RECEIPT_BYTES = 5 * 1024 * 1024;
const todayIso = () => new Date().toISOString().slice(0, 10);

type EditForm = { reimbursementType: string; amount: string; dateOfExpense: string; description: string };

const longDate = (value?: string) =>
  value ? new Date(value).toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' }) : '—';

// Up to two initials for the avatar, matching the leave history cards.
const initials = (name?: string) => {
  const parts = (name || '').trim().split(/\s+/).filter(Boolean);
  if (parts.length === 0) return 'U';
  return (parts[0][0] + (parts.length > 1 ? parts[parts.length - 1][0] : '')).toUpperCase();
};

// A receipt may arrive as receiptUrl or, for older records, on `document`.
const receiptOf = (request: ReimbursementRequest) => {
  if (request.receiptUrl && request.receiptUrl.trim()) return request.receiptUrl;
  return typeof request.document === 'string' && request.document.trim() ? request.document : '';
};

const ReimbursementHistory = () => {
  const { user } = useAuthStore();
  const [reimbursements, setReimbursements] = useState<ReimbursementRequest[]>([]);
  const [employees, setEmployees] = useState<Employee[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  // For a manager/admin: their own claims vs. their team's — a dedicated
  // scope toggle (mirrors AttendanceHistory's "My attendance"/"Employees"
  // segmented control) rather than burying "my own" inside the Employee
  // dropdown. A plain employee has no toggle — they only ever see their own.
  const [viewMode, setViewMode] = useState<'me' | 'team'>('team');
  const [selectedEmployee, setSelectedEmployee] = useState<string>('all');
  const [statusFilter, setStatusFilter] = useState<string>('all');
  // Date filtering: either a single month (which month, how many claims) or a
  // between-dates range (who claimed between two dates), by the date the
  // claim was applied — not the expense date, which can fall in a different
  // month/range than when it was actually submitted.
  const [dateFilterMode, setDateFilterMode] = useState<'month' | 'range'>('range');
  const [monthFilter, setMonthFilter] = useState<number>(new Date().getMonth() + 1);
  const [yearFilter, setYearFilter] = useState<number>(new Date().getFullYear());
  // Far enough back to cover anything on file, without inviting a year that
  // cannot have records.
  const yearOptions = useMemo(() => {
    const thisYear = new Date().getFullYear();
    return Array.from({ length: 6 }, (_, i) => thisYear - i)
      .map((y) => ({ value: String(y), label: String(y) }));
  }, []);
  const [dateFilter, setDateFilter] = useState({ start: '', end: '' });
  const [deleteTarget, setDeleteTarget] = useState<ReimbursementRequest | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [notice, setNotice] = useState<{ type: 'success' | 'error'; text: string } | null>(null);
  const [receipt, setReceipt] = useState<{ url: string; label: string } | null>(null);
  const [editTarget, setEditTarget] = useState<ReimbursementRequest | null>(null);
  const [editForm, setEditForm] = useState<EditForm>({ reimbursementType: '', amount: '', dateOfExpense: '', description: '' });
  const [editReceiptFile, setEditReceiptFile] = useState<File | null>(null);
  const [editErrors, setEditErrors] = useState<Record<string, string>>({});
  const [editSubmitError, setEditSubmitError] = useState('');
  const [savingEdit, setSavingEdit] = useState(false);
  const editFileInputRef = useRef<HTMLInputElement>(null);
  const loadReimbursementsSeq = useRef(0);
  // Only the very first call should show the full-page loading state;
  // every later reload (a filter change) just swaps the list in place.
  const hasLoadedOnce = useRef(false);

  const monthOptions = [
    { value: '1', label: 'January' },   { value: '2', label: 'February' },
    { value: '3', label: 'March' },     { value: '4', label: 'April' },
    { value: '5', label: 'May' },       { value: '6', label: 'June' },
    { value: '7', label: 'July' },      { value: '8', label: 'August' },
    { value: '9', label: 'September' },  { value: '10', label: 'October' },
    { value: '11', label: 'November' },  { value: '12', label: 'December' },
  ];

  // Employee list (for the manager/admin filter dropdown) is independent of
  // the reimbursements list itself and only needs fetching once.
  useEffect(() => {
    if (user?.role !== 'manager' && user?.role !== 'admin') return;
    employeeService.getDirectory()
      .then((data) => setEmployees(data || []))
      .catch((error) => console.error('Failed to load employees:', error));
  }, [user?.role]);

  const loadReimbursements = async () => {
    const seq = ++loadReimbursementsSeq.current;
    if (!hasLoadedOnce.current) setIsLoading(true);
    try {
      let data: ReimbursementRequest[] = [];
      let employeeIdFilter: string | undefined = undefined;
      
      // Determine employee_id filter based on role and selection
      if (user?.role === 'employee') {
        // Employee sees only their own
        employeeIdFilter = user.id;
      } else if ((user?.role === 'manager' || user?.role === 'admin') && viewMode === 'me') {
        // "My reimbursements" scope — the manager/admin's own claims.
        employeeIdFilter = user.id;
      } else if ((user?.role === 'manager' || user?.role === 'admin') && selectedEmployee !== 'all') {
        // Manager/Admin filtering by specific employee
        employeeIdFilter = selectedEmployee;
      }
      // If admin/manager with 'all' selected, employeeIdFilter remains undefined (get all)

      // Reviewing everyone is a review of other people, so the approver's own
      // claims are left out. The "My reimbursements" scope still shows them.
      const isReviewingEveryone =
        (user?.role === 'manager' || user?.role === 'admin') && viewMode === 'team' && selectedEmployee === 'all';

      // Use history endpoint with employee_id filter
      data = await reimbursementService.getReimbursementHistory(employeeIdFilter, isReviewingEveryone);

      // Apply status filter
      if (statusFilter !== 'all') {
        data = data.filter(req => req.status === statusFilter);
      }

      // Apply date filter on the date the claim was applied (appliedDate),
      // not the expense date — a claim applied in August for a July expense
      // belongs in August's filter, since "By Month"/"Date Range" describe
      // when the claim was submitted.
      if (dateFilterMode === 'month') {
        const monthStart = new Date(yearFilter, monthFilter - 1, 1);
        const monthEnd = new Date(yearFilter, monthFilter, 0, 23, 59, 59);
        data = data.filter((req) => {
          const d = new Date(req.appliedDate);
          return d >= monthStart && d <= monthEnd;
        });
      } else {
        if (dateFilter.start) {
          const rangeStart = new Date(dateFilter.start);
          data = data.filter((req) => new Date(req.appliedDate) >= rangeStart);
        }
        if (dateFilter.end) {
          const rangeEnd = new Date(dateFilter.end);
          rangeEnd.setHours(23, 59, 59, 999);
          data = data.filter((req) => new Date(req.appliedDate) <= rangeEnd);
        }
      }

      // Newest first: sort by applied date descending, breaking ties by id so
      // two claims applied the same day keep a stable, most-recent-first order.
      data = [...data].sort((a, b) => {
        const diff = new Date(b.appliedDate).getTime() - new Date(a.appliedDate).getTime();
        return diff !== 0 ? diff : Number(b.id) - Number(a.id);
      });

      if (seq !== loadReimbursementsSeq.current) return; // a newer filter change has already superseded this
      setReimbursements(data);
    } catch (error) {
      // Leave whatever is already on screen in place rather than blanking it
      // out — a single failed reload (this backend has known transient
      // 502/503 flakiness) shouldn't make correctly-loaded data disappear.
      console.error('Failed to load reimbursements:', error);
    } finally {
      if (seq === loadReimbursementsSeq.current) {
        hasLoadedOnce.current = true;
        setIsLoading(false);
      }
    }
  };

  // The single trigger for every reimbursements load: the initial mount (all
  // dependencies already have values on first render) and every subsequent
  // filter/employee-selection change. Previously this ran from two separate
  // effects (one via loadData(), one keyed on the filter values) that both
  // fired on mount — two concurrent requests for identical data. If the
  // redundant one lost the race against this backend's known transient
  // 502/503s, its failure won over the other call's success and the page
  // showed no data at all. One effect, one trigger, removes that race
  // structurally instead of trying to out-guess which call should win.
  useEffect(() => {
    loadReimbursements();
  }, [viewMode, selectedEmployee, statusFilter, dateFilterMode, monthFilter, yearFilter, dateFilter.start, dateFilter.end]);

  const getStatusBadgeClass = (status: string) => {
    switch (status) {
      case 'approved':
        return 'status-badge status-approved';
      case 'rejected':
        return 'status-badge status-rejected';
      default:
        return 'status-badge status-pending';
    }
  };

  const getStatusIcon = (status: string) => {
    switch (status) {
      case 'approved':
        return <FiCheckCircle size={16} />;
      case 'rejected':
        return <FiXCircle size={16} />;
      default:
        return <FiClock size={16} />;
    }
  };

  // Admins can clear finished claims out of the history. A pending claim stays —
  // it still needs a decision. The backend enforces the same rule, and also
  // refuses any claim already paid out on a payslip.
  const canDelete = (request: ReimbursementRequest) =>
    user?.role === 'admin' && request.status !== 'pending';

  // Only the owner can edit their own claim, and only while it's still
  // pending — the backend enforces the same two rules (PUT /reimbursements/{id}
  // 403s on someone else's claim, 400s once it's been reviewed). Once a
  // manager has approved/rejected it, the record needs to stay as reviewed,
  // not be quietly changed out from under that decision.
  const canEdit = (request: ReimbursementRequest) =>
    request.employeeId === user?.id && request.status === 'pending';

  const openEdit = (request: ReimbursementRequest) => {
    setNotice(null);
    setEditTarget(request);
    setEditForm({
      reimbursementType: request.reimbursementType || 'other',
      amount: String(request.amount ?? ''),
      dateOfExpense: request.dateUsed || '',
      description: request.reason || '',
    });
    setEditReceiptFile(null);
    setEditErrors({});
    setEditSubmitError('');
  };

  const closeEdit = () => {
    if (savingEdit) return;
    setEditTarget(null);
  };

  const updateEditField = (field: keyof EditForm, value: string) => {
    setEditForm((current) => ({ ...current, [field]: value }));
    setEditErrors((current) => ({ ...current, [field]: '' }));
  };

  const pickEditReceipt = (file?: File) => {
    if (!file) return;
    if (!ACCEPTED_RECEIPT_TYPES.includes(file.type)) {
      setEditSubmitError('Use a PDF, JPG, PNG, GIF, or WEBP receipt.');
      return;
    }
    if (file.size > MAX_RECEIPT_BYTES) {
      setEditSubmitError('The receipt must be 5 MB or smaller.');
      return;
    }
    setEditReceiptFile(file);
    setEditSubmitError('');
  };

  const validateEdit = () => {
    const errors: Record<string, string> = {};
    if (!editForm.reimbursementType) errors.reimbursementType = 'Select an expense category.';
    if (!editForm.amount || Number(editForm.amount) <= 0) errors.amount = 'Enter an amount greater than zero.';
    if (!editForm.dateOfExpense) errors.dateOfExpense = 'Select the date of expense.';
    else if (editForm.dateOfExpense > todayIso()) errors.dateOfExpense = 'The expense date cannot be in the future.';
    if (editForm.description.trim().length < 5) errors.description = 'Add a description of at least 5 characters.';
    setEditErrors(errors);
    return Object.keys(errors).length === 0;
  };

  const saveEdit = async () => {
    if (!editTarget || !validateEdit()) return;
    setSavingEdit(true);
    setEditSubmitError('');
    try {
      let receiptUrl: string | undefined;
      if (editReceiptFile) {
        const upload = new FormData();
        upload.append('file', editReceiptFile);
        receiptUrl = await reimbursementService.uploadReceipt(upload);
      }
      const updated = await reimbursementService.updateReimbursement(editTarget.id, {
        reimbursementType: editForm.reimbursementType,
        amount: Number(editForm.amount),
        dateOfExpense: editForm.dateOfExpense,
        description: editForm.description.trim(),
        ...(receiptUrl ? { receiptUrl } : {}),
      });
      setReimbursements((current) => current.map((claim) => (claim.id === updated.id ? updated : claim)));
      setNotice({ type: 'success', text: 'Claim updated.' });
      setEditTarget(null);
    } catch (error) {
      setEditSubmitError(error instanceof Error ? error.message : 'The claim could not be updated. Please try again.');
    } finally {
      setSavingEdit(false);
    }
  };

  const confirmDelete = async () => {
    if (!deleteTarget) return;
    const deletedId = deleteTarget.id;
    setDeleting(true);
    try {
      await reimbursementService.purgeReimbursement(deletedId);
      // Removed in place rather than refetching — see LeaveHistory.confirmDelete.
      setReimbursements((current) => current.filter((claim) => claim.id !== deletedId));
      setNotice({ type: 'success', text: 'Claim deleted from history.' });
      setDeleteTarget(null);
    } catch (error) {
      setNotice({
        type: 'error',
        text: error instanceof Error ? error.message : 'The claim could not be deleted. Please try again.',
      });
      setDeleteTarget(null);
    } finally {
      setDeleting(false);
    }
  };

  // A From date after the To date can never match anything — the two active
  // filters (>= start AND <= end) exclude every record — so it silently read
  // as "no claims" with nothing telling the user why. String comparison is
  // safe here since both are plain yyyy-mm-dd values from <input type="date">.
  const dateRangeError = dateFilterMode === 'range' && dateFilter.start && dateFilter.end && dateFilter.start > dateFilter.end
    ? 'Applied From date must be before Applied To date.'
    : '';

  if (isLoading) {
    return (
      <div className="reimbursement-history-loading">
        <div className="spinner"></div>
      </div>
    );
  }

  return (
    <div className="reimbursement-history-page">
      <Card className="reimbursement-history-card">
        <div className="page-header">
          <h2 className="page-title">Reimbursement History</h2>
          {(user?.role === 'manager' || user?.role === 'admin') && (
            <p className="page-subtitle">View reimbursement requests for yourself and your team</p>
          )}
        </div>

        {notice && (
          <div className={`rh-notice rh-notice-${notice.type}`} role="status">
            <span>{notice.text}</span>
            <button type="button" onClick={() => setNotice(null)} aria-label="Dismiss"><FiX /></button>
          </div>
        )}

        {/* Scope toggle: own claims vs. the team's — matches AttendanceHistory's
            "My attendance"/"Employees" segmented control. */}
        {(user?.role === 'manager' || user?.role === 'admin') && (
          <div className="rh-scope-row">
            <div className="rh-segmented" aria-label="Reimbursement history scope">
              <button
                type="button"
                className={viewMode === 'me' ? 'active' : ''}
                onClick={() => { setViewMode('me'); setSelectedEmployee('all'); }}
              >
                <FiUser /> My reimbursements
              </button>
              <button
                type="button"
                className={viewMode === 'team' ? 'active' : ''}
                onClick={() => setViewMode('team')}
              >
                <FiUsers /> Employees
              </button>
            </div>
          </div>
        )}

        {/* Employee + Status filters */}
        <div className="history-filters rh-top-filters">
          {(user?.role === 'manager' || user?.role === 'admin') && viewMode === 'team' && (
            <Select
              label="Employee"
              value={selectedEmployee}
              onChange={(e) => setSelectedEmployee(e.target.value)}
              options={[
                { value: 'all', label: 'All Employees' },
                ...employees.filter((emp) => emp.id !== user?.id).map(emp => ({ value: emp.id, label: emp.name })),
              ]}
            />
          )}

          <Select
            label="Status"
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value)}
            // "Paid" was missing, so a claim that had been paid could not be
            // found on the screen whose job is finding past claims.
            options={[
              { value: 'all', label: 'All Status' },
              ...REIMBURSEMENT_STATUSES.map((s) => ({ value: s.value, label: s.label })),
            ]}
          />
        </div>

        {/* Date filter mode toggle + live result count */}
        <div className="rh-filter-bar">
          <div className="rh-filter-toggle">
            <button
              className={`rh-toggle-btn${dateFilterMode === 'month' ? ' rh-toggle-active' : ''}`}
              onClick={() => setDateFilterMode('month')}
            >
              By Month
            </button>
            <button
              className={`rh-toggle-btn${dateFilterMode === 'range' ? ' rh-toggle-active' : ''}`}
              onClick={() => setDateFilterMode('range')}
            >
              Date Range
            </button>
          </div>
          <span className="rh-filter-count">
            {dateFilterMode === 'month'
              ? `${reimbursements.length} ${reimbursements.length === 1 ? 'claim' : 'claims'} in ${monthOptions[monthFilter - 1]?.label} ${yearFilter}`
              : `${reimbursements.length} ${reimbursements.length === 1 ? 'claim' : 'claims'}`}
          </span>
        </div>

        {/* Date filter inputs (month picker OR between-dates range) */}
        <div className="history-filters rh-date-filters">
          {dateFilterMode === 'month' ? (
            <>
              <Select
                label="Month"
                value={String(monthFilter)}
                onChange={(e) => setMonthFilter(Number(e.target.value))}
                options={monthOptions}
              />
              {/* A dropdown, not a number box. Typing a year into a
                  controlled number input filters on 2, then 20, then 202 on
                  the way to 2025, and clearing it first hands parseInt an
                  empty string and filters on NaN — which is why an older
                  month could not be picked. */}
              <Select
                label="Year"
                value={String(yearFilter)}
                onChange={(e) => setYearFilter(Number(e.target.value))}
                options={yearOptions}
              />
            </>
          ) : (
            <>
              <Input
                label="Applied From"
                type="date"
                value={dateFilter.start}
                onChange={(e) => setDateFilter({ ...dateFilter, start: e.target.value })}
                error={dateRangeError}
              />
              <Input
                label="Applied To"
                type="date"
                value={dateFilter.end}
                onChange={(e) => setDateFilter({ ...dateFilter, end: e.target.value })}
                error={dateRangeError}
              />
            </>
          )}
        </div>

        {/* Reimbursement List */}
        <div className="reimbursements-history-list">
          {reimbursements.length === 0 ? (
            <div className="empty-state">
              <FiDollarSign size={48} />
              <p>No reimbursement requests found</p>
            </div>
          ) : (
            reimbursements.map((request) => (
              <div key={request.id} className="reimbursement-history-item">
                <div className="reimbursement-history-header">
                  <div className="reimbursement-history-employee">
                    <div className="reimbursement-avatar">{initials(request.employeeName)}</div>
                    <div className="reimbursement-employee-details">
                      <span className="reimbursement-employee-name">{request.employeeName || 'Unknown'}</span>
                      <span className="reimbursement-type-badge">{request.reimbursementType || 'Other'}</span>
                    </div>
                  </div>
                  <div className={`${getStatusBadgeClass(request.status)}`}>
                    {getStatusIcon(request.status)}
                    <span>{request.status.charAt(0).toUpperCase() + request.status.slice(1)}</span>
                  </div>
                </div>

                <div className="reimbursement-history-body">
                  <div className="reimbursement-info-grid">
                    <div className="info-item">
                      <span className="info-icon"><FiDollarSign /></span>
                      <span className="info-copy">
                        <span className="info-label">Amount</span>
                        <span className="info-value rh-amount-value">Rs. {Number(request.amount || 0).toLocaleString('en-PK')}</span>
                      </span>
                    </div>
                    <div className="info-item">
                      <span className="info-icon"><FiCalendar /></span>
                      <span className="info-copy">
                        <span className="info-label">Date of Expense</span>
                        <span className="info-value">{longDate(request.dateUsed)}</span>
                      </span>
                    </div>
                    <div className="info-item">
                      <span className="info-icon"><FiFileText /></span>
                      <span className="info-copy">
                        <span className="info-label">Applied On</span>
                        <span className="info-value">{longDate(request.appliedDate)}</span>
                      </span>
                    </div>
                    <div className="info-item">
                      <span className="info-icon"><FiFile /></span>
                      <span className="info-copy">
                        <span className="info-label">Receipt</span>
                        {receiptOf(request) ? (
                          <button
                            type="button"
                            className="rh-receipt-btn"
                            onClick={() => setReceipt({
                              url: receiptOf(request),
                              label: `${request.employeeName || 'Employee'} — Rs. ${Number(request.amount || 0).toLocaleString('en-PK')}`,
                            })}
                          >
                            View receipt
                          </button>
                        ) : (
                          <span className="info-value rh-info-muted">Not attached</span>
                        )}
                      </span>
                    </div>
                  </div>

                  {request.reason && (
                    <div className="reimbursement-reason">
                      <span className="reason-icon"><FiMessageSquare /></span>
                      <span className="reason-copy">
                        <span className="reason-label">Description</span>
                        <span className="reason-text">{request.reason}</span>
                      </span>
                    </div>
                  )}

                  {request.rejectionReason && (
                    <div className="rejection-reason">
                      <span className="rejection-label">Rejection Reason</span>
                      <span className="rejection-text">{request.rejectionReason}</span>
                    </div>
                  )}
                </div>

                <div className="reimbursement-history-footer">
                  <div className="reimbursement-meta">
                    <span className="rh-meta-item">
                      <FiFileText aria-hidden="true" />
                      Applied: <strong>{longDate(request.appliedDate)}</strong>
                    </span>
                    {request.approvedAt && (
                      <span className="rh-meta-item">
                        <FiShield aria-hidden="true" />
                        Approved: <strong>{longDate(request.approvedAt)}</strong>
                        {request.approvedByName ? <> by <strong>{request.approvedByName}</strong></> : null}
                      </span>
                    )}
                    {/* Keyed on whether the money went out, not on whether a
                        date came back with it: a claim swept into a payslip can
                        carry no payment date at all, and gating on `paidAt`
                        showed those rows as merely "Approved". */}
                    {isPaidOut(request) && (
                      <span className="rh-meta-item">
                        <FiCheck aria-hidden="true" />
                        <strong>{paidSummary(request)}</strong>
                      </span>
                    )}
                  </div>
                  <div className="rh-footer-actions">
                    {canEdit(request) && (
                      <button
                        type="button"
                        className="rh-edit-btn"
                        onClick={() => openEdit(request)}
                        title="Edit this claim before it's reviewed"
                      >
                        <FiEdit2 /> Edit
                      </button>
                    )}
                    {canDelete(request) && (
                      <button
                        type="button"
                        className="rh-delete-btn"
                        onClick={() => { setNotice(null); setDeleteTarget(request); }}
                        title="Remove this claim from the history"
                      >
                        <FiTrash2 /> Delete record
                      </button>
                    )}
                  </div>
                </div>
              </div>
            ))
          )}
        </div>
      </Card>

      {receipt && (
        <AttachmentViewer
          url={receipt.url}
          label={receipt.label}
          title="Receipt"
          prefix="rh"
          onClose={() => setReceipt(null)}
        />
      )}

      {editTarget && (
        <div className="rh-confirm-backdrop" onMouseDown={(event) => event.target === event.currentTarget && closeEdit()}>
          <div className="rh-confirm rh-confirm-wide" role="dialog" aria-modal="true" aria-labelledby="rh-edit-title">
            <header>
              <h3 id="rh-edit-title">Edit claim</h3>
              <button type="button" onClick={closeEdit} aria-label="Close" disabled={savingEdit}><FiX /></button>
            </header>

            {editSubmitError && <div className="rh-edit-submit-error" role="alert">{editSubmitError}</div>}

            <div className="rh-edit-fields">
              <Select
                label="Reimbursement type"
                value={editForm.reimbursementType}
                onChange={(e) => updateEditField('reimbursementType', e.target.value)}
                options={REIMBURSEMENT_TYPES}
                error={editErrors.reimbursementType}
              />
              <Input
                label="Amount (PKR)"
                type="number"
                min="1"
                step="0.01"
                value={editForm.amount}
                onChange={(e) => updateEditField('amount', e.target.value)}
                error={editErrors.amount}
              />
              <div className="wide">
                <Input
                  label="Date of expense"
                  type="date"
                  max={todayIso()}
                  value={editForm.dateOfExpense}
                  onChange={(e) => updateEditField('dateOfExpense', e.target.value)}
                  error={editErrors.dateOfExpense}
                />
              </div>
            </div>

            <label className="rh-edit-textarea">
              <span>Description</span>
              <textarea
                rows={3}
                maxLength={500}
                value={editForm.description}
                onChange={(e) => updateEditField('description', e.target.value)}
              />
              {editErrors.description && <small className="rh-edit-error-text">{editErrors.description}</small>}
            </label>

            <div className="rh-edit-receipt">
              <span>
                {editReceiptFile
                  ? <>New receipt: <strong>{editReceiptFile.name}</strong></>
                  : receiptOf(editTarget)
                    ? 'A receipt is already attached — replacing it will discard the old one.'
                    : 'No receipt attached.'}
              </span>
              <input
                ref={editFileInputRef}
                type="file"
                accept=".pdf,.jpg,.jpeg,.png,.gif,.webp"
                style={{ display: 'none' }}
                onChange={(e) => { pickEditReceipt(e.target.files?.[0]); e.target.value = ''; }}
              />
              <button type="button" onClick={() => editFileInputRef.current?.click()}>
                {receiptOf(editTarget) || editReceiptFile ? 'Replace' : 'Attach'}
              </button>
            </div>

            <footer>
              <button type="button" onClick={closeEdit} disabled={savingEdit}>Cancel</button>
              <button type="button" className="rh-confirm-primary" onClick={saveEdit} disabled={savingEdit}>
                {savingEdit ? 'Saving…' : 'Save changes'}
              </button>
            </footer>
          </div>
        </div>
      )}

      {deleteTarget && (
        <div
          className="rh-confirm-backdrop"
          onMouseDown={(event) => event.target === event.currentTarget && setDeleteTarget(null)}
        >
          <div className="rh-confirm" role="dialog" aria-modal="true" aria-labelledby="rh-confirm-title">
            <FiAlertTriangle />
            <h3 id="rh-confirm-title">Delete this claim?</h3>
            <p>
              {deleteTarget.employeeName ? `${deleteTarget.employeeName} — ` : ''}
              {deleteTarget.reimbursementType || 'Claim'}, <strong>Rs. {Number(deleteTarget.amount).toLocaleString('en-PK')}</strong>
            </p>
            <p className="rh-confirm-note">
              This erases the claim permanently and cannot be undone. A claim already
              paid out on a payslip is refused automatically, so payslip totals stay
              able to reconcile.
            </p>
            <footer>
              <button type="button" onClick={() => setDeleteTarget(null)} disabled={deleting}>Keep record</button>
              <button type="button" className="rh-confirm-danger" onClick={confirmDelete} disabled={deleting}>
                {deleting ? 'Deleting…' : 'Delete record'}
              </button>
            </footer>
          </div>
        </div>
      )}
    </div>
  );
};

export default ReimbursementHistory;

