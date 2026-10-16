import { useCallback, useEffect, useMemo, useState, type FormEvent } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import {
  FiAlertTriangle, FiCheck, FiCheckCircle, FiPackage, FiPlus, FiSlash, FiX, FiXCircle,
} from 'react-icons/fi';
import { ROUTES } from '../../config/constants';
import { useAuthStore } from '../../store/authStore';
import { isLabCustodian } from '../../utils/permissions';
import {
  inventoryService, requestStatusOf, whereIs, isOffice,
  type InventoryItem, type ItemCategory, type StockRequest,
} from '../../services/inventoryService';
import '../../components/inventory/inventory.css';
import './Requests.css';

type Tab = 'open' | 'to-issue' | 'closed' | 'all';

const TABS: { key: Tab; label: string }[] = [
  { key: 'open', label: 'Waiting' },
  { key: 'to-issue', label: 'To issue' },
  { key: 'closed', label: 'Closed' },
  { key: 'all', label: 'All' },
];

const inTab = (r: StockRequest, tab: Tab) =>
  tab === 'all' ? true
  : tab === 'open' ? r.status === 'pending'
  : tab === 'to-issue' ? r.status === 'approved'
  : r.status === 'issued' || r.status === 'rejected' || r.status === 'cancelled';

const shortDate = (value?: string | null) => {
  if (!value) return null;
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? value : d.toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' });
};

const qtyText = (qty: number) => (Number.isInteger(qty) ? String(qty) : qty.toFixed(2));

const what = (r: StockRequest) =>
  r.item_name
    ? `${r.item_name}${r.item_tag ? ` (${r.item_tag})` : ''}`
    : `Any ${r.category_name || 'item'}`;

/* ---------------------------- New request ----------------------------- */

const NewRequestForm = ({ categories, initialTag, onCreated, onCancel }: {
  categories: ItemCategory[];
  initialTag: string;
  onCreated: (created: StockRequest) => void;
  onCancel: () => void;
}) => {
  // Asking for "that oscilloscope" and asking for "an oscilloscope" are both
  // legitimate, and the API takes either. Default to whichever the link that
  // brought us here implies.
  const [mode, setMode] = useState<'item' | 'category'>(initialTag ? 'item' : 'category');
  const [tag, setTag] = useState(initialTag);
  const [categoryId, setCategoryId] = useState('');
  const [qty, setQty] = useState('1');
  const [purpose, setPurpose] = useState('');
  const [neededBy, setNeededBy] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError('');
    try {
      let target: { item_id: number } | { category_id: number };
      if (mode === 'item') {
        // The tag is what people have in front of them; the request wants the
        // id. The scan endpoint resolves one to the other and says so verbatim
        // when the tag carries nothing.
        const scanned = await inventoryService.byTag(tag.trim());
        // The store handles lab kit. Office equipment (laptops, phones, SIMs)
        // is issued by admin or HR directly, not through a stock request.
        if (isOffice(scanned.item)) {
          throw new Error(`${scanned.item.tag} is office equipment — ask admin or HR for it, not the lab store.`);
        }
        target = { item_id: scanned.item.id };
      } else {
        target = { category_id: Number(categoryId) };
      }
      const created = await inventoryService.createRequest({
        ...target,
        qty: Number(qty) || 1,
        purpose: purpose.trim(),
        ...(neededBy ? { needed_by: neededBy } : {}),
      });
      onCreated(created);
    } catch (err) {
      setError((err as Error).message || 'That request did not go through.');
    } finally {
      setBusy(false);
    }
  };

  return (
    <form className="req-form" onSubmit={submit}>
      <div className="req-mode" role="radiogroup" aria-label="What are you asking for?">
        <label className={mode === 'item' ? 'is-on' : undefined}>
          <input type="radio" checked={mode === 'item'} onChange={() => setMode('item')} />
          A specific item
        </label>
        <label className={mode === 'category' ? 'is-on' : undefined}>
          <input type="radio" checked={mode === 'category'} onChange={() => setMode('category')} />
          Anything from a category
        </label>
      </div>

      {mode === 'item' ? (
        <label className="inv-field"><span>Tag</span>
          <input
            value={tag}
            onChange={(e) => setTag(e.target.value)}
            placeholder="QGT-EQ-0042"
            autoCapitalize="characters"
            spellCheck={false}
            required
          />
          <small>The number on the label. <Link to={ROUTES.INVENTORY_ITEMS}>Find it in the catalogue</Link>.</small>
        </label>
      ) : (
        <label className="inv-field"><span>Category</span>
          <select value={categoryId} onChange={(e) => setCategoryId(e.target.value)} required>
            <option value="">Choose a category…</option>
            {categories.filter((c) => c.is_active !== false).map((c) => (
              <option key={c.id} value={c.id}>{c.name}</option>
            ))}
          </select>
          <small>The custodian picks the actual item when they issue it.</small>
        </label>
      )}

      <label className="inv-field"><span>How many?</span>
        <input type="number" min="0" step="any" inputMode="decimal" value={qty} onChange={(e) => setQty(e.target.value)} required />
      </label>

      <label className="inv-field"><span>Needed by (optional)</span>
        <input type="date" value={neededBy} onChange={(e) => setNeededBy(e.target.value)} />
      </label>

      <label className="inv-field req-wide"><span>What is it for?</span>
        <textarea rows={2} value={purpose} onChange={(e) => setPurpose(e.target.value)} required
          placeholder="Bring-up of the CBOXX power board" />
      </label>

      {error && <p className="inv-dialog-error req-wide"><FiAlertTriangle /> {error}</p>}

      <div className="req-form-foot req-wide">
        <button type="button" className="inv-btn" onClick={onCancel} disabled={busy}>Cancel</button>
        <button type="submit" className="inv-btn inv-btn-primary" disabled={busy}>
          {busy ? 'Sending…' : 'Send request'}
        </button>
      </div>
    </form>
  );
};

