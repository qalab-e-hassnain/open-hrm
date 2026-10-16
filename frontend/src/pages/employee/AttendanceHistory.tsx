import { Link } from 'react-router-dom';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  FiAlertCircle, FiCalendar, FiCheckCircle, FiClock, FiDownload,
  FiFileText, FiFilter, FiGrid, FiList, FiMapPin, FiPrinter, FiRefreshCw, FiUser, FiUsers, FiUserX, FiX,
} from 'react-icons/fi';
import { attendanceService } from '../../services/attendanceService';
import { employeeService } from '../../services/employeeService';
import { useAuthStore } from '../../store/authStore';
import { type Employee } from '../../types';
import { formatPktTime } from '../../utils/datetime';
import { ROUTES } from '../../config/constants';
import './AttendanceHistory.css';

type AttendanceRow = {
  id: string | number;
  employee_id?: number;
  employee_name?: string;
  date: string;
  check_in_time?: string;
  check_out_time?: string;
  check_in_address?: string;
  check_out_address?: string;
  working_hours?: number;
  regular_hours?: number;
  /** Approved claims ONLY since the overtime rework — 0.0 on nearly every row.
   *  Clock time past the standard day is `extra_hours`, and no money is
   *  attached to that. `overtime_bonus` and `overtime_rate` are deliberately
   *  absent from this type: they have always been 0.0, and typing them is how
   *  they end up rendered as money again. */
  overtime_hours?: number;
  /** Time past the standard day. Unclaimed and unpaid until the employee files
   *  it under Time Logs and it is approved. */
  extra_hours?: number;
  /** Whether an approved overtime claim exists for this day. */
  overtime_claimed?: boolean;
  /** Did the whole DAY reach its hours — several sessions can make one day. */
  day_completed?: boolean;
  day_total_hours?: number;
  /** That person's standard day. 4.0 on a part-time contract, so this is never
   *  assumed to be 8. */
  day_required_hours?: number;
  day_type?: string;
  status?: 'completed' | 'short' | 'incomplete';
  /** The session was closed by the cap below, not by the employee. */
  auto_checked_out?: boolean;
};

type FilterState = {
  month: number;
  year: number;
  startDate: string;
  endDate: string;
  status: 'all' | 'completed' | 'short' | 'incomplete' | 'auto';
  department: string;
};
type SortKey = 'date' | 'checkIn' | 'checkOut' | 'hours' | 'extra' | 'status';

const MONTHS = [
  'January', 'February', 'March', 'April', 'May', 'June',
  'July', 'August', 'September', 'October', 'November', 'December',
];

