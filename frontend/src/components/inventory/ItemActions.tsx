import { useEffect, useMemo, useState, type FormEvent } from 'react';
import {
  FiAlertTriangle, FiArrowDownLeft, FiArrowUpRight, FiClock, FiCornerUpLeft,
  FiMinusCircle, FiPackage, FiRepeat, FiSend, FiSlash, FiSliders, FiTool,
  FiTruck, FiSearch, FiUserPlus, FiX,
} from 'react-icons/fi';
import type { IconType } from 'react-icons';
import { useAuthStore } from '../../store/authStore';
import { isPrivileged, isLabCustodian } from '../../utils/permissions';
import {
  inventoryService, actionVerb, conditionOf, shelvesFor, isOffice, ITEM_CONDITIONS, money,
  type InventoryItem, type StoreLocation,
} from '../../services/inventoryService';
import { usePeople } from './usePeople';
import './inventory.css';

/* --------------------------- The action table --------------------------- */

type FieldKind = 'location' | 'employee' | 'date' | 'number' | 'money' | 'condition' | 'text' | 'textarea';

interface ActionField {
  kind: FieldKind;
  /** The key this field sends in the POST body. */
  name: string;
  label: string;
  required?: boolean;
  help?: string;
  placeholder?: string;
  initial?: string;
  /** Recovery amounts are admin/HR only — the field is simply not offered. */
  privileged?: boolean;
  /** Quantity-tracked items only. */
  quantityOnly?: boolean;
}

interface ActionSpec {
  label: string;
  icon: IconType;
  tone?: 'primary' | 'danger';
  /** Defaults to the action key. Set where one endpoint backs two buttons. */
  verb?: string;
  heading?: string;
  blurb?: string;
  submit?: string;
  /** No fields means no dialog: one tap and it is done. */
  fields?: ActionField[];
}

const LOCATION = (name = 'location_id', label = 'Where is it going?'): ActionField =>
  ({ kind: 'location', name, label, required: true });
const CONDITION: ActionField = { kind: 'condition', name: 'condition', label: 'Condition' };
const REASON = (required = false): ActionField =>
  ({ kind: 'textarea', name: 'reason', label: required ? 'Reason' : 'Reason (optional)', required });
const RECOVERY: ActionField = {
  kind: 'money', name: 'recovery_amount', label: 'Recover from the holder (optional)',
  privileged: true, help: 'Flows into their final settlement as asset recovery.',
};

/**
 * What each action asks for before it fires. Keyed on the kebab verb, which
 * is also the URL — `actionVerb()` normalises whichever spelling the backend
 * sends into this shape.
 *
 * Nothing here decides *whether* an action is allowed. That is the item's own
 * `actions[]`, which already accounts for the caller, the item's state and its
 * tracking type; this table only says what to ask for once it is offered.
 */
