import { useCallback, useEffect, useState, type FormEvent } from 'react';
import { Link, useParams } from 'react-router-dom';
import {
  FiAlertTriangle, FiArrowLeft, FiCheckCircle, FiEdit3, FiFileText, FiPrinter, FiX,
} from 'react-icons/fi';
import { ROUTES } from '../../config/constants';
import { useAuthStore } from '../../store/authStore';
import { isLabCustodian, isPrivileged } from '../../utils/permissions';
import {
  inventoryService, whereIs, money, ownershipLabel, lifecycleLabel, isOffice,
  type InventoryItem, type Movement,
} from '../../services/inventoryService';
import { StatusChip, ConditionChip, OverdueChip, DomainChip } from '../../components/inventory/StatusChip';
import ItemActions from '../../components/inventory/ItemActions';
import MovementTimeline from '../../components/inventory/MovementTimeline';
import ItemLabel from '../../components/inventory/ItemLabel';
import './ItemDetail.css';

const Row = ({ label, value }: { label: string; value?: string | number | null }) => {
  // Cost fields arrive as null rather than missing for anyone without the
  // reach to see them, so this tests the value and drops the whole row.
  if (value === null || value === undefined || value === '') return null;
  return (
    <div className="idt-row">
      <dt>{label}</dt>
      <dd>{value}</dd>
    </div>
  );
};

/** Corrections go through PUT. There is no delete, and status and location are
 *  whatever the movement log says they are — neither is editable here. */
const CorrectionForm = ({ item, onSaved, onCancel, onError }: {
  item: InventoryItem;
  onSaved: (updated: InventoryItem) => void;
  onCancel: () => void;
  onError: (message: string) => void;
}) => {
  const user = useAuthStore((s) => s.user);
  const [form, setForm] = useState({
    name: item.name,
    serial_number: item.serial_number ?? '',
    manufacturer: item.manufacturer ?? '',
    mpn: item.mpn ?? '',
    min_qty: item.min_qty != null ? String(item.min_qty) : '',
    replacement_cost: item.replacement_cost != null ? String(item.replacement_cost) : '',
    notes: item.notes ?? '',
  });
  const [busy, setBusy] = useState(false);
  const set = (key: keyof typeof form, value: string) => setForm((f) => ({ ...f, [key]: value }));

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    try {
      const updated = await inventoryService.updateItem(item.id, {
        name: form.name.trim(),
        serial_number: form.serial_number.trim() || undefined,
        manufacturer: form.manufacturer.trim() || undefined,
        mpn: form.mpn.trim() || undefined,
        ...(form.min_qty.trim() ? { min_qty: Number(form.min_qty) } : {}),
        ...(isPrivileged(user) && form.replacement_cost.trim()
          ? { replacement_cost: Number(form.replacement_cost) } : {}),
        notes: form.notes.trim() || undefined,
      });
      onSaved(updated);
    } catch (err) {
      onError((err as Error).message || 'That correction did not save.');
      setBusy(false);
    }
  };

  return (
    <form className="idt-edit" onSubmit={submit}>
      <label className="inv-field"><span>Name</span>
        <input value={form.name} onChange={(e) => set('name', e.target.value)} required />
      </label>
      <label className="inv-field"><span>Serial number</span>
        <input value={form.serial_number} onChange={(e) => set('serial_number', e.target.value)} />
      </label>
      <label className="inv-field"><span>Manufacturer</span>
        <input value={form.manufacturer} onChange={(e) => set('manufacturer', e.target.value)} />
      </label>
      <label className="inv-field"><span>MPN</span>
        <input value={form.mpn} onChange={(e) => set('mpn', e.target.value)} />
      </label>
      {item.tracking_type === 'quantity' && (
        <label className="inv-field"><span>Reorder at</span>
          <input type="number" value={form.min_qty} onChange={(e) => set('min_qty', e.target.value)} />
        </label>
      )}
      {isPrivileged(user) && (
        <label className="inv-field"><span>Replacement cost (PKR)</span>
          <input type="number" value={form.replacement_cost} onChange={(e) => set('replacement_cost', e.target.value)} />
        </label>
      )}
      <label className="inv-field idt-edit-wide"><span>Notes</span>
        <textarea rows={2} value={form.notes} onChange={(e) => set('notes', e.target.value)} />
      </label>
      <p className="idt-edit-note">
        Status and location are not editable — they are whatever the log below says they are.
      </p>
      <div className="idt-edit-foot">
        <button type="button" className="inv-btn" onClick={onCancel} disabled={busy}>Cancel</button>
        <button type="submit" className="inv-btn inv-btn-primary" disabled={busy}>
          {busy ? 'Saving…' : 'Save correction'}
        </button>
      </div>
    </form>
  );
};

/**
 * One item, everything known about it, and the movement log — which is the
 * actual product. The log is append-only: no edit, no delete, for anybody.
 */
