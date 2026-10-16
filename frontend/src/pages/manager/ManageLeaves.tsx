import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  FiAlertTriangle, FiCalendar, FiCheck, FiCheckCircle, FiChevronRight, FiClock,
  FiDownload, FiFile, FiPaperclip, FiRotateCcw, FiSearch, FiUsers, FiX,
} from 'react-icons/fi';
import { TbCalendarOff } from 'react-icons/tb';
import { leaveService, type LeaveBalanceSummary, LEAVE_STATUSES } from '../../services/leaveService';
import { employeeService } from '../../services/employeeService';
import { useAuthStore } from '../../store/authStore';
import { isPrivileged } from '../../utils/permissions';
import { type Employee, type LeaveApplication } from '../../types';
import './ManageWorkspace.css';
import AttachmentViewer from '../../components/common/AttachmentViewer';

// Leave type display name -> key inside the /leaves/balance response
const LEAVE_TYPE_TO_BALANCE_KEY: Record<string, string> = {
  'Annual Leave': 'annual',
  'Sick Leave': 'sick',
  'Casual Leave': 'personal',
  'Personal Leave': 'personal',
  'Maternity Leave': 'maternity',
  'Paternity Leave': 'paternity',
  'Bereavement Leave': 'bereavement',
  'Unpaid Leave': 'unpaid',
};

// Every leave type the system supports, regardless of whether a request of
// that type currently appears in the table — used for the "Leave type" filter
// so admins can filter by types with zero current requests instead of only
// the types that happen to be present on the page right now.
const ALL_LEAVE_TYPES = [
  'Annual Leave', 'Sick Leave', 'Casual Leave', 'Maternity Leave',
  'Paternity Leave', 'Bereavement Leave', 'Unpaid Leave',
];

// Every department the company defines (mirrors CreateUserPanel's DESIGNATIONS
// keys) — used for the "Department" filter so admins can filter by a
// department with zero current requests, not just whichever departments
// happen to have leave activity right now.
const ALL_DEPARTMENTS = [
  'Executive', 'Engineering', 'Product', 'Human Resources', 'Finance',
  'Operations', 'Marketing', 'Sales',
];

// The real approval chain, as implemented in api/v1/leaves.approve_or_reject_leave:
// a request starts PENDING and an admin or HR-flagged manager makes the one
// decision — approved or rejected. Single stage, not two: regular managers
// have no approval role at all.
const APPROVAL_STEPS = [
  { key: 'pending', label: 'HR / Admin approval', detail: 'Final decision and balance deduction' },
] as const;

type DateRangeMode = 'all' | 'this_month' | 'next_month' | 'custom';

const iso = (date: Date) => date.toISOString().split('T')[0];
const shortDate = (value?: string) =>
  value ? new Date(value).toLocaleDateString('en-GB', { day: '2-digit', month: 'short', year: 'numeric' }) : '—';
const shortDateNoYear = (value?: string) =>
  value ? new Date(value).toLocaleDateString('en-GB', { day: '2-digit', month: 'short' }) : '—';

