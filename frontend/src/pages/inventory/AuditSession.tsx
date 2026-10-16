import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent } from 'react';
import { Link, useParams } from 'react-router-dom';
import {
  FiAlertTriangle, FiArrowLeft, FiCamera, FiCameraOff, FiCheckCircle, FiCircle,
  FiCornerDownLeft, FiLock, FiX,
} from 'react-icons/fi';
import { ROUTES } from '../../config/constants';
import { useAuthStore } from '../../store/authStore';
import { isLabCustodian } from '../../utils/permissions';
import {
  inventoryService, auditOutcomeOf, auditScopeOf, money,
  type AuditCloseResult, type AuditLine, type AuditSession as Session,
} from '../../services/inventoryService';
import { useQrCamera } from '../../components/inventory/useQrCamera';
import '../../components/inventory/inventory.css';
import './Audit.css';

const qtyText = (qty?: number | null) =>
  typeof qty === 'number' ? (Number.isInteger(qty) ? String(qty) : qty.toFixed(2)) : '—';

const when = (value?: string | null) => {
  if (!value) return '';
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? value : d.toLocaleString('en-GB', {
    day: 'numeric', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit',
  });
};

/** Close dialog. Closing is the one step here worth a confirmation: it turns
 *  every unscanned line into a variance and can mark repeat misses lost. */
const CloseDialog = ({ session, onDone, onCancel }: {
  session: Session;
  onDone: (result: AuditCloseResult) => void;
  onCancel: () => void;
}) => {
  const [notes, setNotes] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const missing = session.expected_count - session.scanned_count;

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError('');
    try {
      onDone(await inventoryService.closeAuditSession(session.id, notes.trim() ? { notes: notes.trim() } : {}));
    } catch (err) {
      setError((err as Error).message || 'Could not close the count.');
      setBusy(false);
    }
  };

  return (
    <div className="inv-overlay" role="dialog" aria-modal="true" aria-label="Close the count">
      <form className="inv-dialog" onSubmit={submit}>
        <header>
          <h3>Close this count</h3>
          <button type="button" className="inv-icon-btn" onClick={onCancel} aria-label="Close"><FiX /></button>
        </header>
        <p className="inv-dialog-item">{session.label || auditScopeOf(session)}</p>
        {missing > 0 ? (
          <p className="inv-dialog-warn">
            <FiAlertTriangle />
            {missing} expected line{missing === 1 ? ' has' : 's have'} not been scanned. Anything held by a
            person closes as “with a person”; the rest becomes a variance, and anything also missed in the
            previous count is marked lost.
          </p>
        ) : (
          <p className="inv-dialog-blurb">Everything expected has been scanned.</p>
        )}
        <label className="inv-field"><span>Notes (optional)</span>
          <textarea rows={2} value={notes} onChange={(e) => setNotes(e.target.value)} />
        </label>
        {error && <p className="inv-dialog-error"><FiAlertTriangle /> {error}</p>}
        <footer>
          <button type="button" className="inv-btn" onClick={onCancel} disabled={busy}>Keep counting</button>
          <button type="submit" className="inv-btn inv-btn-primary" disabled={busy}>
            {busy ? 'Closing…' : 'Close the count'}
          </button>
        </footer>
      </form>
    </div>
  );
};

/** Fold the one line a scan touched back into the checklist. */
const mergeLine = (line: AuditLine) => (s: Session | null): Session | null => {
  if (!s) return s;
  const before = s.lines.find((l) => l.id === line.id);
  return {
    ...s,
    lines: before ? s.lines.map((l) => (l.id === line.id ? line : l)) : [...s.lines, line],
    expected_count: before ? s.expected_count : s.expected_count + 1,
    scanned_count: s.scanned_count + (before?.scanned_at ? 0 : 1),
  };
};

/**
 * One count, in progress or closed.
 *
 * The session arrives with its lines already written — what the register
 * expected to find when the count started — so this is a checklist: scan a
 * label, its row ticks off. A label nobody expected here comes back as a new
 * row, "found here", which is the commonest result of a count rather than an
 * error. Scanning is continuous, so walking a rack is point, point, point.
 */
