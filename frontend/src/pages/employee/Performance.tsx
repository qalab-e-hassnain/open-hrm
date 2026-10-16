import { useCallback, useEffect, useState, type FormEvent } from 'react';
import {
  FiAlertTriangle, FiCheckCircle, FiClock, FiInbox, FiPlus, FiStar,
} from 'react-icons/fi';
import {
  performanceService, CYCLE_STATUS_LABEL, REVIEW_STATUS_LABEL,
  type Cycle, type Review, type ReviewStatus,
} from '../../services/performanceService';
import ReviewFormPanel from './ReviewForm';
import { useAuthStore } from '../../store/authStore';
import { isPrivileged } from '../../utils/permissions';
import './Performance.css';

const shortDate = (v?: string | null) => {
  if (!v) return '—';
  const d = new Date(v);
  return Number.isNaN(d.getTime()) ? v : d.toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' });
};

const detailOf = (err: unknown, fallback: string): string => {
  const detail = (err as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail;
  if (typeof detail === 'string' && detail) return detail;
  return (err as { message?: string })?.message || fallback;
};

const STATUS_TONE: Record<ReviewStatus, string> = {
  draft: 'tone-wait', self_submitted: 'tone-info', manager_submitted: 'tone-info',
  shared: 'tone-live', closed: 'tone-done',
};

const Stars = ({ value }: { value?: number | null }) => {
  if (typeof value !== 'number') return <span className="pf-muted">Not rated</span>;
  return (
    <span className="pf-stars" aria-label={`${value} out of 5`}>
      {[1, 2, 3, 4, 5].map((n) => <FiStar key={n} className={n <= Math.round(value) ? 'is-on' : ''} />)}
      <b>{value.toFixed(1)}</b>
    </span>
  );
};

type Tab = 'mine' | 'team' | 'cycles';

const Performance = () => {
  const { user } = useAuthStore();
  const privileged = isPrivileged(user);
  const [tab, setTab] = useState<Tab>('mine');
  const [mine, setMine] = useState<Review[]>([]);
  const [team, setTeam] = useState<Review[]>([]);
  const [cycles, setCycles] = useState<Cycle[]>([]);
  const [open, setOpen] = useState<Review | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');

  const load = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      // An admin's "all" already contains their own reviews, so asking for
      // "mine" as well is the same endpoint answered twice. A manager's
      // "team" is their reports and does not include them, so there the two
      // calls really are different questions.
      const [others, cycleList] = await Promise.all([
        performanceService.reviews(privileged ? 'all' : 'team').catch(() => []),
        performanceService.cycles().catch(() => []),
      ]);
      const own = privileged
        ? others.filter((review) => String(review.employee_id) === String(user?.id))
        : await performanceService.reviews('mine');
      setMine(own);
      setTeam(others);
      setCycles(cycleList);
    } catch (err) {
      setError(detailOf(err, 'Could not load performance reviews.'));
    } finally {
      setLoading(false);
    }
  }, [privileged, user?.id]);

  useEffect(() => { void load(); }, [load]);

  const done = (message: string) => { setNotice(message); setOpen(null); void load(); };
  const hasTeam = team.length > 0;

  return (
    <div className="performance-page">
      <header className="pf-head">
        <div>
          <h1>Performance</h1>
          <p>Goals for the cycle, your own assessment, and your manager's.</p>
        </div>
      </header>

      <div className="pf-tabs">
        <button type="button" className={tab === 'mine' ? 'is-active' : ''} onClick={() => setTab('mine')}>Mine</button>
        {hasTeam && (
          <button type="button" className={tab === 'team' ? 'is-active' : ''} onClick={() => setTab('team')}>
            {privileged ? 'Everyone' : 'My team'}
            {team.filter((r) => r.status === 'self_submitted').length > 0 && (
              <span className="pf-count">{team.filter((r) => r.status === 'self_submitted').length}</span>
            )}
          </button>
        )}
        {privileged && (
          <button type="button" className={tab === 'cycles' ? 'is-active' : ''} onClick={() => setTab('cycles')}>Cycles</button>
        )}
      </div>

      {error && <div className="pf-alert pf-alert-error"><FiAlertTriangle /> {error}</div>}
      {notice && <div className="pf-alert pf-alert-ok"><FiCheckCircle /> {notice}</div>}

      {loading ? <div className="pf-empty">Loading…</div> : (
        <>
          {tab === 'cycles' && privileged && (
            <Cycles cycles={cycles} onDone={done} onError={setError} />
          )}

          {tab !== 'cycles' && (
            (tab === 'mine' ? mine : team).length === 0 ? (
              <div className="pf-empty">
                <FiInbox />
                <p>{tab === 'mine'
                  ? 'No review has been raised for you yet.'
                  : 'Nothing waiting on you.'}</p>
              </div>
            ) : (
              <div className="pf-list">
                {(tab === 'mine' ? mine : team).map((review) => (
                  <article key={review.id} className="pf-card" onClick={() => setOpen(review)}>
                    <div>
                      <h2>{review.cycle_name || `Cycle #${review.cycle_id}`}</h2>
                      <p>
                        {tab === 'team' ? `${review.employee_name} · ` : ''}
                        {review.manager_name ? `reviewed by ${review.manager_name}` : 'no reviewer assigned'}
                      </p>
                    </div>
                    <div className="pf-card-right">
                      {review.status === 'shared' || review.status === 'closed'
                        ? <Stars value={review.overall_rating} />
                        : null}
                      <span className={`pf-badge ${STATUS_TONE[review.status]}`}>
                        {REVIEW_STATUS_LABEL[review.status]}
                      </span>
                    </div>
                  </article>
                ))}
              </div>
            )
          )}
        </>
      )}

      {open && (
        <ReviewFormPanel reviewId={open.id}
          onClose={() => setOpen(null)} onSaved={done} onError={setError} />
      )}
    </div>
  );
};

