import { useCallback, useEffect, useState } from 'react';
import { FiAlertTriangle, FiCheck, FiEye, FiLock, FiX } from 'react-icons/fi';
import {
  formService, performanceService, RECOMMENDATION_LABEL, REVIEW_STATUS_LABEL,
  GOAL_SOURCE_LABEL,
  type FormCompetency, type FormGoal, type FormSection,
  type Recommendation, type ReviewForm as ReviewFormShape, type ScoreWorking,
} from '../../services/performanceService';
import './ReviewForm.css';

const detailOf = (err: unknown, fallback: string): string => {
  const detail = (err as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail;
  if (typeof detail === 'string' && detail) return detail;
  return (err as { message?: string })?.message || fallback;
};

/** The scale arrives with the form so both sides read the same words. */
const scaleText = (scale: ReviewFormShape['rating_scale'], n: number): string => {
  const point = scale?.[String(n)];
  if (!point) return '';
  if (typeof point === 'string') return point;
  return [point.label, point.description].filter(Boolean).join(' — ');
};

const Rating = ({ value, editable, scale, onChange }: {
  value?: number | null; editable: boolean;
  scale: ReviewFormShape['rating_scale'];
  onChange?: (v: number | undefined) => void;
}) => {
  if (!editable) {
    return typeof value === 'number'
      ? <span className="rf-score-pill" title={scaleText(scale, value)}>{value}</span>
      : <span className="rf-dash">—</span>;
  }
  return (
    <span className="rf-rating">
      {[1, 2, 3, 4, 5].map((n) => (
        <button type="button" key={n} className={value === n ? 'is-on' : ''}
          title={scaleText(scale, n)} aria-pressed={value === n}
          onClick={() => onChange?.(value === n ? undefined : n)}>{n}</button>
      ))}
    </span>
  );
};

const ReviewFormPanel = ({ reviewId, onClose, onSaved, onError }: {
  reviewId: number;
  onClose: () => void;
  onSaved: (message: string) => void;
  onError: (message: string) => void;
}) => {
  const [form, setForm] = useState<ReviewFormShape | null>(null);
  const [score, setScore] = useState<ScoreWorking | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [ack, setAck] = useState('');

  // Local edits, keyed the way the write endpoints expect them.
  const [scores, setScores] = useState<Record<string, { rating?: number; comment?: string }>>({});
  const [sections, setSections] = useState<Record<string, string>>({});
  const [goalText, setGoalText] = useState<Record<number, string>>({});
  const [goalRating, setGoalRating] = useState<Record<number, number | undefined>>({});
  const [recommendation, setRecommendation] = useState<Recommendation | ''>('');
  const [calibration, setCalibration] = useState('');
  const [strengths, setStrengths] = useState('');
  const [development, setDevelopment] = useState('');

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const data = await formService.form(reviewId);
      setForm(data);
      const mine = data.your_part;
      const s: Record<string, { rating?: number; comment?: string }> = {};
      for (const c of data.competencies) {
        const side = mine === 'manager' ? c.manager : c.self;
        s[c.key] = { rating: side.rating ?? undefined, comment: side.comment ?? undefined };
      }
      setScores(s);
      const sec: Record<string, string> = {};
      for (const item of data.sections) sec[item.key] = item.value || '';
      setSections(sec);
      setGoalText(Object.fromEntries(data.goals.map((g) => [g.id, g.employee_comment || ''])));
      setGoalRating(Object.fromEntries(data.goals.map((g) => [g.id, g.rating ?? undefined])));
      // The score is withheld from the employee until the review is shared,
      // so a 403 here is the rule working, not a failure worth showing.
      formService.score(reviewId).then(setScore).catch(() => setScore(null));
    } catch (err) {
      onError(detailOf(err, 'Could not load this review.'));
    } finally {
      setLoading(false);
    }
  }, [reviewId, onError]);

  useEffect(() => { void load(); }, [load]);

  if (loading || !form) {
    return (
      <div className="rf-overlay"><div className="rf-panel rf-loading">Loading review…</div></div>
    );
  }

  const isEmployee = form.your_part === 'employee';
  const isManager = form.your_part === 'manager';
  const canEdit = form.you_can_edit;

  const mySections = form.sections.filter((s) => s.rated_by === (isEmployee ? 'employee' : 'manager'));
  const theirSections = form.sections.filter((s) => s.rated_by !== (isEmployee ? 'employee' : 'manager'));

  // Required sections are the server's rule; saying so before submit beats a
  // 400 after it.
  const missing = mySections.filter((s) => s.required && s.editable && !(sections[s.key] || '').trim());

  const save = async (submit: boolean) => {
    setBusy(true);
    try {
      if (isEmployee) {
        await formService.saveSelfForm(reviewId, {
          scores, sections,
          goal_comments: goalText,
          submit,
        });
      } else {
        await formService.saveManagerForm(reviewId, {
          scores, sections,
          goal_ratings: Object.fromEntries(
            Object.entries(goalRating)
              .filter(([, v]) => typeof v === 'number')
              .map(([id, v]) => [Number(id), { rating: v as number }]),
          ),
          ...(strengths.trim() ? { strengths: strengths.trim() } : {}),
          ...(development.trim() ? { development_areas: development.trim() } : {}),
          ...(calibration.trim() ? { calibration_note: calibration.trim() } : {}),
          ...(recommendation ? { recommendation } : {}),
          submit,
        });
      }
      onSaved(submit
        ? (isEmployee ? 'Sent to your manager.' : 'Written. Share it when you have spoken to them.')
        : 'Draft saved.');
    } catch (err) {
      onError(detailOf(err, 'That did not save.'));
      setBusy(false);
    }
  };

  const act = async (fn: () => Promise<unknown>, message: string) => {
    setBusy(true);
    try { await fn(); onSaved(message); }
    catch (err) { onError(detailOf(err, 'That did not work.')); setBusy(false); }
  };

  return (
    <div className="rf-overlay" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="rf-panel" role="dialog" aria-modal="true">
        <header className="rf-head">
          <div>
            <h2>{form.employee_name}</h2>
            <p>
              {form.cycle_name}
              {form.designation ? ` · ${form.designation}` : ''}
              {form.template_name ? ` · ${form.template_name}` : ''}
            </p>
          </div>
          <div className="rf-head-right">
            <span className="rf-status">{REVIEW_STATUS_LABEL[form.status]}</span>
            <button type="button" className="rf-x" onClick={onClose} aria-label="Close"><FiX /></button>
          </div>
        </header>

        <p className="rf-waiting">
          {canEdit
            ? <><FiCheck /> Your turn — this is waiting on {form.waiting_on}.</>
            : <><FiLock /> Waiting on {form.waiting_on}.</>}
        </p>

        {score && typeof score.final_score === 'number' && (
          <div className="rf-score">
            <div><span>Goals</span><b>{Number(score.goal_score ?? 0).toFixed(2)}</b><small>{form.goal_weight}%</small></div>
            <div><span>Competencies</span><b>{Number(score.competency_score ?? 0).toFixed(2)}</b><small>{form.competency_weight}%</small></div>
            <div className="rf-score-final">
              <span>Final</span><b>{Number(score.final_score).toFixed(2)}</b>
              {score.band && <small>{String(score.band)}</small>}
            </div>
          </div>
        )}

        {/* -------- competencies -------- */}
        {form.competencies.length > 0 && (
          <section className="rf-block">
            <h3>Competencies <em>{form.competency_weight}% of the score</em></h3>
            <ul className="rf-comps">
              {form.competencies.map((c: FormCompetency) => {
                const mine = isManager ? c.manager : c.self;
                const theirs = isManager ? c.self : c.manager;
                return (
                  <li key={c.key}>
                    <div className="rf-comp-head">
                      <strong>{c.title}</strong>
                      <span className="rf-weight">{c.weight}%</span>
                    </div>
                    {c.description && <p className="rf-desc">{c.description}</p>}
                    <div className="rf-comp-grid">
                      <div>
                        <span className="rf-who">{isManager ? 'Your rating' : 'Your rating'}</span>
                        <Rating value={scores[c.key]?.rating} editable={mine.editable}
                          scale={form.rating_scale}
                          onChange={(v) => setScores({ ...scores, [c.key]: { ...scores[c.key], rating: v } })} />
                        {mine.editable && (
                          <textarea rows={2} placeholder="The evidence for it."
                            value={scores[c.key]?.comment || ''}
                            onChange={(e) => setScores({ ...scores, [c.key]: { ...scores[c.key], comment: e.target.value } })} />
                        )}
                      </div>
                      <div className="rf-theirs">
                        <span className="rf-who">{isManager ? 'Self-rating' : "Manager's rating"}</span>
                        {theirs.rating != null || theirs.comment
                          ? <>
                              <Rating value={theirs.rating} editable={false} scale={form.rating_scale} />
                              {theirs.comment && <p className="rf-quote">{theirs.comment}</p>}
                            </>
                          : <p className="rf-pending">
                              {isEmployee ? <><FiEye /> Not shared yet</> : 'Not filled in'}
                            </p>}
                      </div>
                    </div>
                  </li>
                );
              })}
            </ul>
          </section>
        )}

        {/* -------- written sections -------- */}
        {mySections.length > 0 && (
          <section className="rf-block">
            <h3>{isEmployee ? 'Your half' : 'Your assessment'}</h3>
            {mySections.map((s: FormSection) => (
              <label key={s.key} className="rf-section">
                <span className="rf-section-title">
                  {s.title}{s.required && <em className="rf-req">required</em>}
                </span>
                {s.prompt && <span className="rf-prompt">{s.prompt}</span>}
                <textarea rows={4} disabled={!s.editable}
                  value={sections[s.key] || ''}
                  onChange={(e) => setSections({ ...sections, [s.key]: e.target.value })} />
              </label>
            ))}
          </section>
        )}

        {theirSections.some((s) => s.value) && (
          <section className="rf-block">
            <h3>{isEmployee ? "Your manager's half" : 'Their self-assessment'}</h3>
            {theirSections.filter((s) => s.value).map((s) => (
              <div key={s.key} className="rf-readonly">
                <span className="rf-section-title">{s.title}</span>
                <p>{s.value}</p>
              </div>
            ))}
          </section>
        )}

        {/* -------- goals -------- */}
        {form.goals.length > 0 && (
          <section className="rf-block">
            <h3>Goals <em>{form.goal_weight}% of the score</em></h3>
            <ul className="rf-goals">
              {form.goals.map((g: FormGoal) => (
                <li key={g.id}>
                  <div className="rf-goal-head">
                    <strong>{g.title}</strong>
                    <span className="rf-chip">{GOAL_SOURCE_LABEL[g.source]}</span>
                    {g.weight > 0 && <span className="rf-weight">{g.weight}%</span>}
                  </div>
                  {g.target && <p className="rf-desc">Target: {g.target}</p>}

                  {g.employee_can_comment ? (
                    <label className="rf-goal-field">
                      <span>What happened, in your words</span>
                      <textarea rows={2} value={goalText[g.id] || ''}
                        onChange={(e) => setGoalText({ ...goalText, [g.id]: e.target.value })} />
                    </label>
                  ) : g.employee_comment ? (
                    <p className="rf-quote">{g.employee_comment}</p>
                  ) : null}

                  <div className="rf-goal-rating">
                    <span className="rf-who">Manager's rating</span>
                    <Rating value={goalRating[g.id]} editable={g.manager_can_rate}
                      scale={form.rating_scale}
                      onChange={(v) => setGoalRating({ ...goalRating, [g.id]: v })} />
                  </div>
                  {g.rating_comment && <p className="rf-quote">{g.rating_comment}</p>}
                </li>
              ))}
            </ul>
          </section>
        )}

        {/* -------- manager-only tail -------- */}
        {isManager && canEdit && (
          <section className="rf-block">
            <h3>Outcome</h3>
            <div className="rf-two">
              <label><span>Strengths</span>
                <textarea rows={3} value={strengths} onChange={(e) => setStrengths(e.target.value)}
                  placeholder="What they are relied on for, with an example." /></label>
              <label><span>Development areas</span>
                <textarea rows={3} value={development} onChange={(e) => setDevelopment(e.target.value)}
                  placeholder="One or two things, specific enough to act on this period." /></label>
            </div>
            <label className="rf-section">
              <span className="rf-section-title">Recommendation</span>
              <select value={recommendation} onChange={(e) => setRecommendation(e.target.value as Recommendation)}>
                <option value="">Choose…</option>
                {(Object.keys(RECOMMENDATION_LABEL) as Recommendation[]).map((r) => (
                  <option key={r} value={r}>{RECOMMENDATION_LABEL[r]}</option>
                ))}
              </select>
            </label>
            <label className="rf-section">
              <span className="rf-section-title">Calibration note <em className="rf-private">private</em></span>
              <span className="rf-prompt">For HR and the leadership review. Never shown to the employee.</span>
              <textarea rows={2} value={calibration} onChange={(e) => setCalibration(e.target.value)} />
            </label>
          </section>
        )}

        {isEmployee && form.status === 'shared' && (
          <section className="rf-block">
            <h3>Acknowledge</h3>
            <textarea rows={2} value={ack} onChange={(e) => setAck(e.target.value)}
              placeholder="Anything you want recorded alongside it (optional)." />
            <div className="rf-actions">
              <button type="button" className="rf-btn rf-btn-primary" disabled={busy}
                onClick={() => void act(
                  () => performanceService.acknowledge(reviewId, ack.trim() || undefined),
                  'Acknowledged.')}>
                <FiCheck /> I have read this
              </button>
            </div>
          </section>
        )}

        <footer className="rf-foot">
          {missing.length > 0 && canEdit && (
            <p className="rf-missing">
              <FiAlertTriangle /> Still needed before submitting: {missing.map((s) => s.title).join(', ')}.
            </p>
          )}
          <div className="rf-actions">
            <button type="button" className="rf-btn" onClick={onClose}>Close</button>
            {canEdit && (
              <>
                <button type="button" className="rf-btn" disabled={busy}
                  onClick={() => void save(false)}>Save draft</button>
                <button type="button" className="rf-btn rf-btn-primary"
                  disabled={busy || missing.length > 0}
                  onClick={() => void save(true)}>
                  <FiCheck /> {isEmployee ? 'Submit to manager' : 'Finish writing'}
                </button>
              </>
            )}
            {isManager && form.status === 'manager_submitted' && (
              /* Separate from writing it: sharing is what the employee reads,
                 and that should follow the conversation. */
              <button type="button" className="rf-btn rf-btn-primary" disabled={busy}
                onClick={() => void act(() => performanceService.share(reviewId), 'Shared with the employee.')}>
                Share with {form.employee_name?.split(' ')[0] || 'them'}
              </button>
            )}
          </div>
        </footer>
      </div>
    </div>
  );
};

export default ReviewFormPanel;