const ItemDetail = () => {
  const { id } = useParams<{ id: string }>();
  const itemId = Number(id);
  const user = useAuthStore((s) => s.user);

  const [item, setItem] = useState<InventoryItem | null>(null);
  const [actions, setActions] = useState<string[]>([]);
  const [heldByMe, setHeldByMe] = useState(false);
  const [movements, setMovements] = useState<Movement[]>([]);
  const [loading, setLoading] = useState(true);
  const [logLoading, setLogLoading] = useState(true);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [editing, setEditing] = useState(false);
  const [labelling, setLabelling] = useState(false);

  const load = useCallback(async () => {
    if (!Number.isFinite(itemId)) { setError('That is not an item id.'); setLoading(false); return; }
    setLoading(true);
    setLogLoading(true);
    try {
      const fetched = await inventoryService.getItem(itemId);
      setItem(fetched);
      setError('');
      // A second call, unlike the scan screen — the permitted actions hang off
      // the tag endpoint, and this page is not the one with a time budget.
      inventoryService.byTag(fetched.tag)
        .then((scan) => { setActions(scan.actions); setHeldByMe(scan.held_by_me); })
        .catch(() => setActions([]));
      inventoryService.movements(itemId)
        .then(setMovements)
        .catch(() => setMovements([]))
        .finally(() => setLogLoading(false));
    } catch (err) {
      setError((err as Error).message || 'Could not read that item.');
      setLogLoading(false);
    } finally {
      setLoading(false);
    }
  }, [itemId]);

  useEffect(() => { void load(); }, [load]);

  if (loading) return <div className="idt-page"><p className="inv-muted">Loading…</p></div>;

  if (!item) {
    return (
      <div className="idt-page">
        <div className="idt-alert idt-alert-error"><FiAlertTriangle /> {error || 'Not found.'}</div>
        <Link className="inv-btn" to={ROUTES.INVENTORY_ITEMS}><FiArrowLeft /> Back to the catalogue</Link>
      </div>
    );
  }

  // Office equipment is admin/HR's alone: the lab custodian cannot correct
  // or relabel it, and it belongs to the office register, not the lab one.
  const office = isOffice(item);
  const canManage = office ? isPrivileged(user) : isLabCustodian(user);
  const backTo = office && isPrivileged(user)
    ? { path: ROUTES.OFFICE_EQUIPMENT, label: 'Office equipment' }
    : { path: ROUTES.INVENTORY_ITEMS, label: 'Catalogue' };

  return (
    <div className="idt-page">
      <Link className="idt-back" to={backTo.path}><FiArrowLeft size={13} /> {backTo.label}</Link>

      <header className="idt-head">
        <div>
          <code className="idt-tag">{item.tag}</code>
          <h1>{item.name}</h1>
          <p className="idt-sub">{item.category_name} · {ownershipLabel(item.ownership)}</p>
        </div>
        <div className="idt-chips">
          <DomainChip domain={item.domain} />
          <StatusChip status={item.status} />
          <ConditionChip condition={item.condition} />
          <OverdueChip item={item} />
          {item.lifecycle_state && <span className="inv-chip inv-chip-info">{lifecycleLabel(item.lifecycle_state)}</span>}
        </div>
      </header>

      {error && <div className="idt-alert idt-alert-error"><FiAlertTriangle /> {error}</div>}
      {notice && <div className="idt-alert idt-alert-ok"><FiCheckCircle /> {notice}</div>}

      <p className="idt-where">{whereIs(item)}</p>

      <section className="idt-actions">
        <ItemActions
          item={item}
          actions={actions}
          heldByMe={heldByMe}
          onDone={(_updated, message) => { setNotice(message); void load(); }}
          onError={setError}
        />
      </section>

      <div className="idt-columns">
        <section className="idt-panel">
          <header className="idt-panel-head">
            <h2>Details</h2>
            {canManage && !editing && (
              <span className="idt-panel-tools">
                <button type="button" className="inv-btn" onClick={() => setLabelling(true)}><FiPrinter size={13} /> Label</button>
                <button type="button" className="inv-btn" onClick={() => setEditing(true)}><FiEdit3 size={13} /> Correct</button>
              </span>
            )}
            {editing && (
              <button type="button" className="inv-icon-btn" onClick={() => setEditing(false)} aria-label="Stop editing"><FiX /></button>
            )}
          </header>

          {editing ? (
            <CorrectionForm
              item={item}
              onCancel={() => setEditing(false)}
              onError={setError}
              onSaved={(updated) => { setItem(updated); setEditing(false); setNotice('Corrected.'); }}
            />
          ) : (
            <dl className="idt-facts">
              <Row label="Description" value={item.description} />
              <Row label="Serial number" value={item.serial_number} />
              <Row label="Manufacturer" value={item.manufacturer} />
              <Row label="MPN" value={item.mpn} />
              <Row label="Tracking" value={item.tracking_type} />
              {item.tracking_type === 'quantity' && <Row label="In stock" value={item.qty} />}
              {item.tracking_type === 'quantity' && <Row label="Reorder at" value={item.min_qty} />}
              <Row label="Holder" value={item.holder_name} />
              <Row label="Location" value={item.location_path || item.location_code} />
              {/* Office equipment is held for the length of the job. */}
              {!office && <Row label="Due back" value={item.hold_due_date} />}
              <Row label="Days held" value={item.days_held} />
              <Row label="Project" value={item.project_name || item.project_code} />
              <Row label="Client" value={item.client_name} />
              {/* Null for everyone who is not admin, HR or the custodian —
                  the rows simply do not appear. */}
              <Row label="Purchase cost" value={money(item.purchase_cost)} />
              <Row label="Replacement cost" value={money(item.replacement_cost)} />
              <Row label="Unit cost" value={money(item.unit_cost)} />
              <Row label="Supplier" value={item.supplier} />
              <Row label="Invoice" value={item.invoice_ref} />
              <Row label="Purchased" value={item.purchase_date} />
              <Row label="Notes" value={item.notes} />
            </dl>
          )}

          {item.datasheet_url && (
            <a className="idt-datasheet" href={item.datasheet_url} target="_blank" rel="noreferrer">
              <FiFileText size={13} /> Datasheet
            </a>
          )}
        </section>

        <section className="idt-panel">
          <header className="idt-panel-head"><h2>History</h2></header>
          <MovementTimeline movements={movements} loading={logLoading} />
        </section>
      </div>

      {labelling && <ItemLabel item={item} onClose={() => setLabelling(false)} />}
    </div>
  );
};

export default ItemDetail;