/* ------------------------------- Dialogs ------------------------------ */

const DeclineDialog = ({ request, onDone, onCancel }: {
  request: StockRequest;
  onDone: (updated: StockRequest) => void;
  onCancel: () => void;
}) => {
  const [reason, setReason] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError('');
    try {
      onDone(await inventoryService.decideRequest(request.id, {
        approved: false, ...(reason.trim() ? { reason: reason.trim() } : {}),
      }));
    } catch (err) {
      setError((err as Error).message || 'That did not go through.');
      setBusy(false);
    }
  };

  return (
    <div className="inv-overlay" role="dialog" aria-modal="true" aria-label="Decline request">
      <form className="inv-dialog" onSubmit={submit}>
        <header>
          <h3>Decline this request</h3>
          <button type="button" className="inv-icon-btn" onClick={onCancel} aria-label="Close"><FiX /></button>
        </header>
        <p className="inv-dialog-item">
          {request.requester_name || 'Someone'} asked for {qtyText(request.qty)} × {what(request)}
        </p>
        <label className="inv-field"><span>Reason (optional)</span>
          <textarea rows={2} value={reason} onChange={(e) => setReason(e.target.value)} />
          <small>They are told who declined it, and this reason if you give one.</small>
        </label>
        {error && <p className="inv-dialog-error"><FiAlertTriangle /> {error}</p>}
        <footer>
          <button type="button" className="inv-btn" onClick={onCancel} disabled={busy}>Cancel</button>
          <button type="submit" className="inv-btn inv-btn-danger" disabled={busy}>
            {busy ? 'Working…' : 'Decline'}
          </button>
        </footer>
      </form>
    </div>
  );
};

/**
 * Hand it over. A request that named a category cannot be issued until the
 * custodian says which actual item is leaving the store, so the picker appears
 * only when `item_id` is null (guide §12).
 */
