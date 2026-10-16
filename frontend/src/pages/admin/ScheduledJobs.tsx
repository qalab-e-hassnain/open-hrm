import { useCallback, useEffect, useState } from 'react';
import {
  FiAlertTriangle, FiCheckCircle, FiClock, FiPlay, FiRefreshCw,
} from 'react-icons/fi';
import { jobsService, type ScheduledJob } from '../../services/jobsService';
import './ScheduledJobs.css';

const when = (value?: string | null) => {
  if (!value) return null;
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? value : d.toLocaleString('en-GB', {
    day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit',
  });
};

const relative = (value?: string | null) => {
  if (!value) return null;
  const d = new Date(value).getTime();
  if (Number.isNaN(d)) return null;
  const mins = Math.round((Date.now() - d) / 60000);
  const ago = mins >= 0;
  const n = Math.abs(mins);
  if (n < 1) return 'just now';
  if (n < 60) return ago ? `${n} min ago` : `in ${n} min`;
  const hrs = Math.round(n / 60);
  if (hrs < 24) return ago ? `${hrs}h ago` : `in ${hrs}h`;
  const days = Math.round(hrs / 24);
  return ago ? `${days}d ago` : `in ${days}d`;
};

const detailOf = (err: unknown, fallback: string): string => {
  const detail = (err as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail;
  return typeof detail === 'string' && detail ? detail : fallback;
};

/** `last_result` is free-form JSON per job. Render its keys rather than
 *  guessing at a shape none of the four share. */
const ResultChips = ({ result }: { result: unknown }) => {
  if (!result || typeof result !== 'object' || Array.isArray(result)) return null;
  const entries = Object.entries(result as Record<string, unknown>)
    .filter(([, v]) => v !== null && typeof v !== 'object');
  if (!entries.length) return null;
  return (
    <div className="sj-result">
      {entries.map(([key, value]) => (
        <span key={key}><b>{key.replace(/_/g, ' ')}</b>{String(value)}</span>
      ))}
    </div>
  );
};

const ScheduledJobs = () => {
  const [jobs, setJobs] = useState<ScheduledJob[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [running, setRunning] = useState<string | null>(null);

  const load = useCallback(async () => {
    setError('');
    try {
      setJobs(await jobsService.list());
    } catch (err) {
      setError(detailOf(err, 'Could not read the job state.'));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  const runNow = async (job: ScheduledJob) => {
    setRunning(job.name);
    setError('');
    setNotice('');
    try {
      await jobsService.run(job.name);
      setNotice(`${job.description} ran.`);
      await load();
    } catch (err) {
      // 409 means it is already running — a sentence, not a code.
      setError(detailOf(err, `${job.description} did not run.`));
    } finally {
      setRunning(null);
    }
  };

  const failing = jobs.filter((job) => job.last_status === 'error').length;

  return (
    <div className="jobs-page">
      <header className="sj-head">
        <div>
          <h1>Scheduled jobs</h1>
          <p>
            There is no cron. These four sweeps run off the portal's own traffic —
            a request asks at most once every few minutes whether anything is due.
            This panel is the only place their state is visible.
          </p>
        </div>
        <button type="button" className="sj-refresh" onClick={() => void load()}>
          <FiRefreshCw /> Refresh
        </button>
      </header>

      {error && <div className="sj-alert sj-alert-error"><FiAlertTriangle /> {error}</div>}
      {notice && <div className="sj-alert sj-alert-ok"><FiCheckCircle /> {notice}</div>}
      {failing > 0 && !error && (
        <div className="sj-alert sj-alert-error">
          <FiAlertTriangle /> {failing} {failing === 1 ? 'sweep' : 'sweeps'} failed on the last run.
        </div>
      )}

      {loading ? (
        <div className="sj-empty">Loading…</div>
      ) : jobs.length === 0 ? (
        <div className="sj-empty">No sweeps are registered.</div>
      ) : (
        <div className="sj-grid">
          {jobs.map((job) => {
            const neverRun = !job.last_run_at;
            return (
              <article key={job.name} className={`sj-card${job.last_status === 'error' ? ' is-error' : ''}`}>
                <header>
                  <div>
                    <h2>{job.description}</h2>
                    <code>{job.name}</code>
                  </div>
                  {job.is_running
                    ? <span className="sj-badge tone-run">Running</span>
                    : job.last_status === 'error'
                      ? <span className="sj-badge tone-err">Failed</span>
                      : neverRun
                        /* A job that has never run is the normal initial
                           state, not a fault. */
                        ? <span className="sj-badge tone-idle">Not yet run</span>
                        : job.is_due
                          ? <span className="sj-badge tone-due">Due</span>
                          : <span className="sj-badge tone-ok">Healthy</span>}
                </header>

                <dl className="sj-facts">
                  <div>
                    <dt>Last run</dt>
                    <dd>{neverRun ? '—' : <>{when(job.last_run_at)} <small>{relative(job.last_run_at)}</small></>}</dd>
                  </div>
                  <div>
                    <dt>Next due</dt>
                    <dd>{job.is_due ? 'Now' : when(job.next_due_at) || '—'}</dd>
                  </div>
                  <div>
                    <dt>Every</dt>
                    <dd>{job.interval_hours}h</dd>
                  </div>
                  <div>
                    <dt>Runs</dt>
                    <dd>
                      {job.run_count}
                      {job.error_count > 0 && <small className="sj-errcount">{job.error_count} failed</small>}
                    </dd>
                  </div>
                </dl>

                {job.last_error && <p className="sj-error-text"><FiAlertTriangle /> {job.last_error}</p>}
                <ResultChips result={job.last_result} />

                <footer>
                  {job.last_duration_ms != null && !job.is_running && (
                    <span className="sj-duration"><FiClock /> {(job.last_duration_ms / 1000).toFixed(1)}s</span>
                  )}
                  <button type="button" onClick={() => void runNow(job)} disabled={running === job.name || job.is_running}>
                    <FiPlay /> {running === job.name ? 'Running…' : 'Run now'}
                  </button>
                </footer>
              </article>
            );
          })}
        </div>
      )}
    </div>
  );
};

export default ScheduledJobs;
