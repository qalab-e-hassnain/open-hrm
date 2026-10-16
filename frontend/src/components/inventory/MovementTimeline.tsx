import { movementLabel, money, conditionOf, type Movement } from '../../services/inventoryService';
import './inventory.css';

const stamp = (value?: string | null) => {
  if (!value) return '';
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return value;
  return d.toLocaleString('en-GB', {
    day: 'numeric', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit',
  });
};

/** Where it went, in the words of the movement itself. */
const journey = (m: Movement): string => {
  const from = m.from_holder_name || m.from_location_code;
  const to = m.to_holder_name || m.to_location_code;
  if (from && to) return `${from} → ${to}`;
  if (to) return `→ ${to}`;
  if (from) return `${from} →`;
  return '';
};

/**
 * An item's whole history, newest first. Append-only: there is no edit and no
 * delete on a movement for anybody, admins included. A correction arrives as a
 * further `audit_adjust` row, so the mistake and the fix both stay on the
 * timeline — which is the point of it.
 */
const MovementTimeline = ({ movements, loading }: { movements: Movement[]; loading?: boolean }) => {
  if (loading) return <p className="inv-muted">Reading the log…</p>;
  if (movements.length === 0) return <p className="inv-muted">Nothing on the log yet.</p>;

  return (
    <ol className="inv-timeline">
      {movements.map((m) => {
        const route = journey(m);
        return (
          <li key={m.id}>
            <div className="inv-tl-head">
              {/* An unrecognised type is a backend code change that shipped
                  ahead of this build, not bad data — show the word. */}
              <strong>{movementLabel(m.movement_type)}</strong>
              <time>{stamp(m.created_at)}</time>
            </div>
            {route && <p className="inv-tl-route">{route}</p>}
            <p className="inv-tl-meta">
              {m.performed_by_name && <span>by {m.performed_by_name}</span>}
              {/* Signed, and 0 on a custody move — only a real change is worth
                  a line. */}
              {typeof m.qty_delta === 'number' && m.qty_delta !== 0 && (
                <span>{m.qty_delta > 0 ? `+${m.qty_delta}` : m.qty_delta}</span>
              )}
              {m.condition && <span>{conditionOf(m.condition).label}</span>}
              {m.reference && <span>ref {m.reference}</span>}
              {money(m.recovery_amount) && <span>recovered {money(m.recovery_amount)}</span>}
            </p>
            {m.reason && <p className="inv-tl-note">{m.reason}</p>}
          </li>
        );
      })}
    </ol>
  );
};

export default MovementTimeline;
