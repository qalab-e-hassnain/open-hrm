import { useEffect, useMemo, useState, type FormEvent } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { FiAlertTriangle, FiArrowLeft, FiCheckCircle, FiPrinter } from 'react-icons/fi';
import { ROUTES } from '../../config/constants';
import {
  inventoryService, conditionOf, ownershipLabel, ITEM_CONDITIONS, OWNERSHIPS,
  type Domain, type InventoryItem, type ItemCategory, type StoreLocation,
} from '../../services/inventoryService';
import ItemLabel from '../../components/inventory/ItemLabel';
import './Receiving.css';

const BLANK = {
  category_id: '', name: '', serial_number: '', current_location_id: '', tag: '',
  manufacturer: '', mpn: '', ownership: 'company', condition: 'new',
  qty: '', min_qty: '', purchase_cost: '', replacement_cost: '', unit_cost: '',
  supplier: '', invoice_ref: '', purchase_date: '', project_code: '', notes: '',
};

/**
 * The receiving table: register the thing and print its label before it
 * leaves the table, because a label printed later is a label never printed.
 *
 * Registering and receiving are one event — the create call writes an intake
 * movement to the location given, which is why a location is required and why
 * there is no "register it now, find it a home later".
 *
 * The same form registers office equipment with `domain="office"`: only the
 * office categories are offered (the backend refuses an office category from
 * anyone short of admin/HR), and the page links back to the office register.
 * Issuing it to somebody is the next step, from the item's own page.
 */