const ACTIONS: Record<string, ActionSpec> = {
  checkout: {
    label: 'Take', icon: FiArrowUpRight, tone: 'primary',
    // The common case is "this one, to me, now" — an empty body, no dialog,
    // no confirmation. A mistake is undone by the opposite action, which is
    // one more tap; a dialog would cost everybody two seconds forever.
  },
  'checkout-to': {
    label: 'Issue to…', icon: FiUserPlus, verb: 'checkout',
    heading: 'Check this out to somebody else',
    fields: [
      { kind: 'employee', name: 'employee_id', label: 'Who is taking it?', required: true },
      { kind: 'date', name: 'expected_return_date', label: 'Back by (optional)' },
      CONDITION,
      REASON(),
    ],
  },
  checkin: {
    label: 'Return', icon: FiArrowDownLeft, tone: 'primary',
    heading: 'Put it back',
    // The one field the old return did not have. It is asked because the
    // answer matters, not as a confirmation step — hence the remembered
    // default and a single tap to accept it.
    blurb: 'The register needs to know which shelf it is going back on.',
    submit: 'Return it',
    fields: [LOCATION('location_id', 'Which shelf?'), CONDITION, RECOVERY, REASON()],
  },
  transfer: {
    label: 'Hand over', icon: FiRepeat,
    heading: 'Hand it to somebody else',
    blurb: 'A perfectly good answer to "where is it" — the item stays out, the register follows it.',
    fields: [
      { kind: 'employee', name: 'to_employee_id', label: 'Who is picking it up?', required: true },
      { kind: 'date', name: 'expected_return_date', label: 'Back by (optional)' },
      REASON(),
    ],
  },
  renew: {
    label: 'Extend', icon: FiClock,
    heading: 'Keep it longer',
    fields: [{ kind: 'number', name: 'days', label: 'For how many more days?', initial: '7' }],
  },
  receive: {
    label: 'Receive', icon: FiPackage,
    heading: 'Receive into stock',
    fields: [
      LOCATION('location_id', 'Onto which shelf?'),
      { kind: 'number', name: 'qty', label: 'How many?', quantityOnly: true },
      { kind: 'text', name: 'reference', label: 'Reference (optional)', placeholder: 'Invoice, DC or PO number' },
      CONDITION,
    ],
  },
  consume: {
    label: 'Consume', icon: FiMinusCircle,
    heading: 'Take some out of the bin',
    fields: [
      { kind: 'number', name: 'qty', label: 'How many?', required: true, initial: '1' },
      { kind: 'number', name: 'project_id', label: 'Project id (optional)', help: 'Typed, not looked up — QGTBuild is not wired in yet.' },
      REASON(),
    ],
  },
  adjust: {
    label: 'Adjust count', icon: FiSliders,
    heading: 'Correct the count',
    blurb: 'Writes a correction movement. The original count stays on the timeline — that is the point.',
    fields: [
      { kind: 'number', name: 'qty_delta', label: 'Change by', required: true, help: 'Negative to take some off the count.' },
      REASON(true),
      { kind: 'text', name: 'reference', label: 'Reference (optional)' },
    ],
  },
  'ship-to-client': {
    label: 'Ship to client', icon: FiTruck,
    heading: 'Ship it out',
    fields: [
      { kind: 'text', name: 'reference', label: 'Reference', required: true, placeholder: 'Courier tracking or DC number' },
      { kind: 'date', name: 'expected_return_date', label: 'Back by (optional)' },
      REASON(),
    ],
  },
  'confirm-delivery': {
    label: 'Confirm delivery', icon: FiSend,
    heading: 'It arrived',
    fields: [LOCATION('location_id', 'Where did it land?'), REASON()],
  },
  'return-from-client': {
    label: 'Back from client', icon: FiCornerUpLeft,
    heading: 'Returned from the client',
    fields: [
      LOCATION('location_id', 'Onto which shelf?'),
      CONDITION,
      { kind: 'text', name: 'reference', label: 'Reference (optional)' },
    ],
  },
  'repair-out': {
    label: 'Send for repair', icon: FiTool,
    heading: 'Out for repair',
    fields: [
      { kind: 'text', name: 'reference', label: 'Reference', required: true, placeholder: 'RMA or job number' },
      REASON(),
    ],
  },
  'repair-in': {
    label: 'Back from repair', icon: FiTool,
    heading: 'Back from repair',
    fields: [
      LOCATION('location_id', 'Onto which shelf?'),
      CONDITION,
      { kind: 'text', name: 'reference', label: 'Reference (optional)' },
    ],
  },
  scrap: {
    label: 'Scrap', icon: FiSlash, tone: 'danger',
    heading: 'Take it off the books',
    blurb: 'The tag and the whole history stay. This is how something leaves the register — there is no delete.',
    fields: [REASON(true)],
  },
  lost: {
    label: 'Mark lost', icon: FiAlertTriangle, tone: 'danger',
    heading: 'Mark it lost',
    fields: [REASON(true), RECOVERY],
  },
  found: {
    label: 'Found it', icon: FiSearch,
    heading: 'It turned up',
    fields: [LOCATION('location_id', 'Where was it?'), REASON()],
  },
  'write-off': {
    label: 'Write off', icon: FiSlash, tone: 'danger',
    heading: 'Write it off',
    blurb: 'Closes the clearance line without the item coming back. Anything entered below is charged to the leaver.',
    fields: [REASON(true), RECOVERY],
  },
};

