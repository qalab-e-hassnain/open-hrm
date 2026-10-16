import { Fragment, useEffect, useMemo, useState, type ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { FiAlertTriangle, FiChevronDown, FiChevronUp, FiRefreshCw } from 'react-icons/fi';
import { ROUTES } from '../../config/constants';
import { useAuthStore } from '../../store/authStore';
import { isLabCustodian, isPrivileged } from '../../utils/permissions';
import {
  inventoryService, whereIs, money, ownershipLabel, isOffice,
  type Domain, type HolderReportRow, type InventoryItem, type ProjectCostReportRow,
  type ReportName, type StockReportRow,
} from '../../services/inventoryService';
import { StatusChip, OverdueChip } from '../../components/inventory/StatusChip';
import '../../components/inventory/inventory.css';
import './Reports.css';

interface ReportSpec {
  name: ReportName;
  label: string;
  blurb: string;
  /** by-holder, overdue and low-stock need the custodian; the rest admin/HR. */
  reach: 'custodian' | 'admin';
  /** Whether the endpoint narrows by `domain`. The rest are lab-shaped
   *  (bins, projects, client kit) and are not offered for the office. */
  sectioned?: boolean;
}

const REPORTS: ReportSpec[] = [
  { name: 'by-holder', label: 'Who has what', blurb: 'Everything out with a person, grouped by who has it.', reach: 'custodian', sectioned: true },
  { name: 'overdue', label: 'Overdue', blurb: 'What should have come back by now, oldest due date first.', reach: 'custodian', sectioned: true },
  { name: 'low-stock', label: 'Low stock', blurb: 'Bins at or below their reorder level.', reach: 'custodian' },
  { name: 'project-cost', label: 'Project cost', blurb: 'Real material cost per project, from what was consumed against it.', reach: 'admin' },
  { name: 'client-hardware', label: 'Client hardware', blurb: 'Client-owned and loaned-in kit in our custody — our exposure on other people’s property.', reach: 'admin' },
  { name: 'idle', label: 'Idle', blurb: 'On a shelf with nothing recorded against it for a long time. Candidates to reallocate or sell.', reach: 'admin', sectioned: true },
  { name: 'register', label: 'Asset register', blurb: 'Everything we own that is not scrapped, with what it cost.', reach: 'admin', sectioned: true },
];

/**
 * Office equipment is held for the length of the job — there is nothing to
 * be overdue — so its reports are who has what, what sits idle, and the
 * register. All three are admin/HR's, like everything else in that section.
 */
const OFFICE_REPORTS: ReportSpec[] = [
  { name: 'by-holder', label: 'Who has what', blurb: 'Every piece of office equipment out with a person, grouped by who has it.', reach: 'admin', sectioned: true },
  { name: 'idle', label: 'Idle', blurb: 'In the store with nothing recorded against it for a long time. Candidates to reissue or sell.', reach: 'admin', sectioned: true },
  { name: 'register', label: 'Office register', blurb: 'Every piece of office equipment that is not scrapped, with what it cost.', reach: 'admin', sectioned: true },
];

const shortDate = (value?: string | null) => {
  if (!value) return '—';
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? value : d.toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' });
};

const qtyText = (qty?: number | null) =>
  typeof qty === 'number' ? (Number.isInteger(qty) ? String(qty) : qty.toFixed(2)) : '—';

const TagLink = ({ id, tag }: { id: number; tag: string }) => (
  <Link className="rpt-tag" to={ROUTES.INVENTORY_ITEM(id)}>{tag}</Link>
);

const ItemCell = ({ item }: { item: InventoryItem }) => (
  <>
    <span className="rpt-name">{item.name}</span>
    <small>{item.category_name}{item.serial_number ? ` · ${item.serial_number}` : ''}</small>
  </>
);

const costOf = (item: InventoryItem) => item.replacement_cost ?? item.purchase_cost ?? null;

const Table = ({ head, children }: { head: ReactNode; children: ReactNode }) => (
  <div className="rpt-table-wrap">
    <table className="rpt-table">
      <thead>{head}</thead>
      <tbody>{children}</tbody>
    </table>
  </div>
);

/* ------------------------------ The tables ----------------------------- */

const ByHolder = ({ rows, office }: { rows: HolderReportRow[]; office: boolean }) => {
  const [open, setOpen] = useState<number | null>(null);
  const showValue = rows.some((r) => r.total_value > 0);
  // Office equipment has no hold to run over, so no overdue column.
  const showOverdue = !office;
  const columns = 3 + (showOverdue ? 1 : 0) + (showValue ? 1 : 0);
  return (
    <Table head={(
      <tr>
        <th>Person</th>
        <th className="rpt-num">Items</th>
        {showOverdue && <th className="rpt-num">Overdue</th>}
        {showValue && <th className="rpt-num">Value</th>}
        <th aria-label="Expand" />
      </tr>
    )}>
      {rows.map((r) => (
        <Fragment key={r.employee_id}>
          <tr className="rpt-clickable" onClick={() => setOpen(open === r.employee_id ? null : r.employee_id)}>
            <td><span className="rpt-name">{r.employee_name}</span></td>
            <td className="rpt-num">{r.item_count}</td>
            {showOverdue && <td className={`rpt-num${r.overdue_count ? ' rpt-bad' : ''}`}>{r.overdue_count || '—'}</td>}
            {showValue && <td className="rpt-num">{money(r.total_value) ?? ''}</td>}
            <td className="rpt-num">{open === r.employee_id ? <FiChevronUp /> : <FiChevronDown />}</td>
          </tr>
          {open === r.employee_id && (
            <tr className="rpt-sub">
              <td colSpan={columns}>
                <ul>
                  {r.items.map((item) => (
                    <li key={item.id}>
                      <TagLink id={item.id} tag={item.tag} />
                      <span>{item.name}</span>
                      {item.hold_due_date && !isOffice(item) && <small>due {shortDate(item.hold_due_date)}</small>}
                      <OverdueChip item={item} />
                    </li>
                  ))}
                </ul>
              </td>
            </tr>
          )}
        </Fragment>
      ))}
    </Table>
  );
};

const Overdue = ({ rows }: { rows: InventoryItem[] }) => (
  <Table head={<tr><th>Tag</th><th>Item</th><th>With</th><th>Due back</th><th className="rpt-num">Days held</th></tr>}>
    {rows.map((item) => (
      <tr key={item.id}>
        <td><TagLink id={item.id} tag={item.tag} /></td>
        <td><ItemCell item={item} /></td>
        <td>{whereIs(item)}</td>
        <td className="rpt-bad">{shortDate(item.hold_due_date)}</td>
        <td className="rpt-num">{typeof item.days_held === 'number' ? item.days_held : '—'}</td>
      </tr>
    ))}
  </Table>
);

const LowStock = ({ rows }: { rows: StockReportRow[] }) => (
  <Table head={<tr><th>Tag</th><th>Item</th><th>Shelf</th><th className="rpt-num">In bin</th><th className="rpt-num">Reorder at</th></tr>}>
    {rows.map((r) => (
      <tr key={r.item_id}>
        <td><TagLink id={r.item_id} tag={r.tag} /></td>
        <td><span className="rpt-name">{r.name}</span></td>
        <td>{r.location_code || '—'}</td>
        <td className={`rpt-num${r.qty <= 0 ? ' rpt-bad' : ''}`}>{qtyText(r.qty)}</td>
        <td className="rpt-num">{qtyText(r.min_qty)}</td>
      </tr>
    ))}
  </Table>
);

const ProjectCost = ({ rows }: { rows: ProjectCostReportRow[] }) => (
  <Table head={<tr><th>Project</th><th className="rpt-num">Consumptions</th><th className="rpt-num">Material cost</th></tr>}>
    {rows.map((r) => (
      <tr key={r.project_id ?? 'none'}>
        <td>
          <span className="rpt-name">
            {r.project_id == null ? 'No project' : (r.project_code || `Project ${r.project_id}`)}
          </span>
          {r.project_name && <small>{r.project_name}</small>}
        </td>
        <td className="rpt-num">{r.line_count}</td>
        <td className="rpt-num">{money(r.material_cost) ?? '—'}</td>
      </tr>
    ))}
  </Table>
);

const ClientHardware = ({ rows }: { rows: InventoryItem[] }) => (
  <Table head={<tr><th>Client</th><th>Tag</th><th>Item</th><th>Owner</th><th>Where</th><th>Status</th></tr>}>
    {rows.map((item) => (
      <tr key={item.id}>
        <td>{item.client_name || '—'}</td>
        <td><TagLink id={item.id} tag={item.tag} /></td>
        <td><ItemCell item={item} /></td>
        <td>{ownershipLabel(item.ownership)}</td>
        <td>{whereIs(item)}</td>
        <td><StatusChip status={item.status} /></td>
      </tr>
    ))}
  </Table>
);

const ItemList = ({ rows }: { rows: InventoryItem[] }) => {
  // Cost columns appear only when there are costs to show (guide §4).
  const showCost = rows.some((i) => money(costOf(i)) !== null);
  const total = rows.reduce((sum, i) => sum + (costOf(i) ?? 0), 0);
  return (
    <Table head={(
      <tr>
        <th>Tag</th><th>Item</th><th>Where</th><th>Status</th>
        {showCost && <th className="rpt-num">Replacement</th>}
      </tr>
    )}>
      {rows.map((item) => (
        <tr key={item.id}>
          <td><TagLink id={item.id} tag={item.tag} /></td>
          <td><ItemCell item={item} /></td>
          <td>{whereIs(item)}</td>
          <td><StatusChip status={item.status} /></td>
          {showCost && <td className="rpt-num">{money(costOf(item)) ?? ''}</td>}
        </tr>
      ))}
      {showCost && (
        <tr className="rpt-total">
          <td colSpan={4}>Total</td>
          <td className="rpt-num">{money(total)}</td>
        </tr>
      )}
    </Table>
  );
};

/* -------------------------------- Page -------------------------------- */

/**
 * The read-only reports. Who gets which tab follows the backend's gates —
 * the holder, overdue and low-stock reports are the custodian's, the ones
 * that carry costs are admin/HR's — and a 403 from the server is still shown
 * verbatim if the two ever disagree.
 *
 * `domain` picks the section: the lab reports (the default, under the Lab
 * nav) or the office ones (admin/HR only). Every report that can be narrowed
 * by section is; the server also pins by-holder and overdue to the lab for
 * anyone short of admin/HR.
 */
const Reports = ({ domain = 'lab' }: { domain?: Domain }) => {
  const user = useAuthStore((s) => s.user);
  const office = domain === 'office';
  const custodian = isLabCustodian(user);
  const admin = isPrivileged(user);
  const available = useMemo(
    () => (office ? OFFICE_REPORTS : REPORTS).filter((r) => (r.reach === 'admin' ? admin : custodian)),
    [office, admin, custodian],
  );
  const title = office ? 'Office reports' : 'Lab reports';

  const [active, setActive] = useState<ReportName | null>(available[0]?.name ?? null);
  const [rows, setRows] = useState<unknown[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  // The box shows the number actually in use rather than sitting empty under
  // a "default" placeholder, so the report says what it is reporting. It
  // mirrors INV_IDLE_ASSET_DAYS on the server and is always sent, so the two
  // cannot quietly disagree about what "idle" means.
  const DEFAULT_IDLE_DAYS = '180';
  const [idleDays, setIdleDays] = useState(DEFAULT_IDLE_DAYS);
  // Applied on Run, not per keystroke.
  const [appliedDays, setAppliedDays] = useState(DEFAULT_IDLE_DAYS);
  const [daysError, setDaysError] = useState('');
  const [nonce, setNonce] = useState(0);

  useEffect(() => {
    if (!active) return;
    let live = true;
    setLoading(true);
    setError('');
    const sectioned = (office ? OFFICE_REPORTS : REPORTS).find((r) => r.name === active)?.sectioned;
    const params: Record<string, unknown> = {
      ...(sectioned ? { domain } : {}),
      ...(active === 'idle' && Number(appliedDays) > 0 ? { days: Number(appliedDays) } : {}),
      // A number below 1 never reaches here: Run refuses it and says so,
      // rather than dropping the parameter and running on the default, which
      // looked exactly like it had accepted -5.
    };
    inventoryService.report<unknown>(active, params)
      .then((data) => { if (live) setRows(data); })
      .catch((err) => { if (live) { setRows([]); setError((err as Error).message || 'Could not run that report.'); } })
      .finally(() => { if (live) setLoading(false); });
    return () => { live = false; };
  }, [active, appliedDays, nonce, office, domain]);

  const spec = available.find((r) => r.name === active);

  const body = () => {
    if (loading) return <p className="inv-muted">Loading…</p>;
    if (error) return null;
    if (rows.length === 0) return <div className="rpt-empty">Nothing to report.</div>;
    switch (active) {
      case 'by-holder': return <ByHolder rows={rows as HolderReportRow[]} office={office} />;
      case 'overdue': return <Overdue rows={rows as InventoryItem[]} />;
      case 'low-stock': return <LowStock rows={rows as StockReportRow[]} />;
      case 'project-cost': return <ProjectCost rows={rows as ProjectCostReportRow[]} />;
      case 'client-hardware': return <ClientHardware rows={rows as InventoryItem[]} />;
      case 'idle':
      case 'register': return <ItemList rows={rows as InventoryItem[]} />;
      default: return null;
    }
  };

  if (available.length === 0) {
    return (
      <div className="rpt-page">
        <header className="rpt-head"><div><h1>{title}</h1></div></header>
        <div className="rpt-empty">
          {office ? 'The office reports are for admin and HR.' : 'The lab reports are for the custodian, admin and HR.'}
        </div>
      </div>
    );
  }

  return (
    <div className="rpt-page">
      <header className="rpt-head">
        <div>
          <h1>{title}</h1>
          <p>Read-only views over the {office ? 'office equipment' : 'lab'} register. Nothing here changes anything.</p>
        </div>
        <button type="button" className="inv-btn" onClick={() => setNonce((n) => n + 1)} disabled={loading}>
          <FiRefreshCw /> Refresh
        </button>
      </header>

      <div className="rpt-tabs" role="tablist">
        {available.map((r) => (
          <button
            key={r.name}
            type="button"
            role="tab"
            aria-selected={active === r.name}
            className={active === r.name ? 'is-on' : undefined}
            onClick={() => { if (r.name !== active) { setRows([]); setLoading(true); setActive(r.name); } }}
          >
            {r.label}
          </button>
        ))}
      </div>

      {spec && (
        <div className="rpt-intro">
          <p>{spec.blurb}{!loading && !error && rows.length > 0 ? ` ${rows.length} row${rows.length === 1 ? '' : 's'}.` : ''}</p>
          {active === 'idle' && (
            <form className="rpt-param" onSubmit={(e) => {
              e.preventDefault();
              const days = Number(idleDays);
              if (!Number.isFinite(days) || !Number.isInteger(days) || days < 1) {
                setDaysError('Give a whole number of days, 1 or more.');
                return;
              }
              setDaysError('');
              setAppliedDays(idleDays);
              setNonce((n) => n + 1);
            }}>
              <label>
                Idle for at least
                <input type="number" min="1" step="1" value={idleDays}
                  aria-invalid={daysError ? true : undefined}
                  aria-describedby={daysError ? 'rpt-days-error' : undefined}
                  onChange={(e) => { setIdleDays(e.target.value); setDaysError(''); }} />
                days
              </label>
              <button type="submit" className="inv-btn" disabled={loading}>Run</button>
              {daysError && (
                <span className="rpt-param-error" id="rpt-days-error" role="alert">{daysError}</span>
              )}
            </form>
          )}
        </div>
      )}

      {error && <div className="rpt-alert"><FiAlertTriangle /> {error}</div>}
      {body()}
    </div>
  );
};

export default Reports;