const IssueDialog = ({ request, categories, onDone, onCancel }: {
  request: StockRequest;
  categories: ItemCategory[];
  onDone: (updated: StockRequest) => void;
  onCancel: () => void;
}) => {
  const needsItem = !request.item_id;
  const [candidates, setCandidates] = useState<InventoryItem[]>([]);
  const [loadingCandidates, setLoadingCandidates] = useState(needsItem);
  const [itemId, setItemId] = useState('');
  const [qty, setQty] = useState(qtyText(request.qty));
  const [returnBy, setReturnBy] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  // A request that named an item: read it once, only to know whether it is a
  // bin (consumed, no return date) or a unit (checked out, may have one).
  const [named, setNamed] = useState<InventoryItem | null>(null);
  useEffect(() => {
    if (!request.item_id) return;
    let live = true;
    inventoryService.getItem(request.item_id)
      .then((row) => { if (live) setNamed(row); })
      .catch(() => { /* the issue call itself will say what is wrong */ });
    return () => { live = false; };
  }, [request.item_id]);

  useEffect(() => {
    if (!needsItem || !request.category_id) return;
    let live = true;
    inventoryService.listItems({ domain: 'lab', category_id: request.category_id, status: 'in_stock', limit: 200 })
      .then((rows) => { if (live) setCandidates(rows); })
      .catch((err) => { if (live) setError((err as Error).message || 'Could not read the shelf.'); })
      .finally(() => { if (live) setLoadingCandidates(false); });
    return () => { live = false; };
  }, [needsItem, request.category_id]);

  // A bin is consumed against the request, a unit is checked out to the
  // requester; only the second has a "back by". Category tells us which.
  const category = categories.find((c) => c.id === request.category_id);
  const picked = candidates.find((c) => String(c.id) === itemId);
  const isQuantity = (picked?.tracking_type ?? named?.tracking_type ?? category?.tracking_type) === 'quantity';

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError('');
    try {
      onDone(await inventoryService.issueRequest(request.id, {
        ...(needsItem ? { item_id: Number(itemId) } : {}),
        ...(Number(qty) > 0 && Number(qty) !== request.qty ? { qty: Number(qty) } : {}),
        ...(!isQuantity && returnBy ? { expected_return_date: returnBy } : {}),
      }));
    } catch (err) {
      setError((err as Error).message || 'That did not issue.');
      setBusy(false);
    }
  };

  return (
    <div className="inv-overlay" role="dialog" aria-modal="true" aria-label="Issue request">
      <form className="inv-dialog" onSubmit={submit}>
        <header>
          <h3>Issue to {request.requester_name || 'the requester'}</h3>
          <button type="button" className="inv-icon-btn" onClick={onCancel} aria-label="Close"><FiX /></button>
        </header>
        <p className="inv-dialog-item">
          {request.item_tag && <code>{request.item_tag}</code>} {qtyText(request.qty)} × {what(request)}
        </p>
        <p className="inv-dialog-blurb">“{request.purpose}”</p>

        {needsItem && (
          <label className="inv-field"><span>Which one is leaving the store?</span>
            <select value={itemId} onChange={(e) => setItemId(e.target.value)} required disabled={loadingCandidates}>
              <option value="">{loadingCandidates ? 'Reading the shelf…' : 'Choose an item…'}</option>
              {candidates.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.tag} — {c.name}
                  {c.tracking_type === 'quantity' && typeof c.qty === 'number' ? ` (${c.qty} in bin)` : ''}
                  {` · ${whereIs(c)}`}
                </option>
              ))}
            </select>
            {!loadingCandidates && candidates.length === 0 && (
              <small>Nothing in this category is on a shelf right now.</small>
            )}
          </label>
        )}

        <label className="inv-field"><span>How many?</span>
          <input type="number" min="0" step="any" inputMode="decimal" value={qty} onChange={(e) => setQty(e.target.value)} />
        </label>

        {!isQuantity && (
          <label className="inv-field"><span>Back by (optional)</span>
            <input type="date" value={returnBy} onChange={(e) => setReturnBy(e.target.value)} />
          </label>
        )}

        {error && <p className="inv-dialog-error"><FiAlertTriangle /> {error}</p>}
        <footer>
          <button type="button" className="inv-btn" onClick={onCancel} disabled={busy}>Cancel</button>
          <button type="submit" className="inv-btn inv-btn-primary" disabled={busy || (needsItem && !itemId)}>
            {busy ? 'Working…' : 'Issue it'}
          </button>
        </footer>
      </form>
    </div>
  );
};

/* -------------------------------- Page -------------------------------- */

/**
 * Stock requests: three states, one inbox.
 *
 * `pending → approved → issued`, or declined, or cancelled. Everybody can ask;
 * a team lead decides for their own team; the custodian decides for anybody
 * (themselves included — there is one of them, and making them find an
 * approver would stall the store) and is the only one who issues.
 *
 * Unlike an item, a request carries no `actions[]`, so which buttons appear is
 * a mirror of the backend's rule rather than its answer. The server is still
 * the authority: a lead who does not approve for this person gets its 403
 * verbatim, and nobody is shown a button for a state the request is not in.
 */
