import { useState, useEffect, useMemo, useRef } from 'react';
import {
  FiAlertTriangle, FiCalendar, FiCheckCircle, FiClock, FiFileText,
  FiMessageSquare, FiShield, FiTrash2, FiUser, FiUsers, FiX, FiXCircle,
} from 'react-icons/fi';
import { leaveService } from '../../services/leaveService';
import { employeeService } from '../../services/employeeService';
import { useAuthStore } from '../../store/authStore';
import { isPrivileged } from '../../utils/permissions';
import Card from '../../components/common/Card';
import Input from '../../components/common/Input';
import Select from '../../components/common/Select';
import { type LeaveApplication, type Employee } from '../../types';
import './LeaveHistory.css';

const longDate = (value?: string) =>
  value ? new Date(value).toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' }) : '—';

// Up to two initials for the avatar, so "Faseeh Haider" reads as FH.
const initials = (name?: string) => {
  const parts = (name || '').trim().split(/\s+/).filter(Boolean);
  if (parts.length === 0) return 'U';
  return (parts[0][0] + (parts.length > 1 ? parts[parts.length - 1][0] : '')).toUpperCase();
};

const statusIcon = (status: string) => {
  if (status === 'approved') return <FiCheckCircle aria-hidden="true" />;
  if (status === 'rejected') return <FiXCircle aria-hidden="true" />;
  if (status === 'cancelled') return <FiXCircle aria-hidden="true" />;
  return <FiClock aria-hidden="true" />;
};

