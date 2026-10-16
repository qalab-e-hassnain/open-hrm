import { useEffect, useMemo, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import {
  FiArrowRight,
  FiBriefcase,
  FiCalendar,
  FiCheckCircle,
  FiClock,
  FiCreditCard,
  FiUsers,
} from 'react-icons/fi';
import { useAuthStore } from '../../store/authStore';
import { employeeService } from '../../services/employeeService';
import { leaveService } from '../../services/leaveService';
import { attendanceService } from '../../services/attendanceService';
import { type Employee, type LeaveApplication } from '../../types';
import AnnouncementsCard from '../../components/common/AnnouncementsCard';
import AnnouncementTicker from '../../components/common/AnnouncementTicker';
import GetTheApp from '../../components/common/GetTheApp';
import { formatPktTime } from '../../utils/datetime';
import { ROUTES } from '../../config/constants';
import './Dashboard.css';

type AttendanceStatus = {
  status?: string;
  check_in_time?: string;
  working_hours?: number;
  auto_checked_out?: boolean;
  sessions_today?: number;
};

type WeeklyAttendanceDay = {
  date: string;
  dayName?: string;
  status?: string;
  checkIn?: string;
  checkOut?: string;
  // Already returned by attendanceService.getWeeklyAttendance — used for the
  // per-day hours bar below.
  workingHours?: number;
  /** Past the standard day, off the clock. What the +extra flag reads. */
  extraHours?: number;
  /** Approved claims only. */
  overtimeHours?: number;
};

type WeeklyOvertime = {
  /** Approved claims only since the overtime rework — usually 0. */
  total_overtime_hours?: number;
  /** Clock time past the standard day across the week. Unclaimed, unpaid. */
  total_extra_hours?: number;
};

// The dashboard keeps itself current instead of offering a Refresh button:
// each tier below re-polls on its own interval, and all fire immediately
// whenever the tab regains focus, so a change made elsewhere (or by someone
// else) shows up on its own. Cadence is tiered by how often each piece
// actually changes — polling everything on one fast interval (the old
// behaviour) was what drove the "keeps refreshing" feeling and put avoidable
// load on the database for data that barely moves minute to minute.
const FAST_REFRESH_MS = 10000; // own status + team roster check-in/out
const MEDIUM_REFRESH_MS = 10 * 60 * 1000; // pending approvals — matches the nav badge cadence
const SLOW_REFRESH_MS = 20 * 60 * 1000; // weekly chart, overtime, team roster list

// Scale for the per-day hours bar. Matches the backend's standard working day
// (STANDARD_WORK_HOURS_PER_DAY), which is also where a session auto-closes.
const STANDARD_DAY_HOURS = 8;

// Attendance reads as three distinct states rather than a single Present/Absent
// pair: someone still working is "Checked in", someone whose day has ended is
// "Checked out", and only a genuine no-show is "Not checked in".
const describeAttendance = (status?: AttendanceStatus) => {
  if (!status) {
    return { label: 'Syncing attendance…', tone: 'badge-pending', detail: '' };
  }
  const hours = typeof status.working_hours === 'number' ? `${status.working_hours}h` : '';
  if (status.status === 'checked_in') {
    return {
      label: 'Checked in',
      tone: 'badge-in',
      detail: status.check_in_time
        ? `Checked in at ${formatPktTime(status.check_in_time)}`
        : 'Currently working',
    };
  }
  if (status.status === 'checked_out') {
    return {
      label: status.auto_checked_out ? 'Auto checked out' : 'Checked out',
      tone: 'badge-done',
      detail: status.auto_checked_out
        ? `Closed automatically at the daily cap${hours ? ` after ${hours}` : ''}`
        : hours
          ? `Worked ${hours} today`
          : 'Day complete',
    };
  }
  return { label: 'Not checked in', tone: 'badge-absent', detail: 'No attendance recorded today' };
};

type ToastState = {
  type: 'success' | 'error';
  message: string;
} | null;

const ManagerDashboard = () => {
  const { user, isCheckedIn, setIsCheckedIn } = useAuthStore();
  const [teamMembers, setTeamMembers] = useState<Employee[]>([]);
  const [upcomingLeaves, setUpcomingLeaves] = useState<LeaveApplication[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [checkingAttendance, setCheckingAttendance] = useState(false);
  const [weeklyAttendance, setWeeklyAttendance] = useState<WeeklyAttendanceDay[]>([]);
  const [weeklyOvertime, setWeeklyOvertime] = useState<WeeklyOvertime | null>(null);
  const [memberStatuses, setMemberStatuses] = useState<Record<string, AttendanceStatus>>({});
  // Until the first reply, nobody is known to be present — which is not at
  // all the same as everybody being absent. Without this the cards spent the
  // first few seconds confidently reporting nought present and the whole team
  // missing, then corrected themselves.
  const [statusesLoaded, setStatusesLoaded] = useState(false);
  // Attendance has three states, not two. A day that has been checked out of
  // is not the same as a day that has not started, and labelling both of them
  // "Check in" made a finished day look like an untouched one. Checking in
  // again is still allowed — a day can have more than one session — so the
  // button says so rather than pretending nothing happened.
  const [dayState, setDayState] = useState<'not_checked_in' | 'checked_in' | 'completed'>('not_checked_in');
  const [error, setError] = useState('');
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);
  const [toast, setToast] = useState<ToastState>(null);

  const isAdmin = user?.role === 'admin';

  const showToast = (type: 'success' | 'error', message: string) => {
    setToast({ type, message });
    window.setTimeout(() => setToast(null), 3200);
  };

  // Guards against overlapping fetches of the same tier — see employee
  // Dashboard.tsx for the full reasoning: without this, an initial load
  // that's still in flight (slow backend/DB pool contention) can race
  // against a poll tick for the same tier, and whichever happens to
  // resolve LAST wins even if it was the OLDER, staler response.
  const fastBusyRef = useRef(false);
  const mediumBusyRef = useRef(false);
  const slowBusyRef = useRef(false);

  // Called from both the fast tier (every 10s) and the slow tier (whenever
  // the roster itself refreshes) without either awaiting it — two
  // independent, un-awaited call sites writing the same state. Guarded the
  // same way as the tiers themselves so an overlap can't let a stale call
  // (an older team snapshot) overwrite a fresher one that resolved first.
  const teamStatusBusyRef = useRef(false);
  const loadTeamStatuses = async () => {
    if (teamStatusBusyRef.current) return;
    teamStatusBusyRef.current = true;
    try {
      // One request for the whole team. This used to be one request per
      // person on every ten-second tick, which overwhelmed the connection
      // pool and left some people showing the wrong status.
      const statuses = await attendanceService.getTeamTodayStatus();
      setMemberStatuses(statuses as Record<string, AttendanceStatus>);
      setStatusesLoaded(true);
    } finally {
      teamStatusBusyRef.current = false;
    }
  };

  // Always holds the current team roster, so the fast tier's interval tick
  // can re-check statuses for the latest roster without waiting on the slow
  // tier (which is what actually re-fetches the roster itself).
  const teamMembersRef = useRef<Employee[]>(teamMembers);
  teamMembersRef.current = teamMembers;

  // Fast tier: this manager's own check-in status, plus the team's live
  // check-in/out statuses — the two things that can change from someone
  // else's action between one look and the next.
  const fetchFastData = async () => {
    if (fastBusyRef.current) return { failed: 0, total: 0 };
    fastBusyRef.current = true;
    let failed = 0;
    try {
      const status = await attendanceService.getAttendanceStatus();
      if (status) {
        setIsCheckedIn(status.isCheckedIn);
        setDayState((status.status as typeof dayState) || 'not_checked_in');
      }
    } catch {
      failed = 1;
    } finally {
      fastBusyRef.current = false;
    }
    void loadTeamStatuses();
    return { failed, total: 1 };
  };

  // Medium tier: pending approvals — matches the cadence of the pending-
  // approval dots in Navigation.tsx, since it's the same underlying data.
  const fetchMediumData = async () => {
    if (mediumBusyRef.current) return { failed: 0, total: 0 };
    mediumBusyRef.current = true;
    try {
      const leaves = await leaveService.getUpcomingLeaves();
      setUpcomingLeaves(Array.isArray(leaves) ? leaves : []);
      return { failed: 0, total: 1 };
    } catch {
      return { failed: 1, total: 1 };
    } finally {
      mediumBusyRef.current = false;
    }
  };

  // Slow tier: weekly chart, overtime, and the team roster list itself
  // (names/departments change rarely — org changes, not minute to minute).
  const fetchSlowData = async (employeeId?: number) => {
    if (slowBusyRef.current) return { failed: 0, total: 0 };
    slowBusyRef.current = true;
    try {
    const requests = await Promise.allSettled([
      attendanceService.getWeeklyAttendance(employeeId),
      attendanceService.getWeeklyOvertime(),
      employeeService.getTeamOverview(),
    ]);
    const [weekResult, overtimeResult, teamResult] = requests;
    if (weekResult.status === 'fulfilled') setWeeklyAttendance(weekResult.value ?? []);
    if (overtimeResult.status === 'fulfilled') setWeeklyOvertime(overtimeResult.value);
    if (teamResult.status === 'fulfilled') {
      const team = teamResult.value ?? [];
      setTeamMembers(team);
      void loadTeamStatuses();
    }
    return { failed: requests.filter((r) => r.status === 'rejected').length, total: requests.length };
    } finally {
      slowBusyRef.current = false;
    }
  };

  // Full load: all three tiers together. Used for the initial page load and
  // right after this manager's own check-in/check-out, since that action can
  // affect fast data (status) and slow data (today's row in the weekly
  // chart, overtime) at once.
  const loadDashboardData = async (refresh = false) => {
    if (refresh) setIsRefreshing(true);
    else setIsLoading(true);
    setError('');

    const employeeId = user?.id ? Number(user.id) : undefined;
    const [fast, medium, slow] = await Promise.all([
      fetchFastData(),
      fetchMediumData(),
      fetchSlowData(employeeId),
    ]);
    const failedCount = fast.failed + medium.failed + slow.failed;
    const totalCount = fast.total + medium.total + slow.total;

    // totalCount can be 0 if every tier happened to be skipped (already in
    // flight from a concurrent poll tick) rather than attempted and failed —
    // see employee Dashboard.tsx for why that must not hit the
    // failedCount === totalCount branch below.
    if (totalCount === 0) {
      // Nothing attempted; leave whatever is already on screen alone.
    } else if (failedCount === totalCount) {
      setError('We could not load the dashboard. Check your connection and try again.');
    } else if (failedCount > 0 && !refresh) {
      // Only the first load shows the persistent banner — see employee
      // Dashboard.tsx for the full reasoning. A background poll already
      // retried once via withRetry, and every value that DID load was
      // already written to state, so what's on screen is still correct.
      setError('Some dashboard information is temporarily unavailable.');
    } else if (failedCount > 0 && refresh) {
      showToast('error', 'Some data could not be refreshed — showing the last known values.');
    }

    setLastUpdated(new Date());
    setIsLoading(false);
    setIsRefreshing(false);
  };

  // Background fast/medium ticks: silent. At a 10s/10min cadence a single
  // missed request self-heals on the next tick, so a banner/toast here would
  // just be noise — and the axios layer already retries transient
  // 502/503/504s once before this even sees a failure.
  const refreshFast = async () => {
    if (!user?.id) return;
    await fetchFastData();
  };
  const refreshMedium = async () => {
    if (!user?.id) return;
    await fetchMediumData();
  };

  // Background slow-tier tick: infrequent enough (20 min) that the existing
  // "Updating…" indicator and failure toast still carry real information
  // instead of being noise.
  const refreshSlow = async () => {
    if (!user?.id) return;
    setIsRefreshing(true);
    const { failed } = await fetchSlowData(user?.id ? Number(user.id) : undefined);
    if (failed > 0) {
      showToast('error', 'Some data could not be refreshed — showing the last known values.');
    }
    setLastUpdated(new Date());
    setIsRefreshing(false);
  };

  useEffect(() => {
    if (!user?.id) return;
    void loadDashboardData();
    // The user id is the only external value that should trigger a full reload.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [user?.id]);

  // Kept in refs so the auto-refresh timers below always call the current
  // closure without having to re-create the interval on every render.
  const reloadFastRef = useRef(refreshFast);
  reloadFastRef.current = refreshFast;
  const reloadMediumRef = useRef(refreshMedium);
  reloadMediumRef.current = refreshMedium;
  const reloadSlowRef = useRef(refreshSlow);
  reloadSlowRef.current = refreshSlow;

  // Auto-refresh replaces the old manual Refresh button. Polling stops while
  // the tab is hidden (a background tab does not need to stay current) and
  // all three tiers fire the moment it becomes visible again, so returning to
  // the tab never shows stale numbers.
  useEffect(() => {
    if (!user?.id) return;

    let fastTimer: number | undefined;
    let mediumTimer: number | undefined;
    let slowTimer: number | undefined;

    const startFast = () => {
      window.clearInterval(fastTimer);
      fastTimer = window.setInterval(() => {
        if (document.visibilityState === 'visible') void reloadFastRef.current();
      }, FAST_REFRESH_MS);
    };
    const startMedium = () => {
      window.clearInterval(mediumTimer);
      mediumTimer = window.setInterval(() => {
        if (document.visibilityState === 'visible') void reloadMediumRef.current();
      }, MEDIUM_REFRESH_MS);
    };
    const startSlow = () => {
      window.clearInterval(slowTimer);
      slowTimer = window.setInterval(() => {
        if (document.visibilityState === 'visible') void reloadSlowRef.current();
      }, SLOW_REFRESH_MS);
    };

    const handleVisibility = () => {
      if (document.visibilityState === 'visible') {
        void reloadFastRef.current();
        void reloadMediumRef.current();
        void reloadSlowRef.current();
        startFast();
        startMedium();
        startSlow();
      } else {
        window.clearInterval(fastTimer);
        window.clearInterval(mediumTimer);
        window.clearInterval(slowTimer);
      }
    };

    startFast();
    startMedium();
    startSlow();
    document.addEventListener('visibilitychange', handleVisibility);
    window.addEventListener('focus', handleVisibility);
    return () => {
      window.clearInterval(fastTimer);
      window.clearInterval(mediumTimer);
      window.clearInterval(slowTimer);
      document.removeEventListener('visibilitychange', handleVisibility);
      window.removeEventListener('focus', handleVisibility);
    };
  }, [user?.id]);

  const handleAttendance = async () => {
    setCheckingAttendance(true);
    // A device with no GPS is not evidence of absence. Ask for a fix, and if
    // there isn't one, send the check-in without coordinates anyway — the
    // server can tell from the source address whether this is the office
    // connection, and it is the only side that can.
    //
    // Outside the try on purpose: this never rejects, and the catch below needs
    // both halves of the answer to explain a refusal.
    const { position, error: locationError } = await attendanceService.tryUserLocation();
    try {
      const location = position
        ? {
            latitude: position.coords.latitude,
            longitude: position.coords.longitude,
            address: 'Office Location',
          }
        : {};

      if (isCheckedIn) {
        await attendanceService.checkOut(location);
        showToast('success', 'You have checked out successfully.');
      } else {
        await attendanceService.checkIn(location);
        showToast('success', 'You have checked in successfully.');
      }
      await loadDashboardData(true);
    } catch (attendanceError) {
      // Until the API accepts a check-in with no coordinates, it answers a
      // GPS-less device with a field-validation error. The location reason is
      // the useful thing to say in that case; anything else the server sends
      // is written for the person reading it and goes through as-is.
      const raw = attendanceError instanceof Error ? attendanceError.message : '';
      const rejectedForMissingFix = !position && /latitude|longitude/i.test(raw);
      const message = (rejectedForMissingFix && locationError) || raw
        || 'We could not update your attendance. Please try again.';
      showToast('error', message);
    } finally {
      setCheckingAttendance(false);
    }
  };

  const weeklyStats = useMemo(() => {
    let totalHours = 0;
    let daysPresent = 0;
    weeklyAttendance.forEach((day) => {
      if (day.checkIn && day.checkOut) {
        totalHours += (new Date(day.checkOut).getTime() - new Date(day.checkIn).getTime()) / 3600000;
        daysPresent += 1;
      }
    });
    return {
      totalHours: Math.round(totalHours * 10) / 10,
      daysPresent,
      avgHours: daysPresent ? Math.round((totalHours / daysPresent) * 10) / 10 : 0,
    };
  }, [weeklyAttendance]);

  const presentToday = Object.values(memberStatuses).filter(
    (status) => status.status === 'checked_in' || status.status === 'checked_out',
  ).length;
  const attendanceRate = teamMembers.length
    ? Math.round((presentToday / teamMembers.length) * 100)
    : 0;
  const absentToday = Math.max(0, teamMembers.length - presentToday);
  const attendanceSummary = !statusesLoaded
    ? 'Checking today’s attendance…'
    : teamMembers.length === 0
      ? 'No team members assigned'
      : presentToday === 0
        ? 'Team attendance has not started'
        : `${attendanceRate}% team attendance`;

  const cleanDepartment = (value?: string) => {
    const normalized = value?.trim();
    if (!normalized || normalized.toLowerCase() === 'string') return 'Not assigned';
    return normalized;
  };

  const cleanDesignation = (value?: string) => {
    const normalized = value?.trim();
    if (!normalized || normalized.toLowerCase() === 'string') return 'Employee';
    return normalized;
  };

  const todayIso = new Date().toISOString().split('T')[0];
  const hour = new Date().getHours();
  const greeting = hour < 12 ? 'Good morning' : hour < 17 ? 'Good afternoon' : 'Good evening';
  const todayLabel = new Date().toLocaleDateString('en-GB', {
    weekday: 'long',
    day: 'numeric',
    month: 'long',
    year: 'numeric',
  });

  if (isLoading) {
    return (
      <div className="manager-dashboard" aria-busy="true" aria-label="Loading dashboard">
        {/* Mounted during loading, not after it. The ticker fetches its own
            announcements, and while it lived only in the loaded branch that
            request could not even start until every dashboard call had already
            finished — so the one element the browser picks as LCP was waiting
            on a round trip that had not been issued yet. It still renders
            nothing until it has something to say. */}
        <AnnouncementTicker />
      {/* Real text, not a grey bar. LCP only ever counts text or an image, so
          an empty skeleton div can never be the largest contentful paint — the
          first thing that qualified used to be whatever arrived from the
          slowest dashboard call, which is how a 6s LCP happens on a page whose
          frame was ready in two. The greeting, the date and the first name all
          come from the clock and the already-persisted session, so they paint
          as soon as the bundle runs. The markup is the loaded header's, so
          nothing moves when the data lands and CLS stays at 0. */}
      <header className="dash-page-header">
        <div>
          <h1>{greeting}, {user?.name?.split(' ')[0] ?? 'there'}</h1>
          <p>{todayLabel}</p>
        </div>
        <div className="dash-header-actions">
          <span className="dash-last-updated">Updating…</span>
          <button type="button" className={`dash-check-btn${isCheckedIn ? ' dash-check-btn-out' : ''}`} disabled>
            <FiClock aria-hidden="true" /> {isCheckedIn ? 'Check out' : 'Check in'}
          </button>
        </div>
      </header>
        <div className="dash-kpi-grid">
          {[1, 2, 3, 4].map((item) => <div className="dash-skeleton dash-skeleton-kpi" key={item} />)}
        </div>
        <div className="dash-primary-grid">
          <div className="dash-skeleton dash-skeleton-panel" />
          <div className="dash-skeleton dash-skeleton-panel" />
        </div>
      </div>
    );
  }

  return (
    <div className="manager-dashboard">
      {toast && <div className={`dash-toast dash-toast-${toast.type}`} role="status">{toast.message}</div>}

      <AnnouncementTicker />

      <header className="dash-page-header">
        <div>
          <h1>{greeting}, {user?.name?.split(' ')[0] ?? 'there'}</h1>
          <p>{todayLabel} · {attendanceSummary}</p>
        </div>
        <div className="dash-header-actions">
          <span className="dash-last-updated" aria-live="polite">
            {isRefreshing
              ? 'Updating…'
              : `Updated ${lastUpdated?.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) ?? ''}`}
          </span>
          <button
            type="button"
            className={`dash-check-btn${isCheckedIn ? ' dash-check-btn-out' : ''}`}
            onClick={handleAttendance}
            disabled={checkingAttendance}
          >
            <FiClock aria-hidden="true" />
            {checkingAttendance
              ? 'Updating...'
              : isCheckedIn
                ? 'Check out'
                : dayState === 'completed' ? 'Check in again' : 'Check in'}
          </button>
        </div>
      </header>

      {error && (
        <div className="dash-error-state" role="alert">
          <span>{error}</span>
          <button type="button" onClick={() => void loadDashboardData(true)}>Try again</button>
        </div>
      )}

      <nav className="dash-quick-access" aria-label="Quick access">
        <span className="dash-quick-label">Quick access</span>
        <div className="dash-quick-links">
          {isAdmin && <Link to={ROUTES.EMPLOYEES}><FiUsers aria-hidden="true" /> User management</Link>}
          <Link to={ROUTES.ATTENDANCE_HISTORY}><FiClock aria-hidden="true" /> Attendance</Link>
          <Link to={ROUTES.LEAVES}><FiCalendar aria-hidden="true" /> Leave requests</Link>
          <Link to={ROUTES.REIMBURSEMENTS}><FiCreditCard aria-hidden="true" /> Reimbursements</Link>
          {isAdmin && <Link to={ROUTES.PAYROLL}><FiBriefcase aria-hidden="true" /> Payroll</Link>}
        </div>
        <GetTheApp />
      </nav>

      <section className="dash-kpi-grid" aria-label="Dashboard summary">
        <article className="dash-kpi-card">
          <span className="dash-kpi-icon dash-kpi-red"><FiUsers /></span>
          <div><span className="dash-kpi-label">{isAdmin ? 'Total employees' : 'Team members'}</span><strong>{teamMembers.length}</strong><small>Active directory</small></div>
        </article>
        <article className="dash-kpi-card">
          <span className="dash-kpi-icon dash-kpi-green"><FiCheckCircle /></span>
          <div><span className="dash-kpi-label">Present today</span>
            <strong>{statusesLoaded ? presentToday : '—'}</strong>
            <small>{statusesLoaded ? `${attendanceRate}% of team` : 'Checking…'}</small></div>
        </article>
        <article className="dash-kpi-card">
          <span className="dash-kpi-icon dash-kpi-orange"><FiCalendar /></span>
          <div><span className="dash-kpi-label">Pending leave</span><strong>{upcomingLeaves.length}</strong><small>Awaiting review</small></div>
        </article>
        <article className="dash-kpi-card">
          <span className="dash-kpi-icon dash-kpi-blue"><FiClock /></span>
          <div>
            <span className="dash-kpi-label">{isAdmin ? 'Absent today' : 'Weekly hours'}</span>
            <strong>{isAdmin ? (statusesLoaded ? absentToday : '—') : weeklyStats.totalHours}</strong>
            <small>{isAdmin ? 'Based on attendance records' : `${weeklyStats.daysPresent} days recorded`}</small>
          </div>
        </article>
      </section>

      <section className="dash-primary-grid">
        <article className="dash-panel dash-attendance-panel">
          <div className="dash-panel-header">
            <div><h2>Attendance overview</h2><p>Your working week at a glance</p></div>
            <Link to={ROUTES.ATTENDANCE_HISTORY}>View attendance <FiArrowRight /></Link>
          </div>
          {weeklyAttendance.length === 0 ? (
            <div className="dash-empty-state"><FiClock /><strong>No attendance recorded</strong><span>Attendance will appear after your first check-in.</span></div>
          ) : (
            <>
              <div className="dash-week-grid">
                {weeklyAttendance.map((day) => {
                  const dayDate = new Date(day.date);
                  const hasRecord = Boolean(day.checkIn);
                  const isToday = day.date === todayIso;
                  const isWeekend = dayDate.getDay() === 0 || dayDate.getDay() === 6;
                  const inProgress = hasRecord && !day.checkOut;
                  const hours = day.workingHours ?? 0;
                  // The bar is capped at the standard day, so a full day fills it
                  // and overtime is shown by the separate marker rather than by
                  // overflowing.
                  const fill = Math.min(Math.round((hours / STANDARD_DAY_HOURS) * 100), 100);
                  return (
                    <div
                      className={`dash-day-card${isToday ? ' day-today' : ''}${isWeekend ? ' day-weekend' : ''}`}
                      key={day.date}
                      title={hasRecord
                        ? `${formatPktTime(day.checkIn!)}${day.checkOut ? ` – ${formatPktTime(day.checkOut)}` : ' — still working'}`
                        : 'No attendance recorded'}
                    >
                      <span className="day-name">{day.dayName?.slice(0, 3)}</span>
                      <span className="day-num">{dayDate.getDate()}</span>
                      <span className="day-hours">
                        {hasRecord ? (hours > 0 ? `${hours.toFixed(1)}h` : '—') : '—'}
                      </span>
                      <span
                        className={`day-bar${inProgress ? ' is-active' : ''}${hasRecord && !inProgress ? ' is-done' : ''}`}
                        aria-hidden="true"
                      >
                        <i style={{ width: `${hasRecord ? Math.max(fill, 6) : 0}%` }} />
                      </span>
                      {(day.extraHours ?? 0) > 0 && <span className="day-ot" title="Past the standard day. Unpaid unless claimed under Time Logs.">+extra</span>}
                    </div>
                  );
                })}
              </div>
              {/* Explains the bar colours, which otherwise carry meaning with no
                  key. Each day box also has a tooltip with its exact times. */}
              <div className="dash-week-legend">
                <span><i className="legend-done" />Completed</span>
                <span><i className="legend-active" />In progress</span>
                <span><i className="legend-none" />No record</span>
              </div>
              <div className="dash-inline-stats">
                <div><span>Days present</span><strong>{weeklyStats.daysPresent}</strong></div>
                <div><span>Average hours</span><strong>{weeklyStats.avgHours}h</strong></div>
                <div><span>Extra hours</span><strong>{weeklyOvertime?.total_extra_hours ?? 0}h</strong></div>
                {(weeklyOvertime?.total_overtime_hours ?? 0) > 0 && (
                  <div><span>Approved overtime</span><strong>{weeklyOvertime?.total_overtime_hours}h</strong></div>
                )}
              </div>
            </>
          )}
        </article>

        <AnnouncementsCard />
      </section>

      <section className="dash-secondary-grid">
        <article className="dash-panel">
          <div className="dash-panel-header">
            <div><h2>Employee overview</h2><p>Current team attendance status</p></div>
            {isAdmin && <Link to={ROUTES.EMPLOYEES}>View all <FiArrowRight /></Link>}
          </div>
          {teamMembers.length === 0 ? (
            <div className="dash-empty-state"><FiUsers /><strong>No team members found</strong><span>Employees assigned to your team will appear here.</span></div>
          ) : (
            <div className="dash-team-table">
              <div className="dash-team-header"><span>Employee</span><span>Department</span><span>Today</span></div>
              {teamMembers.slice(0, 6).map((member) => {
                const status = memberStatuses[member.id];
                const attendance = describeAttendance(status);
                return (
                  <div className="dash-team-row" key={member.id}>
                    <span className="dash-team-person"><b>{member.name?.[0] ?? 'U'}</b><span><strong>{member.name}</strong><small>{cleanDesignation(member.designation)}</small></span></span>
                    <span>{cleanDepartment(member.department)}</span>
                    <span className={`team-badge ${attendance.tone}`} title={attendance.detail}>{attendance.label}</span>
                  </div>
                );
              })}
            </div>
          )}
        </article>

        <article className="dash-panel">
          <div className="dash-panel-header">
            <div><h2>Pending approvals</h2><p>Items that need attention</p></div>
            {upcomingLeaves.length > 0 && <Link to={ROUTES.LEAVES}>Review all <FiArrowRight /></Link>}
          </div>
          {upcomingLeaves.length === 0 ? (
            <div className="dash-empty-state"><FiCheckCircle /><strong>Nothing pending</strong><span>All approval requests have been reviewed.</span></div>
          ) : (
            <div className="dash-approval-list">
              {upcomingLeaves.slice(0, 5).map((leave) => (
                <Link to={ROUTES.LEAVES} className="dash-approval-row" key={leave.id}>
                  <span className="dash-approval-icon"><FiCalendar /></span>
                  <span><strong>{leave.employeeName ?? 'Employee'}</strong><small>{leave.leaveType ?? 'Leave request'}</small></span>
                  <time>{leave.startDate ? new Date(leave.startDate).toLocaleDateString('en-GB', { day: 'numeric', month: 'short' }) : 'Pending'}</time>
                </Link>
              ))}
            </div>
          )}
        </article>
      </section>

    </div>
  );
};

export default ManagerDashboard;