const ManageLeaves = () => {
  const { user } = useAuthStore();
  const [leaves, setLeaves] = useState<LeaveApplication[]>([]);
  const [allLeaves, setAllLeaves] = useState<LeaveApplication[]>([]);
  const [employees, setEmployees] = useState<Employee[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState('');
  const [employeeBalances, setEmployeeBalances] = useState<Record<string, LeaveBalanceSummary>>({});
  const [processingId, setProcessingId] = useState<string | null>(null);
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [notes, setNotes] = useState<Record<string, string>>({});
  const [notice, setNotice] = useState<{ type: 'success' | 'error'; text: string } | null>(null);
  const [attachment, setAttachment] = useState<{ url: string; label: string } | null>(null);

  const [search, setSearch] = useState('');
  const [statusFilter, setStatusFilter] = useState('all');
  const [typeFilter, setTypeFilter] = useState('all');
  const [departmentFilter, setDepartmentFilter] = useState('all');
  const [rangeMode, setRangeMode] = useState<DateRangeMode>('all');
  const [customRange, setCustomRange] = useState({ start: '', end: '' });

  const employeeById = useMemo(
    () => new Map(employees.map((employee) => [String(employee.id), employee])),
    [employees],
  );

  const loadEmployeeBalances = useCallback(async (leaveList: LeaveApplication[]) => {
    const ids = [...new Set(leaveList.map((leave) => leave.employeeId).filter(Boolean))];
    const results = await Promise.all(
      ids.map(async (id) => ({ id, balance: await leaveService.getEmployeeLeaveBalance(id) })),
    );
    const map: Record<string, LeaveBalanceSummary> = {};
    results.forEach(({ id, balance }) => {
      if (balance) map[id] = balance;
    });
    setEmployeeBalances(map);
  }, []);

  // `silent` skips the loading state so a background refresh after an approval
  // does not replace the page with the spinner.
  const loadData = useCallback(async (silent = false) => {
    if (!silent) setIsLoading(true);
    setError('');
    // The queue is what needs action; the full history is only used for the
    // month-over-month and on-leave-today counters below.
    const [queueResult, historyResult, employeeResult] = await Promise.allSettled([
      leaveService.getUpcomingLeaves(),
      leaveService.getLeaveHistory(undefined, true),
      employeeService.getEmployees(),
    ]);

    if (queueResult.status === 'fulfilled' && Array.isArray(queueResult.value)) {
      setLeaves(queueResult.value);
      void loadEmployeeBalances(queueResult.value);
    } else {
      setLeaves([]);
      setError('The approval queue could not be loaded. Check your connection and try again.');
    }
    if (historyResult.status === 'fulfilled' && Array.isArray(historyResult.value)) {
      setAllLeaves(historyResult.value);
    }
    if (employeeResult.status === 'fulfilled') {
      setEmployees(employeeResult.value ?? []);
    }
    if (!silent) setIsLoading(false);
  }, [loadEmployeeBalances]);

  useEffect(() => { void loadData(); }, [loadData]);

  const isOwnLeave = (leave: LeaveApplication) => String(leave.employeeId) === String(user?.id);

  const canApprove = (leave: LeaveApplication) => {
    if (isOwnLeave(leave)) return false;
    if (!isPrivileged(user)) return false; // regular managers have no approval role
    return leave.status === 'pending' || leave.status === 'manager_approved'; // latter only for pre-existing legacy rows
  };

  // Own request, still awaiting a decision: nobody can approve their own leave,
  // so unlike a subordinate's request this one has no action buttons at all.
  // Without an explanation that reads as a silent bug ("why won't this
  // approve?"), so spell out what has to happen instead.
  const needsAnotherApprover = (leave: LeaveApplication) =>
    isOwnLeave(leave) && (leave.status === 'pending' || leave.status === 'manager_approved');

  const statusLabel = (status: string) => {
    if (status === 'pending') return 'HR / Admin approval';
    if (status === 'manager_approved') return 'HR / Admin approval'; // legacy rows only
    if (status === 'approved') return 'Approved';
    if (status === 'rejected') return 'Rejected';
    if (status === 'cancelled') return 'Cancelled';
    return status;
  };

  // Short form for the table's Status badge specifically. CSV export and the
  // Approver/step fallback still use the full-length statusLabel above.
  const statusBadgeLabel = (status: string) => {
    if (status === 'pending' || status === 'manager_approved') return 'Pending';
    return statusLabel(status);
  };

  const statusTone = (status: string) => {
    if (status === 'approved') return 'ml-badge-approved';
    if (status === 'rejected') return 'ml-badge-rejected';
    if (status === 'cancelled') return 'ml-badge-neutral';
    return 'ml-badge-pending';
  };

  // ---- Counters, all derived from loaded records ------------------------------

  const today = iso(new Date());

  const stats = useMemo(() => {
    const now = new Date();
    const thisMonth = now.getMonth();
    const thisYear = now.getFullYear();
    const lastMonthDate = new Date(thisYear, thisMonth - 1, 1);

    const approvedIn = (month: number, year: number) => allLeaves.filter((leave) => {
      if (leave.status !== 'approved') return false;
      const reference = leave.approvedAt || leave.startDate;
      if (!reference) return false;
      const date = new Date(reference);
      return date.getMonth() === month && date.getFullYear() === year;
    }).length;

    const approvedThisMonth = approvedIn(thisMonth, thisYear);
    const approvedLastMonth = approvedIn(lastMonthDate.getMonth(), lastMonthDate.getFullYear());

    const onLeaveToday = allLeaves.filter((leave) =>
      leave.status === 'approved'
      && leave.startDate <= today
      && (leave.endDate || leave.startDate) >= today).length;

    return {
      pending: leaves.length,
      approvedThisMonth,
      approvedDelta: approvedThisMonth - approvedLastMonth,
      onLeaveToday,
    };
  }, [allLeaves, leaves.length, today]);

  // ---- Filtering --------------------------------------------------------------

  const leaveTypes = ALL_LEAVE_TYPES;
  const departments = useMemo(() => {
    const existing = employees.map((employee) => employee.department).filter(Boolean) as string[];
    return Array.from(new Set([...ALL_DEPARTMENTS, ...existing])).sort();
  }, [employees]);

  const activeFilters = [
    search.trim() !== '',
    statusFilter !== 'all',
    typeFilter !== 'all',
    departmentFilter !== 'all',
    rangeMode !== 'all',
  ].filter(Boolean).length;

  const resetFilters = () => {
    setSearch('');
    setStatusFilter('all');
    setTypeFilter('all');
    setDepartmentFilter('all');
    setRangeMode('all');
    setCustomRange({ start: '', end: '' });
  };

  const visibleLeaves = useMemo(() => {
    const term = search.trim().toLowerCase();
    const now = new Date();
    let rangeStart = '';
    let rangeEnd = '';
    if (rangeMode === 'this_month') {
      rangeStart = iso(new Date(now.getFullYear(), now.getMonth(), 1));
      rangeEnd = iso(new Date(now.getFullYear(), now.getMonth() + 1, 0));
    } else if (rangeMode === 'next_month') {
      rangeStart = iso(new Date(now.getFullYear(), now.getMonth() + 1, 1));
      rangeEnd = iso(new Date(now.getFullYear(), now.getMonth() + 2, 0));
    } else if (rangeMode === 'custom') {
      rangeStart = customRange.start;
      rangeEnd = customRange.end;
    }

    return leaves.filter((leave) => {
      if (term && !(leave.employeeName || '').toLowerCase().includes(term)) return false;
      if (statusFilter !== 'all' && leave.status !== statusFilter) return false;
      if (typeFilter !== 'all' && leave.leaveType !== typeFilter) return false;
      if (departmentFilter !== 'all') {
        const department = employeeById.get(String(leave.employeeId))?.department;
        if (department !== departmentFilter) return false;
      }
      // A request counts for a period when its span overlaps it, so a leave
      // crossing the boundary still shows on both sides.
      const start = leave.startDate;
      const end = leave.endDate || leave.startDate;
      if (rangeStart && end < rangeStart) return false;
      if (rangeEnd && start > rangeEnd) return false;
      return true;
    });
  }, [customRange, departmentFilter, employeeById, leaves, rangeMode, search, statusFilter, typeFilter]);

  const upcomingTeamLeave = useMemo(
    () => allLeaves
      .filter((leave) => leave.status === 'approved' && (leave.endDate || leave.startDate) >= today)
      .sort((a, b) => a.startDate.localeCompare(b.startDate))
      .slice(0, 5),
    [allLeaves, today],
  );

  // ---- Actions ---------------------------------------------------------------

  const handleAction = async (leave: LeaveApplication, action: 'approve' | 'reject') => {
    const note = (notes[leave.id] || '').trim();
    if (action === 'reject' && !note) {
      setNotice({ type: 'error', text: 'A note is required when rejecting — the employee is told why.' });
      setExpandedId(leave.id);
      return;
    }

    setProcessingId(leave.id);
    setNotice(null);
    try {
      if (action === 'approve') {
        await leaveService.approveLeave(leave.id, note || undefined);
        setNotice({ type: 'success', text: `${leave.leaveType} for ${leave.employeeName || 'the employee'} approved.` });
      } else {
        await leaveService.rejectLeave(leave.id, note);
        setNotice({ type: 'success', text: `${leave.leaveType} for ${leave.employeeName || 'the employee'} rejected.` });
      }
      setNotes((current) => ({ ...current, [leave.id]: '' }));
      setExpandedId(null);
      // The decided request leaves the queue immediately. allLeaves is patched
      // in place too — not just left for the background refresh below — because
      // the KPI cards are computed from allLeaves on every render: leaving a
      // just-rejected/approved request at its old status there means the
      // counts stay stale until that refresh happens to land, which in
      // practice can take several seconds behind whatever else the page has
      // in flight (attachment thumbnails, balance calls, notification polling).
      const nextStatus = action === 'approve' ? 'approved' : 'rejected';
      setAllLeaves((current) => current.map((item) => (
        String(item.id) === String(leave.id) ? { ...item, status: nextStatus } : item
      )));
      setLeaves((current) => current.filter((item) => item.id !== leave.id));
      void loadData(true);
    } catch (actionError) {
      setNotice({
        type: 'error',
        text: actionError instanceof Error ? actionError.message : 'The request could not be updated. Please try again.',
      });
    } finally {
      setProcessingId(null);
    }
  };

  const exportCsv = () => {
    const headings = ['Employee', 'Leave type', 'Start', 'End', 'Working days', 'Applied on', 'Status'];
    const rows = visibleLeaves.map((leave) => [
      leave.employeeName || 'Unknown',
      leave.leaveType,
      leave.startDate,
      leave.endDate,
      String(leave.duration ?? ''),
      leave.appliedDate,
      statusLabel(leave.status),
    ]);
    const csv = [headings, ...rows]
      .map((row) => row.map((cell) => `"${String(cell ?? '').replace(/"/g, '""')}"`).join(','))
      .join('\n');
    const link = document.createElement('a');
    link.href = URL.createObjectURL(new Blob([csv], { type: 'text/csv;charset=utf-8' }));
    link.download = `leave-requests-${today}.csv`;
    link.click();
    URL.revokeObjectURL(link.href);
  };

  const balanceChips = (leave: LeaveApplication) => {
    const balance = employeeBalances[leave.employeeId];
    if (!balance) return null;
    const requestedKey = LEAVE_TYPE_TO_BALANCE_KEY[leave.leaveType];
    const entries = Object.entries(balance.balances)
      .filter(([, value]) => value.total > 0)
      .sort(([a], [b]) => (a === requestedKey ? -1 : b === requestedKey ? 1 : a.localeCompare(b)));
    return (
      <div className="ml-balance-chips">
        {entries.map(([key, value]) => (
          <span key={key} className={`ml-chip${key === requestedKey ? ' ml-chip-active' : ''}`}>
            {key.charAt(0).toUpperCase() + key.slice(1)}: {value.used} taken / {value.remaining} left
          </span>
        ))}
      </div>
    );
  };

  const requestedBalance = (leave: LeaveApplication) => {
    const balance = employeeBalances[leave.employeeId];
    const key = LEAVE_TYPE_TO_BALANCE_KEY[leave.leaveType];
    const entry = key ? balance?.balances[key] : undefined;
    if (!entry) return { primary: '—', secondary: '' };
    return {
      primary: `${entry.remaining} days`,
      secondary: `of ${entry.total}`,
    };
  };

  const attachmentUrl = (leave: LeaveApplication) =>
    typeof leave.document === 'string' && leave.document.trim() ? leave.document : '';

  if (isLoading) {
    return (
      <div className="manage-leaves-loading">
        <div className="spinner" />
      </div>
    );
  }

  return (
    <div className="manage-leaves-page">
      <div className="ml-toolbar">
        <button type="button" className="ml-toolbar-btn" onClick={exportCsv} disabled={!visibleLeaves.length}>
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

      <section className="ml-kpi-grid ml-kpi-grid-3" aria-label="Leave summary">
        <article className="ml-kpi">
          <span className="ml-kpi-icon ml-kpi-red"><FiCalendar /></span>
          <div>
            <span className="ml-kpi-label">Pending requests</span>
            <strong>{stats.pending}</strong>
            <small>Awaiting your approval</small>
          </div>
        </article>
        <article className="ml-kpi">
          <span className="ml-kpi-icon ml-kpi-green"><FiCheckCircle /></span>
          <div>
            <span className="ml-kpi-label">Approved this month</span>
            <strong>{stats.approvedThisMonth}</strong>
            <small>
              {stats.approvedDelta === 0
                ? 'Same as last month'
                : `${stats.approvedDelta > 0 ? '+' : ''}${stats.approvedDelta} vs last month`}
            </small>
          </div>
        </article>
        <article className="ml-kpi">
          <span className="ml-kpi-icon ml-kpi-blue"><FiUsers /></span>
          <div>
            <span className="ml-kpi-label">On leave today</span>
            <strong>{stats.onLeaveToday}</strong>
            <small>Approved leave covering today</small>
          </div>
        </article>
      </section>

      <div className="ml-layout">
        <div className="ml-main">
          <section className="ml-filters" aria-label="Filter requests">
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
              <span>Status</span>
              <select value={statusFilter} onChange={(event) => setStatusFilter(event.target.value)}>
                <option value="all">All statuses</option>
                {LEAVE_STATUSES.map((s) => (
                  <option key={s.value} value={s.value}>{s.label}</option>
                ))}
              </select>
            </label>

            <label className="ml-field">
              <span>Leave type</span>
              <select value={typeFilter} onChange={(event) => setTypeFilter(event.target.value)}>
                <option value="all">All types</option>
                {leaveTypes.map((type) => <option key={type} value={type}>{type}</option>)}
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
              <span>Date range</span>
              <select value={rangeMode} onChange={(event) => setRangeMode(event.target.value as DateRangeMode)}>
                <option value="all">Any date</option>
                <option value="this_month">This month</option>
                <option value="next_month">Next month</option>
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

          <section className="ml-table-wrap">
            <div className="ml-table-head">
              <span>Employee / leave type</span>
              <span>Date range</span>
              <span>Duration</span>
              <span>Applied on</span>
              <span>Balance</span>
              <span>Approver</span>
              <span>Status</span>
              <span className="ml-actions-col">Actions</span>
            </div>

            {visibleLeaves.length === 0 ? (
              <div className="ml-empty">
                <TbCalendarOff size={26} />
                <strong>{leaves.length === 0 ? 'Nothing awaiting approval' : 'No requests match these filters'}</strong>
                <span>
                  {leaves.length === 0
                    ? 'Requests appear here as soon as someone applies for leave.'
                    : 'Clear a filter or widen the date range.'}
                </span>
                {leaves.length > 0 && activeFilters > 0 && (
                  <button type="button" onClick={resetFilters}>Clear filters</button>
                )}
              </div>
            ) : (
              visibleLeaves.map((leave) => {
                const isExpanded = expandedId === leave.id;
                const balance = requestedBalance(leave);
                const document = attachmentUrl(leave);
                const employee = employeeById.get(String(leave.employeeId));
                return (
                  <div className={`ml-row-group${isExpanded ? ' is-expanded' : ''}`} key={leave.id}>
                    <div className="ml-row">
                      <span className="ml-cell-employee">
                        <i className="ml-avatar">{(leave.employeeName || 'U').slice(0, 1).toUpperCase()}</i>
                        <span>
                          <strong>{leave.employeeName || 'Unknown employee'}</strong>
                          <small>{employee?.designation || employee?.department || 'Employee'}</small>
                          <em className="ml-type-badge">{leave.leaveType}</em>
                        </span>
                      </span>

                      <span className="ml-cell">
                        <strong>{shortDateNoYear(leave.startDate)} – {shortDate(leave.endDate)}</strong>
                      </span>

                      <span className="ml-cell"><strong>{leave.duration} {leave.duration === 1 ? 'day' : 'days'}</strong></span>

                      <span className="ml-cell"><strong>{shortDate(leave.appliedDate)}</strong></span>

                      <span className="ml-cell">
                        <strong>{balance.primary}</strong>
                        {balance.secondary && <small>{balance.secondary}</small>}
                      </span>

                      <span className="ml-cell">
                        <strong>HR / Admin</strong>
                        <small>Awaiting decision</small>
                      </span>

                      <span className="ml-cell">
                        <em className={`ml-badge ${statusTone(leave.status)}`}>{statusBadgeLabel(leave.status)}</em>
                      </span>

                      <span className="ml-cell ml-actions-col">
                        {document && (
                          <button
                            type="button"
                            className="ml-icon-btn"
                            title="View attached document"
                            aria-label="View attached document"
                            onClick={() => setAttachment({ url: document, label: `${leave.employeeName || 'Employee'} — ${leave.leaveType}` })}
                          >
                            <FiPaperclip />
                          </button>
                        )}
                        {canApprove(leave) && (
                          <>
                            <button
                              type="button"
                              className="ml-icon-btn ml-icon-approve"
                              title="Approve"
                              aria-label="Approve"
                              disabled={processingId === leave.id}
                              onClick={() => void handleAction(leave, 'approve')}
                            >
                              <FiCheck />
                            </button>
                            <button
                              type="button"
                              className="ml-icon-btn ml-icon-reject"
                              title="Reject"
                              aria-label="Reject"
                              disabled={processingId === leave.id}
                              onClick={() => { setExpandedId(leave.id); setNotice({ type: 'error', text: 'Add a note explaining the rejection, then confirm.' }); }}
                            >
                              <FiX />
                            </button>
                          </>
                        )}
                        {needsAnotherApprover(leave) && (
                          <span
                            className="ml-self-flag"
                            title="You can't approve your own leave request — another admin or HR manager has to review it. See details below."
                          >
                            <FiAlertTriangle />
                          </span>
                        )}
                        <button
                          type="button"
                          className={`ml-icon-btn ml-chevron${isExpanded ? ' is-open' : ''}`}
                          title={isExpanded ? 'Hide details' : 'View details'}
                          aria-label={isExpanded ? 'Hide details' : 'View details'}
                          aria-expanded={isExpanded}
                          onClick={() => setExpandedId(isExpanded ? null : leave.id)}
                        >
                          <FiChevronRight />
                        </button>
                      </span>
                    </div>

                    {isExpanded && (
                      <div className="ml-detail">
                        <div className="ml-detail-left">
                          <div className="ml-detail-block">
                            <span className="ml-detail-label">Reason</span>
                            <p>{leave.reason || 'No reason given'}</p>
                          </div>

                          <div className="ml-detail-block">
                            <span className="ml-detail-label">Leave balance</span>
                            {balanceChips(leave) || <p className="ml-muted">Balance unavailable</p>}
                          </div>

                          <div className="ml-detail-block">
                            <span className="ml-detail-label">Resumption date</span>
                            <p>{shortDate(leave.resumptionDate)}</p>
                          </div>

                          {document ? (
                            <div className="ml-detail-block">
                              <span className="ml-detail-label">Attached document</span>
                              <button
                                type="button"
                                className="ml-doc-btn"
                                onClick={() => setAttachment({ url: document, label: `${leave.employeeName || 'Employee'} — ${leave.leaveType}` })}
                              >
                                <FiFile /> View document
                              </button>
                            </div>
                          ) : (
                            <div className="ml-detail-block">
                              <span className="ml-detail-label">Attached document</span>
                              <p className="ml-muted">None attached</p>
                            </div>
                          )}

                          {leave.warnings && leave.warnings.length > 0 && (
                            <div className="ml-detail-block">
                              <span className="ml-detail-label">Policy notes</span>
                              {leave.warnings.map((warning) => (
                                <p key={warning} className="ml-warning"><FiAlertTriangle /> {warning}</p>
                              ))}
                            </div>
                          )}
                        </div>

                        <div className="ml-detail-right">
                          {canApprove(leave) && (
                            <label className="ml-note-field">
                              <span className="ml-detail-label">Note to employee <em>(required to reject)</em></span>
                              <textarea
                                value={notes[leave.id] || ''}
                                onChange={(event) => setNotes({ ...notes, [leave.id]: event.target.value })}
                                placeholder="Add a note for the employee..."
                                rows={4}
                              />
                            </label>
                          )}
                          {needsAnotherApprover(leave) && (
                            <p className="ml-warning ml-self-note">
                              <FiAlertTriangle /> You can't approve or reject your own leave request. Another
                              admin, or a manager flagged as HR in Manage Users, needs to review this one.
                            </p>
                          )}

                          <div className="ml-detail-actions">
                            <button type="button" onClick={() => setExpandedId(null)} disabled={processingId === leave.id}>
                              Close
                            </button>
                            {canApprove(leave) && (
                              <>
                                <button
                                  type="button"
                                  className="ml-approve"
                                  onClick={() => void handleAction(leave, 'approve')}
                                  disabled={processingId === leave.id}
                                >
                                  {processingId === leave.id ? 'Working…' : 'Approve'}
                                </button>
                                <button
                                  type="button"
                                  className="ml-reject"
                                  onClick={() => void handleAction(leave, 'reject')}
                                  disabled={processingId === leave.id}
                                >
                                  {processingId === leave.id ? 'Working…' : 'Reject'}
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
        </div>

        <aside className="ml-side">
          <section className="ml-panel">
            <h3>Upcoming team leave</h3>
            {upcomingTeamLeave.length === 0 ? (
              <p className="ml-muted">No approved leave scheduled.</p>
            ) : (
              <ul className="ml-upcoming">
                {upcomingTeamLeave.map((leave) => (
                  <li key={leave.id}>
                    <i>{(leave.employeeName || 'U').slice(0, 1).toUpperCase()}</i>
                    <span>
                      <strong>{leave.employeeName || 'Employee'}</strong>
                      <small>
                        {shortDate(leave.startDate)}
                        {leave.endDate && leave.endDate !== leave.startDate ? ` – ${shortDate(leave.endDate)}` : ''}
                      </small>
                      <em>{leave.leaveType}</em>
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </section>

          <section className="ml-panel">
            <h3>Approval flow</h3>
            <ol className="ml-flow">
              {APPROVAL_STEPS.map((step) => (
                <li key={step.key}>
                  <i><FiCheckCircle /></i>
                  <span>
                    <strong>{step.label}</strong>
                    <small>{step.detail}</small>
                  </span>
                </li>
              ))}
            </ol>
            <p className="ml-flow-note">
              <FiClock /> Regular managers cannot approve or reject leave — only an admin or a manager flagged as HR in Manage Users can.
            </p>
          </section>
        </aside>
      </div>

      {attachment && (
        <AttachmentViewer
          url={attachment.url}
          label={attachment.label}
          title="Attached document"
          prefix="ml"
          onClose={() => setAttachment(null)}
        />
      )}
    </div>
  );
};

export default ManageLeaves;