const LeaveHistory = () => {
  const { user } = useAuthStore();
  const [leaves, setLeaves] = useState<LeaveApplication[]>([]);
  const [employees, setEmployees] = useState<Employee[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  // For a manager/admin: their own leaves vs. their team's — a dedicated
  // scope toggle (mirrors AttendanceHistory's "My attendance"/"Employees"
  // segmented control) rather than burying "my own" inside the Employee
  // dropdown. A plain employee has no toggle — they only ever see their own.
  const [viewMode, setViewMode] = useState<'me' | 'team'>('team');
  const [selectedEmployee, setSelectedEmployee] = useState<string>('all');
  const [statusFilter, setStatusFilter] = useState<string>('all');
  // Date filtering: either a single month (which month, how many leaves) or a
  // between-dates range (who is on leave between two dates).
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
  const [startDateFilter, setStartDateFilter] = useState<string>('');
  const [endDateFilter, setEndDateFilter] = useState<string>('');
  const [deleteTarget, setDeleteTarget] = useState<LeaveApplication | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [cancelTarget, setCancelTarget] = useState<LeaveApplication | null>(null);
  const [cancelling, setCancelling] = useState(false);
  const [notice, setNotice] = useState<{ type: 'success' | 'error'; text: string } | null>(null);
  // Rapidly changing a filter (e.g. typing both date-range inputs quickly)
  // fires loadLeaves() once per change. Those fetches can resolve
  // out of order — a slower, now-stale call finishing after a newer one
  // would silently overwrite the correct, up-to-date result. This sequence
  // guard makes only the most recently STARTED call allowed to apply its
  // result, regardless of which one finishes first.
  const loadLeavesSeq = useRef(0);
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
  // the leaves list itself and only needs fetching once.
  useEffect(() => {
    if (user?.role !== 'manager' && user?.role !== 'admin') return;
    employeeService.getDirectory()
      .then((data) => setEmployees(data || []))
      .catch((error) => console.error('Failed to load employees:', error));
  }, [user?.role]);

  const loadLeaves = async () => {
    const seq = ++loadLeavesSeq.current;
    if (!hasLoadedOnce.current) setIsLoading(true);
    try {
      let data: LeaveApplication[] = [];
      let employeeIdFilter: string | undefined = undefined;
      
      // Determine employee_id filter based on role and selection
      if (user?.role === 'employee') {
        // Employee sees only their own
        employeeIdFilter = user.id;
      } else if ((user?.role === 'manager' || user?.role === 'admin') && viewMode === 'me') {
        // "My leaves" scope — the manager/admin's own requests, same id the
        // backend already accepts as a valid employee_id for them (it's
        // always in their own allowed subordinate_ids/all-users scope).
        employeeIdFilter = user.id;
      } else if ((user?.role === 'manager' || user?.role === 'admin') && selectedEmployee !== 'all') {
        // Manager/Admin filtering by specific employee
        employeeIdFilter = selectedEmployee;
      }
      // If admin/manager with 'all' selected, employeeIdFilter remains undefined (get all)

      // This is a full audit/history browse, not the approval queue — "All
      // Employees" + "All Status" should mean literally everything, the
      // viewer's own requests included. Excluding self here silently dropped
      // whichever pending/rejected leaves happened to belong to the person
      // browsing, which reads as "the status filter is broken" rather than
      // "your own requests are hidden".
      data = await leaveService.getLeaveHistory(employeeIdFilter);

      // Apply status filter
      if (statusFilter !== 'all') {
        data = data.filter(leave => leave.status === statusFilter);
      }

      // Apply date filter. Filters by when the request was APPLIED FOR
      // (appliedDate), not the leave's own start/end dates — this is a
      // history of applications, so "leaves in this period" means "requests
      // submitted in this period", the same way the list is sorted by
      // appliedDate below. Filtering by the leave's own dates instead used to
      // mean a request submitted today for a date weeks out would vanish from
      // "this month"/a nearby range until the actual leave date arrived.
      if (dateFilterMode === 'month') {
        const monthStart = new Date(yearFilter, monthFilter - 1, 1);
        const monthEnd = new Date(yearFilter, monthFilter, 0, 23, 59, 59);
        data = data.filter((leave) => {
          const applied = new Date(leave.appliedDate);
          return applied >= monthStart && applied <= monthEnd;
        });
      } else {
        if (startDateFilter) {
          const rangeStart = new Date(startDateFilter);
          data = data.filter((leave) => new Date(leave.appliedDate) >= rangeStart);
        }
        if (endDateFilter) {
          const rangeEnd = new Date(endDateFilter);
          rangeEnd.setHours(23, 59, 59, 999);
          data = data.filter((leave) => new Date(leave.appliedDate) <= rangeEnd);
        }
      }

      // Newest first: sort by applied date descending, breaking ties by id so
      // two leaves applied the same day keep a stable, most-recent-first order.
      data = [...data].sort((a, b) => {
        const diff = new Date(b.appliedDate).getTime() - new Date(a.appliedDate).getTime();
        return diff !== 0 ? diff : Number(b.id) - Number(a.id);
      });

      // Discard the result if a newer call has started since this one began
      // — this response is stale even though it just resolved.
      if (seq !== loadLeavesSeq.current) return;
      setLeaves(data);
    } catch (error) {
      // Leave whatever is already on screen in place rather than blanking it
      // out — a single failed reload shouldn't make correctly-loaded data
      // disappear.
      console.error('Failed to load leaves:', error);
    } finally {
      if (seq === loadLeavesSeq.current) {
        hasLoadedOnce.current = true;
        setIsLoading(false);
      }
    }
  };

  // The single trigger for every leaves load: the initial mount (all
  // dependencies already have values on first render) and every subsequent
  // filter/employee-selection change. Previously this ran from two separate
  // effects (one via loadData(), one keyed on the filter values) that both
  // fired on mount — two concurrent requests for identical data. If the
  // redundant one lost the race against this backend's known transient
  // 502/503s, its failure won over the other call's success and the page
  // showed no data at all. One effect, one trigger, removes that race
  // structurally instead of trying to out-guess which call should win.
  useEffect(() => {
    loadLeaves();
  }, [viewMode, selectedEmployee, statusFilter, dateFilterMode, monthFilter, yearFilter, startDateFilter, endDateFilter]);

  const getStatusBadgeClass = (status: string) => {
    switch (status) {
      case 'pending':
        return 'status-badge status-pending';
      case 'manager_approved':
        return 'status-badge status-manager-approved';
      case 'approved':
        return 'status-badge status-approved';
      case 'rejected':
        return 'status-badge status-rejected';
      case 'cancelled':
        return 'status-badge status-cancelled';
      default:
        return 'status-badge status-pending';
    }
  };

  const getStatusDisplay = (status: string) => {
    switch (status) {
      case 'pending':
        return 'Pending HR / Admin Approval';
      case 'manager_approved': // legacy rows only — nothing sets this anymore
        return 'Pending HR / Admin Approval';
      case 'approved':
        return 'Approved';
      case 'rejected':
        return 'Rejected';
      case 'cancelled':
        return 'Cancelled';
      default:
        return status;
    }
  };

  // Admins can clear finished records out of the history. Anything still
  // awaiting a decision stays — it belongs in the approval queue, not the bin.
  // The backend enforces the same rules and refuses records payroll depends on.
  const canDelete = (leave: LeaveApplication) =>
    user?.role === 'admin'
    && (leave.status === 'approved' || leave.status === 'rejected' || leave.status === 'cancelled');

  // Withdrawing only makes sense while the request is still awaiting a
  // decision. Once approved it's committed — payroll/attendance may already
  // be planning around it — so cancelling it is deliberately not
  // self-service past that point; it takes HR/Admin's LEAVE_PURGE cleanup or
  // a fresh reject/approve decision instead, not a quiet client-side pull-back.
  // Mirrors the backend: the owner can cancel their own, admin/HR can cancel
  // anyone's — a plain manager cannot cancel a subordinate's request (that's
  // not the same authority as rejecting it), so their own row is all that
  // qualifies.
  const canCancel = (leave: LeaveApplication) =>
    (leave.status === 'pending' || leave.status === 'manager_approved')
    && (leave.employeeId === user?.id || isPrivileged(user));

  const confirmCancel = async () => {
    if (!cancelTarget) return;
    const targetId = cancelTarget.id;
    setCancelling(true);
    try {
      await leaveService.cancelLeave(targetId);
      // The backend keeps the row and flips its status rather than removing
      // it, so reflect that in place instead of dropping it from the list.
      setLeaves((current) => current.map((leave) => (
        leave.id === targetId ? { ...leave, status: 'cancelled' } : leave
      )));
      setNotice({ type: 'success', text: 'Leave request cancelled.' });
      setCancelTarget(null);
    } catch (error) {
      setNotice({
        type: 'error',
        text: error instanceof Error ? error.message : 'The request could not be cancelled. Please try again.',
      });
      setCancelTarget(null);
    } finally {
      setCancelling(false);
    }
  };

  const confirmDelete = async () => {
    if (!deleteTarget) return;
    const deletedId = deleteTarget.id;
    setDeleting(true);
    try {
      await leaveService.purgeLeave(deletedId);
      // Drop the row from the list in place. Reloading would flip isLoading and
      // swap the whole page for the spinner, which reads as a full page refresh
      // for the sake of removing one row we already know is gone.
      setLeaves((current) => current.filter((leave) => leave.id !== deletedId));
      setNotice({ type: 'success', text: 'Leave record deleted from history.' });
      setDeleteTarget(null);
    } catch (error) {
      setNotice({
        type: 'error',
        text: error instanceof Error ? error.message : 'The record could not be deleted. Please try again.',
      });
      setDeleteTarget(null);
    } finally {
      setDeleting(false);
    }
  };

  // A From date after the To date can never match anything — the two active
  // filters (>= start AND <= end) exclude every record — so it silently read
  // as "no leaves" with nothing telling the user why. String comparison is
  // safe here since both are plain yyyy-mm-dd values from <input type="date">.
  const dateRangeError = dateFilterMode === 'range' && startDateFilter && endDateFilter && startDateFilter > endDateFilter
    ? 'Applied From date must be before Applied To date.'
    : '';

  if (isLoading) {
    return (
      <div className="leave-history-loading">
        <div className="spinner"></div>
      </div>
    );
  }

  return (
    <div className="leave-history-page">
      <Card className="leave-history-card">
        <div className="page-header">
          <h2 className="page-title">Leave History</h2>
          {(user?.role === 'manager' || user?.role === 'admin') && (
            <p className="page-subtitle">View leave applications for yourself and your team</p>
          )}
        </div>

        {notice && (
          <div className={`lh-notice lh-notice-${notice.type}`} role="status">
            <span>{notice.text}</span>
            <button type="button" onClick={() => setNotice(null)} aria-label="Dismiss"><FiX /></button>
          </div>
        )}

        {/* Scope toggle: own leaves vs. the team's — matches AttendanceHistory's
            "My attendance"/"Employees" segmented control. */}
        {(user?.role === 'manager' || user?.role === 'admin') && (
          <div className="lh-scope-row">
            <div className="lh-segmented" aria-label="Leave history scope">
              <button
                type="button"
                className={viewMode === 'me' ? 'active' : ''}
                onClick={() => { setViewMode('me'); setSelectedEmployee('all'); }}
              >
                <FiUser /> My leaves
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
        <div className="history-filters lh-top-filters">
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
            options={[
              { value: 'all', label: 'All Status' },
              { value: 'pending', label: 'Pending HR / Admin Approval' },
              { value: 'approved', label: 'Approved' },
              { value: 'rejected', label: 'Rejected' },
              { value: 'cancelled', label: 'Cancelled' },
            ]}
          />
        </div>

        {/* Date filter mode toggle + live result count */}
        <div className="lh-filter-bar">
          <div className="lh-filter-toggle">
            <button
              className={`lh-toggle-btn${dateFilterMode === 'month' ? ' lh-toggle-active' : ''}`}
              onClick={() => setDateFilterMode('month')}
            >
              By Month
            </button>
            <button
              className={`lh-toggle-btn${dateFilterMode === 'range' ? ' lh-toggle-active' : ''}`}
              onClick={() => setDateFilterMode('range')}
            >
              Date Range
            </button>
          </div>
          <span className="lh-filter-count">
            {dateFilterMode === 'month'
              ? `${leaves.length} ${leaves.length === 1 ? 'leave' : 'leaves'} in ${monthOptions[monthFilter - 1]?.label} ${yearFilter}`
              : `${leaves.length} ${leaves.length === 1 ? 'leave' : 'leaves'}`}
          </span>
        </div>

        {/* Date filter inputs (month picker OR between-dates range) */}
        <div className="history-filters lh-date-filters">
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
                value={startDateFilter}
                onChange={(e) => setStartDateFilter(e.target.value)}
                error={dateRangeError}
              />
              <Input
                label="Applied To"
                type="date"
                value={endDateFilter}
                onChange={(e) => setEndDateFilter(e.target.value)}
                error={dateRangeError}
              />
            </>
          )}
        </div>

        {/* Leave List */}
        <div className="leaves-history-list">
          {leaves.length === 0 ? (
            <div className="empty-state">
              <FiCalendar size={48} />
              <p>No leave applications found</p>
            </div>
          ) : (
            leaves.map((leave) => (
              <div key={leave.id} className="leave-history-item">
                <div className="leave-history-header">
                  <div className="leave-history-employee">
                    <div className="leave-avatar">{initials(leave.employeeName)}</div>
                    <div className="leave-employee-details">
                      <span className="leave-employee-name">{leave.employeeName || 'Unknown'}</span>
                      <span className="leave-type">{leave.leaveType}</span>
                    </div>
                  </div>
                  <div className={getStatusBadgeClass(leave.status)}>
                    {statusIcon(leave.status)}
                    <span>{getStatusDisplay(leave.status)}</span>
                  </div>
                </div>

                <div className="leave-history-body">
                  <div className="leave-dates-info">
                    <div className="date-item">
                      <span className="date-icon"><FiCalendar /></span>
                      <span className="date-copy">
                        <span className="date-label">Start Date</span>
                        <span className="date-value">{longDate(leave.startDate)}</span>
                      </span>
                    </div>
                    <div className="date-item">
                      <span className="date-icon"><FiCalendar /></span>
                      <span className="date-copy">
                        <span className="date-label">End Date</span>
                        <span className="date-value">{longDate(leave.endDate)}</span>
                      </span>
                    </div>
                    <div className="date-item">
                      <span className="date-icon"><FiClock /></span>
                      <span className="date-copy">
                        <span className="date-label">Duration</span>
                        <span className="date-value">{leave.duration} {leave.duration === 1 ? 'day' : 'days'}</span>
                      </span>
                    </div>
                    <div className="date-item">
                      <span className="date-icon"><FiFileText /></span>
                      <span className="date-copy">
                        <span className="date-label">Applied On</span>
                        <span className="date-value">{longDate(leave.appliedDate)}</span>
                      </span>
                    </div>
                  </div>

                  {leave.reason && (
                    <div className="leave-reason">
                      <span className="reason-icon"><FiMessageSquare /></span>
                      <span className="reason-copy">
                        <span className="reason-label">Reason</span>
                        <span className="reason-text">{leave.reason}</span>
                      </span>
                    </div>
                  )}

                  {/* Display rejection reason if leave is rejected */}
                  {leave.status === 'rejected' && leave.rejectionReason && (
                    <div className="leave-rejection-reason">
                      <span className="rejection-label">Rejection Reason</span>
                      <span className="rejection-text">{leave.rejectionReason}</span>
                    </div>
                  )}

                  <div className="leave-history-footer">
                    <div className="leave-meta">
                      <span className="lh-meta-item">
                        <FiFileText aria-hidden="true" />
                        Applied: <strong>{longDate(leave.appliedDate)}</strong>
                      </span>
                      {leave.managerApprovedAt && (
                        <span className="lh-meta-item">
                          <FiShield aria-hidden="true" />
                          Manager Approved: <strong>{longDate(leave.managerApprovedAt)}</strong>
                          {leave.managerApproverName ? <> by <strong>{leave.managerApproverName}</strong></> : null}
                        </span>
                      )}
                      {leave.approvedAt && (
                        <span className="lh-meta-item">
                          <FiShield aria-hidden="true" />
                          Final Approved: <strong>{longDate(leave.approvedAt)}</strong>
                          {leave.approvedByName ? <> by <strong>{leave.approvedByName}</strong></> : null}
                        </span>
                      )}
                    </div>
                    <div className="lh-footer-actions">
                      {canCancel(leave) && (
                        <button
                          type="button"
                          className="lh-cancel-btn"
                          onClick={() => { setNotice(null); setCancelTarget(leave); }}
                          title="Withdraw this leave request"
                        >
                          <FiXCircle /> Cancel request
                        </button>
                      )}
                      {canDelete(leave) && (
                        <button
                          type="button"
                          className="lh-cancel-btn"
                          onClick={() => { setNotice(null); setDeleteTarget(leave); }}
                          title="Remove this record from the history"
                        >
                          <FiTrash2 /> Delete record
                        </button>
                      )}
                    </div>
                  </div>
                </div>
              </div>
            ))
          )}
        </div>
      </Card>

      {deleteTarget && (
        <div
          className="lh-confirm-backdrop"
          onMouseDown={(event) => event.target === event.currentTarget && setDeleteTarget(null)}
        >
          <div className="lh-confirm" role="dialog" aria-modal="true" aria-labelledby="lh-confirm-title">
            <FiAlertTriangle />
            <h3 id="lh-confirm-title">Delete this record?</h3>
            <p>
              {deleteTarget.employeeName ? `${deleteTarget.employeeName} — ` : ''}
              {deleteTarget.leaveType},{' '}
              <strong>{new Date(deleteTarget.startDate).toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' })}</strong>
              {deleteTarget.endDate !== deleteTarget.startDate && (
                <> to <strong>{new Date(deleteTarget.endDate).toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' })}</strong></>
              )}
              {' '}({deleteTarget.duration} {deleteTarget.duration === 1 ? 'day' : 'days'}).
            </p>
            <p className="lh-confirm-note">
              This erases the record permanently — it cannot be undone, and the
              leave balance is not adjusted. Records that payroll depends on are
              refused automatically.
            </p>
            <footer>
              <button type="button" onClick={() => setDeleteTarget(null)} disabled={deleting}>Keep record</button>
              <button type="button" className="lh-confirm-danger" onClick={confirmDelete} disabled={deleting}>
                {deleting ? 'Deleting…' : 'Delete record'}
              </button>
            </footer>
          </div>
        </div>
      )}

      {cancelTarget && (
        <div
          className="lh-confirm-backdrop"
          onMouseDown={(event) => event.target === event.currentTarget && setCancelTarget(null)}
        >
          <div className="lh-confirm" role="dialog" aria-modal="true" aria-labelledby="lh-cancel-confirm-title">
            <FiAlertTriangle />
            <h3 id="lh-cancel-confirm-title">Cancel this leave request?</h3>
            <p>
              {cancelTarget.employeeName ? `${cancelTarget.employeeName} — ` : ''}
              {cancelTarget.leaveType},{' '}
              <strong>{new Date(cancelTarget.startDate).toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' })}</strong>
              {cancelTarget.endDate !== cancelTarget.startDate && (
                <> to <strong>{new Date(cancelTarget.endDate).toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' })}</strong></>
              )}
              {' '}({cancelTarget.duration} {cancelTarget.duration === 1 ? 'day' : 'days'}).
            </p>
            <p className="lh-confirm-note">
              This withdraws the request. It stays in history marked as cancelled.
            </p>
            <footer>
              <button type="button" onClick={() => setCancelTarget(null)} disabled={cancelling}>Keep request</button>
              <button type="button" className="lh-confirm-danger" onClick={confirmCancel} disabled={cancelling}>
                {cancelling ? 'Cancelling…' : 'Cancel request'}
              </button>
            </footer>
          </div>
        </div>
      )}
    </div>
  );
};

export default LeaveHistory;