/** Roughly the order people reach for them; anything unlisted sorts last. */
const ORDER = [
  'checkout', 'checkin', 'checkout-to', 'transfer', 'renew', 'consume', 'adjust',
  'receive', 'ship-to-client', 'confirm-delivery', 'return-from-client',
  'repair-out', 'repair-in', 'found', 'lost', 'scrap', 'write-off',
];

const LAST_LOCATION_KEY = 'qgt-inv-last-location';

const readLastLocation = (): string => {
  try { return localStorage.getItem(LAST_LOCATION_KEY) || ''; } catch { return ''; }
};

/* ------------------------------ The dialog ------------------------------ */

const LocationSelect = ({ item, value, onChange, locations, required }: {
  item: InventoryItem; value: string; onChange: (v: string) => void;
  locations: StoreLocation[]; required?: boolean;
}) => {
  // Client-owned hardware belongs on a client shelf and company kit does not.
  // The API refuses the mismatch with a clear 400; filtering here saves the
  // user the round trip. Grouped by the backend's own location_type rather
  // than a list this file has to keep in sync.
  const groups = useMemo(() => {
    const out = new Map<string, StoreLocation[]>();
    for (const loc of shelvesFor(item, locations)) {
      const key = loc.location_type || 'Locations';
      out.set(key, [...(out.get(key) ?? []), loc]);
    }
    return [...out.entries()];
  }, [item, locations]);

  return (
    <select value={value} onChange={(e) => onChange(e.target.value)} required={required}>
      <option value="">Choose a location…</option>
      {groups.map(([type, list]) => (
        <optgroup key={type} label={type.replace(/_/g, ' ')}>
          {list.map((loc) => (
            <option key={loc.id} value={loc.id}>
              {loc.path || loc.code}{typeof loc.item_count === 'number' ? ` (${loc.item_count})` : ''}
            </option>
          ))}
        </optgroup>
      ))}
    </select>
  );
};

