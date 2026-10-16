import { useCallback, useEffect, useState, type FormEvent } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { FiAlertTriangle, FiChevronRight, FiClipboard, FiPlus } from 'react-icons/fi';
import { ROUTES } from '../../config/constants';
import { useAuthStore } from '../../store/authStore';
import { isLabCustodian } from '../../utils/permissions';
import {
  inventoryService, money, auditScopeOf as scopeOf,
  type AuditSession, type ItemCategory, type StoreLocation,
} from '../../services/inventoryService';
import '../../components/inventory/inventory.css';
import './Audit.css';

const when = (value?: string | null) => {
  if (!value) return '';
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? value : d.toLocaleString('en-GB', {
    day: 'numeric', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit',
  });
};

const SessionRow = ({ s }: { s: AuditSession }) => {
  const pct = s.expected_count ? Math.round((s.scanned_count / s.expected_count) * 100) : 0;
  return (
    <li>
      <Link className="aud-row" to={ROUTES.INVENTORY_AUDIT_SESSION(s.id)}>
        <span className="aud-row-main">
          <strong>{s.label || scopeOf(s)}</strong>
          <small>
            {s.label ? `${scopeOf(s)} · ` : ''}
            {s.status === 'open' ? `opened ${when(s.opened_at)}` : `closed ${when(s.closed_at)}`}
            {s.opened_by_name ? ` by ${s.opened_by_name}` : ''}
          </small>
        </span>
        <span className="aud-row-side">
          {s.status === 'open' ? (
            <span className="aud-mini-progress" title={`${s.scanned_count} of ${s.expected_count} scanned`}>
              <span style={{ width: `${pct}%` }} />
              <em>{s.scanned_count}/{s.expected_count}</em>
            </span>
          ) : s.variance_count ? (
            <span className="inv-chip inv-chip-warning">
              {s.variance_count} variance{s.variance_count === 1 ? '' : 's'}
              {money(s.variance_value) && s.variance_value > 0 ? ` · ${money(s.variance_value)}` : ''}
            </span>
          ) : (
            <span className="inv-chip inv-chip-success">No variance</span>
          )}
          <FiChevronRight />
        </span>
      </Link>
    </li>
  );
};

/**
 * Stock counts. A count is scoped to a shelf or a category, and starts with
 * the list of what the register expects to find there already written — the
 * session screen ticks it off as labels are scanned.
 */
const Audit = () => {
  const user = useAuthStore((s) => s.user);
  const navigate = useNavigate();
  const custodian = isLabCustodian(user);
  const [sessions, setSessions] = useState<AuditSession[]>([]);
  const [locations, setLocations] = useState<StoreLocation[]>([]);
  const [categories, setCategories] = useState<ItemCategory[]>([]);
  const [loading, setLoading] = useState(custodian);
  const [error, setError] = useState('');
  const [starting, setStarting] = useState(false);
  const [form, setForm] = useState({ location_id: '', category_id: '', label: '' });
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setSessions(await inventoryService.auditSessions());
      setError('');
    } catch (err) {
      setError((err as Error).message || 'Could not read the counts.');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!custodian) return;
    void load();
    inventoryService.locations().then(setLocations).catch(() => setLocations([]));
    // A stock count is the lab custodian's; office categories are not theirs.
    inventoryService.categories('lab').then(setCategories).catch(() => setCategories([]));
  }, [custodian, load]);

  const start = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError('');
    try {
      const session = await inventoryService.openAuditSession({
        ...(form.location_id ? { location_id: Number(form.location_id) } : {}),
        ...(form.category_id ? { category_id: Number(form.category_id) } : {}),
        ...(form.label.trim() ? { label: form.label.trim() } : {}),
      });
      navigate(ROUTES.INVENTORY_AUDIT_SESSION(session.id));
    } catch (err) {
      setError((err as Error).message || 'Could not start the count.');
      setBusy(false);
    }
  };

  if (!custodian) {
    return (
      <div className="aud-page">
        <header className="aud-head"><div><h1>Stock count</h1></div></header>
        <div className="aud-empty">Stock counts are run by the lab custodian.</div>
      </div>
    );
  }

  const open = sessions.filter((s) => s.status === 'open');
  const closed = sessions.filter((s) => s.status !== 'open');

  return (
    <div className="aud-page">
      <header className="aud-head">
        <div>
          <h1>Stock count</h1>
          <p>Pick a shelf or a category, walk it with the camera, close it when you are done.</p>
        </div>
        {!starting && (
          <button type="button" className="inv-btn inv-btn-primary" onClick={() => setStarting(true)}>
            <FiPlus /> Start a count
          </button>
        )}
      </header>

      {error && <div className="aud-alert aud-alert-error"><FiAlertTriangle /> {error}</div>}

      {starting && (
        <form className="aud-form" onSubmit={start}>
          <label className="inv-field"><span>Shelf or location</span>
            <select value={form.location_id} onChange={(e) => setForm((f) => ({ ...f, location_id: e.target.value }))}>
              <option value="">Any location</option>
              {locations.map((l) => <option key={l.id} value={l.id}>{l.path || l.code}</option>)}
            </select>
          </label>
          <label className="inv-field"><span>Category</span>
            <select value={form.category_id} onChange={(e) => setForm((f) => ({ ...f, category_id: e.target.value }))}>
              <option value="">Any category</option>
              {categories.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
            </select>
          </label>
          <label className="inv-field aud-wide"><span>Label (optional)</span>
            <input value={form.label} onChange={(e) => setForm((f) => ({ ...f, label: e.target.value }))}
              placeholder="Quarterly count — store room rack B" maxLength={160} />
            <small>Choose a location, a category, or both. What the register expects there is listed the moment it opens.</small>
          </label>
          <div className="aud-form-foot aud-wide">
            <button type="button" className="inv-btn" onClick={() => setStarting(false)} disabled={busy}>Cancel</button>
            <button type="submit" className="inv-btn inv-btn-primary" disabled={busy || (!form.location_id && !form.category_id)}>
              <FiClipboard /> {busy ? 'Opening…' : 'Open the count'}
            </button>
          </div>
        </form>
      )}

      {loading ? (
        <p className="inv-muted">Loading…</p>
      ) : sessions.length === 0 ? (
        <div className="aud-empty">No counts yet.</div>
      ) : (
        <>
          {open.length > 0 && (
            <section className="aud-section">
              <h2>In progress</h2>
              <ul className="aud-list">{open.map((s) => <SessionRow key={s.id} s={s} />)}</ul>
            </section>
          )}
          {closed.length > 0 && (
            <section className="aud-section">
              <h2>Closed</h2>
              <ul className="aud-list">{closed.map((s) => <SessionRow key={s.id} s={s} />)}</ul>
            </section>
          )}
        </>
      )}
    </div>
  );
};

export default Audit;
