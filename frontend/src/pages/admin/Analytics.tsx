import { useEffect, useState } from 'react';
import {
  FiAlertCircle, FiAlertTriangle, FiCalendar, FiClock, FiDollarSign,
  FiInfo, FiTrendingDown, FiUsers,
} from 'react-icons/fi';
import {
  analyticsService,
  type AttendanceSeries, type AttentionItem, type Compensation,
  type HeadcountSeries, type LeaveStats, type Overview,
  type OvertimeSeries, type PayrollSeries, type Point,
} from '../../services/analyticsService';
import './Analytics.css';

const num = (v: number, dp = 0) =>
  v.toLocaleString('en-PK', { minimumFractionDigits: dp, maximumFractionDigits: dp });

const money = (v: number) => `PKR ${num(Math.round(v))}`;

/** The most recent month that actually has a value, for a figure where a
 *  series would be noise. */
const lastKnown = (values: (number | null)[], fmt: (v: number) => string) => {
  for (let i = values.length - 1; i >= 0; i -= 1) {
    const v = values[i];
    if (typeof v === 'number') return fmt(v);
  }
  return '—';
};

const monthLabel = (key: string) => {
  const [year, month] = key.split('-');
  const d = new Date(Number(year), Number(month) - 1, 1);
  return Number.isNaN(d.getTime()) ? key : d.toLocaleDateString('en-GB', { month: 'short' });
};

