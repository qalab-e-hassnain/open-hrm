import { useEffect, useMemo, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import {
  FiArrowRight,
  FiCalendar,
  FiCheckCircle,
  FiClock,
  FiCreditCard,
  FiFileText,
} from 'react-icons/fi';
import { useAuthStore } from '../../store/authStore';
import { leaveService } from '../../services/leaveService';
import { attendanceService } from '../../services/attendanceService';
import AnnouncementsCard from '../../components/common/AnnouncementsCard';
import AnnouncementTicker from '../../components/common/AnnouncementTicker';
import GetTheApp from '../../components/common/GetTheApp';
import { type LeaveType } from '../../types';
import { formatPktTime } from '../../utils/datetime';
import { ROUTES } from '../../config/constants';
import './Dashboard.css';

type AttendanceSession = {
  id: string;
  check_in_time?: string;
  check_out_time?: string;
  working_hours?: number;
  status?: string;
};

type WeeklyAttendanceDay = {
  date: string;
  dayName?: string;
  status?: string;
  checkIn?: string;
  checkOut?: string;
};

type WeeklyOvertime = {
  /** Approved claims only since the overtime rework — usually 0. */
  total_overtime_hours?: number;
  /** Clock time past the standard day across the week. Unclaimed, unpaid. */
  total_extra_hours?: number;
};

type ToastState = {
  type: 'success' | 'error';
  message: string;
} | null;

// Poll cadence, tiered by how often each piece actually changes. Attendance
// status is time-critical (a session auto-closes at the daily cap and the
// employee needs to see that without reloading), so it polls fast. Leave
// balance / weekly chart / overtime change at most a few times a day, so
// polling those on the same fast cadence was pure wasted load — this is what
// was driving the "keeps refreshing" feeling and adding avoidable pressure on
// the database.
const FAST_REFRESH_MS = 10000;
const SLOW_REFRESH_MS = 20 * 60 * 1000;

const EmployeeDashboard = () => {
  const { user, isCheckedIn, setIsCheckedIn } = useAuthStore();
  const [leaveTypes, setLeaveTypes] = useState<LeaveType[]>([]);
  const [todaySessions, setTodaySessions] = useState<AttendanceSession[]>([]);
  const [todayWorkingHours, setTodayWorkingHours] = useState(0);
  const [weeklyAttendance, setWeeklyAttendance] = useState<WeeklyAttendanceDay[]>([]);
  const [weeklyOvertime, setWeeklyOvertime] = useState<WeeklyOvertime | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [checkingAttendance, setCheckingAttendance] = useState(false);
  const [error, setError] = useState('');
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);
  const [toast, setToast] = useState<ToastState>(null);

  const showToast = (type: 'success' | 'error', message: string) => {
    setToast({ type, message });
    window.setTimeout(() => setToast(null), 3200);
  };

  // Guards against overlapping fetches of the same tier — e.g. the initial
  // load is still in flight (slow under a cold backend/DB pool contention)
  // when the 10s fast-tier interval also fires. Without this, both requests
  // land and whichever happens to RESOLVE LAST wins, even if it was the
  // OLDER of the two — so the page could end up showing the stale response
  // over the fresher one. This is what "dashboard shows wrong data, a
  // manual refresh fixes it" actually was: not wrong data from the server,
  // an out-of-order race on the client.
  const fastBusyRef = useRef(false);
  const slowBusyRef = useRef(false);

  // Fast tier: attendance status + today's sessions — the two things that can
  // change from someone else's/the backend's action (auto-checkout) between
  // one look and the next.
  const fetchFastData = async (employeeId: number, today: string) => {
    if (fastBusyRef.current) return { failed: 0, total: 0 };
    fastBusyRef.current = true;
    try {
      const [statusResult, sessionsResult] = await Promise.allSettled([
        attendanceService.getAttendanceStatus(),
        attendanceService.filterAttendance({ date: today, employeeId }),
      ]);
      if (statusResult.status === 'fulfilled' && statusResult.value) {
        setIsCheckedIn(statusResult.value.isCheckedIn);
        // The backend only fills in a session's own working_hours once it's
        // checked out (see /attendance/filter), so summing todaySessions freezes
        // at 0 for the entire time someone is still checked in. The daily-status
        // endpoint computes working hours live against the current time instead,
        // including the open session — that's the number this card needs.
        setTodayWorkingHours(statusResult.value.workingHours ?? 0);
      }
      if (sessionsResult.status === 'fulfilled') setTodaySessions(sessionsResult.value ?? []);
      return { failed: [statusResult, sessionsResult].filter((r) => r.status === 'rejected').length, total: 2 };
    } finally {
      fastBusyRef.current = false;
    }
  };

  // Slow tier: leave balance, weekly chart, overtime.
  const fetchSlowData = async (employeeId: number) => {
    if (slowBusyRef.current) return { failed: 0, total: 0 };
    slowBusyRef.current = true;
    try {
    const requests = await Promise.allSettled([
      leaveService.getMyLeaveBalance(),
      // Leave history was fetched here only to work out how much had been
      // used. The balance endpoint carries that, so this is one fewer request
      // on every dashboard load.
      attendanceService.getWeeklyAttendance(employeeId),
      attendanceService.getWeeklyOvertime(),
    ]);
    const [balanceResult, weekResult, overtimeResult] = requests;
    if (balanceResult.status === 'fulfilled' && balanceResult.value) {
      // Everything here is the server's arithmetic. Entitlement is accrued per
      // person and moves on the 1st of each month, personal leave is a monthly
      // allowance rather than a slice of a yearly pool, and both round
      // server-side — so `remaining` is rendered as sent, never recomputed.
      const balance = balanceResult.value;
      const LABELS: Record<string, string> = {
        annual: 'Annual Leave', sick: 'Sick Leave', personal: 'Personal Leave',
        casual: 'Personal Leave', maternity: 'Maternity Leave',
        paternity: 'Paternity Leave', bereavement: 'Bereavement Leave',
        unpaid: 'Unpaid Leave',
      };
      // The server decides who may request what, so eligibility is not
      // re-derived from gender and marital status here.
      const eligible = balance.eligibleLeaveTypes.length
        ? balance.eligibleLeaveTypes
        : Object.keys(balance.balances);
      setLeaveTypes(
        eligible
          .map((key) => {
            const b = balance.balances[key] ?? balance.balances[key === 'casual' ? 'personal' : key];
            if (!b) return null;
            return {
              id: key,
              name: LABELS[key] ?? key,
              totalDays: b.total,
              usedDays: b.used,
              remainingDays: b.remaining,
            };
          })
          .filter((t): t is NonNullable<typeof t> => t !== null),
      );
    }

    if (weekResult.status === 'fulfilled') setWeeklyAttendance(weekResult.value ?? []);
    if (overtimeResult.status === 'fulfilled') setWeeklyOvertime(overtimeResult.value);
    return { failed: requests.filter((r) => r.status === 'rejected').length, total: requests.length };
    } finally {
      slowBusyRef.current = false;
    }
  };

  // Full load: both tiers together. Used for the initial page load and right
  // after the employee's own check-in/check-out, since that action can affect
  // both the fast data (status) and the slow data (today's row in the weekly
  // chart, overtime) at once.
  const loadDashboardData = async (refresh = false) => {
    if (!user?.id) return;
    if (refresh) setIsRefreshing(true);
    else setIsLoading(true);
    setError('');

    const today = new Date().toISOString().split('T')[0];
    const employeeId = Number(user.id);
    const [fast, slow] = await Promise.all([
      fetchFastData(employeeId, today),
      fetchSlowData(employeeId),
    ]);
    const failedCount = fast.failed + slow.failed;
    const totalCount = fast.total + slow.total;

    // totalCount can be 0 if both tiers happened to be skipped (already
    // in flight from a concurrent poll tick) rather than attempted and
    // failed — that's not an error, so it must not hit the failedCount ===
    // totalCount branch below (0 === 0 would otherwise show a false
    // "could not load" banner over data that never actually failed).
    if (totalCount === 0) {
      // Nothing attempted; leave whatever is already on screen alone.
    } else if (failedCount === totalCount) {
      setError('We could not load your dashboard. Check your connection and try again.');
    } else if (failedCount > 0 && !refresh) {
      // Only the first load shows the persistent banner — see fetchSlowData's
      // callers for the background-poll cases, which stay quiet instead.
      setError('Some dashboard information is temporarily unavailable.');
    } else if (failedCount > 0 && refresh) {
      showToast('error', 'Some data could not be refreshed — showing the last known values.');
    }

    setLastUpdated(new Date());
    setIsLoading(false);
    setIsRefreshing(false);
  };

  // Background fast-tier tick: silent. At a 10s cadence a single missed
  // request self-heals on the next tick, so a banner/toast here would just be
  // noise — and the axios layer already retries transient 502/503/504s once
  // before this even sees a failure.
  const refreshFast = async () => {
    if (!user?.id) return;
    const today = new Date().toISOString().split('T')[0];
    await fetchFastData(Number(user.id), today);
  };

  // Background slow-tier tick: infrequent enough (20 min) that the existing
  // "Updating…" indicator and failure toast still carry real information
  // instead of being noise.
  const refreshSlow = async () => {
    if (!user?.id) return;
    setIsRefreshing(true);
    const { failed } = await fetchSlowData(Number(user.id));
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

  // Kept in refs so the auto-refresh timers always call the current closure.
  const reloadFastRef = useRef(refreshFast);
  reloadFastRef.current = refreshFast;
  const reloadSlowRef = useRef(refreshSlow);
  reloadSlowRef.current = refreshSlow;

  // Auto-refresh replaces the manual Refresh button: each tier re-polls on
  // its own interval and both fire immediately when the tab regains focus, so
  // returning to the tab never shows stale numbers.
  useEffect(() => {
    if (!user?.id) return;

    let fastTimer: number | undefined;
    let slowTimer: number | undefined;

    const startFast = () => {
      window.clearInterval(fastTimer);
      fastTimer = window.setInterval(() => {
        if (document.visibilityState === 'visible') void reloadFastRef.current();
      }, FAST_REFRESH_MS);
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
        void reloadSlowRef.current();
        startFast();
        startSlow();
      } else {
        window.clearInterval(fastTimer);
        window.clearInterval(slowTimer);
      }
    };

    startFast();
    startSlow();
    document.addEventListener('visibilitychange', handleVisibility);
    window.addEventListener('focus', handleVisibility);
    return () => {
      window.clearInterval(fastTimer);
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
      showToast(
        'error',
        (rejectedForMissingFix && locationError) || raw
          || 'We could not update your attendance. Please try again.',
      );
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

  // Sum of what the server says is left, not total minus used.
  const availableLeave = leaveTypes.reduce((total, leave) => total + (leave.remainingDays ?? 0), 0);
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
      <div className="employee-dashboard employee-dashboard-v2" aria-busy="true" aria-label="Loading dashboard">
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
      <header className="employee-page-header">
        <div>
          <h1>{greeting}, {user?.name?.split(' ')[0] ?? 'there'}</h1>
          <p>{todayLabel}</p>
        </div>
        <div className="employee-header-actions">
          <span>Updating…</span>
          <button type="button" className={`employee-check-btn${isCheckedIn ? ' is-checkout' : ''}`} disabled>
            <FiClock /> {isCheckedIn ? 'Check out' : 'Check in'}
          </button>
        </div>
      </header>
        <div className="employee-kpi-grid">
          {[1, 2, 3, 4].map((item) => <div className="employee-skeleton employee-skeleton-kpi" key={item} />)}
        </div>
        <div className="employee-primary-grid">
          <div className="employee-skeleton employee-skeleton-panel" />
          <div className="employee-skeleton employee-skeleton-panel" />
        </div>
      </div>
    );
  }

  return (
    <div className="employee-dashboard employee-dashboard-v2">
      {toast && <div className={`employee-toast employee-toast-${toast.type}`} role="status">{toast.message}</div>}

      <AnnouncementTicker />

      <header className="employee-page-header">
        <div>
          <h1>{greeting}, {user?.name?.split(' ')[0] ?? 'there'}</h1>
          <p>{todayLabel} · {isCheckedIn ? 'Currently checked in' : 'Not checked in'}</p>
        </div>
        <div className="employee-header-actions">
          <span aria-live="polite">
            {isRefreshing
              ? 'Updating…'
              : `Updated ${lastUpdated?.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) ?? ''}`}
          </span>
          <button type="button" className={`employee-check-btn${isCheckedIn ? ' is-checkout' : ''}`} onClick={handleAttendance} disabled={checkingAttendance}>
            <FiClock /> {checkingAttendance ? 'Updating...' : isCheckedIn ? 'Check out' : 'Check in'}
          </button>
        </div>
      </header>

      {error && <div className="employee-error-state" role="alert"><span>{error}</span><button onClick={() => void loadDashboardData(true)}>Try again</button></div>}

      <section className="employee-quick-access">
        <div><h2>Quick access</h2><p>Your frequently used HR tools</p></div>
        <div>
          <Link to={ROUTES.ATTENDANCE_HISTORY}><FiClock /> Attendance</Link>
          <Link to={ROUTES.LEAVES}><FiCalendar /> Leave</Link>
          <Link to={ROUTES.REIMBURSEMENTS}><FiCreditCard /> Reimbursement</Link>
          <Link to={ROUTES.PAYSLIPS}><FiFileText /> Payslips</Link>
          <GetTheApp />
        </div>
      </section>

      <section className="employee-kpi-grid" aria-label="Work summary">
        <article className="employee-kpi-card"><span className="employee-kpi-icon kpi-green"><FiCheckCircle /></span><div><span>Status</span><strong>{isCheckedIn ? 'Working' : 'Off clock'}</strong><small>Today</small></div></article>
        <article className="employee-kpi-card"><span className="employee-kpi-icon kpi-blue"><FiClock /></span><div><span>Hours today</span><strong>{todayWorkingHours.toFixed(1)}</strong><small>{todaySessions.length} session{todaySessions.length === 1 ? '' : 's'}</small></div></article>
        <article className="employee-kpi-card"><span className="employee-kpi-icon kpi-orange"><FiCalendar /></span><div><span>Leave available</span><strong>{availableLeave}</strong><small>Across all categories</small></div></article>
        <article className="employee-kpi-card"><span className="employee-kpi-icon kpi-red"><FiClock /></span><div><span>Weekly hours</span><strong>{weeklyStats.totalHours}</strong><small>{weeklyStats.daysPresent} days recorded</small></div></article>
      </section>

      <section className="employee-primary-grid">
        <article className="employee-panel">
          <div className="employee-panel-header"><div><h2>Attendance overview</h2><p>Your working week at a glance</p></div><Link to={ROUTES.ATTENDANCE_HISTORY}>View history <FiArrowRight /></Link></div>
          {weeklyAttendance.length === 0 ? (
            <div className="employee-empty-state"><FiClock /><strong>No attendance recorded</strong><span>Your attendance will appear after your first check-in.</span></div>
          ) : (
            <>
              <div className="employee-week-grid">
                {weeklyAttendance.map((day) => (
                  <div className={`employee-day-card${day.date === new Date().toISOString().split('T')[0] ? ' is-today' : ''}`} key={day.date}>
                    <span>{day.dayName?.slice(0, 3)}</span><strong>{new Date(day.date).getDate()}</strong>
                    <small className={day.checkIn ? 'has-record' : ''}>{day.checkIn ? 'Recorded' : 'No record'}</small>
                    <time>{day.checkIn ? formatPktTime(day.checkIn) : '—'}</time>
                  </div>
                ))}
              </div>
              <div className="employee-inline-stats"><div><span>Days present</span><strong>{weeklyStats.daysPresent}</strong></div><div><span>Average hours</span><strong>{weeklyStats.avgHours}h</strong></div><div><span>Extra hours</span><strong>{weeklyOvertime?.total_extra_hours ?? 0}h</strong></div>{(weeklyOvertime?.total_overtime_hours ?? 0) > 0 && <div><span>Approved overtime</span><strong>{weeklyOvertime?.total_overtime_hours}h</strong></div>}</div>
            </>
          )}
        </article>
        <AnnouncementsCard />
      </section>

      <section className="employee-secondary-grid">
        <article className="employee-panel">
          <div className="employee-panel-header"><div><h2>Today’s sessions</h2><p>Recorded check-in and check-out activity</p></div></div>
          {todaySessions.length === 0 ? (
            <div className="employee-empty-state"><FiClock /><strong>No sessions today</strong><span>Use Check in to begin your work session.</span></div>
          ) : (
            <div className="employee-session-list">
              {todaySessions.map((session, index) => (
                <div className="employee-session-row" key={session.id}>
                  <span className="employee-session-number">{index + 1}</span>
                  <span><small>Check in</small><strong>{session.check_in_time ? formatPktTime(session.check_in_time) : '—'}</strong></span>
                  <span><small>Check out</small><strong>{session.check_out_time ? formatPktTime(session.check_out_time) : 'Working'}</strong></span>
                  <span><small>Hours</small><strong>{session.working_hours?.toFixed(1) ?? '—'}</strong></span>
                </div>
              ))}
            </div>
          )}
        </article>

        <article className="employee-panel">
          <div className="employee-panel-header"><div><h2>Leave balance</h2><p>Available days by leave category</p></div><Link to={ROUTES.LEAVES}>Manage leave <FiArrowRight /></Link></div>
          {leaveTypes.length === 0 ? (
            <div className="employee-empty-state"><FiCalendar /><strong>No leave balance available</strong><span>Your leave entitlement has not been configured.</span></div>
          ) : (
            <div className="employee-leave-list">
              {/* Every category, not a slice — "Leave available" above sums
                  all of them, so a truncated list here would show a smaller
                  total than the KPI card and read as the two disagreeing. */}
              {leaveTypes.map((leave) => (
                <div className="employee-leave-row" key={leave.id}>
                  <span><strong>{leave.name}</strong><small>{leave.usedDays ?? 0} of {leave.totalDays ?? 0} used</small></span>
                  <b>{leave.remainingDays ?? 0}<small> days</small></b>
                </div>
              ))}
            </div>
          )}
        </article>
      </section>
    </div>
  );
};

export default EmployeeDashboard;