const Receiving = ({ domain = 'lab' }: { domain?: Domain }) => {
  const office = domain === 'office';
  const [params] = useSearchParams();
  const [categories, setCategories] = useState<ItemCategory[]>([]);
  const [locations, setLocations] = useState<StoreLocation[]>([]);
  // A scan that found nothing sends the tag it read over here.
  const [form, setForm] = useState({ ...BLANK, tag: params.get('tag') ?? '' });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [created, setCreated] = useState<InventoryItem | null>(null);
  const [labelling, setLabelling] = useState(false);

  useEffect(() => {
    inventoryService.categories(domain).then(setCategories).catch(() => setCategories([]));
    inventoryService.locations().then(setLocations).catch(() => setLocations([]));
  }, [domain]);

  const category = useMemo(
    () => categories.find((c) => String(c.id) === form.category_id),
    [categories, form.category_id],
  );
  const isQuantity = category?.tracking_type === 'quantity';
  const isProject = category?.tracking_type === 'project';
  // Read off the category rather than waiting for the 400 to tell us.
  const serialRequired = Boolean(category?.serial_required);

  const set = (key: keyof typeof BLANK, value: string) => setForm((f) => ({ ...f, [key]: value }));

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError('');
    try {
      const item = await inventoryService.createItem({
        category_id: Number(form.category_id),
        name: form.name.trim(),
        current_location_id: Number(form.current_location_id),
        ...(form.tag.trim() ? { tag: form.tag.trim() } : {}),
        ...(form.serial_number.trim() ? { serial_number: form.serial_number.trim() } : {}),
        ...(form.manufacturer.trim() ? { manufacturer: form.manufacturer.trim() } : {}),
        ...(form.mpn.trim() ? { mpn: form.mpn.trim() } : {}),
        ...(form.ownership ? { ownership: form.ownership as InventoryItem['ownership'] } : {}),
        ...(form.condition ? { condition: form.condition as InventoryItem['condition'] } : {}),
        ...(isQuantity && form.qty.trim() ? { qty: Number(form.qty) } : {}),
        ...(isQuantity && form.min_qty.trim() ? { min_qty: Number(form.min_qty) } : {}),
        ...(form.purchase_cost.trim() ? { purchase_cost: Number(form.purchase_cost) } : {}),
        ...(form.replacement_cost.trim() ? { replacement_cost: Number(form.replacement_cost) } : {}),
        ...(form.unit_cost.trim() ? { unit_cost: Number(form.unit_cost) } : {}),
        ...(form.supplier.trim() ? { supplier: form.supplier.trim() } : {}),
        ...(form.invoice_ref.trim() ? { invoice_ref: form.invoice_ref.trim() } : {}),
        ...(form.purchase_date ? { purchase_date: form.purchase_date } : {}),
        ...(isProject && form.project_code.trim() ? { project_code: form.project_code.trim() } : {}),
        ...(form.notes.trim() ? { notes: form.notes.trim() } : {}),
      });
      setCreated(item);
      // Straight to the label, because that is the next physical step and the
      // only moment it reliably happens.
      setLabelling(true);
      setForm({ ...BLANK, category_id: form.category_id, current_location_id: form.current_location_id });
    } catch (err) {
      setError((err as Error).message || 'That did not register.');
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="rcv-page">
      <Link className="rcv-back" to={office ? ROUTES.OFFICE_EQUIPMENT : ROUTES.INVENTORY_ITEMS}>
        <FiArrowLeft size={13} /> {office ? 'Office equipment' : 'Catalogue'}
      </Link>

      <header className="rcv-head">
        <h1>{office ? 'Register office equipment' : 'Receiving'}</h1>
        <p>
          {office
            ? 'Register it into the store and print its label. Issue it to somebody from its page afterwards.'
            : 'Register it, then print the label before it leaves the table.'}
        </p>
      </header>

      {created && (
        <div className="rcv-alert rcv-alert-ok">
          <FiCheckCircle />
          <div>
            <p>
              Registered as <strong>{created.tag}</strong> — <Link to={ROUTES.INVENTORY_ITEM(created.id)}>{created.name}</Link>
            </p>
            <button type="button" className="inv-btn" onClick={() => setLabelling(true)}>
              <FiPrinter size={13} /> Print the label again
            </button>
          </div>
        </div>
      )}

      {error && <div className="rcv-alert rcv-alert-error"><FiAlertTriangle /> {error}</div>}

      <form className="rcv-form" onSubmit={submit}>
        <label className="inv-field"><span>Category</span>
          <select value={form.category_id} onChange={(e) => set('category_id', e.target.value)} required>
            <option value="">Choose a category…</option>
            {categories.filter((c) => c.is_active !== false).map((c) => (
              <option key={c.id} value={c.id}>{c.name}</option>
            ))}
          </select>
          {category && <small>Tracked {category.tracking_type === 'quantity' ? 'by count' : 'individually'}.</small>}
        </label>

        <label className="inv-field"><span>Name</span>
          <input value={form.name} onChange={(e) => set('name', e.target.value)}
            placeholder={office ? 'Dell Latitude 5440 laptop' : 'Rigol DS1054Z oscilloscope'} required />
        </label>

        <label className="inv-field"><span>Where is it going?</span>
          <select value={form.current_location_id} onChange={(e) => set('current_location_id', e.target.value)} required>
            <option value="">Choose a location…</option>
            {locations.map((l) => <option key={l.id} value={l.id}>{l.path || l.code}</option>)}
          </select>
          {/* Not a nicety: the row is refused without one, because everything
              in the register is on a shelf or with a person from the moment it
              is registered. Something still in the post goes to the in-transit
              node; a licence to its own. */}
          <small>Required. Registering writes an intake movement to this location.</small>
        </label>

        <label className="inv-field"><span>Serial number{serialRequired ? '' : ' (optional)'}</span>
          <input value={form.serial_number} onChange={(e) => set('serial_number', e.target.value)}
            required={serialRequired} />
        </label>

        <label className="inv-field"><span>Manufacturer</span>
          <input value={form.manufacturer} onChange={(e) => set('manufacturer', e.target.value)} />
        </label>

        <label className="inv-field"><span>MPN</span>
          <input value={form.mpn} onChange={(e) => set('mpn', e.target.value)} />
        </label>

        <label className="inv-field"><span>Owner</span>
          <select value={form.ownership} onChange={(e) => set('ownership', e.target.value)}>
            {OWNERSHIPS.map((o) => <option key={o} value={o}>{ownershipLabel(o)}</option>)}
          </select>
        </label>

        <label className="inv-field"><span>Condition</span>
          <select value={form.condition} onChange={(e) => set('condition', e.target.value)}>
            {ITEM_CONDITIONS.map((c) => <option key={c} value={c}>{conditionOf(c).label}</option>)}
          </select>
        </label>

        {isQuantity && (
          <>
            <label className="inv-field"><span>How many?</span>
              <input type="number" value={form.qty} onChange={(e) => set('qty', e.target.value)} />
            </label>
            <label className="inv-field"><span>Reorder at</span>
              <input type="number" value={form.min_qty} onChange={(e) => set('min_qty', e.target.value)} />
            </label>
            <label className="inv-field"><span>Unit cost (PKR)</span>
              <input type="number" value={form.unit_cost} onChange={(e) => set('unit_cost', e.target.value)} />
            </label>
          </>
        )}

        {isProject && (
          <label className="inv-field"><span>Project code</span>
            <input value={form.project_code} onChange={(e) => set('project_code', e.target.value)} placeholder="CBOXX" />
            <small>Typed, not looked up — QGTBuild is not wired in yet.</small>
          </label>
        )}

        <label className="inv-field"><span>Purchase cost (PKR)</span>
          <input type="number" value={form.purchase_cost} onChange={(e) => set('purchase_cost', e.target.value)} />
        </label>

        <label className="inv-field"><span>Replacement cost (PKR)</span>
          <input type="number" value={form.replacement_cost} onChange={(e) => set('replacement_cost', e.target.value)} />
        </label>

        <label className="inv-field"><span>Supplier</span>
          <input value={form.supplier} onChange={(e) => set('supplier', e.target.value)} />
        </label>

        <label className="inv-field"><span>Invoice reference</span>
          <input value={form.invoice_ref} onChange={(e) => set('invoice_ref', e.target.value)} />
        </label>

        <label className="inv-field"><span>Purchase date</span>
          <input type="date" value={form.purchase_date} onChange={(e) => set('purchase_date', e.target.value)} />
        </label>

        <label className="inv-field"><span>Tag</span>
          <input value={form.tag} onChange={(e) => set('tag', e.target.value)} placeholder="Leave blank to have one issued" />
          {/* Numbers are never reused, scrapped ones included, and the next one
              is the backend's to decide — so it is shown after the create, not
              predicted before it. */}
          <small>Only fill this in if the thing already wears a sticker with a number on it.</small>
        </label>

        <label className="inv-field rcv-wide"><span>Notes</span>
          <textarea rows={2} value={form.notes} onChange={(e) => set('notes', e.target.value)} />
        </label>

        <div className="rcv-foot">
          <button type="submit" className="inv-btn inv-btn-primary" disabled={busy}>
            {busy ? 'Registering…' : 'Register and print'}
          </button>
        </div>
      </form>

      {labelling && created && <ItemLabel item={created} onClose={() => setLabelling(false)} />}
    </div>
  );
};

export default Receiving;