const detailOf = (err: unknown, fallback: string): string => {
  const detail = (err as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail;
  if (typeof detail === 'string' && detail) return detail;
  return (err as { message?: string })?.message || fallback;
};

/**
 * A line over the month keys. Hand-drawn rather than pulled from a charting
 * library: the series are parallel arrays of at most sixty points, and the
 * bundle already warns about its size.
 */
const LineChart = ({ months, series }: {
  months: string[];
  series: { label: string; values: (number | null)[]; tone: string }[];
}) => {
  const W = 640;
  const H = 180;
  const PAD = 8;
  // A null is a month with no record — before the biometric sync began, or
  // one nobody has been paid for yet. It is not a zero, and drawing it as
  // one produces a line diving to the axis that reads as "nobody came in".
  const known = series.flatMap((s) => s.values).filter((v): v is number => typeof v === 'number');
  const max = Math.max(1, ...known);
  const step = months.length > 1 ? (W - PAD * 2) / (months.length - 1) : 0;
  const y = (v: number) => H - PAD - (v / max) * (H - PAD * 2);

  /** Contiguous runs of real numbers, so the line breaks across a gap. */
  const segments = (values: (number | null)[]) => {
    const runs: { x: number; y: number }[][] = [];
    let run: { x: number; y: number }[] = [];
    values.forEach((v, i) => {
      if (typeof v === 'number') run.push({ x: PAD + i * step, y: y(v) });
      else if (run.length) { runs.push(run); run = []; }
    });
    if (run.length) runs.push(run);
    return runs;
  };

  return (
    <div className="an-chart">
      <svg viewBox={`0 0 ${W} ${H}`} role="img" preserveAspectRatio="none">
        {[0.25, 0.5, 0.75].map((f) => (
          <line key={f} x1={PAD} x2={W - PAD} y1={y(max * f)} y2={y(max * f)} className="an-grid" />
        ))}
        {series.map((s) => segments(s.values).map((run, i) => (
          run.length === 1
            /* One known month between two gaps has no line to draw. */
            ? <circle key={`${s.label}-${i}`} className={`an-dot an-${s.tone}`}
                cx={run[0].x} cy={run[0].y} r="2.5" />
            : <polyline key={`${s.label}-${i}`} className={`an-line an-${s.tone}`}
                points={run.map((pt) => `${pt.x},${pt.y}`).join(' ')} />
        )))}
      </svg>
      <div className="an-axis">
        {months.map((m, i) => (
          <span key={m}>{months.length > 8 && i % 2 ? '' : monthLabel(m)}</span>
        ))}
      </div>
      <div className="an-legend">
        {series.map((s) => (
          <span key={s.label}><i className={`an-${s.tone}`} />{s.label}</span>
        ))}
      </div>
    </div>
  );
};

/** Points arrive sorted largest first, so render in order. */
const Bars = ({ points, format = (v: number) => num(v) }: {
  points: Point[]; format?: (v: number) => string;
}) => {
  if (!points.length) return <p className="an-nothing">Nothing recorded.</p>;
  const max = Math.max(...points.map((p) => p.value), 1);
  return (
    <ul className="an-bars">
      {points.map((p) => (
        <li key={p.label}>
          <span className="an-bar-label" title={p.label}>{p.label}</span>
          <span className="an-bar-track"><i style={{ width: `${(p.value / max) * 100}%` }} /></span>
          <span className="an-bar-value">{format(p.value)}</span>
        </li>
      ))}
    </ul>
  );
};

const Tile = ({ label, value, hint, tone }: {
  label: string; value: string; hint?: string; tone?: string;
}) => (
  <div className={`an-tile${tone ? ` an-tile-${tone}` : ''}`}>
    <span className="an-tile-label">{label}</span>
    <strong>{value}</strong>
    {hint && <small>{hint}</small>}
  </div>
);

const Analytics = () => {
  const [overview, setOverview] = useState<Overview | null>(null);
  const [headcount, setHeadcount] = useState<HeadcountSeries | null>(null);
  const [leave, setLeave] = useState<LeaveStats | null>(null);
  const [payroll, setPayroll] = useState<PayrollSeries | null>(null);
  const [overtime, setOvertime] = useState<OvertimeSeries | null>(null);
  const [attendance, setAttendance] = useState<AttendanceSeries | null>(null);
  const [pay, setPay] = useState<Compensation | null>(null);
  const [attention, setAttention] = useState<AttentionItem[]>([]);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let live = true;
    // Each panel appears as its own request lands. Waiting for all eight
    // held the page on "Loading…" for as long as the slowest took — and
    // attendance and overview are both around nine seconds against Azure,
    // so the tiles sat blank for nine seconds with nothing wrong.
    const settle = <T,>(promise: Promise<T>, apply: (value: T) => void, label?: string) => {
      promise
        .then((value) => { if (live) apply(value); })
        .catch((err) => {
          if (!live || !label) return;
          setError((prev) => prev || detailOf(err, `Could not load ${label}.`));
        })
        .finally(() => { if (live) setLoading(false); });
    };

    settle(analyticsService.overview(), setOverview, 'the overview');
    settle(analyticsService.headcount(12), setHeadcount);
    settle(analyticsService.leave(), setLeave);
    settle(analyticsService.payroll(12), setPayroll);
    settle(analyticsService.overtime(12), setOvertime);
    settle(analyticsService.attendance(12), setAttendance);
    settle(analyticsService.compensation(), setPay);
    settle(analyticsService.attention(), setAttention);

    return () => { live = false; };
  }, []);

  return (
    <div className="analytics-page">
      <header className="an-head">
        <div>
          <h1>Analytics</h1>
          <p>Headcount, leave, payroll and overtime. Nobody outside HR sees this screen.</p>
        </div>
        {overview && (
          <span className="an-generated">
            As at {new Date(overview.generated_at).toLocaleString('en-GB', {
              day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit',
            })}
          </span>
        )}
      </header>

      {error && <div className="an-alert"><FiAlertTriangle /> {error}</div>}
      {loading && !overview && <div className="an-empty">Loading…</div>}

      {/* The only panel naming something somebody has to do today, so it
          sits above the numbers rather than below them. */}
      {attention.length > 0 && (
        <section className="an-attention">
          <h2>Waiting on somebody</h2>
          <ul>
            {attention.map((item) => (
              <li key={item.key} className={`sev-${item.severity}`}>
                <span className="an-sev" aria-label={item.severity}>
                  {item.severity === 'critical' ? <FiAlertCircle />
                    : item.severity === 'warning' ? <FiAlertTriangle /> : <FiInfo />}
                </span>
                <span className="an-att-text">
                  <strong>{item.label}</strong>
                  {item.detail && <small>{item.detail}</small>}
                  {item.people.length > 0 && (
                    <small className="an-people">
                      {item.people.slice(0, 6).join(', ')}
                      {item.people.length > 6 ? ` and ${item.people.length - 6} more` : ''}
                    </small>
                  )}
                </span>
                <b>{item.count}</b>
              </li>
            ))}
          </ul>
        </section>
      )}

      {overview && (
        <section className="an-tiles">
          <Tile label="Headcount" value={num(overview.headcount)}
            hint={`${overview.joiners_this_year} joined, ${overview.leavers_this_year} left this year`} />
          <Tile label="Attrition" value={`${num(overview.attrition_rate_percent, 1)}%`}
            hint="Leavers over average headcount" tone={overview.attrition_rate_percent > 20 ? 'warn' : undefined} />
          <Tile label="Average tenure" value={`${num(overview.average_tenure_years, 1)} yrs`} />
          {/* The encashment bill before it is owed — useful precisely
              because nobody has invoiced it. */}
          {/* The whole year's entitlement is not what is owed today. */}
          <Tile label="Leave liability" value={money(overview.leave_accrued_amount)}
            hint={`${num(overview.leave_accrued_days, 1)} days earned so far · ${money(overview.leave_liability_amount)} if the year ran in full`}
            tone="flag" />
          <Tile label="On probation" value={num(overview.on_probation)} />
          <Tile label="Open resignations" value={num(overview.open_resignations)}
            tone={overview.open_resignations > 0 ? 'warn' : undefined} />
          <Tile label="Awaiting a decision"
            value={num(overview.pending_leave_requests + overview.pending_overtime_requests)}
            hint={`${overview.pending_leave_requests} leave · ${overview.pending_overtime_requests} overtime`} />
          {overview.advances_outstanding > 0 && (
            <Tile label="Advances outstanding" value={money(overview.advances_outstanding)} />
          )}
          {overview.assets_issued_count > 0 && (
            <Tile label="Equipment out" value={num(overview.assets_issued_count)}
              hint={`${money(overview.assets_issued_value)} of kit in the field`} />
          )}
        </section>
      )}

      <div className="an-grid">
        {headcount && headcount.months.length > 0 && (
          <section className="an-card">
            <h2><FiUsers /> Headcount</h2>
            <LineChart months={headcount.months} series={[
              { label: 'Headcount', values: headcount.headcount, tone: 'brand' },
              { label: 'Joiners', values: headcount.joiners, tone: 'good' },
              { label: 'Leavers', values: headcount.leavers, tone: 'bad' },
            ]} />
          </section>
        )}

        {payroll && payroll.months.length > 0 && (
          <section className="an-card">
            <h2><FiDollarSign /> Payroll cost</h2>
            {payroll.current_month_pending && (
              <p className="an-sub">
                This month is not shown — it has not been paid yet, and a month with no payroll
                is not a month the payroll was zero.
                {typeof payroll.tax_percent_of_gross === 'number'
                  && ` Tax is ${num(payroll.tax_percent_of_gross, 2)}% of gross.`}
              </p>
            )}
            <LineChart months={payroll.months} series={[
              { label: 'Gross', values: payroll.gross, tone: 'brand' },
              { label: 'Net', values: payroll.net, tone: 'good' },
              { label: 'Tax', values: payroll.tax, tone: 'bad' },
            ]} />
          </section>
        )}

        {overview && overview.headcount_by_department.length > 0 && (
          <section className="an-card">
            <h2><FiUsers /> By department</h2>
            <Bars points={overview.headcount_by_department} />
          </section>
        )}

        {leave && (
          <section className="an-card">
            <h2><FiCalendar /> Leave in {leave.year}</h2>
            <p className="an-sub">
              {num(leave.approved_days, 1)} days approved · absence rate {num(leave.absence_rate_percent, 1)}%
              {typeof leave.average_decision_days === 'number'
                ? ` · decided in ${num(leave.average_decision_days, 1)} days on average` : ''}
            </p>
            <h3>By type</h3>
            <Bars points={leave.by_type} format={(v) => `${num(v, 1)} d`} />
            {leave.top_takers.length > 0 && (
              <>
                <h3>Most days taken</h3>
                <Bars points={leave.top_takers} format={(v) => `${num(v, 1)} d`} />
              </>
            )}
          </section>
        )}

        {overtime && (
          <section className="an-card">
            <h2><FiTrendingDown /> Overtime</h2>
            {/* Nothing recorded is a sentence. A flat line along the axis
                reads as broken rather than as empty. */}
            {overtime.has_data ? (
              <>
                <p className="an-sub">
                  {num(overtime.total_hours, 1)} hours approved · {money(overtime.total_pay)} paid
                </p>
                <LineChart months={overtime.months} series={[
                  { label: 'Hours approved', values: overtime.approved_hours, tone: 'brand' },
                ]} />
                {overtime.by_employee.length > 0 && (
                  <>
                    <h3>Most hours claimed</h3>
                    <Bars points={overtime.by_employee} format={(v) => `${num(v, 1)} h`} />
                  </>
                )}
              </>
            ) : (
              <p className="an-none">No overtime has been approved in this period.</p>
            )}
          </section>
        )}

        {attendance && attendance.months.length > 0 && (
          <section className="an-card">
            <h2><FiClock /> Attendance</h2>
            {attendance.records_begin && (
              <p className="an-sub">
                Records begin {monthLabel(attendance.records_begin)} — the biometric sync is the
                only source, so earlier months are unknown rather than empty.
              </p>
            )}
            {/* One series only. A percentage, an hours-per-day figure and a
                headcount on a shared axis makes all three unreadable — 8.27
                hours plotted against 40% against 24 people says nothing. */}
            <LineChart months={attendance.months} series={[
              { label: 'Attendance rate %', values: attendance.attendance_rate_percent, tone: 'brand' },
            ]} />
            <dl className="an-figures">
              <div>
                <dt>Average hours/day</dt>
                <dd>{lastKnown(attendance.average_hours_per_day, (v) => num(v, 2))}</dd>
              </div>
              <div>
                <dt>Days present</dt>
                <dd>{num(attendance.days_present.reduce((a, b) => a + b, 0))}</dd>
              </div>
              <div>
                <dt>Incomplete days</dt>
                <dd>{num(attendance.incomplete_days.reduce((a, b) => a + b, 0))}</dd>
              </div>
              {attendance.punctuality_available && (
                <div>
                  <dt>Late arrivals</dt>
                  {/* Only counted for months that have a record at all. The
                      API returns 0 rather than null for months before the
                      sync began, and a zero there would read as nobody being
                      late in a month nobody was measured. */}
                  <dd>{num(attendance.late_arrivals.reduce((total: number, v, i) => (
                    typeof v === 'number' && attendance.attendance_rate_percent[i] !== null
                      ? total + v : total
                  ), 0))}</dd>
                </div>
              )}
            </dl>
            {!attendance.punctuality_available && (
              <p className="an-none">
                Lateness is not shown — it is only answerable for people whose shift states what
                being on time means.
              </p>
            )}
          </section>
        )}

        {pay && (
          <section className="an-card">
            <h2><FiDollarSign /> Salary bill</h2>
            <p className="an-sub">
              {money(pay.monthly_cost)} a month · {money(pay.annual_cost)} a year
            </p>
            <dl className="an-figures">
              <div><dt>Median</dt><dd>{money(pay.median_salary)}</dd></div>
              <div><dt>Average</dt><dd>{money(pay.average_salary)}</dd></div>
              <div><dt>Range</dt><dd>{money(pay.lowest)} – {money(pay.highest)}</dd></div>
              {typeof pay.spread_ratio === 'number' && (
                <div><dt>Spread</dt><dd>{num(pay.spread_ratio, 1)}×</dd></div>
              )}
            </dl>
            {pay.bands.length > 0 && (<><h3>By band</h3><Bars points={pay.bands} /></>)}
          </section>
        )}
      </div>
    </div>
  );
};

export default Analytics;
