import { statusOf, conditionOf, isOffice, DOMAIN_LABEL, type Domain, type Tone } from '../../services/inventoryService';
import './inventory.css';

/** One chip, one tone. Unknown values fall back to the humanised raw string. */
const Chip = ({ label, tone, title }: { label: string; tone: Tone; title?: string }) => (
  <span className={`inv-chip inv-chip-${tone}`} title={title}>{label}</span>
);

export const StatusChip = ({ status }: { status?: string | null }) => {
  const { label, tone } = statusOf(status);
  return <Chip label={label} tone={tone} />;
};

export const ConditionChip = ({ condition }: { condition?: string | null }) => {
  const { label, tone } = conditionOf(condition);
  return (
    <Chip
      label={label}
      tone={tone}
      // "Unknown" is the honest state of most of the seeded lab stock: counted,
      // never powered on. Spelling that out stops it reading as a data gap.
      title={condition === 'unknown' ? 'Counted, but never powered on' : undefined}
    />
  );
};

export const OverdueChip = ({ item }: { item: { is_overdue?: boolean; days_held?: number | null; domain?: string | null } }) => {
  // Office equipment is held for the length of the job: there is no hold to
  // run over, whatever a stray date on the row says.
  if (!item.is_overdue || isOffice(item)) return null;
  // is_overdue is computed server-side precisely because the two clocks
  // disagree around midnight in PKT. Never re-derive it from hold_due_date.
  return (
    <span className="inv-chip inv-chip-danger">
      Overdue{typeof item.days_held === 'number' ? ` · ${item.days_held}d held` : ''}
    </span>
  );
};

/** "Lab" or "Office" — which section of the register a row belongs to. */
export const DomainChip = ({ domain }: { domain?: string | null }) => {
  if (domain !== 'lab' && domain !== 'office') return null;
  return <Chip label={DOMAIN_LABEL[domain as Domain]} tone={domain === 'office' ? 'info' : 'neutral'} />;
};

export default StatusChip;