const ActionDialog = ({ item, actionKey, onCancel, onDone }: {
  item: InventoryItem;
  actionKey: string;
  onCancel: () => void;
  onDone: (updated: InventoryItem, message: string) => void;
}) => {
  const spec = ACTIONS[actionKey];
  const user = useAuthStore((s) => s.user);
  const people = usePeople();
  const [locations, setLocations] = useState<StoreLocation[]>([]);
  const [values, setValues] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  const fields = useMemo(() => (spec.fields ?? []).filter((f) => {
    if (f.privileged && !isPrivileged(user)) return false;
    if (f.quantityOnly && item.tracking_type !== 'quantity') return false;
    // Office equipment is held for the length of the job — no "back by".
    if (f.name === 'expected_return_date' && isOffice(item)) return false;
    return true;
  }), [spec.fields, user, item]);

  const needsLocation = fields.some((f) => f.kind === 'location');

  useEffect(() => {
    setValues(Object.fromEntries(fields.map((f) => [
      f.name,
      // The shelf somebody put something on last is overwhelmingly the shelf
      // they are putting this one on, so the picker opens already answered.
      f.initial ?? (f.kind === 'location' ? readLastLocation() : ''),
    ])));
  }, [fields]);

  useEffect(() => {
    if (!needsLocation) return;
    inventoryService.locations().then(setLocations).catch(() => setLocations([]));
  }, [needsLocation]);

  const set = (name: string, value: string) => setValues((v) => ({ ...v, [name]: value }));

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    const missing = fields.find((f) => f.required && !values[f.name]?.trim());
    if (missing) { setError(`${missing.label} is needed.`); return; }

    const body: Record<string, unknown> = {};
    for (const field of fields) {
      const raw = values[field.name]?.trim();
      if (!raw) continue;
      const numeric = field.kind === 'number' || field.kind === 'money'
        || field.kind === 'location' || field.kind === 'employee';
      body[field.name] = numeric ? Number(raw) : raw;
    }

    setBusy(true);
    setError('');
    try {
      const updated = await inventoryService.act(item.id, spec.verb ?? actionKey, body);
      if (body.location_id) {
        try { localStorage.setItem(LAST_LOCATION_KEY, String(body.location_id)); } catch { /* private mode */ }
      }
      onDone(updated, `${item.tag} — ${spec.label.toLowerCase()} recorded.`);
    } catch (err) {
      // api.ts has already turned the backend's `detail` into the message,
      // including the 422 shape where detail is a list rather than a string.
      setError((err as Error).message || 'That did not go through.');
      setBusy(false);
    }
  };

  const recovery = Number(values.recovery_amount || 0);

  return (
    <div className="inv-overlay" role="dialog" aria-modal="true" aria-label={spec.heading || spec.label}>
      <form className="inv-dialog" onSubmit={submit}>
        <header>
          <h3>{spec.heading || spec.label}</h3>
          <button type="button" className="inv-icon-btn" onClick={onCancel} aria-label="Close"><FiX /></button>
        </header>

        <p className="inv-dialog-item">
          <code>{item.tag}</code> {item.name}
        </p>
        {spec.blurb && <p className="inv-dialog-blurb">{spec.blurb}</p>}

        {fields.map((field) => (
          <label key={field.name} className="inv-field">
            <span>{field.label}</span>

            {field.kind === 'location' && (
              <LocationSelect
                item={item}
                value={values[field.name] ?? ''}
                onChange={(v) => set(field.name, v)}
                locations={locations}
                required={field.required}
              />
            )}

            {field.kind === 'employee' && (
              <select value={values[field.name] ?? ''} onChange={(e) => set(field.name, e.target.value)}>
                <option value="">Choose somebody…</option>
                {people.map((person) => (
                  <option key={person.id} value={person.id}>
                    {person.name}{person.detail ? ` — ${person.detail}` : ''}
                  </option>
                ))}
              </select>
            )}

            {field.kind === 'condition' && (
              <select value={values[field.name] ?? ''} onChange={(e) => set(field.name, e.target.value)}>
                <option value="">Unchanged</option>
                {ITEM_CONDITIONS.map((c) => <option key={c} value={c}>{conditionOf(c).label}</option>)}
              </select>
            )}

            {field.kind === 'date' && (
              <input type="date" value={values[field.name] ?? ''} onChange={(e) => set(field.name, e.target.value)} />
            )}

            {(field.kind === 'number' || field.kind === 'money') && (
              <input
                type="number"
                inputMode="numeric"
                step={field.kind === 'money' ? '1' : 'any'}
                value={values[field.name] ?? ''}
                placeholder={field.placeholder}
                onChange={(e) => set(field.name, e.target.value)}
              />
            )}

            {field.kind === 'text' && (
              <input
                type="text"
                value={values[field.name] ?? ''}
                placeholder={field.placeholder}
                onChange={(e) => set(field.name, e.target.value)}
              />
            )}

            {field.kind === 'textarea' && (
              <textarea
                rows={2}
                value={values[field.name] ?? ''}
                onChange={(e) => set(field.name, e.target.value)}
              />
            )}

            {field.help && <small>{field.help}</small>}
          </label>
        ))}

        {/* The person approving this is charging somebody money. Say the number
            back to them before they commit it. */}
        {recovery > 0 && (
          <p className="inv-dialog-warn">
            <FiAlertTriangle /> {money(recovery)} will be deducted in {item.holder_name || 'the holder'}&apos;s final settlement.
          </p>
        )}

        {error && <p className="inv-dialog-error"><FiAlertTriangle /> {error}</p>}

        <footer>
          <button type="button" className="inv-btn" onClick={onCancel} disabled={busy}>Cancel</button>
          <button type="submit" className={`inv-btn inv-btn-${spec.tone === 'danger' ? 'danger' : 'primary'}`} disabled={busy}>
            {busy ? 'Working…' : (spec.submit || spec.label)}
          </button>
        </footer>
      </form>
    </div>
  );
};

/* ------------------------------ The buttons ----------------------------- */

interface Props {
  item: InventoryItem;
  /** Straight from the API. Render exactly these and no others. */
  actions: string[];
  heldByMe?: boolean;
  /** `scan` gives the likeliest action a full-width primary button. */
  layout?: 'scan' | 'detail';
  onDone: (updated: InventoryItem, message: string) => void;
  onError?: (message: string) => void;
}

