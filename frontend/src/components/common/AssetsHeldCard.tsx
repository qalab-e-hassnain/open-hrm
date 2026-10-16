import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { FiHardDrive } from 'react-icons/fi';
import { ROUTES } from '../../config/constants';
import { inventoryService, isOffice, type InventoryItem } from '../../services/inventoryService';
import { DomainChip } from '../inventory/StatusChip';
import './AssetsHeldCard.css';

const shortDate = (v?: string | null) => {
  if (!v) return '';
  const d = new Date(v);
  return Number.isNaN(d.getTime()) ? '' : d.toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' });
};

/**
 * Company equipment the employee currently holds — the list they hand back
 * when they leave, so it is theirs to see without any special rights.
 *
 * Reads the lab register (`/inventory/…`) rather than the old `/assets`
 * shim. Same rows, but each one is a real item with a tag and a history
 * behind it. With `employeeId` this is the holdings tab on somebody else's
 * profile, which the server shows to that person, their line manager and the
 * custodian and to nobody else.
 *
 * Both sections of the register — lab kit and office equipment — because
 * both are on the clearance list. Each row says which. Office equipment is
 * held for the length of the job, so it carries no due date and no overdue.
 */
const AssetsHeldCard = ({ employeeId }: { employeeId?: number }) => {
  const [held, setHeld] = useState<InventoryItem[]>([]);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    const request = employeeId
      ? inventoryService.employeeHoldings(employeeId)
      : inventoryService.myHoldings();
    request
      .then(setHeld)
      .catch(() => setHeld([]))
      .finally(() => setLoaded(true));
  }, [employeeId]);

  // An empty register is the normal state until the lab is tagged, so this
  // stays out of the way rather than announcing that nothing is held.
  if (!loaded || held.length === 0) return null;

  return (
    <div className="ah-card">
      <h4><FiHardDrive /> Equipment held</h4>
      <ul>
        {held.map((item) => (
          <li key={item.id} className={item.is_overdue && !isOffice(item) ? 'ah-overdue' : undefined}>
            <span className="ah-name">
              <Link to={ROUTES.INVENTORY_ITEM(item.id)}>{item.name}</Link>
              <code>{item.tag}</code>
              <DomainChip domain={item.domain} />
            </span>
            <small>
              {item.category_name}
              {/* Precomputed server-side; the two clocks disagree at midnight. */}
              {item.is_overdue && !isOffice(item) ? ' · overdue' : ''}
              {!isOffice(item) && shortDate(item.hold_due_date) ? ` · due back ${shortDate(item.hold_due_date)}` : ''}
              {typeof item.days_held === 'number' ? ` · held ${item.days_held} days` : ''}
            </small>
          </li>
        ))}
      </ul>
      <p className="ah-note">Return these before your last working day. Anything not returned is recovered in the final settlement.</p>
    </div>
  );
};

export default AssetsHeldCard;