const Requests = () => {
  const user = useAuthStore((s) => s.user);
  const [params] = useSearchParams();
  const me = Number(user?.id);
  const custodian = isLabCustodian(user);
  const lead = user?.role === 'manager';
  // Leads and custodians see everybody's; the rest only ever get their own.
  const seesOthers = custodian || lead;

  const [requests, setRequests] = useState<StockRequest[]>([]);
  const [categories, setCategories] = useState<ItemCategory[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [tab, setTab] = useState<Tab>(custodian ? 'to-issue' : 'open');
  const [mineOnly, setMineOnly] = useState(false);
  const [composing, setComposing] = useState(Boolean(params.get('tag')));
  const [declining, setDeclining] = useState<StockRequest | null>(null);
  const [issuing, setIssuing] = useState<StockRequest | null>(null);
  const [busyId, setBusyId] = useState<number | null>(null);

  useEffect(() => {
    // Requests are for the lab store; office equipment is issued by admin/HR.
    inventoryService.categories('lab').then(setCategories).catch(() => setCategories([]));
  }, []);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setRequests(await inventoryService.listRequests(mineOnly ? { mine: true } : undefined));
      setError('');
    } catch (err) {
      setError((err as Error).message || 'Could not read the requests.');
    } finally {
      setLoading(false);
    }
  }, [mineOnly]);

  useEffect(() => { void load(); }, [load]);

  const counts = useMemo(() => {
    const out: Record<Tab, number> = { open: 0, 'to-issue': 0, closed: 0, all: requests.length };
    for (const r of requests) {
      if (inTab(r, 'open')) out.open += 1;
      else if (inTab(r, 'to-issue')) out['to-issue'] += 1;
      else out.closed += 1;
    }
    return out;
  }, [requests]);

  const shown = requests.filter((r) => inTab(r, tab));

  const replace = (updated: StockRequest, message: string) => {
    setRequests((rows) => rows.map((r) => (r.id === updated.id ? updated : r)));
    setNotice(message);
    setError('');
  };

  const approve = async (r: StockRequest) => {
    setBusyId(r.id);
    setNotice('');
    try {
      replace(await inventoryService.decideRequest(r.id, { approved: true }), 'Approved. The custodian will issue it.');
    } catch (err) {
      setError((err as Error).message || 'That did not go through.');
    } finally {
      setBusyId(null);
    }
  };

  const cancel = async (r: StockRequest) => {
    setBusyId(r.id);
    setNotice('');
    try {
      replace(await inventoryService.cancelRequest(r.id), 'Request cancelled.');
    } catch (err) {
      setError((err as Error).message || 'That did not go through.');
    } finally {
      setBusyId(null);
    }
  };

  return (
    <div className="req-page">
      <header className="req-head">
        <div>
          <h1>Requests</h1>
          <p>Ask the store for something. Your lead approves it, the custodian hands it over.</p>
        </div>
        {!composing && (
          <button type="button" className="inv-btn inv-btn-primary" onClick={() => { setComposing(true); setNotice(''); }}>
            <FiPlus /> New request
          </button>
        )}
      </header>

      {error && <div className="req-alert req-alert-error"><FiAlertTriangle /> {error}</div>}
      {notice && <div className="req-alert req-alert-ok"><FiCheckCircle /> {notice}</div>}

      {composing && (
        <NewRequestForm
          categories={categories}
          initialTag={params.get('tag') ?? ''}
          onCancel={() => setComposing(false)}
          onCreated={(created) => {
            setComposing(false);
            setRequests((rows) => [created, ...rows]);
            setTab('open');
            setNotice(created.status === 'pending'
              ? 'Request sent. Your lead has been told.'
              : `Request recorded — ${requestStatusOf(created.status).label.toLowerCase()}.`);
          }}
        />
      )}

      <div className="req-toolbar">
        <div className="req-tabs" role="tablist">
          {TABS.map((t) => (
            <button
              key={t.key}
              type="button"
              role="tab"
              aria-selected={tab === t.key}
              className={tab === t.key ? 'is-on' : undefined}
              onClick={() => setTab(t.key)}
            >
              {t.label}{counts[t.key] ? <span>{counts[t.key]}</span> : null}
            </button>
          ))}
        </div>
        {seesOthers && (
          <label className="req-check">
            <input type="checkbox" checked={mineOnly} onChange={(e) => setMineOnly(e.target.checked)} />
            Only mine
          </label>
        )}
      </div>

      {loading ? (
        <p className="inv-muted">Loading…</p>
      ) : shown.length === 0 ? (
        <div className="req-empty">
          {tab === 'open' ? 'Nothing is waiting for a decision.'
            : tab === 'to-issue' ? 'Nothing approved is waiting to be handed over.'
            : 'No requests here.'}
        </div>
      ) : (
        <ul className="req-list">
          {shown.map((r) => {
            const mine = r.requester_id === me;
            const status = requestStatusOf(r.status);
            // Mirrors decide_request: nobody approves their own except the
            // custodian; a lead approves for their team (the server checks
            // which team); the custodian approves anything.
            const canDecide = r.status === 'pending' && (custodian || (lead && !mine));
            const canIssue = r.status === 'approved' && custodian;
            const canCancel = (r.status === 'pending' || r.status === 'approved') && (mine || custodian);
            const busy = busyId === r.id;
            return (
              <li key={r.id} className={`req-card req-${r.status}`}>
                <div className="req-main">
                  <div className="req-title">
                    <strong>{qtyText(r.qty)} × {what(r)}</strong>
                    <span className={`inv-chip inv-chip-${status.tone}`}>{status.label}</span>
                  </div>
                  <p className="req-purpose">{r.purpose}</p>
                  <p className="req-meta">
                    {seesOthers && <span>{mine ? 'You' : (r.requester_name || 'Someone')}</span>}
                    <span>asked {shortDate(r.created_at)}</span>
                    {r.needed_by && <span>needed by {shortDate(r.needed_by)}</span>}
                    {r.approver_name && r.status !== 'pending' && r.status !== 'cancelled' && (
                      <span>{r.status === 'rejected' ? 'declined' : 'approved'} by {r.approver_name}</span>
                    )}
                    {r.item_id && r.status === 'issued' && (
                      <Link to={ROUTES.INVENTORY_ITEM(r.item_id)}>{r.item_tag || 'the item'}</Link>
                    )}
                  </p>
                  {r.status === 'rejected' && r.rejection_reason && (
                    <p className="req-reason">“{r.rejection_reason}”</p>
                  )}
                </div>

                {(canDecide || canIssue || canCancel) && (
                  <div className="req-actions">
                    {canDecide && (
                      <>
                        <button type="button" className="inv-btn inv-btn-primary" disabled={busy} onClick={() => void approve(r)}>
                          <FiCheck /> Approve
                        </button>
                        <button type="button" className="inv-btn inv-btn-danger-ghost" disabled={busy}
                          onClick={() => { setNotice(''); setDeclining(r); }}>
                          <FiXCircle /> Decline
                        </button>
                      </>
                    )}
                    {canIssue && (
                      <button type="button" className="inv-btn inv-btn-primary" disabled={busy}
                        onClick={() => { setNotice(''); setIssuing(r); }}>
                        <FiPackage /> Issue
                      </button>
                    )}
                    {canCancel && (
                      <button type="button" className="inv-btn" disabled={busy} onClick={() => void cancel(r)}>
                        <FiSlash /> {mine ? 'Withdraw' : 'Cancel'}
                      </button>
                    )}
                  </div>
                )}
              </li>
            );
          })}
        </ul>
      )}

      {declining && (
        <DeclineDialog
          request={declining}
          onCancel={() => setDeclining(null)}
          onDone={(updated) => { setDeclining(null); replace(updated, 'Declined. They have been told.'); }}
        />
      )}
      {issuing && (
        <IssueDialog
          request={issuing}
          categories={categories}
          onCancel={() => setIssuing(null)}
          onDone={(updated) => {
            setIssuing(null);
            replace(updated, `Issued ${updated.item_tag ? `${updated.item_tag} ` : ''}to ${updated.requester_name ?? 'the requester'}.`);
          }}
        />
      )}
    </div>
  );
};

export default Requests;