const todayKey = () => {
  const now = new Date();
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`;
};

/**
 * The cap that closes a running session automatically. Not a completion
 * threshold: a required day is `day_required_hours`, which is 4 on a part-time
 * contract, and whether a day was completed is the server's answer.
 */
const AUTO_CLOSE_HOURS = 8;

type DayStatus = 'completed' | 'short' | 'incomplete';

/**
 * The server's verdict, read rather than recomputed.
 *
 * A working day is capped and closed automatically, so one day is often stored
 * as several sessions — an 8-hour block plus an evening block. This screen used
 * to judge hours against 8 in the browser, which labelled the evening session
 * of an eleven-hour day "Not completed". `status` already accounts for the
 * whole day: `completed` and `short` both mean checked out, and they differ
 * only in whether the DAY reached its required hours.
 */
const rowStatus = (record: AttendanceRow): DayStatus => {
  if (record.status === 'completed' || record.status === 'short' || record.status === 'incomplete') {
    return record.status;
  }
  // A payload from before the rework. The only thing safe to read off a single
  // row is whether its session is still open — completion is the day's
  // business, and `day_completed` is the only other thing worth trusting.
  if (!record.check_out_time) return 'incomplete';
  return record.day_completed === false ? 'short' : 'completed';
};

/**
 * One verdict for a whole date, for the calendar and the heat strip. Every
 * session of a day already carries that day's verdict, so this only has to
 * notice an open one.
 */
const verdictForDate = (sessions: AttendanceRow[]): DayStatus | null => {
  if (!sessions.length) return null;
  if (sessions.some((session) => rowStatus(session) === 'incomplete')) return 'incomplete';
  if (sessions.some((session) => rowStatus(session) === 'short')) return 'short';
  return 'completed';
};

/** The day behind the row: what the person actually did, against what they owe. */
const dayContext = (record: AttendanceRow): string | null => {
  const total = record.day_total_hours;
  if (typeof total !== 'number') return null;
  const required = record.day_required_hours;
  return typeof required === 'number'
    ? `${formatHours(total)} of ${formatHours(required)} that day`
    : `${formatHours(total)} that day`;
};

/** Extra time nobody has claimed is extra time nobody will be paid for. */
const hasUnclaimedExtra = (record: AttendanceRow): boolean =>
  Boolean(record.extra_hours && record.extra_hours > 0) && !record.overtime_claimed;

const STATUS_COPY: Record<DayStatus, { label: string; hint: string }> = {
  completed: { label: 'Completed', hint: 'Checked out, and the day reached its required hours.' },
  short: { label: 'Short day', hint: 'Checked out, but the day never reached its required hours. A day can run over several sessions — this is the whole day, not this one session.' },
  incomplete: { label: 'Still open', hint: 'No check-out recorded, so this session is still running. Not the same thing as a short day.' },
};

const STATUS_FILTER_LABEL: Record<FilterState['status'], string> = {
  all: 'All statuses',
  completed: 'Completed',
  short: 'Short day',
  incomplete: 'Still open',
  auto: 'Auto checked out',
};

// Every state a day can be in, in the order they are worth looking for.
// "Completed" and "Not completed" between them covered 80 of October's 87
// days, which left the seven worth investigating with no way to find them.
const STATUS_FILTERS = ['all', 'completed', 'short', 'incomplete', 'auto'] as const;

const AttendanceHistory = () => {
  // setIsCheckedIn still syncs the shared store from the status request below,
  // so the dashboard's check-in button is correct after visiting this page.
  const { user, setIsCheckedIn } = useAuthStore();
  const now = new Date();
  const storedView = localStorage.getItem('hrm-attendance-view');
  const [records, setRecords] = useState<AttendanceRow[]>([]);
  const [employees, setEmployees] = useState<Employee[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [error, setError] = useState('');
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);
  const [filterType, setFilterType] = useState<'month' | 'range'>('month');
  const [displayMode, setDisplayMode] = useState<'list' | 'calendar'>(storedView === 'calendar' ? 'calendar' : 'list');
  const [viewMode, setViewMode] = useState<'me' | 'employees'>('me');
  const [selectedEmployeeId, setSelectedEmployeeId] = useState('');
  const [selectedDate, setSelectedDate] = useState('');
  const [metricFilter, setMetricFilter] = useState<'all' | 'attended' | 'extra' | 'absent'>('all');
  const [drawerRecord, setDrawerRecord] = useState<AttendanceRow | null>(null);
  const [sort, setSort] = useState<{ key: SortKey; direction: 'asc' | 'desc' }>({ key: 'date', direction: 'desc' });
  const [isExportingPdf, setIsExportingPdf] = useState(false);
  const recordsRef = useRef<HTMLElement>(null);
  const [filters, setFilters] = useState<FilterState>({
    month: now.getMonth() + 1,
    year: now.getFullYear(),
    startDate: '',
    endDate: '',
    status: 'all',
    department: '',
  });

  const canViewOthers = user?.role === 'admin' || user?.role === 'manager';
  const viewingOwn = viewMode === 'me';
  const viewingAllEmployees = !viewingOwn && !selectedEmployeeId;
  const employeeMap = useMemo(() => new Map(employees.map((employee) => [Number(employee.id), employee])), [employees]);
  const departments = useMemo(() => Array.from(new Set(employees.map((employee) => employee.department).filter(Boolean))).sort(), [employees]);
  const availableEmployees = useMemo(
    () => filters.department ? employees.filter((employee) => employee.department === filters.department) : employees,
    [employees, filters.department],
  );

  useEffect(() => {
    if (!canViewOthers) return;
    employeeService.getDirectory()
      .then((list) => setEmployees(list.filter((employee) => employee.id !== String(user?.id))))
      .catch(() => setError('Employee filters are temporarily unavailable. Attendance records remain accessible.'));
  }, [canViewOthers, user?.id]);

  const loadAttendance = useCallback(async (refresh = false) => {
    if (!user?.id) return;
    if (validateDateRange(filterType, filters.startDate, filters.endDate)) {
      // Invalid range: show it as "no records" rather than sending a request
      // whose start/end would just get swapped or ignored server-side.
      setRecords([]);
      setError('');
      return;
    }
    if (refresh) setIsRefreshing(true);
    else setIsLoading(true);
    setError('');
    try {
      // limit is deliberately high when reviewing every employee at once: a
      // per-employee cap here would silently truncate the combined result
      // once enough people/days accumulate, instead of just being slower.
      const params: Parameters<typeof attendanceService.filterAttendance>[0] = {
        limit: viewingOwn || selectedEmployeeId ? 250 : 2000,
      };
      if (filterType === 'month') {
        params.month = filters.month;
        params.year = filters.year;
      } else {
        if (filters.startDate) params.startDate = filters.startDate;
        if (filters.endDate) params.endDate = filters.endDate;
      }
      // Status is filtered client-side, and has to be: the server's
      // `record_status` query still asks only whether a check-out exists,
      // while this page distinguishes a completed day from a short one off
      // each row's own `status`.
      if (viewingOwn) {
        params.employeeId = Number(user.id);
      } else {
        // Reviewing other people: the signed-in admin's or manager's own rows
        // belong in "My attendance", not in this list. Without this the
        // unfiltered request returns every record the caller can see, their
        // own included.
        params.excludeSelf = true;
        if (selectedEmployeeId) params.employeeId = Number(selectedEmployeeId);
      }

      const [attendanceResult, statusResult] = await Promise.allSettled([
        attendanceService.filterAttendance(params),
        attendanceService.getAttendanceStatus(),
      ]);
      if (attendanceResult.status === 'rejected') throw attendanceResult.reason;
      setRecords(attendanceResult.value ?? []);
      if (statusResult.status === 'fulfilled') setIsCheckedIn(Boolean(statusResult.value?.isCheckedIn));
      setLastUpdated(new Date());
    } catch {
      setError('Attendance could not be synced. Existing records are still shown.');
    } finally {
      setIsLoading(false);
      setIsRefreshing(false);
    }
    // Deliberately NOT `filters` as a whole: status and department are both
    // filtered client-side (see the comment above and scopedRecords below),
    // so including the whole object here refetched the exact same request
    // from the server every time either one changed — a pointless network
    // round-trip and loading flicker for a filter that already had its
    // answer sitting in `records`.
  }, [filterType, filters.month, filters.year, filters.startDate, filters.endDate, selectedEmployeeId, setIsCheckedIn, user?.id, viewingOwn]);

  useEffect(() => { void loadAttendance(); }, [loadAttendance]);

  useEffect(() => {
    localStorage.setItem('hrm-attendance-view', displayMode);
  }, [displayMode]);

  // Department/date/status narrow WHO and WHEN we're looking at; metricFilter
  // (the stat-card click toggle) only ever narrows which of those rows the
  // TABLE displays — the stat cards above it are supposed to keep showing the
  // scoped total for context, not shrink to match their own toggle. Splitting
  // this in two, so stats/teamSize below can depend on scopedRecords (real
  // filters only) while the table keeps the metricFilter-narrowed view.
  const scopedRecords = useMemo(() => records.filter((record) => {
    const employee = record.employee_id ? employeeMap.get(record.employee_id) : undefined;
    if (!viewingOwn && filters.department && employee?.department !== filters.department) return false;
    if (selectedDate && record.date !== selectedDate) return false;
    // "Auto checked out" is a fact about how the day ended, not one of the
    // statuses a day can hold, so it is matched on its own flag.
    if (filters.status === 'auto') {
      if (!record.auto_checked_out) return false;
    } else if (filters.status !== 'all' && rowStatus(record) !== filters.status) {
      return false;
    }
    return true;
  }), [employeeMap, filters.department, filters.status, records, selectedDate, viewingOwn]);

  const filteredRecords = useMemo(() => scopedRecords.filter((record) => {
    if (metricFilter === 'extra' && !(record.extra_hours && record.extra_hours > 0)) return false;
    if (metricFilter === 'attended' && !record.working_hours) return false;
    return true;
  }), [metricFilter, scopedRecords]);

  const displayedRecords = useMemo(() => [...filteredRecords].sort((left, right) => {
    const value = (record: AttendanceRow) => {
      if (sort.key === 'checkIn') return record.check_in_time || '';
      if (sort.key === 'checkOut') return record.check_out_time || '';
      if (sort.key === 'hours') return record.working_hours || 0;
      if (sort.key === 'extra') return record.extra_hours || 0;
      if (sort.key === 'status') return STATUS_COPY[rowStatus(record)].label;
      return record.date;
    };
    const a = value(left);
    const b = value(right);
    const result = typeof a === 'number' && typeof b === 'number' ? a - b : String(a).localeCompare(String(b));
    return result * (sort.direction === 'asc' ? 1 : -1);
  }), [filteredRecords, sort]);

  const toggleSort = (key: SortKey) => setSort((current) => ({
    key,
    direction: current.key === key && current.direction === 'desc' ? 'asc' : 'desc',
  }));

  // Was keyed on the raw, unfiltered `records` — so picking a Department (or
  // any other real filter) narrowed the table underneath but left every stat
  // card above it (and its "Across N employees" note) reporting totals for
  // the whole team regardless. Now scoped the same way the table is.
  const teamSize = useMemo(() => new Set(scopedRecords.map((record) => record.employee_id)).size, [scopedRecords]);

  // Who has not checked in.
  //
  // Attendance only ever holds people who DID check in — an absence has no row
  // of its own — so this is the roster minus whoever has a record that day.
  // It is a question about one day, so it defaults to today rather than
  // spanning the filtered period.
  const absenceDate = selectedDate || todayKey();

  // Everyone would look absent on a day the loaded records do not cover, which
  // is worse than showing nothing. Say so instead.
  const absenceDateInPeriod = filterType === 'month'
    ? absenceDate.startsWith(`${filters.year}-${String(filters.month).padStart(2, '0')}`)
    : Boolean(filters.startDate && filters.endDate
        && absenceDate >= filters.startDate && absenceDate <= filters.endDate);

  const absentees = useMemo(() => {
    if (viewingOwn || !absenceDateInPeriod) return [];
    const checkedIn = new Set(
      records.filter((record) => record.date === absenceDate)
        .map((record) => Number(record.employee_id)),
    );
    return availableEmployees
      .filter((employee) => employee.status !== 'inactive')
      .filter((employee) => !checkedIn.has(Number(employee.id)))
      .sort((a, b) => a.name.localeCompare(b.name));
  }, [absenceDate, absenceDateInPeriod, availableEmployees, records, viewingOwn]);

  const stats = useMemo(() => {
    // "days" counts distinct employee+date pairs, not rows: an overtime
    // session creates a second Attendance row on the same date for the same
    // person (see check-in's is_overtime_session handling), which overcounted
    // attended days when keyed on date alone. Keying on employee+date (not
    // just date) also keeps this correct when viewing every employee at
    // once — two different people both attending on the same day are two
    // attended days, not one; a date-only key would have collapsed them.
    const attendedDays = new Set<string>();
    let hours = 0;
    // Two different things, deliberately counted apart: time on the clock past
    // the standard day, and time an approved claim will actually pay for.
    let extra = 0;
    let claimed = 0;
    scopedRecords.forEach((record) => {
      if (record.working_hours) attendedDays.add(`${record.employee_id ?? 'self'}|${record.date}`);
      hours += record.working_hours ?? 0;
      extra += record.extra_hours ?? 0;
      claimed += record.overtime_hours ?? 0;
    });
    return { days: attendedDays.size, hours, extra, claimed };
  }, [scopedRecords]);

  const exportCsv = () => {
    if (!displayedRecords.length) return;
    const includeEmployee = !viewingOwn;
    const headings = [...(includeEmployee ? ['Employee'] : []), 'Date', 'Check in', 'Check out',
      'Session hours', 'Day total', 'Day required', 'Extra', 'Approved overtime', 'Status'];
    const rows = displayedRecords.map((record) => [
      ...(includeEmployee ? [record.employee_name || employeeMap.get(record.employee_id || 0)?.name || 'Unknown'] : []),
      formatDate(record.date), formatTime(record.check_in_time),
      record.check_out_time ? formatTime(record.check_out_time) : 'Not checked out',
      formatHours(record.working_hours), formatHours(record.day_total_hours),
      formatHours(record.day_required_hours), formatHours(record.extra_hours),
      formatHours(record.overtime_hours), STATUS_COPY[rowStatus(record)].label,
    ]);
    const csv = [headings, ...rows].map((row) => row.map(csvCell).join(',')).join('\r\n');
    const link = document.createElement('a');
    link.href = URL.createObjectURL(new Blob(['\ufeff' + csv], { type: 'text/csv;charset=utf-8' }));
    link.download = `attendance-${filters.year}-${String(filters.month).padStart(2, '0')}.csv`;
    link.click();
    URL.revokeObjectURL(link.href);
  };

  // Genuinely distinct from Print (below): downloads an actual .pdf file in
  // one click, no OS print dialog involved. Was previously window.print()
  // labeled "Export PDF" — same bug already fixed on Tax Certificate.
  const handleSavePdf = async () => {
    if (!recordsRef.current) return;
    setIsExportingPdf(true);
    try {
      const { exportElementToPdf } = await import('../../utils/pdfExport');
      await exportElementToPdf(
        recordsRef.current,
        `Attendance-${filters.year}-${String(filters.month).padStart(2, '0')}.pdf`,
        // The day-activity intensity strip is an on-screen widget (31 tiny
        // buttons) that doesn't belong in an exported document — same reason
        // the print CSS already hides .attendance-intensity.
        { ignoreElements: (el) => el.classList.contains('attendance-intensity') },
      );
    } finally {
      setIsExportingPdf(false);
    }
  };

  const clearFilters = () => {
    setFilters((current) => ({ ...current, status: 'all', department: '' }));
    setSelectedEmployeeId('');
    setSelectedDate('');
    setMetricFilter('all');
  };

  const dateRangeError = useMemo(
    () => validateDateRange(filterType, filters.startDate, filters.endDate),
    [filterType, filters.startDate, filters.endDate],
  );

  const activeFilterCount = Number(filters.status !== 'all') + Number(Boolean(filters.department))
    + Number(Boolean(selectedEmployeeId)) + Number(Boolean(selectedDate)) + Number(metricFilter !== 'all');
  const periodLabel = filterType === 'month'
    ? `${MONTHS[filters.month - 1]} ${filters.year}`
    : `${filters.startDate || 'Start'} – ${filters.endDate || 'Today'}`;

  return (
    <main className="attendance-page" id="main-content">
      <header className="attendance-page-header">
        <div className="attendance-heading">
          <span className="attendance-heading-icon"><FiCalendar /></span>
          <div><p>People / Attendance</p><h1>Attendance history</h1><span>Review working time, exceptions, and overtime records.</span></div>
        </div>
        {/* Check in/out lives on the dashboard only — one place to perform the
            action, so there is no question of which button is authoritative.
            This page reviews the record. */}
        <div className="attendance-header-actions">
          <button className="attendance-export" onClick={exportCsv} disabled={!displayedRecords.length}><FiDownload /> Export Excel</button>
          <button className="attendance-export" onClick={handleSavePdf} disabled={!displayedRecords.length || isExportingPdf}><FiFileText /> {isExportingPdf ? 'Saving…' : 'Save PDF'}</button>
          <button className="attendance-export" onClick={() => window.print()} disabled={!displayedRecords.length}><FiPrinter /> Print</button>
        </div>
      </header>

      <section className="attendance-controls" aria-label="Attendance controls">
        <div className="attendance-scope-row">
          {canViewOthers ? <div className="attendance-segmented" aria-label="Attendance scope">
            <button className={viewMode === 'me' ? 'active' : ''} onClick={() => { setViewMode('me'); setSelectedEmployeeId(''); }}><FiUser /> My attendance</button>
            <button className={viewMode === 'employees' ? 'active' : ''} onClick={() => setViewMode('employees')}><FiUsers /> Employees</button>
          </div> : <strong>My attendance</strong>}
          <div className="attendance-sync"><i />{lastUpdated ? `Synced ${relativeTime(lastUpdated)}` : 'Syncing'}<span />Timezone: Asia/Karachi</div>
        </div>

        <div className="attendance-filter-head">
          <strong><FiFilter /> Filters {activeFilterCount > 0 && <b>{activeFilterCount}</b>}</strong>
          <div className="attendance-segmented compact">
            <button className={filterType === 'month' ? 'active' : ''} onClick={() => setFilterType('month')}>By month</button>
            <button className={filterType === 'range' ? 'active' : ''} onClick={() => setFilterType('range')}>Date range</button>
          </div>
        </div>

        <div className={`attendance-filter-grid${viewingOwn ? ' own' : ''}`}>
          {filterType === 'month' ? <>
            <label>Month<select value={filters.month} onChange={(event) => setFilters({ ...filters, month: Number(event.target.value) })}>{MONTHS.map((month, index) => <option key={month} value={index + 1}>{month}</option>)}</select></label>
            <label>Year<input type="number" min="2020" max="2100" value={filters.year} onChange={(event) => setFilters({ ...filters, year: Number(event.target.value) })} /></label>
          </> : <>
            <label className={dateRangeError && dateRangeError.startsWith('Start') ? 'attendance-field-invalid' : ''}>
              Start date<input type="date" max={isoDate(now)} value={filters.startDate} onChange={(event) => setFilters({ ...filters, startDate: event.target.value })} />
            </label>
            <label className={dateRangeError && dateRangeError.startsWith('End') ? 'attendance-field-invalid' : ''}>
              End date<input type="date" max={isoDate(now)} value={filters.endDate} onChange={(event) => setFilters({ ...filters, endDate: event.target.value })} />
            </label>
          </>}
          <label>Status<select value={filters.status} onChange={(event) => setFilters({ ...filters, status: event.target.value as FilterState['status'] })}>{STATUS_FILTERS.map((value) => <option key={value} value={value}>{STATUS_FILTER_LABEL[value]}</option>)}</select></label>
          {!viewingOwn && <label>Department<select value={filters.department} onChange={(event) => { setFilters({ ...filters, department: event.target.value }); setSelectedEmployeeId(''); }}><option value="">All departments</option>{departments.map((department) => <option key={department}>{department}</option>)}</select></label>}
          {!viewingOwn && <label>Employee<select value={selectedEmployeeId} onChange={(event) => setSelectedEmployeeId(event.target.value)}><option value="">All employees</option>{availableEmployees.map((employee) => <option key={employee.id} value={employee.id}>{employee.name}{employee.designation ? ` — ${employee.designation}` : ''}</option>)}</select></label>}
          <button className="attendance-apply" onClick={() => loadAttendance(true)} disabled={isRefreshing || Boolean(dateRangeError)}>{isRefreshing ? <FiRefreshCw className="spin" /> : <FiFilter />}{isRefreshing ? 'Refreshing' : 'Apply filters'}</button>
        </div>
        {dateRangeError && <div className="attendance-filter-error" role="alert"><FiAlertCircle /> {dateRangeError}</div>}
        <div className="attendance-filter-summary"><span><strong>{periodLabel}</strong> · {STATUS_FILTER_LABEL[filters.status]}{filters.department ? ` · ${filters.department}` : ''}</span>{activeFilterCount > 0 && <button onClick={clearFilters}>Clear filters</button>}</div>

        {activeFilterCount > 0 && <div className="attendance-chips">
          {filters.status !== 'all' && <button onClick={() => setFilters({ ...filters, status: 'all' })}>Status: {STATUS_FILTER_LABEL[filters.status]} <FiX /></button>}
          {filters.department && <button onClick={() => setFilters({ ...filters, department: '' })}>Department: {filters.department} <FiX /></button>}
          {selectedEmployeeId && <button onClick={() => setSelectedEmployeeId('')}>Employee: {employeeMap.get(Number(selectedEmployeeId))?.name} <FiX /></button>}
          {selectedDate && <button onClick={() => setSelectedDate('')}>Date: {formatDate(selectedDate)} <FiX /></button>}
          {metricFilter !== 'all' && <button onClick={() => setMetricFilter('all')}>View: {capitalize(metricFilter)} <FiX /></button>}
          <button className="clear" onClick={clearFilters}>Clear all</button>
        </div>}
      </section>

      {error && <div className="attendance-warning" role="alert"><FiAlertCircle /><div><strong>Sync issue</strong><span>{error}</span></div><button onClick={() => loadAttendance(true)}>Retry</button></div>}

      {isLoading ? <StatsSkeleton /> : <section className="attendance-stats" aria-label="Attendance summary">
        {viewingAllEmployees
          ? <StatCard label="Total attended days" value={String(stats.days)} note={`Across ${teamSize} employee${teamSize === 1 ? '' : 's'}`} icon={<FiCalendar />} active={metricFilter === 'attended'} onClick={() => setMetricFilter(metricFilter === 'attended' ? 'all' : 'attended')} />
          : <StatCard label="Days attended" value={String(stats.days)} note={`${stats.days} of ${workingDaysInPeriod(filterType, filters)} working days`} icon={<FiCalendar />} active={metricFilter === 'attended'} onClick={() => setMetricFilter(metricFilter === 'attended' ? 'all' : 'attended')} />}
        {!viewingOwn && !selectedEmployeeId && (
          <StatCard
            label="Not checked in"
            value={absenceDateInPeriod ? String(absentees.length) : '—'}
            note={absenceDateInPeriod
              ? `${formatDate(absenceDate)}${absenceDate === todayKey() ? ' (today)' : ''}`
              : 'Pick a date inside this period'}
            icon={<FiUserX />}
            active={metricFilter === 'absent'}
            onClick={() => setMetricFilter(metricFilter === 'absent' ? 'all' : 'absent')}
          />
        )}
        <StatCard label="Total hours" value={formatHours(stats.hours)} note={stats.days ? `Average: ${formatHours(stats.hours / stats.days)}/day` : 'No recorded hours'} icon={<FiClock />} />
        <StatCard label="Extra hours" value={formatHours(stats.extra)} note="Past the standard day · unpaid unless claimed" icon={<FiClock />} active={metricFilter === 'extra'} onClick={() => setMetricFilter(metricFilter === 'extra' ? 'all' : 'extra')} />
        <StatCard label="Approved overtime" value={formatHours(stats.claimed)} note="From approved Time Logs claims" icon={<FiCheckCircle />} />
      </section>}

      <section className="attendance-records" ref={recordsRef}>
        <header>
          <div><h2>Attendance records <span>{displayedRecords.length}</span></h2><p>{displayedRecords.length} record{displayedRecords.length === 1 ? '' : 's'} · {periodLabel}{viewingAllEmployees ? ` · ${teamSize} employee${teamSize === 1 ? '' : 's'}` : ''}</p></div>
          {/* A calendar cell holds one day, so it cannot show several people at
              once. Reviewing employees is list-only. */}
          {viewingOwn && <div className="attendance-segmented compact">
            <button className={displayMode === 'list' ? 'active' : ''} onClick={() => setDisplayMode('list')}><FiList /> List</button>
            <button className={displayMode === 'calendar' ? 'active' : ''} onClick={() => setDisplayMode('calendar')}><FiGrid /> Calendar</button>
          </div>}
        </header>

        {/* scopedRecords, not records: same "ignored the Department filter"
            gap as the stat cards — the day-activity colors below must match
            what the table actually shows, not the whole team regardless of
            filter. */}
        {filterType === 'month' && <IntensityStrip month={filters.month} year={filters.year} records={scopedRecords} selectedDate={selectedDate} onSelect={(date) => setSelectedDate(selectedDate === date ? '' : date)} />}

        {metricFilter === 'absent' ? (
          <AbsenceList
            employees={absentees}
            date={absenceDate}
            inPeriod={absenceDateInPeriod}
            department={filters.department}
          />
        ) : isLoading ? <TableSkeleton /> : !displayedRecords.length ? <div className="attendance-empty"><FiCalendar /><strong>{records.length ? 'No records match these filters' : 'No attendance records found'}</strong><span>{records.length ? 'Clear a filter or select another date.' : 'Attendance will appear after the first check-in for this period.'}</span>{activeFilterCount > 0 && <button onClick={clearFilters}>Clear filters</button>}</div>
          : displayMode === 'calendar' && viewingOwn ? <CalendarView month={filters.month} year={filters.year} records={displayedRecords} selectedId={drawerRecord?.id} onOpen={setDrawerRecord} />
            : <AttendanceTable records={displayedRecords} showEmployee={viewingAllEmployees} selectedId={drawerRecord?.id} sort={sort} onSort={toggleSort} onOpen={setDrawerRecord} />}
      </section>

      {drawerRecord && <RecordDrawer record={drawerRecord} employee={employeeMap.get(drawerRecord.employee_id || 0)} onClose={() => setDrawerRecord(null)} />}
    </main>
  );
};

const AttendanceTable = ({ records, showEmployee, selectedId, sort, onSort, onOpen }: { records: AttendanceRow[]; showEmployee: boolean; selectedId?: string | number; sort: { key: SortKey; direction: 'asc' | 'desc' }; onSort: (key: SortKey) => void; onOpen: (record: AttendanceRow) => void }) => <div className="attendance-table-wrap"><table className="attendance-table"><thead><tr>{showEmployee && <th>Employee</th>}<SortHeading label="Date" value="date" sort={sort} onSort={onSort} /><SortHeading label="Check in" value="checkIn" sort={sort} onSort={onSort} /><SortHeading label="Check out" value="checkOut" sort={sort} onSort={onSort} /><SortHeading label="Hours" value="hours" sort={sort} onSort={onSort} /><SortHeading label="Extra" value="extra" sort={sort} onSort={onSort} /><SortHeading label="Status" value="status" sort={sort} onSort={onSort} /></tr></thead><tbody>{records.map((record) => {
  // The server's verdict for the day this session belongs to. Nothing here
  // compares hours against a number.
  const status = rowStatus(record);
  const context = dayContext(record);
  return <tr key={record.id} className={selectedId === record.id ? 'selected' : ''} onClick={() => onOpen(record)} tabIndex={0} onKeyDown={(event) => event.key === 'Enter' && onOpen(record)}>{showEmployee && <td><strong>{record.employee_name || 'Unknown'}</strong></td>}<td><span className="date-cell"><i className={status} /> <strong>{formatDate(record.date)}</strong><small>{new Date(`${record.date}T00:00:00`).toLocaleDateString('en-US', { weekday: 'long' })}</small></span></td><td className="time-cell">{formatTime(record.check_in_time)}</td><td className="time-cell">{record.check_out_time ? <>{formatTime(record.check_out_time)}{isNextDay(record) && <small className="next-day">Next day</small>}</> : <span className="missing">Missing check-out</span>}</td><td><strong>{formatHours(record.working_hours)}</strong>{/* A 51-minute evening session stops looking like a failure once the day behind it is on screen. */}{context && <small className="day-context">{context}</small>}</td><td>{record.extra_hours ? <span className="overtime" title="Clock time past the standard day. No money is attached to it — it is paid only on an approved Time Logs claim.">+{formatHours(record.extra_hours)}</span> : <span className="empty-value">None</span>}{hasUnclaimedExtra(record) && <small className="unclaimed">Unclaimed</small>}</td><td><StatusBadge status={status} /></td></tr>;
})}</tbody></table></div>;

const SortHeading = ({ label, value, sort, onSort }: { label: string; value: SortKey; sort: { key: SortKey; direction: 'asc' | 'desc' }; onSort: (key: SortKey) => void }) => <th><button className={`table-sort${sort.key === value ? ' active' : ''}`} onClick={() => onSort(value)}>{label}{sort.key === value && <span>{sort.direction === 'asc' ? '↑' : '↓'}</span>}</button></th>;

const IntensityStrip = ({ month, year, records, selectedDate, onSelect }: { month: number; year: number; records: AttendanceRow[]; selectedDate: string; onSelect: (date: string) => void }) => {
  const count = new Date(year, month, 0).getDate();
  // Grouped, not keyed by date: reviewing a team puts several people on one day.
  const byDate = new Map<string, AttendanceRow[]>();
  records.forEach((record) => byDate.set(record.date, [...(byDate.get(record.date) ?? []), record]));
  const today = isoDate(new Date());
  return <div className="attendance-intensity"><div><strong>{MONTHS[month - 1]} {year}</strong><span>Attendance activity</span></div><div className="intensity-days">{Array.from({ length: count }, (_, index) => { const date = `${year}-${String(month).padStart(2, '0')}-${String(index + 1).padStart(2, '0')}`; const dayRecords = byDate.get(date) ?? []; const weekend = [0, 6].includes(new Date(`${date}T00:00:00`).getDay()); const hours = dayRecords.reduce((total, record) => total + (record.working_hours ?? 0), 0); const verdict = verdictForDate(dayRecords); const state = !dayRecords.length ? (weekend ? 'weekend' : 'empty') : verdict !== 'completed' ? 'incomplete' : dayRecords.some((record) => record.extra_hours) ? 'overtime' : 'present'; return <button key={date} className={`${state}${selectedDate === date ? ' selected' : ''}${today === date ? ' today' : ''}`} onClick={() => onSelect(date)} title={`${formatDate(date)} · ${dayRecords.length ? `${dayRecords.length} record${dayRecords.length === 1 ? '' : 's'} · ${formatHours(hours)} worked${verdict ? ` · ${STATUS_COPY[verdict].label}` : ''}` : weekend ? 'Weekend · no record' : 'No record'}`}>{index + 1}</button>; })}</div><div className="intensity-legend"><span><i className="present" />Completed</span><span><i className="overtime" />Extra hours</span><span><i className="incomplete" />Short or open</span></div></div>;
};

const CalendarView = ({ month, year, records, selectedId, onOpen }: { month: number; year: number; records: AttendanceRow[]; selectedId?: string | number; onOpen: (record: AttendanceRow) => void }) => {
  const firstDay = new Date(year, month - 1, 1).getDay();
  const dayCount = new Date(year, month, 0).getDate();
  const recordMap = new Map(records.map((record) => [record.date, record]));
  return <div className="attendance-calendar"><div className="calendar-weekdays">{['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'].map((day) => <span key={day}>{day}</span>)}</div><div className="calendar-grid">{Array.from({ length: firstDay }, (_, index) => <span key={`empty-${index}`} className="calendar-day blank" />)}{Array.from({ length: dayCount }, (_, index) => { const date = `${year}-${String(month).padStart(2, '0')}-${String(index + 1).padStart(2, '0')}`; const record = recordMap.get(date); const selected = record && selectedId === record.id; return <button key={date} className={`calendar-day ${record ? rowStatus(record) : 'none'}${selected ? ' selected' : ''}`} disabled={!record} onClick={() => record && onOpen(record)}><strong>{index + 1}</strong>{record ? <><StatusBadge status={rowStatus(record)} /><small>{record.check_out_time ? `${formatTime(record.check_in_time)} → ${formatTime(record.check_out_time)}` : `${formatTime(record.check_in_time)} → Missing checkout`}</small>{Boolean(record.extra_hours) && <span className="calendar-overtime" title="Past the standard day. Unpaid unless claimed under Time Logs.">Extra · {formatHours(record.extra_hours)}</span>}</> : <small>No record</small>}</button>; })}</div></div>;
};

/**
 * Who has not checked in on a given day.
 *
 * A list of people, not of sessions — there is no attendance row to show,
 * which is the whole point. Someone on approved leave still appears here: this
 * answers "who is not clocked in", and why is a separate question the leave
 * screen answers.
 */
const AbsenceList = ({ employees, date, inPeriod, department }: {
  employees: Employee[]; date: string; inPeriod: boolean; department: string;
}) => {
  if (!inPeriod) {
    return <div className="attendance-empty"><FiUserX /><strong>Pick a date inside the selected period</strong>
      <span>Attendance is only loaded for the period above, so absence can only be worked out for a day inside it.</span></div>;
  }
  if (!employees.length) {
    return <div className="attendance-empty"><FiCheckCircle /><strong>Everyone has checked in</strong>
      <span>No {department ? `${department} ` : ''}employee is without an attendance record for {formatDate(date)}.</span></div>;
  }
  return (
    <div className="attendance-table-wrap">
      <p className="absence-caption">
        <strong>{employees.length}</strong> {employees.length === 1 ? 'person has' : 'people have'} no
        attendance record for <strong>{formatDate(date)}</strong>
        {department ? <> in {department}</> : null}
        {date === todayKey() ? ' — they may simply not have checked in yet.' : '.'}
      </p>
      <table className="attendance-table">
        <thead><tr><th>Employee</th><th>Employee ID</th><th>Department</th><th>Designation</th></tr></thead>
        <tbody>
          {employees.map((employee) => (
            <tr key={employee.id}>
              <td><strong>{employee.name}</strong></td>
              <td>{employee.employeeId || <span className="empty-value">—</span>}</td>
              <td>{employee.department || <span className="empty-value">—</span>}</td>
              <td>{employee.designation || <span className="empty-value">—</span>}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
};

const RecordDrawer = ({ record, employee, onClose }: { record: AttendanceRow; employee?: Employee; onClose: () => void }) => {
  const extra = record.extra_hours ?? 0;
  const claimed = Boolean(record.overtime_claimed);
  // The server's verdict for the whole day. No sibling rows needed, and no
  // hours compared here — that is what got an eleven-hour day marked short.
  const status = rowStatus(record);
  const worked = record.working_hours ?? 0;
  // The day is the unit. A session is a slice of it, and on a part-time
  // contract the required day is 4 hours, not 8.
  const dayTotal = record.day_total_hours ?? worked;
  const required = record.day_required_hours;
  const progress = required && required > 0 ? Math.min(100, (dayTotal / required) * 100) : null;
  const shortfall = required ? Math.max(0, required - dayTotal) : 0;
  return <div className="attendance-drawer-backdrop" onMouseDown={(event) => event.target === event.currentTarget && onClose()}><aside className="attendance-drawer" aria-label="Attendance details">
    <header>
      <div className="drawer-header-copy"><p>Attendance details</p><h2>{formatLongDate(record.date)}</h2><div className="drawer-header-employee"><span><FiUser /></span><div><strong>{record.employee_name || employee?.name || 'Employee'}</strong><small>{employee?.designation || 'Workforce member'}{employee?.department ? ` · ${employee.department}` : ''}</small></div></div></div>
      <button onClick={onClose} aria-label="Close record details"><FiX /></button>
    </header>
    <div className="attendance-drawer-body">
      <div className={`drawer-day-card ${status}`}>
        <div className="drawer-day-top"><div><small>{dayTotal === worked ? 'Hours worked' : 'Worked that day'}</small><strong>{formatHours(dayTotal)}</strong>{dayTotal !== worked && <em className="drawer-day-session">this session {formatHours(worked)}</em>}</div><StatusBadge status={status} /></div>
        {progress !== null && <div className="drawer-day-track" role="img" aria-label={`${formatHours(dayTotal)} of a ${formatHours(required)} day`}><span style={{ width: `${progress}%` }} /></div>}
        <div className="drawer-day-foot"><span><FiClock />{formatTime(record.check_in_time)} → {record.check_out_time ? formatTime(record.check_out_time) : 'Not checked out'}</span><span>{status === 'completed' ? `${required ? formatHours(required) : 'Full'} day met` : status === 'incomplete' ? 'Awaiting check-out' : required ? `${formatHours(shortfall)} short of ${formatHours(required)}` : 'Short day'}</span></div>
        <p>{STATUS_COPY[status].hint}</p>
      </div>
      {record.auto_checked_out && (
        <p className="drawer-note drawer-note-flag">
          ⓘ This session was closed automatically at {AUTO_CLOSE_HOURS} hours, so it records{' '}
          {AUTO_CLOSE_HOURS.toFixed(2)} however long you actually stayed. If you worked past that,
          claim the extra under <Link to={ROUTES.TIME_LOGS}>Time Logs</Link> — an auto-closed
          session cannot be corrected here.
        </p>
      )}
      <section><h3>Time summary</h3><Detail label="Check in" value={formatTime(record.check_in_time)} /><Detail label="Check out" value={record.check_out_time ? `${formatTime(record.check_out_time)}${isNextDay(record) ? ' · Next day' : ''}` : 'Missing check-out'} /><Detail label="This session" value={formatHours(record.working_hours)} /><Detail label="Whole day" value={formatHours(dayTotal)} hint="Every session that day, added up" /><Detail label="Required that day" value={required ? formatHours(required) : 'Not stated'} hint="From this employee's own contract" /><Detail label="Regular hours" value={formatHours(record.regular_hours)} /></section>
      <section><h3>Location</h3><Detail label="Check-in location" value={record.check_in_address || 'Location not captured'} icon={<FiMapPin />} /><Detail label="Check-out location" value={record.check_out_address || 'Location not captured'} icon={<FiMapPin />} /><Detail label="Timezone" value="Asia/Karachi" /></section>
      {/* Extra time and overtime are now two different things. Extra is what the
          clock says; overtime is what an approved claim will pay for. Nothing on
          this screen can turn the first into the second — only the employee can,
          and only by filing it. */}
      <section><h3>Extra time</h3>
        <Detail label="Past the standard day" value={extra > 0 ? formatHours(extra) : 'None'} />
        <Detail label="Approved overtime" value={record.overtime_hours ? formatHours(record.overtime_hours) : 'None'} hint="Only an approved Time Logs claim counts here" />
        {extra > 0 && (claimed
          ? <p className="drawer-note">ⓘ A claim for this day has been approved, so these hours are on their way to payroll.</p>
          : <p className="drawer-note drawer-note-flag">
              ⓘ {formatHours(extra)} past your standard day, and nothing has been claimed for it —
              as it stands it will not be paid. File it under <Link to={ROUTES.TIME_LOGS}>Time Logs</Link>,
              where your line manager and then HR sign it off. A claim filed on or before the 25th is
              paid with that month's salary; after that it goes to the next month's payroll.
            </p>)}
      </section>
      <section><h3>Audit information</h3><Detail label="Source" value="Automatic attendance" /><Detail label="Created" value={formatLongDateTime(record.check_in_time)} /><Detail label="Last updated" value={record.check_out_time ? formatLongDateTime(record.check_out_time) : formatLongDateTime(record.check_in_time)} /><Detail label="Modified by" value="System" /><Detail label="Record ID" value={formatRecordId(record)} /><p className="drawer-audit-note">No manual changes</p></section>
    </div>
    <footer><span>For missing or incorrect times, contact your HR administrator.</span></footer>
  </aside></div>;
};

const StatCard = ({ label, value, note, icon, active = false, onClick }: { label: string; value: string; note: string; icon: React.ReactNode; active?: boolean; onClick?: () => void }) => <button className={`attendance-stat${active ? ' active' : ''}`} onClick={onClick} disabled={!onClick}><span><small>{label}</small><strong>{value}</strong><em>{note}</em></span><i>{icon}</i></button>;
const StatusBadge = ({ status }: { status: DayStatus }) => <span className={`attendance-status ${status}`} title={STATUS_COPY[status].hint}><i />{STATUS_COPY[status].label}</span>;
const Detail = ({ label, value, icon, hint }: { label: string; value: string; icon?: React.ReactNode; hint?: string }) => <div className="attendance-detail"><span>{icon}{label}{hint && <small>{hint}</small>}</span><strong>{value}</strong></div>;
const StatsSkeleton = () => <div className="attendance-stats">{Array.from({ length: 4 }, (_, index) => <div className="attendance-stat skeleton" key={index} />)}</div>;
const TableSkeleton = () => <div className="attendance-table-skeleton">{Array.from({ length: 6 }, (_, index) => <span key={index} />)}</div>;
const formatTime = (value?: string) => value ? formatPktTime(value) : '—';
const formatDate = (value: string) => new Date(`${value.slice(0, 10)}T00:00:00`).toLocaleDateString('en-US', { weekday: 'short', day: '2-digit', month: 'short', year: 'numeric' });
const formatLongDate = (value: string) => new Date(`${value.slice(0, 10)}T00:00:00`).toLocaleDateString('en-US', { weekday: 'long', day: 'numeric', month: 'long', year: 'numeric' });
const formatLongDateTime = (value?: string) => value ? new Date(value).toLocaleString('en-PK', { dateStyle: 'medium', timeStyle: 'short', timeZone: 'Asia/Karachi' }) : 'Not available';
const formatHours = (value?: number) => { if (!value) return '0h 00m'; const hours = Math.floor(value); const minutes = Math.round((value - hours) * 60); return `${hours}h ${String(minutes).padStart(2, '0')}m`; };
const csvCell = (value: unknown) => `"${String(value ?? '').replaceAll('"', '""')}"`;
const capitalize = (value: string) => value.charAt(0).toUpperCase() + value.slice(1).replaceAll('_', ' ');
const isoDate = (date: Date) => `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`;

// Nothing caught an inverted or future date range (e.g. start 31 Aug, end 10
// Aug) before this — it just silently ran, returning zero records with no
// explanation. Shared between the inline error message and loadAttendance's
// fetch guard so both agree on what "invalid" means.
const validateDateRange = (filterType: 'month' | 'range', startDate: string, endDate: string): string => {
  if (filterType !== 'range') return '';
  const today = isoDate(new Date());
  if (startDate && startDate > today) return 'Start date cannot be in the future.';
  if (endDate && endDate > today) return 'End date cannot be in the future.';
  if (startDate && endDate && startDate > endDate) return 'Start date must not be after the end date.';
  return '';
};
const relativeTime = (date: Date) => { const seconds = Math.max(0, Math.round((Date.now() - date.getTime()) / 1000)); if (seconds < 60) return 'just now'; const minutes = Math.floor(seconds / 60); return `${minutes}m ago`; };
const isNextDay = (record: AttendanceRow) => Boolean(record.check_in_time && record.check_out_time && new Date(record.check_out_time).toDateString() !== new Date(record.check_in_time).toDateString());
const workingDaysInPeriod = (type: 'month' | 'range', filters: FilterState) => {
  const start = type === 'month' ? new Date(filters.year, filters.month - 1, 1) : filters.startDate ? new Date(`${filters.startDate}T00:00:00`) : new Date();
  const naturalEnd = type === 'month' ? new Date(filters.year, filters.month, 0) : filters.endDate ? new Date(`${filters.endDate}T00:00:00`) : new Date();
  const end = naturalEnd > new Date() ? new Date() : naturalEnd;
  let count = 0;
  for (const cursor = new Date(start); cursor <= end; cursor.setDate(cursor.getDate() + 1)) if (![0, 6].includes(cursor.getDay())) count += 1;
  return count;
};
const formatRecordId = (record: AttendanceRow) => `ATT-${record.date.replaceAll('-', '')}-${String(record.id).padStart(4, '0')}`;

export default AttendanceHistory;
