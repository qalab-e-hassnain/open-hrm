import { useEffect, useMemo, useState, type ReactNode } from 'react';
import {
  FiAlertTriangle, FiChevronDown, FiGitBranch, FiList, FiMaximize,
  FiSearch, FiShare2, FiTarget, FiUsers, FiZoomIn, FiZoomOut,
} from 'react-icons/fi';
import { COMPANY } from '../../config/constants';
import { orgService, type OrgNode } from '../../services/analyticsService';
import './OrgChart.css';

const initials = (name: string) =>
  name.split(/\s+/).filter(Boolean).slice(0, 2).map((p) => p[0]?.toUpperCase() ?? '').join('');

const detailOf = (err: unknown, fallback: string): string => {
  const detail = (err as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail;
  if (typeof detail === 'string' && detail) return detail;
  return (err as { message?: string })?.message || fallback;
};

const countAll = (nodes: OrgNode[]): number =>
  nodes.reduce((total, node) => total + 1 + countAll(node.reports), 0);

/** Keeps a node whose own details match, or which has a matching descendant,
 *  so filtering never orphans somebody's manager. */
const filterTree = (nodes: OrgNode[], term: string): OrgNode[] => {
  const q = term.trim().toLowerCase();
  if (!q) return nodes;
  const walk = (node: OrgNode): OrgNode | null => {
    const reports = node.reports.map(walk).filter((n): n is OrgNode => n !== null);
    const hit = [node.full_name, node.designation, node.department, node.employee_id]
      .some((field) => field?.toLowerCase().includes(q));
    return hit || reports.length ? { ...node, reports } : null;
  };
  return nodes.map(walk).filter((n): n is OrgNode => n !== null);
};

/* Colour follows the department, not the position in the tree, so the same
   team reads as the same colour wherever it has been placed. Eight bands,
   chosen by a stable hash so a department keeps its colour between loads. */
const BANDS = 8;
const bandOf = (node: OrgNode): number => {
  const key = (node.department || node.designation || node.full_name).toLowerCase();
  let h = 0;
  for (let i = 0; i < key.length; i += 1) h = (h * 31 + key.charCodeAt(i)) % 100000;
  return h % BANDS;
};

const Avatar = ({ node, className = '' }: { node: OrgNode; className?: string }) =>
  node.profile_picture_url
    ? <img src={node.profile_picture_url} alt="" className={`oc-avatar ${className}`} />
    : <span className={`oc-avatar oc-avatar-fallback ${className}`}>{initials(node.full_name)}</span>;

const Tags = ({ node }: { node: OrgNode }) => (
  <>
    {node.is_hr && <em className="oc-tag oc-tag-hr">HR</em>}
    {node.role === 'admin' && <em className="oc-tag oc-tag-admin">Admin</em>}
  </>
);

const Toggle = ({ open, count, name, onClick }:
  { open: boolean; count: number; name: string; onClick: () => void }) => (
  <button type="button" className={`oc-toggle${open ? ' is-open' : ''}`} onClick={onClick}
    aria-expanded={open}
    aria-label={`${open ? 'Collapse' : 'Expand'} ${count} report${count === 1 ? '' : 's'} of ${name}`}>
    {count} <FiChevronDown />
  </button>
);

/* ------------------------------------------------------------------ list -- */

const ListNode = ({ node, depth }: { node: OrgNode; depth: number }) => {
  const [open, setOpen] = useState(depth < 2);
  const hasReports = node.reports.length > 0;

  return (
    <li className="oc-node">
      <div className={`oc-card${node.role === 'admin' ? ' is-admin' : ''}`}>
        <Avatar node={node} />
        <span className="oc-who">
          <strong>{node.full_name}</strong>
          <small>{node.designation || '—'}{node.department ? ` · ${node.department}` : ''}</small>
        </span>
        <span className="oc-tags">
          <Tags node={node} />
          {hasReports && (
            <Toggle open={open} count={node.reports.length} name={node.full_name}
              onClick={() => setOpen(!open)} />
          )}
        </span>
      </div>
      {hasReports && open && (
        <ul className="oc-children">
          {node.reports.map((child) => <ListNode key={child.id} node={child} depth={depth + 1} />)}
        </ul>
      )}
    </li>
  );
};

/* ------------------------------------------------- boxes and lines (both) -- */

/** One card, used by the top-down tree and the left-to-right tree alike.
 *  The two differ only in which way the connectors run, which is CSS. */
const BranchNode = ({ node, depth }: { node: OrgNode; depth: number }) => {
  const [open, setOpen] = useState(depth < 3);
  const hasReports = node.reports.length > 0;

  return (
    <li className="oc-b-node">
      <div className="oc-b-slot">
        <div className={`oc-b-card oc-band-${bandOf(node)}${node.role === 'admin' ? ' is-admin' : ''}`}>
          <Avatar node={node} className="oc-b-avatar" />
          <span className="oc-b-text">
            <span className="oc-b-role">{node.designation || '—'}</span>
            <strong className="oc-b-name">{node.full_name}</strong>
            {node.department && <small className="oc-b-dept">{node.department}</small>}
          </span>
          <span className="oc-b-tags"><Tags node={node} /></span>
        </div>
        {hasReports && (
          <Toggle open={open} count={node.reports.length} name={node.full_name}
            onClick={() => setOpen(!open)} />
        )}
      </div>
      {hasReports && open && (
        <ul className="oc-b-level">
          {node.reports.map((child) => <BranchNode key={child.id} node={child} depth={depth + 1} />)}
        </ul>
      )}
    </li>
  );
};

/* ----------------------------------------------------------------- orbit -- */

type Slice = { node: OrgNode; depth: number; mid: number; span: number; parent: number };
type Placed = { node: OrgNode; x: number; y: number; depth: number; px: number; py: number };

const MIN_RING = 170;   /* closest two rings may ever sit, in px */
const LABEL_W = 124;    /* a card's width — two neighbours must clear it */

const leafCount = (n: OrgNode): number =>
  n.reports.length ? n.reports.reduce((s, c) => s + leafCount(c), 0) : 1;

/** Everybody gets a slice of the circle in proportion to how many people sit
 *  beneath them, and their reports are laid out inside that slice. A large
 *  team therefore spreads out instead of overlapping a small one. */
const sliceUp = (nodes: OrgNode[], from: number, to: number, depth: number,
                 parent: number, out: Slice[]) => {
  const total = nodes.reduce((s, n) => s + leafCount(n), 0) || 1;
  let angle = from;
  nodes.forEach((node) => {
    const span = (to - from) * (leafCount(node) / total);
    const index = out.length;
    out.push({ node, depth, mid: angle + span / 2, span, parent });
    if (node.reports.length) sliceUp(node.reports, angle, angle + span, depth + 1, index, out);
    angle += span;
  });
};

/** Where each ring sits. Evenly spaced rings look right until a ring gets
 *  crowded, and then names sit on top of each other — so each ring is pushed
 *  out far enough that the chord between its tightest pair clears a label.
 *  The chart grows with the company instead of needing the spacing retuned. */
const orbit = (roots: OrgNode[]): Placed[] => {
  const slices: Slice[] = [];
  sliceUp(roots, 0, Math.PI * 2, 0, -1, slices);
  if (!slices.length) return [];

  /* Turn the whole circle so the first person at the top of the company sits
     at the top of the chart. Everyone sits at the middle of their own slice,
     and the slice of someone with the whole company under them wraps nearly
     the full circle — so without this the chief executive lands at the
     bottom, opposite where their slice began, which reads as arbitrary. */
  const turn = -Math.PI / 2 - slices[0].mid;
  slices.forEach((s) => { s.mid += turn; });

  const deepest = slices.reduce((m, s) => Math.max(m, s.depth), 0);
  const radii: number[] = [];
  for (let d = 0; d <= deepest; d += 1) {
    const tightest = slices.filter((s) => s.depth === d)
      .reduce((m, s) => Math.min(m, s.span), Math.PI * 2);
    const needed = LABEL_W / (2 * Math.sin(Math.min(tightest, Math.PI) / 2));
    radii[d] = Math.max(MIN_RING * (d + 1), needed, (radii[d - 1] ?? 0) + MIN_RING);
  }

  const at = (s: Slice) => ({ x: Math.cos(s.mid) * radii[s.depth], y: Math.sin(s.mid) * radii[s.depth] });
  return slices.map((s) => {
    const { x, y } = at(s);
    const parent = s.parent >= 0 ? at(slices[s.parent]) : { x: 0, y: 0 };
    return { node: s.node, x, y, depth: s.depth, px: parent.x, py: parent.y };
  });
};

const Orbit = ({ roots }: { roots: OrgNode[] }) => {
  const placed = useMemo(() => orbit(roots), [roots]);
  /* Counts who is on the chart, not who is in the company: with a search
     running, the ring holds the matches and the middle should say so. */
  const shown = placed.length;

  const reach = placed.reduce((m, p) => Math.max(m, Math.abs(p.x), Math.abs(p.y)), MIN_RING);
  const pad = 110;
  const size = (reach + pad) * 2;
  const c = size / 2;

  return (
    <div className="oc-orbit" style={{ width: size, height: size }}>
      <svg className="oc-orbit-lines" width={size} height={size} aria-hidden="true">
        {[...new Set(placed.map((p) => Math.round(Math.hypot(p.x, p.y))))].map((r) => (
          <circle key={r} cx={c} cy={c} r={r} className="oc-orbit-ring" />
        ))}
        {placed.map((p) => (
          <line key={p.node.id} x1={c + p.px} y1={c + p.py} x2={c + p.x} y2={c + p.y}
            className="oc-orbit-link" />
        ))}
      </svg>
      <div className="oc-orbit-core" style={{ left: c, top: c }}>
        <strong>{COMPANY.SHORT_NAME}</strong>
        <small>{shown} {shown === 1 ? 'person' : 'people'}</small>
      </div>
      {placed.map((p) => (
        <div key={p.node.id} className={`oc-orbit-node oc-band-${bandOf(p.node)}`}
          style={{ left: c + p.x, top: c + p.y }}>
          <Avatar node={p.node} className="oc-orbit-avatar" />
          <span className="oc-orbit-label">
            <strong>{p.node.full_name}</strong>
            <small>{p.node.designation || '—'}</small>
          </span>
        </div>
      ))}
    </div>
  );
};

/* ------------------------------------------------------------------ page -- */

type Layout = 'tree' | 'branch' | 'orbit' | 'list';

const LAYOUTS: { key: Layout; label: string; icon: ReactNode; hint: string }[] = [
  { key: 'tree', label: 'Tree', icon: <FiShare2 />, hint: 'Top down, spreading sideways' },
  { key: 'branch', label: 'Sideways', icon: <FiGitBranch />, hint: 'Left to right' },
  { key: 'orbit', label: 'Orbit', icon: <FiTarget />, hint: 'Rings around the company' },
  { key: 'list', label: 'Compact', icon: <FiList />, hint: 'Indented list' },
];

const STORED = 'hrm.orgchart.layout';
const isLayout = (v: string | null): v is Layout =>
  v === 'tree' || v === 'branch' || v === 'orbit' || v === 'list';

const OrgChart = () => {
  const [roots, setRoots] = useState<OrgNode[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [search, setSearch] = useState('');
  const [includeInactive, setIncludeInactive] = useState(false);
  const [layout, setLayout] = useState<Layout>(() => {
    try {
      const saved = localStorage.getItem(STORED);
      return isLayout(saved) ? saved : 'tree';
    } catch { return 'tree'; }
  });
  const [zoom, setZoom] = useState(1);

  useEffect(() => {
    setLoading(true);
    orgService.chart(includeInactive)
      .then(setRoots)
      .catch((err) => setError(detailOf(err, 'Could not load the org chart.')))
      .finally(() => setLoading(false));
  }, [includeInactive]);

  const choose = (next: Layout) => {
    setLayout(next);
    setZoom(1);
    try { localStorage.setItem(STORED, next); } catch { /* private window */ }
  };

  const shown = useMemo(() => filterTree(roots, search), [roots, search]);
  const total = useMemo(() => countAll(roots), [roots]);
  const scalable = layout !== 'list';

  return (
    <div className="orgchart-page">
      <header className="oc-head">
        <div>
          <h1>Org chart</h1>
          <p>
            Who reports to whom, from the reporting manager on each record.
            {total > 0 && ` ${total} ${total === 1 ? 'person' : 'people'}.`}
          </p>
        </div>
        <div className="oc-controls">
          <label className="oc-search">
            <FiSearch />
            <input value={search} onChange={(e) => setSearch(e.target.value)}
              placeholder="Name, designation or department" />
          </label>
          <label className="oc-check">
            <input type="checkbox" checked={includeInactive}
              onChange={(e) => setIncludeInactive(e.target.checked)} />
            Show leavers
          </label>
        </div>
      </header>

      <div className="oc-bar">
        <div className="oc-views" role="group" aria-label="Chart layout">
          {LAYOUTS.map((l) => (
            <button key={l.key} type="button" title={l.hint}
              className={`oc-view${layout === l.key ? ' is-on' : ''}`}
              aria-pressed={layout === l.key} onClick={() => choose(l.key)}>
              {l.icon}<span>{l.label}</span>
            </button>
          ))}
        </div>
        {scalable && (
          <div className="oc-zoom" role="group" aria-label="Zoom">
            <button type="button" onClick={() => setZoom((z) => Math.max(0.4, +(z - 0.1).toFixed(2)))}
              aria-label="Zoom out"><FiZoomOut /></button>
            <span>{Math.round(zoom * 100)}%</span>
            <button type="button" onClick={() => setZoom((z) => Math.min(1.6, +(z + 0.1).toFixed(2)))}
              aria-label="Zoom in"><FiZoomIn /></button>
            <button type="button" onClick={() => setZoom(1)} aria-label="Reset zoom"><FiMaximize /></button>
          </div>
        )}
      </div>

      {error && <div className="oc-alert"><FiAlertTriangle /> {error}</div>}

      {loading ? (
        <div className="oc-empty">Loading…</div>
      ) : shown.length === 0 ? (
        <div className="oc-empty">
          <FiUsers />
          <p>{search ? 'Nobody matches that.' : 'Nobody has a reporting line recorded yet.'}</p>
        </div>
      ) : (
        <>
          {/* An array of roots, not one root: two founders at the top is a
              real shape, and anyone whose manager has left surfaces here
              rather than vanishing from the tree. */}
          {roots.length > 1 && !search && (
            <p className="oc-note">
              {roots.length} people sit at the top — either founders, or someone
              whose manager has left and who needs a new reporting line.
            </p>
          )}

          {layout === 'list' && (
            <ul className="oc-tree">
              {shown.map((node) => <ListNode key={node.id} node={node} depth={0} />)}
            </ul>
          )}

          {layout !== 'list' && (
            /* The stage scrolls; the chart inside it is scaled. Scaling the
               scroll container instead would shrink the scrollbars with it. */
            <div className="oc-stage">
              <div className="oc-canvas" style={{ transform: `scale(${zoom})` }}>
                {layout === 'orbit'
                  ? <Orbit roots={shown} />
                  : (
                    <ul className={`oc-b-level oc-b-root ${layout === 'branch' ? 'oc-b-sideways' : 'oc-b-down'}`}>
                      {shown.map((node) => <BranchNode key={node.id} node={node} depth={0} />)}
                    </ul>
                  )}
              </div>
            </div>
          )}
        </>
      )}
    </div>
  );
};

export default OrgChart;