const AuditSession = () => {
  const { id } = useParams();
  const sessionId = Number(id);
  const user = useAuthStore((s) => s.user);
  const custodian = isLabCustodian(user);

  const [session, setSession] = useState<Session | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [flash, setFlash] = useState<{ tone: 'ok' | 'warn'; text: string } | null>(null);
  const [tag, setTag] = useState('');
  const [count, setCount] = useState('');
  const [closing, setClosing] = useState(false);
  const [result, setResult] = useState<AuditCloseResult | null>(null);
  const [showAll, setShowAll] = useState(false);
  const inFlight = useRef(new Set<string>());

  const load = useCallback(async () => {
    try {
      setSession(await inventoryService.auditSession(sessionId));
      setError('');
    } catch (err) {
      setError((err as Error).message || 'Could not read this count.');
    } finally {
      setLoading(false);
    }
  }, [sessionId]);

  useEffect(() => { if (custodian) void load(); }, [custodian, load]);

  const record = useCallback(async (raw: string, countedQty?: number) => {
    const scanned = raw.trim();
    if (!scanned || inFlight.current.has(scanned)) return;
    inFlight.current.add(scanned);
    setError('');
    try {
      const line = await inventoryService.auditScan(sessionId, {
        tag: scanned,
        ...(typeof countedQty === 'number' ? { counted_qty: countedQty } : {}),
      });
      setSession(mergeLine(line));
      const outcome = auditOutcomeOf(line.outcome);
      const label = `${line.item_tag ?? scanned}${line.item_name ? ` — ${line.item_name}` : ''}`;
      setFlash(line.outcome === 'found_elsewhere'
        ? { tone: 'warn', text: `${label}: ${outcome.label.toLowerCase()}.` }
        : { tone: 'ok', text: `${label}${line.variance ? ` · counted ${qtyText(line.counted_qty)} of ${qtyText(line.expected_qty)}` : ''}` });
    } catch (err) {
      setFlash(null);
      setError((err as Error).message || `Could not record ${scanned}.`);
    } finally {
      inFlight.current.delete(scanned);
    }
  }, [sessionId]);

  const camera = useQrCamera((text) => { void record(text); });

  const submitManual = (event: FormEvent) => {
    event.preventDefault();
    // A count matters for bins; leave it blank for a unit and the server
    // records it as the one that was expected.
    void record(tag, count.trim() ? Number(count) : undefined);
    setTag('');
    setCount('');
  };

  const lines = useMemo(() => session?.lines ?? [], [session]);
  const pending = lines.filter((l) => !l.scanned_at && !l.outcome);
  const done = lines.filter((l) => l.scanned_at || l.outcome);
  const isOpen = session?.status === 'open';
  const pct = session?.expected_count ? Math.round((session.scanned_count / session.expected_count) * 100) : 0;

  if (!custodian) {
    return (
      <div className="aud-page">
        <Link className="aud-back" to={ROUTES.INVENTORY_AUDIT}><FiArrowLeft size={13} /> Stock counts</Link>
        <div className="aud-empty">Stock counts are run by the lab custodian.</div>
      </div>
    );
  }

  return (
    <div className="aud-page">
      <Link className="aud-back" to={ROUTES.INVENTORY_AUDIT}><FiArrowLeft size={13} /> Stock counts</Link>

      {loading ? (
        <p className="inv-muted">Loading…</p>
      ) : !session ? (
        error && <div className="aud-alert aud-alert-error"><FiAlertTriangle /> {error}</div>
      ) : (
        <>
          <header className="aud-head">
            <div>
              <h1>{session.label || auditScopeOf(session)}</h1>
              <p>
                {session.label ? `${auditScopeOf(session)} · ` : ''}
                opened {when(session.opened_at)}
                {session.closed_at ? ` · closed ${when(session.closed_at)}` : ''}
              </p>
            </div>
            {isOpen ? (
              <button type="button" className="inv-btn inv-btn-primary" onClick={() => { camera.stop(); setClosing(true); }}>
                <FiLock /> Close the count
              </button>
            ) : (
              <span className="inv-chip inv-chip-muted">Closed</span>
            )}
          </header>

          <div className="aud-progress" aria-label={`${session.scanned_count} of ${session.expected_count} scanned`}>
            <div className="aud-progress-bar"><span style={{ width: `${Math.min(pct, 100)}%` }} /></div>
            <span>
              <strong>{session.scanned_count}</strong> of {session.expected_count} scanned
            </span>
          </div>

          {result && (
            <div className={`aud-alert ${result.variances ? 'aud-alert-warn' : 'aud-alert-ok'}`}>
              {result.variances ? <FiAlertTriangle /> : <FiCheckCircle />}
              <div>
                <p>
                  {result.variances
                    ? `Closed with ${result.variances} variance${result.variances === 1 ? '' : 's'}`
                      + `${money(result.variance_value) && result.variance_value > 0 ? ` worth ${money(result.variance_value)}` : ''}.`
                    : 'Closed with no variance.'}
                </p>
                {result.marked_lost.length > 0 && (
                  <p>
                    Not located in two counts running, so marked lost: {result.marked_lost.join(', ')}.
                  </p>
                )}
              </div>
            </div>
          )}

          {!isOpen && !result && session.variance_count > 0 && (
            <div className="aud-alert aud-alert-warn">
              <FiAlertTriangle />
              <p>
                {session.variance_count} variance{session.variance_count === 1 ? '' : 's'}
                {money(session.variance_value) && session.variance_value > 0 ? ` worth ${money(session.variance_value)}` : ''}.
                {session.notes ? ` ${session.notes}` : ''}
              </p>
            </div>
          )}

          {isOpen && (
            <section className="aud-scanner">
              <div className={`aud-viewport${camera.live ? ' is-live' : ''}`}>
                <video ref={camera.videoRef} muted playsInline />
                <canvas ref={camera.canvasRef} hidden />
                {!camera.live && (
                  <button type="button" className="aud-start" onClick={() => void camera.start()}>
                    <FiCamera size={22} /> Start scanning
                  </button>
                )}
                {camera.live && <span className="aud-reticle" aria-hidden="true" />}
              </div>
              {camera.live && (
                <button type="button" className="inv-btn" onClick={camera.stop}><FiCameraOff /> Stop the camera</button>
              )}
              {camera.cameraError && <p className="aud-camera-note"><FiAlertTriangle /> {camera.cameraError}</p>}

              <form className="aud-manual" onSubmit={submitManual}>
                <input
                  value={tag}
                  onChange={(e) => setTag(e.target.value)}
                  placeholder="Tag — QGT-EQ-0042"
                  autoCapitalize="characters"
                  spellCheck={false}
                  aria-label="Tag"
                />
                <input
                  className="aud-count"
                  type="number"
                  min="0"
                  step="any"
                  inputMode="decimal"
                  value={count}
                  onChange={(e) => setCount(e.target.value)}
                  placeholder="Count"
                  aria-label="Counted quantity (bins only)"
                  title="Bins only. Leave blank for a single unit."
                />
                <button type="submit" className="inv-btn inv-btn-primary" disabled={!tag.trim()}>
                  <FiCornerDownLeft /> Record
                </button>
              </form>

              {flash && (
                <div className={`aud-alert ${flash.tone === 'ok' ? 'aud-alert-ok' : 'aud-alert-warn'}`}>
                  {flash.tone === 'ok' ? <FiCheckCircle /> : <FiAlertTriangle />} {flash.text}
                </div>
              )}
            </section>
          )}

          {error && <div className="aud-alert aud-alert-error"><FiAlertTriangle /> {error}</div>}

          {isOpen && (
            <section className="aud-section">
              <h2>Still to find <span>{pending.length}</span></h2>
              {pending.length === 0 ? (
                <div className="aud-empty">Everything expected has been scanned.</div>
              ) : (
                <ul className="aud-check">
                  {pending.map((l) => (
                    <li key={l.id}>
                      <FiCircle className="aud-tick" />
                      <code>{l.item_tag}</code>
                      <span className="aud-check-name">{l.item_name}</span>
                      <span className="aud-check-qty">expect {qtyText(l.expected_qty)}</span>
                    </li>
                  ))}
                </ul>
              )}
            </section>
          )}

          <section className="aud-section">
            <h2>
              {isOpen ? 'Scanned' : 'Lines'} <span>{isOpen ? done.length : lines.length}</span>
              {!isOpen && (
                <label className="aud-toggle">
                  <input type="checkbox" checked={showAll} onChange={(e) => setShowAll(e.target.checked)} />
                  Show matching lines too
                </label>
              )}
            </h2>
            {(() => {
              // A closed count is mostly rows that matched; the variances are
              // what anybody opens it to read.
              const rows = isOpen ? [...done].reverse()
                : showAll ? lines : lines.filter((l) => l.variance || l.outcome !== 'present');
              if (rows.length === 0) {
                return <div className="aud-empty">{isOpen ? 'Nothing scanned yet.' : 'Every line matched.'}</div>;
              }
              return (
                <div className="aud-table-wrap">
                  <table className="aud-table">
                    <thead>
                      <tr>
                        <th>Tag</th>
                        <th>Item</th>
                        <th className="aud-num">Expected</th>
                        <th className="aud-num">Counted</th>
                        <th>Outcome</th>
                        <th className="aud-num">Variance</th>
                      </tr>
                    </thead>
                    <tbody>
                      {rows.map((l) => {
                        const outcome = auditOutcomeOf(l.outcome);
                        return (
                          <tr key={l.id}>
                            <td><Link className="aud-tag" to={ROUTES.INVENTORY_ITEM(l.item_id)}>{l.item_tag}</Link></td>
                            <td>
                              {l.item_name}
                              {l.note && <small>{l.note}</small>}
                            </td>
                            <td className="aud-num">{qtyText(l.expected_qty)}</td>
                            <td className="aud-num">{qtyText(l.counted_qty)}</td>
                            <td>{l.outcome ? <span className={`inv-chip inv-chip-${outcome.tone}`}>{outcome.label}</span> : '—'}</td>
                            <td className={`aud-num${l.variance ? ' aud-off' : ''}`}>
                              {l.variance ? `${l.variance > 0 ? '+' : ''}${qtyText(l.variance)}` : '0'}
                              {money(l.variance_value) && l.variance_value > 0 && <small>{money(l.variance_value)}</small>}
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              );
            })()}
          </section>
        </>
      )}

      {closing && session && (
        <CloseDialog
          session={session}
          onCancel={() => setClosing(false)}
          onDone={(closed) => {
            setClosing(false);
            setResult(closed);
            void load();
          }}
        />
      )}
    </div>
  );
};

export default AuditSession;
