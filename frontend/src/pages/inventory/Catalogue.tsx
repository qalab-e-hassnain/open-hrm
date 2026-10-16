import { useCallback, useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import {
  FiAlertTriangle, FiChevronLeft, FiChevronRight, FiMaximize, FiPieChart, FiPlus, FiSearch,
} from 'react-icons/fi';
import { ROUTES } from '../../config/constants';
import { useAuthStore } from '../../store/authStore';
import { isLabCustodian, isPrivileged } from '../../utils/permissions';
import {
  inventoryService, whereIs, money, statusOf, ownershipLabel,
  ITEM_STATUSES, OWNERSHIPS,
  type Domain, type InventoryItem, type ItemCategory, type ItemStatus, type Ownership, type StoreLocation,
} from '../../services/inventoryService';
import { StatusChip, ConditionChip, OverdueChip } from '../../components/inventory/StatusChip';
import { usePeople } from '../../components/inventory/usePeople';
import './Catalogue.css';

const PAGE = 50;

/**
 * One section of the register, searchable. The lab catalogue is open to
 * everybody — what things cost is not, which is why the cost column appears
 * only when there are costs in the rows to put in it. The office section is
 * the same table over `domain=office`, behind an admin/HR route: who holds
 * which laptop is the column that matters there, and there is no overdue,
 * because office equipment is held for the length of the job.
 */
const Catalogue = ({ domain = 'lab' }: { domain?: Domain }) => {
  const user = useAuthStore((s) => s.user);
  const office = domain === 'office';
  // Who may register into this section: the lab custodian runs the lab;
  // office equipment is admin and HR's alone.
  const canRegister = office ? isPrivileged(user) : isLabCustodian(user);
  const people = usePeople();
  const [items, setItems] = useState<InventoryItem[]>([]);
  const [categories, setCategories] = useState<ItemCategory[]>([]);
  const [locations, setLocations] = useState<StoreLocation[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  const [search, setSearch] = useState('');
  const [typed, setTyped] = useState('');
  const [status, setStatus] = useState<ItemStatus | ''>('');
  const [categoryId, setCategoryId] = useState('');
  const [locationId, setLocationId] = useState('');
  const [ownership, setOwnership] = useState<Ownership | ''>('');
  const [heldBy, setHeldBy] = useState('');
  const [overdueOnly, setOverdueOnly] = useState(false);
  const [page, setPage] = useState(0);

  // Typing filters the list without firing a request per keystroke.
  useEffect(() => {
    const id = window.setTimeout(() => { setSearch(typed); setPage(0); }, 300);
    return () => window.clearTimeout(id);
  }, [typed]);

  useEffect(() => {
    inventoryService.categories(domain).then(setCategories).catch(() => setCategories([]));
    inventoryService.locations().then(setLocations).catch(() => setLocations([]));
  }, [domain]);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setItems(await inventoryService.listItems({
        domain,
        ...(status ? { status } : {}),
        ...(categoryId ? { category_id: Number(categoryId) } : {}),
        ...(locationId ? { location_id: Number(locationId) } : {}),
        ...(ownership ? { ownership } : {}),
        ...(heldBy ? { held_by: Number(heldBy) } : {}),
        ...(overdueOnly && !office ? { overdue_only: true } : {}),
        ...(search.trim() ? { search: search.trim() } : {}),
        limit: PAGE,
        offset: page * PAGE,
      }));
      setError('');
    } catch (err) {
      setError((err as Error).message || 'Could not read the register.');
    } finally {
      setLoading(false);
    }
  }, [domain, office, status, categoryId, locationId, ownership, heldBy, overdueOnly, search, page]);

  useEffect(() => { void load(); }, [load]);

  // Cost fields come back null — not omitted, not zero — for anyone who is not
  // admin, HR or the custodian. An engineer gets no column rather than a
  // column of dashes.
  const showCost = useMemo(
    () => items.some((i) => money(i.replacement_cost) !== null || money(i.purchase_cost) !== null),
    [items],
  );

  return (
    <div className="cat-page">
      <header className="cat-head">
        <div>
          <h1>{office ? 'Office equipment' : 'Lab inventory'}</h1>
          <p>
            {office
              ? 'Laptops, phones, SIMs, access cards and the rest — who has each one. Issued and taken back by admin or HR.'
              : 'Every tagged thing in the lab, where it is and who has it.'}
          </p>
        </div>
        <div className="cat-head-actions">
          {office
            ? <Link className="inv-btn" to={ROUTES.OFFICE_EQUIPMENT_REPORTS}><FiPieChart /> Reports</Link>
            : <Link className="inv-btn" to={ROUTES.INVENTORY_SCAN}><FiMaximize /> Scan</Link>}
          {canRegister && (
            <Link className="inv-btn inv-btn-primary" to={office ? ROUTES.OFFICE_EQUIPMENT_NEW : ROUTES.INVENTORY_RECEIVE}>
              <FiPlus /> Register an item
            </Link>
          )}
        </div>
      </header>

      {error && <div className="cat-alert"><FiAlertTriangle /> {error}</div>}

      <div className="cat-filters">
        <label className="cat-search">
          <FiSearch />
          <input
            value={typed}
            onChange={(e) => setTyped(e.target.value)}
            placeholder="Tag, name, serial or MPN"
            aria-label="Search the register"
          />
        </label>
        <select value={status} onChange={(e) => { setStatus(e.target.value as ItemStatus | ''); setPage(0); }}>
          <option value="">Any status</option>
          {ITEM_STATUSES.map((s) => <option key={s} value={s}>{statusOf(s).label}</option>)}
        </select>
        <select value={categoryId} onChange={(e) => { setCategoryId(e.target.value); setPage(0); }}>
          <option value="">Any category</option>
          {categories.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
        </select>
        <select value={locationId} onChange={(e) => { setLocationId(e.target.value); setPage(0); }}>
          <option value="">Anywhere</option>
          {locations.map((l) => <option key={l.id} value={l.id}>{l.path || l.code}</option>)}
        </select>
        <select value={ownership} onChange={(e) => { setOwnership(e.target.value as Ownership | ''); setPage(0); }}>
          <option value="">Any owner</option>
          {OWNERSHIPS.map((o) => <option key={o} value={o}>{ownershipLabel(o)}</option>)}
        </select>
        {/* "What is Imran holding?" — the question the custodian asks before
            somebody's last working day. */}
        <select value={heldBy} onChange={(e) => { setHeldBy(e.target.value); setPage(0); }}>
          <option value="">Anybody</option>
          {people.map((person) => <option key={person.id} value={person.id}>{person.name}</option>)}
        </select>
        {!office && (
          <label className="cat-check">
            <input type="checkbox" checked={overdueOnly} onChange={(e) => { setOverdueOnly(e.target.checked); setPage(0); }} />
            Overdue only
          </label>
        )}
      </div>

      {loading ? (
        <p className="inv-muted">Loading…</p>
      ) : items.length === 0 ? (
        <div className="cat-empty">Nothing matches that.</div>
      ) : (
        <div className="cat-table-wrap">
          <table className="cat-table">
            <thead>
              <tr>
                <th>Tag</th>
                <th>Item</th>
                {office && <th>Held by</th>}
                <th>Where</th>
                <th>Status</th>
                <th>Condition</th>
                {showCost && <th className="cat-num">Replacement</th>}
              </tr>
            </thead>
            <tbody>
              {items.map((item) => (
                <tr key={item.id}>
                  <td><Link className="cat-tag" to={ROUTES.INVENTORY_ITEM(item.id)}>{item.tag}</Link></td>
                  <td>
                    <span className="cat-name">{item.name}</span>
                    <small>
                      {item.category_name}
                      {/* A bin is counted, not chased — the number is the point. */}
                      {item.tracking_type === 'quantity' && typeof item.qty === 'number'
                        ? ` · ${item.qty} in stock${typeof item.min_qty === 'number' ? ` (min ${item.min_qty})` : ''}`
                        : ''}
                      {item.serial_number ? ` · ${item.serial_number}` : ''}
                    </small>
                  </td>
                  {office && <td>{item.holder_name || '—'}</td>}
                  <td>{office && item.holder_name ? (item.location_path || item.location_code || '—') : whereIs(item)}</td>
                  <td>
                    <span className="cat-stack">
                      <StatusChip status={item.status} />
                      <OverdueChip item={item} />
                    </span>
                  </td>
                  <td><ConditionChip condition={item.condition} /></td>
                  {showCost && (
                    <td className="cat-num">{money(item.replacement_cost) ?? money(item.purchase_cost) ?? ''}</td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {(page > 0 || items.length === PAGE) && (
        <nav className="cat-pager">
          <button type="button" className="inv-btn" disabled={page === 0} onClick={() => setPage((p) => p - 1)}>
            <FiChevronLeft /> Back
          </button>
          <span>Showing {page * PAGE + 1}–{page * PAGE + items.length}</span>
          <button type="button" className="inv-btn" disabled={items.length < PAGE} onClick={() => setPage((p) => p + 1)}>
            More <FiChevronRight />
          </button>
        </nav>
      )}
    </div>
  );
};

export default Catalogue;