const ItemActions = ({ item, actions, heldByMe, layout = 'detail', onDone, onError }: Props) => {
  const user = useAuthStore((s) => s.user);
  const [open, setOpen] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const office = isOffice(item);

  const keys = useMemo(() => {
    let offered = actions.map(actionVerb).filter((verb) => {
      if (ACTIONS[verb]) return true;
      // A verb this build has never heard of. Unlike a status or a movement
      // type there is no safe way to render it — we would not know what to put
      // in the body — and a button that always 422s is worse than no button.
      console.warn(`[inventory] no dialog defined for action "${verb}" — not offering it`);
      return false;
    });
    if (office) {
      // Office equipment is never self-served: "checkout" means admin or HR
      // issuing it to an employee, so it is only ever the "Issue to…" dialog,
      // never a one-tap "Take". And there is no hold to extend.
      offered = offered.filter((verb) => verb !== 'renew');
      if (offered.includes('checkout')) {
        offered = offered.filter((verb) => verb !== 'checkout');
        offered.push('checkout-to');
      }
    } else if (offered.includes('checkout') && isLabCustodian(user)) {
      // A custodian checking something out to somebody else is a second
      // button on the same permission, not a second permission.
      offered.push('checkout-to');
    }
    // The one action the API deliberately leaves out of `actions[]`, because
    // it belongs to the offboarding flow rather than to scanning a shelf —
    // and without it a clearance line has only two of its three endings.
    // Offered on exactly the terms the endpoint enforces: admin or HR, and
    // only while somebody is still holding the thing.
    if (layout === 'detail' && isPrivileged(user) && item.current_holder_id) offered.push('write-off');
    return offered.sort((a, b) => {
      const ai = ORDER.indexOf(a), bi = ORDER.indexOf(b);
      return (ai === -1 ? ORDER.length : ai) - (bi === -1 ? ORDER.length : bi);
    });
  }, [actions, user, layout, item.current_holder_id, office]);

  // The one thing this person most likely walked up to the item to do.
  const primary = heldByMe && keys.includes('checkin') ? 'checkin'
    : keys.includes('checkout') ? 'checkout'
    : null;

  const fire = async (key: string) => {
    const spec = ACTIONS[key];
    const fields = (spec.fields ?? []).filter((f) => !(f.privileged && !isPrivileged(user)));
    if (fields.length > 0) { setOpen(key); return; }
    setBusy(key);
    try {
      const updated = await inventoryService.act(item.id, spec.verb ?? key, {});
      onDone(updated, `${item.tag} — ${spec.label.toLowerCase()} recorded.`);
    } catch (err) {
      onError?.((err as Error).message || 'That did not go through.');
    } finally {
      setBusy(null);
    }
  };

  if (keys.length === 0) {
    return <p className="inv-no-actions">Nothing to do with this one right now.</p>;
  }

  const rest = keys.filter((k) => k !== primary);

  return (
    <>
      <div className={`inv-actions inv-actions-${layout}`}>
        {primary && layout === 'scan' && (() => {
          const spec = ACTIONS[primary];
          const Icon = spec.icon;
          return (
            <button type="button" className="inv-primary-tap" onClick={() => fire(primary)} disabled={busy === primary}>
              <Icon size={22} /> {busy === primary ? 'Working…' : spec.label}
            </button>
          );
        })()}

        <div className="inv-action-row">
          {(layout === 'scan' ? rest : keys).map((key) => {
            const spec = ACTIONS[key];
            const Icon = spec.icon;
            return (
              <button
                key={key}
                type="button"
                className={`inv-btn${spec.tone === 'danger' ? ' inv-btn-danger-ghost' : ''}${key === primary ? ' inv-btn-primary' : ''}`}
                onClick={() => fire(key)}
                disabled={busy === key}
              >
                <Icon size={14} /> {busy === key ? 'Working…' : spec.label}
              </button>
            );
          })}
        </div>
      </div>

      {open && (
        <ActionDialog
          item={item}
          actionKey={open}
          onCancel={() => setOpen(null)}
          onDone={(updated, message) => { setOpen(null); onDone(updated, message); }}
        />
      )}
    </>
  );
};

export default ItemActions;
