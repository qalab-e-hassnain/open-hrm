import { useCallback, useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import {
  FiAlertTriangle, FiCheckCircle, FiChevronDown, FiChevronUp, FiExternalLink, FiMaximize,
} from 'react-icons/fi';
import { ROUTES } from '../../config/constants';
import {
  inventoryService, isOffice, type InventoryItem, type ScanResult,
} from '../../services/inventoryService';
import { StatusChip, ConditionChip, OverdueChip, DomainChip } from '../../components/inventory/StatusChip';
import ItemActions from '../../components/inventory/ItemActions';
import './MyHoldings.css';

const shortDate = (value?: string | null) => {
  if (!value) return null;
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? value : d.toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' });
};

/**
 * Everything the signed-in person is holding — the list they are asked to
 * hand back, and the one they are shown on their last working day.
 *
 * Opening a row fetches that item's own `actions[]` before offering a single
 * button. The alternative — assuming that whatever you hold you may return —
 * is exactly the client-side permission guess that goes wrong the first time
 * an item is under repair or out with a client.
 *
 * Both sections of the register are listed — lab kit and office equipment
 * alike are on the clearance list — each with a Lab / Office badge. Office
 * equipment is held for the length of the job, so it shows no due date, and
 * it goes back through admin or HR rather than from here.
 */
const MyHoldings = () => {
  const [items, setItems] = useState<InventoryItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [open, setOpen] = useState<number | null>(null);
  const [scan, setScan] = useState<ScanResult | null>(null);
  const [scanning, setScanning] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setItems(await inventoryService.myHoldings());
      setError('');
    } catch (err) {
      setError((err as Error).message || 'Could not read your holdings.');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  const toggle = async (item: InventoryItem) => {
    if (open === item.id) { setOpen(null); setScan(null); return; }
    setOpen(item.id);
    setScan(null);
    setScanning(true);
    try {
      setScan(await inventoryService.byTag(item.tag));
    } catch (err) {
      setError((err as Error).message || 'Could not read that item.');
    } finally {
      setScanning(false);
    }
  };

  return (
    <div className="hold-page">
      <header className="hold-head">
        <div>
          <h1>My equipment</h1>
          <p>What the register says is with you. Return anything you are done with.</p>
        </div>
        <Link className="inv-btn inv-btn-primary hold-scan-link" to={ROUTES.INVENTORY_SCAN}>
          <FiMaximize /> Scan
        </Link>
      </header>

      {error && <div className="hold-alert hold-alert-error"><FiAlertTriangle /> {error}</div>}
      {notice && <div className="hold-alert hold-alert-ok"><FiCheckCircle /> {notice}</div>}

      {loading ? (
        <p className="inv-muted">Loading…</p>
      ) : items.length === 0 ? (
        <div className="hold-empty">
          <p>Nothing is out with you.</p>
          <Link to={ROUTES.INVENTORY_ITEMS}>Browse the catalogue</Link>
        </div>
      ) : (
        <ul className="hold-list">
          {items.map((item) => (
            <li key={item.id} className={item.is_overdue && !isOffice(item) ? 'is-overdue' : undefined}>
              <button type="button" className="hold-row" onClick={() => void toggle(item)} aria-expanded={open === item.id}>
                <span className="hold-main">
                  <code>{item.tag}</code>
                  <strong>{item.name}</strong>
                  <small>
                    {item.category_name}
                    {!isOffice(item) && shortDate(item.hold_due_date) ? ` · due back ${shortDate(item.hold_due_date)}` : ''}
                    {/* Null whenever nobody holds it — which is not this list,
                        but the guard costs nothing and the format would. */}
                    {typeof item.days_held === 'number' ? ` · ${item.days_held} days` : ''}
                  </small>
                </span>
                <span className="hold-chips">
                  <DomainChip domain={item.domain} />
                  <OverdueChip item={item} />
                  <StatusChip status={item.status} />
                  <ConditionChip condition={item.condition} />
                  {open === item.id ? <FiChevronUp /> : <FiChevronDown />}
                </span>
              </button>

              {open === item.id && (
                <div className="hold-drawer">
                  {scanning && <p className="inv-muted">Checking what you can do…</p>}
                  {scan && isOffice(scan.item) && scan.actions.length === 0 && (
                    <p className="inv-no-actions">Office equipment is returned or handed over through admin or HR.</p>
                  )}
                  {scan && !(isOffice(scan.item) && scan.actions.length === 0) && (
                    <ItemActions
                      item={scan.item}
                      actions={scan.actions}
                      heldByMe={scan.held_by_me}
                      onDone={(_updated, message) => {
                        setNotice(message);
                        setOpen(null);
                        setScan(null);
                        void load();
                      }}
                      onError={setError}
                    />
                  )}
                  <Link className="hold-detail-link" to={ROUTES.INVENTORY_ITEM(item.id)}>
                    <FiExternalLink size={12} /> History
                  </Link>
                </div>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
};

export default MyHoldings;