const Cycles = ({ cycles, onDone, onError }: {
  cycles: Cycle[]; onDone: (m: string) => void; onError: (m: string) => void;
}) => {
  const [creating, setCreating] = useState(false);
  const [form, setForm] = useState({ name: '', period_start: '', period_end: '', self_assessment_due: '', manager_review_due: '', instructions: '' });
  const [saving, setSaving] = useState(false);

  const act = async (fn: () => Promise<unknown>, message: string) => {
    try { await fn(); onDone(message); }
    catch (err) { onError(detailOf(err, 'That did not work.')); }
  };

  return (
    <section>
      <div className="pf-section-head">
        <h2>Review cycles</h2>
        <button type="button" className="pf-btn pf-btn-primary" onClick={() => setCreating(!creating)}>
          <FiPlus /> New cycle
        </button>
      </div>

      {creating && (
        <form className="pf-cycle-form" onSubmit={async (e: FormEvent) => {
          e.preventDefault();
          setSaving(true);
          try {
            await performanceService.createCycle({
              name: form.name.trim(),
              period_start: form.period_start, period_end: form.period_end,
              ...(form.self_assessment_due ? { self_assessment_due: form.self_assessment_due } : {}),
              ...(form.manager_review_due ? { manager_review_due: form.manager_review_due } : {}),
              ...(form.instructions.trim() ? { instructions: form.instructions.trim() } : {}),
            });
            setCreating(false);
            onDone('Cycle created as a draft. Employees see nothing until you open it.');
          } catch (err) { onError(detailOf(err, 'That cycle could not be created.')); }
          finally { setSaving(false); }
        }}>
          <label>Name<input required value={form.name} placeholder="H2 2026"
            onChange={(e) => setForm({ ...form, name: e.target.value })} /></label>
          <label>Period starts<input type="date" required value={form.period_start}
            onChange={(e) => setForm({ ...form, period_start: e.target.value })} /></label>
          <label>Period ends<input type="date" required value={form.period_end}
            onChange={(e) => setForm({ ...form, period_end: e.target.value })} /></label>
          <label>Self-assessment due<input type="date" value={form.self_assessment_due}
            onChange={(e) => setForm({ ...form, self_assessment_due: e.target.value })} /></label>
          <label>Manager review due<input type="date" value={form.manager_review_due}
            onChange={(e) => setForm({ ...form, manager_review_due: e.target.value })} /></label>
          <label className="pf-wide">Instructions<textarea rows={2} value={form.instructions}
            onChange={(e) => setForm({ ...form, instructions: e.target.value })} /></label>
          <div className="pf-form-actions">
            <button type="button" className="pf-btn" onClick={() => setCreating(false)}>Cancel</button>
            <button type="submit" className="pf-btn pf-btn-primary" disabled={saving}>Create draft</button>
          </div>
        </form>
      )}

      {cycles.length === 0 ? (
        <div className="pf-empty">No cycles yet.</div>
      ) : (
        <div className="pf-list">
          {cycles.map((cycle) => (
            <article key={cycle.id} className="pf-card is-static">
              <div>
                <h2>{cycle.name}</h2>
                <p>
                  {shortDate(cycle.period_start)} – {shortDate(cycle.period_end)}
                  {cycle.review_count > 0 && ` · ${cycle.completed_count} of ${cycle.review_count} complete`}
                </p>
                {cycle.self_assessment_due && (
                  <p className="pf-due"><FiClock /> Self-assessment due {shortDate(cycle.self_assessment_due)}</p>
                )}
              </div>
              <div className="pf-card-right">
                <span className={`pf-badge tone-${cycle.status}`}>{CYCLE_STATUS_LABEL[cycle.status]}</span>
                {cycle.status === 'draft' && (
                  <button type="button" className="pf-btn pf-btn-primary"
                    onClick={() => void act(
                      () => performanceService.openCycle(cycle.id),
                      'Cycle opened. A review was raised for every active employee and they have been told.')}>
                    Open
                  </button>
                )}
                {cycle.status === 'open' && (
                  <button type="button" className="pf-btn"
                    onClick={() => void act(() => performanceService.closeCycle(cycle.id), 'Cycle closed.')}>
                    Close
                  </button>
                )}
              </div>
            </article>
          ))}
        </div>
      )}
    </section>
  );
};

export default Performance;
