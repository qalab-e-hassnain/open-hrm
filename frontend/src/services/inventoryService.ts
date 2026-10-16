import { apiClient } from './api';
import { API_ENDPOINTS } from '../config/constants';

const INV = API_ENDPOINTS.INVENTORY;

/* ----------------------------- Vocabulary ----------------------------- */

/** Comes from the item's category and drives every difference in the UI. */
export type TrackingType = 'serial' | 'project' | 'quantity';

export type ItemStatus =
  | 'in_stock' | 'issued' | 'in_transit' | 'with_client'
  | 'under_repair' | 'lost' | 'scrapped';

export type ItemCondition = 'new' | 'working' | 'faulty' | 'scrapped' | 'unknown';

export type Ownership = 'company' | 'client' | 'loaned_in' | 'loaned_out';

/** Project-tracked items only. */
export type LifecycleState = 'in_build' | 'under_test' | 'with_client' | 'shelved' | 'scrapped';

export type MovementType =
  | 'receive' | 'checkout' | 'checkin' | 'transfer' | 'ship_to_client'
  | 'return_from_client' | 'consume' | 'repair_out' | 'repair_in'
  | 'scrap' | 'lost' | 'found' | 'audit_adjust';

/**
 * Which section of the one register an item belongs to, read off its
 * category. The lab is run by the custodian; office equipment (laptops,
 * phones, SIMs, access cards…) is issued and taken back by admin or HR only,
 * is never self-served, and is held for the length of the job — so it has no
 * hold due date, no overdue and nothing to renew.
 */
export type Domain = 'lab' | 'office';

export const DOMAIN_LABEL: Record<Domain, string> = { lab: 'Lab', office: 'Office' };

/** Office equipment carries no hold clock — hide due dates, overdue and renew. */
export const isOffice = (item?: { domain?: string | null } | null): boolean =>
  item?.domain === 'office';

export type Tone = 'neutral' | 'info' | 'warning' | 'danger' | 'muted' | 'success';

/**
 * A snake_case backend value the portal has not been taught yet. New movement
 * types ship as a backend code change with no migration, so they arrive before
 * this file knows about them — rendering the raw string with its underscores
 * knocked out beats rendering a blank cell.
 */
export const humanize = (value: string): string => {
  const spaced = value.replace(/_/g, ' ').trim();
  return spaced ? spaced.charAt(0).toUpperCase() + spaced.slice(1) : '—';
};

const STATUS: Record<ItemStatus, { label: string; tone: Tone }> = {
  in_stock:     { label: 'On the shelf', tone: 'neutral' },
  issued:       { label: 'With someone', tone: 'info'    },
  in_transit:   { label: 'In transit',   tone: 'warning' },
  with_client:  { label: 'With client',  tone: 'warning' },
  under_repair: { label: 'Under repair', tone: 'warning' },
  lost:         { label: 'Lost',         tone: 'danger'  },
  scrapped:     { label: 'Scrapped',     tone: 'muted'   },
};

const CONDITION: Record<ItemCondition, { label: string; tone: Tone }> = {
  new:      { label: 'New',      tone: 'success' },
  working:  { label: 'Working',  tone: 'success' },
  faulty:   { label: 'Faulty',   tone: 'danger'  },
  scrapped: { label: 'Scrapped', tone: 'muted'   },
  // Counted but never powered on — which is most of the seeded lab stock, and
  // is emphatically not the same claim as "working".
  unknown:  { label: 'Unknown',  tone: 'muted'   },
};

const OWNERSHIP: Record<Ownership, string> = {
  company: 'Company owned',
  client: 'Client owned',
  loaned_in: 'Loaned in',
  loaned_out: 'Loaned out',
};

const LIFECYCLE: Record<LifecycleState, string> = {
  in_build: 'In build',
  under_test: 'Under test',
  with_client: 'With client',
  shelved: 'Shelved',
  scrapped: 'Scrapped',
};

const MOVEMENT: Record<MovementType, string> = {
  receive: 'Received',
  checkout: 'Checked out',
  checkin: 'Checked in',
  transfer: 'Transferred',
  ship_to_client: 'Shipped to client',
  return_from_client: 'Returned from client',
  consume: 'Consumed',
  repair_out: 'Sent for repair',
  repair_in: 'Back from repair',
  scrap: 'Scrapped',
  lost: 'Marked lost',
  found: 'Found',
  audit_adjust: 'Audit correction',
};

export const statusOf = (value?: string | null) =>
  (value && STATUS[value as ItemStatus]) || { label: humanize(value || ''), tone: 'neutral' as Tone };

export const conditionOf = (value?: string | null) =>
  (value && CONDITION[value as ItemCondition]) || { label: humanize(value || ''), tone: 'muted' as Tone };

export const ownershipLabel = (value?: string | null) =>
  (value && OWNERSHIP[value as Ownership]) || humanize(value || '');

export const lifecycleLabel = (value?: string | null) =>
  (value && LIFECYCLE[value as LifecycleState]) || humanize(value || '');

export const movementLabel = (value?: string | null) =>
  (value && MOVEMENT[value as MovementType]) || humanize(value || '');

export const ITEM_STATUSES = Object.keys(STATUS) as ItemStatus[];
export const ITEM_CONDITIONS = Object.keys(CONDITION) as ItemCondition[];
export const OWNERSHIPS = Object.keys(OWNERSHIP) as Ownership[];

/* ------------------------------- Shapes ------------------------------- */

export interface InventoryItem {
  id: number;
  tag: string;
  name: string;
  category_id: number;
  category_name?: string | null;
  /** 'lab' or 'office', from the category. */
  domain?: Domain | null;
  tracking_type?: TrackingType | null;
  status: ItemStatus;
  condition: ItemCondition;
  ownership: Ownership;
  /** Project-tracked items only. */
  lifecycle_state?: LifecycleState | null;

  current_holder_id?: number | null;
  holder_name?: string | null;
  current_location_id?: number | null;
  location_code?: string | null;
  location_path?: string | null;

  hold_due_date?: string | null;
  days_held?: number | null;
  /** Precomputed server-side. Never recalculate it from dates in the client. */
  is_overdue?: boolean;

  /** Quantity-tracked items only; everything else is 1. */
  qty?: number | null;
  min_qty?: number | null;

  serial_number?: string | null;
  manufacturer?: string | null;
  mpn?: string | null;
  description?: string | null;
  photo_url?: string | null;
  datasheet_url?: string | null;
  notes?: string | null;

  project_id?: number | null;
  project_code?: string | null;
  project_name?: string | null;
  client_id?: number | null;
  client_name?: string | null;

  /** Null — not absent, not zero — for anyone who is not admin, HR or the
   *  custodian. Test the value, never `'purchase_cost' in item`. */
  purchase_cost?: number | null;
  replacement_cost?: number | null;
  unit_cost?: number | null;
  supplier?: string | null;
  invoice_ref?: string | null;
  purchase_date?: string | null;
  currency?: string | null;
  created_at?: string | null;
}

/**
 * What the scan endpoint returns. `actions` is the authoritative list of what
 * this caller may do to this item in its current state — render exactly those
 * and nothing else.
 */
export interface ScanResult {
  item: InventoryItem;
  actions: string[];
  held_by_me: boolean;
}

/**
 * One row of an item's history. The register is append-only, so a movement is
 * a fact that never changes. Fields beyond the type and the timestamp vary by
 * movement type, so the timeline renders whatever is present.
 */
export interface Movement {
  id: number;
  item_id: number;
  item_tag?: string | null;
  item_name?: string | null;
  movement_type: MovementType | string;
  from_location_id?: number | null;
  to_location_id?: number | null;
  from_location_code?: string | null;
  to_location_code?: string | null;
  from_holder_id?: number | null;
  to_holder_id?: number | null;
  from_holder_name?: string | null;
  to_holder_name?: string | null;
  /** Signed: negative takes stock off the count. 0 on custody moves. */
  qty_delta?: number;
  project_id?: number | null;
  unit_cost?: number | null;
  performed_by?: number | null;
  performed_by_name?: string | null;
  reason?: string | null;
  reference?: string | null;
  expected_return_date?: string | null;
  condition?: ItemCondition | null;
  recovery_amount?: number | null;
  created_at: string;
}

export interface ItemCategory {
  id: number;
  name: string;
  tracking_type: TrackingType;
  tag_prefix?: string | null;
  /** Mark the serial field required in the form rather than waiting for a 400. */
  serial_required: boolean;
  default_audit_days?: number;
  domain?: Domain | null;
  is_active: boolean;
  item_count?: number;
}

/** `location_type` mirrors the backend's LocationTypeEnum. */
export type LocationType =
  | 'internal' | 'client_shelf' | 'in_transit'
  | 'with_person' | 'customs_hold' | 'quarantine';

export interface StoreLocation {
  id: number;
  code: string;
  name: string;
  path: string;
  parent_id?: number | null;
  location_type: LocationType | string;
  client_id?: number | null;
  client_name?: string | null;
  is_active: boolean;
  item_count?: number;
}

/**
 * Which shelves an item may actually go on. A client shelf holds client-owned
 * hardware and nothing else, and company kit may not go on one — the API
 * refuses the mismatch with a clear 400, so the picker refuses it first.
 */
export const shelvesFor = (item: InventoryItem, locations: StoreLocation[]): StoreLocation[] => {
  const clientOwned = item.ownership === 'client';
  return locations.filter((loc) => (loc.location_type === 'client_shelf') === clientOwned);
};

export interface ItemInput {
  category_id: number;
  name: string;
  /** Required where the category says so. */
  serial_number?: string;
  /** Required: nothing enters the register without a shelf or a holder. */
  current_location_id: number;
  /** Omit to have one issued from the category's prefix. */
  tag?: string;
  manufacturer?: string;
  mpn?: string;
  ownership?: Ownership;
  condition?: ItemCondition;
  qty?: number;
  min_qty?: number;
  purchase_cost?: number;
  replacement_cost?: number;
  unit_cost?: number;
  supplier?: string;
  invoice_ref?: string;
  purchase_date?: string;
  project_code?: string;
  project_name?: string;
  notes?: string;
}

export interface ItemFilters {
  status?: ItemStatus;
  category_id?: number;
  location_id?: number;
  held_by?: number;
  project_id?: number;
  ownership?: Ownership;
  overdue_only?: boolean;
  /** Matches tag, name, serial and MPN. */
  search?: string;
  /** One section of the register. Omit for both. */
  domain?: Domain;
  limit?: number;
  offset?: number;
}

/* ------------------------------ Helpers ------------------------------- */

/**
 * Where the thing is, in one cell. While an item is in stock or issued exactly
 * one of holder and location is set — but scrapped, lost and in-transit items
 * have neither, which is the case that otherwise renders as an empty cell.
 */
export const whereIs = (item: InventoryItem): string => {
  if (item.holder_name) return `With ${item.holder_name}`;
  if (item.location_code) return item.location_path || item.location_code;
  if (item.status === 'in_transit') return 'In transit';
  if (item.status === 'with_client') return `With ${item.client_name || 'client'}`;
  return '—';
};

/** Only render a cost where there is one: an engineer gets null, not zero. */
export const money = (value?: number | null): string | null =>
  typeof value === 'number' && Number.isFinite(value)
    ? `PKR ${value.toLocaleString('en-PK', { maximumFractionDigits: 0 })}`
    : null;

/**
 * The action tokens in `actions[]` and the URL verbs are the same words, but
 * the guide writes one snake_case (`write_off`) and the other kebab
 * (`write-off`). Normalise to kebab so either spelling from the backend maps
 * to the same registry entry and the same POST.
 */
export const actionVerb = (action: string): string => action.trim().replace(/_/g, '-');

/* ------------------------------ Requests ------------------------------ */

/** `pending → approved → issued`, or `rejected`, or `cancelled`. */
export type RequestStatus = 'pending' | 'approved' | 'issued' | 'rejected' | 'cancelled';

/** Mirrors RequestOut in the backend's schemas/inventory.py. */
export interface StockRequest {
  id: number;
  status: RequestStatus;
  requester_id: number;
  requester_name?: string | null;
  /** Null when the request named a category; the custodian picks the item at issue. */
  item_id?: number | null;
  item_tag?: string | null;
  item_name?: string | null;
  category_id?: number | null;
  category_name?: string | null;
  qty: number;
  project_id?: number | null;
  purpose: string;
  needed_by?: string | null;
  /** Whoever approved *or* declined it — both are a decision. */
  approver_id?: number | null;
  approver_name?: string | null;
  approved_at?: string | null;
  rejection_reason?: string | null;
  issued_movement_id?: number | null;
  created_at: string;
}

export interface RequestInput {
  /** One of item_id / category_id is required. */
  item_id?: number;
  category_id?: number;
  qty: number;
  project_id?: number;
  purpose: string;
  needed_by?: string;
}

const REQUEST_STATUS: Record<RequestStatus, { label: string; tone: Tone }> = {
  pending:   { label: 'Waiting for approval', tone: 'warning' },
  approved:  { label: 'Approved — to issue',  tone: 'info'    },
  issued:    { label: 'Issued',               tone: 'success' },
  rejected:  { label: 'Declined',             tone: 'danger'  },
  cancelled: { label: 'Cancelled',            tone: 'muted'   },
};

export const requestStatusOf = (value?: string | null) =>
  (value && REQUEST_STATUS[value as RequestStatus]) || { label: humanize(value || ''), tone: 'neutral' as Tone };

/* -------------------------------- Audit ------------------------------- */

export type AuditOutcome = 'present' | 'found_elsewhere' | 'with_person' | 'not_located';

const AUDIT_OUTCOME: Record<AuditOutcome, { label: string; tone: Tone }> = {
  present:         { label: 'Present',          tone: 'success' },
  // The commonest result of a count, and a result rather than an error.
  found_elsewhere: { label: 'Found here — not expected', tone: 'warning' },
  with_person:     { label: 'With a person',    tone: 'info'    },
  not_located:     { label: 'Not located',      tone: 'danger'  },
};

export const auditOutcomeOf = (value?: string | null) =>
  (value && AUDIT_OUTCOME[value as AuditOutcome]) || { label: humanize(value || ''), tone: 'neutral' as Tone };

/** Mirrors AuditLineOut. */
export interface AuditLine {
  id: number;
  item_id: number;
  item_tag?: string | null;
  item_name?: string | null;
  expected_qty: number;
  counted_qty?: number | null;
  variance: number;
  variance_value: number;
  outcome?: AuditOutcome | string | null;
  note?: string | null;
  /** Null until the tag is scanned in this session. */
  scanned_at?: string | null;
}

/** Mirrors AuditSessionOut. `lines` is empty on the list endpoint. */
export interface AuditSession {
  id: number;
  label?: string | null;
  scope_location_id?: number | null;
  scope_location_code?: string | null;
  scope_category_id?: number | null;
  scope_category_name?: string | null;
  status: 'open' | 'closed' | string;
  opened_by: number;
  opened_by_name?: string | null;
  opened_at: string;
  closed_at?: string | null;
  variance_count: number;
  variance_value: number;
  expected_count: number;
  scanned_count: number;
  notes?: string | null;
  lines: AuditLine[];
}

/** "STORE-B · Passives" — what a count covered, in one line. */
export const auditScopeOf = (s: Pick<AuditSession, 'scope_location_code' | 'scope_category_name'>): string =>
  [s.scope_location_code, s.scope_category_name].filter(Boolean).join(' · ') || 'Unscoped';

export interface AuditCloseResult {
  session_id: number;
  variances: number;
  variance_value: number;
  /** Tags that were not located for a second consecutive count. */
  marked_lost: string[];
}

/* ------------------------------- Reports ------------------------------ */

/**
 * by-holder, overdue and low-stock need the custodian; the rest admin/HR.
 * by-holder, overdue, idle and register take `domain`; for anyone short of
 * admin/HR the server pins by-holder and overdue to the lab.
 */
export type ReportName =
  | 'by-holder' | 'overdue' | 'low-stock' | 'project-cost'
  | 'client-hardware' | 'idle' | 'register';

export interface HolderReportRow {
  employee_id: number;
  employee_name: string;
  item_count: number;
  overdue_count: number;
  total_value: number;
  items: InventoryItem[];
}

export interface StockReportRow {
  item_id: number;
  tag: string;
  name: string;
  qty: number;
  min_qty?: number | null;
  location_code?: string | null;
}

export interface ProjectCostReportRow {
  project_id?: number | null;
  project_code?: string | null;
  project_name?: string | null;
  material_cost: number;
  line_count: number;
}

/* ------------------------------ The client ---------------------------- */

const list = <T>(data: unknown): T[] => (Array.isArray(data) ? (data as T[]) : []);

// Categories and locations change when somebody labels a new shelf, so they
// are fetched once and shared for the rest of the session. The promise itself
// is cached, so two screens mounting together make one request between them.
// Keyed by section ('' is both), so the lab and office pickers never mix.
const categoriesOnce = new Map<string, Promise<ItemCategory[]>>();
let locationsOnce: Promise<StoreLocation[]> | null = null;

export const inventoryService = {
  /* Items ------------------------------------------------------------- */

  listItems: async (filters?: ItemFilters): Promise<InventoryItem[]> => {
    const res = await apiClient.get<InventoryItem[]>(INV.ITEMS, { params: filters });
    return list<InventoryItem>(res.data);
  },

  /** The scan endpoint: item, permitted actions and held_by_me in one GET. */
  byTag: async (tag: string): Promise<ScanResult> => {
    const res = await apiClient.get<ScanResult>(INV.BY_TAG(tag));
    return res.data as ScanResult;
  },

  getItem: async (id: number): Promise<InventoryItem> => {
    const res = await apiClient.get<InventoryItem>(INV.ITEM(id));
    return res.data as InventoryItem;
  },

  /** Custodian. Omit `tag` and the system issues one from the category prefix. */
  createItem: async (body: ItemInput): Promise<InventoryItem> => {
    const res = await apiClient.post<InventoryItem>(INV.ITEMS, body);
    return res.data as InventoryItem;
  },

  /** Custodian. Status and location are what the movement log says they are,
   *  so neither can be set here. There is no delete — scrap it instead. */
  updateItem: async (id: number, body: Partial<ItemInput>): Promise<InventoryItem> => {
    const res = await apiClient.put<InventoryItem>(INV.ITEM(id), body);
    return res.data as InventoryItem;
  },

  movements: async (id: number): Promise<Movement[]> => {
    const res = await apiClient.get<Movement[]>(INV.MOVEMENTS(id));
    return list<Movement>(res.data);
  },

  /**
   * Every custody action — checkout, checkin, transfer, scrap, write-off, all
   * seventeen — is this one call, and every one returns the updated item.
   * `verb` comes from the item's own `actions[]`, never from a role check.
   */
  act: async (id: number, verb: string, body: Record<string, unknown> = {}): Promise<InventoryItem> => {
    const res = await apiClient.post<InventoryItem>(INV.ACTION(id, actionVerb(verb)), body);
    return res.data as InventoryItem;
  },

  /* Holdings ---------------------------------------------------------- */

  myHoldings: async (): Promise<InventoryItem[]> => {
    const res = await apiClient.get<InventoryItem[]>(INV.ME_HOLDINGS);
    return list<InventoryItem>(res.data);
  },

  /** Visible to that person, their line manager and the custodian. */
  employeeHoldings: async (employeeId: number): Promise<InventoryItem[]> => {
    const res = await apiClient.get<InventoryItem[]>(INV.EMPLOYEE_HOLDINGS(employeeId));
    return list<InventoryItem>(res.data);
  },

  /* Reference data ---------------------------------------------------- */

  /** Pass a section to get only its categories; omit for both. */
  categories: (domain?: Domain): Promise<ItemCategory[]> => {
    const key = domain ?? '';
    let pending = categoriesOnce.get(key);
    if (!pending) {
      pending = apiClient
        .get<ItemCategory[]>(INV.CATEGORIES, { params: domain ? { domain } : undefined })
        .then((res) => list<ItemCategory>(res.data))
        .catch((err) => { categoriesOnce.delete(key); throw err; });
      categoriesOnce.set(key, pending);
    }
    return pending;
  },

  locations: (): Promise<StoreLocation[]> => {
    locationsOnce = locationsOnce ?? apiClient
      .get<StoreLocation[]>(INV.LOCATIONS)
      .then((res) => list<StoreLocation>(res.data))
      .catch((err) => { locationsOnce = null; throw err; });
    return locationsOnce;
  },

  /** Call after a custodian adds a shelf or a category, so the next read is fresh. */
  forgetReferenceData: () => { categoriesOnce.clear(); locationsOnce = null; },

  /* Requests ---------------------------------------------------------- */

  /** Only the caller's own unless they are a manager or custodian — the
   *  server filters, so the client does not. */
  listRequests: async (params?: { status?: RequestStatus; mine?: boolean }): Promise<StockRequest[]> => {
    const res = await apiClient.get<StockRequest[]>(INV.REQUESTS, { params });
    return list<StockRequest>(res.data);
  },

  createRequest: async (body: RequestInput): Promise<StockRequest> => {
    const res = await apiClient.post<StockRequest>(INV.REQUESTS, body);
    return res.data as StockRequest;
  },

  decideRequest: async (id: number, body: { approved: boolean; reason?: string }): Promise<StockRequest> => {
    const res = await apiClient.post<StockRequest>(INV.REQUEST_DECISION(id), body);
    return res.data as StockRequest;
  },

  /** Custodian. A request naming a category rather than an item needs one picked. */
  issueRequest: async (id: number, body: {
    item_id?: number; qty?: number; expected_return_date?: string;
  }): Promise<StockRequest> => {
    const res = await apiClient.post<StockRequest>(INV.REQUEST_ISSUE(id), body);
    return res.data as StockRequest;
  },

  cancelRequest: async (id: number): Promise<StockRequest> => {
    const res = await apiClient.post<StockRequest>(INV.REQUEST_CANCEL(id));
    return res.data as StockRequest;
  },

  /* Audit (custodian) ------------------------------------------------- */

  auditSessions: async (params?: { status?: 'open' | 'closed' }): Promise<AuditSession[]> => {
    const res = await apiClient.get<AuditSession[]>(INV.AUDIT_SESSIONS, { params });
    return list<AuditSession>(res.data);
  },

  /** One of location_id / category_id is required. The session comes back
   *  with its `lines` already written — the list of what should be there. */
  openAuditSession: async (body: { location_id?: number; category_id?: number; label?: string }): Promise<AuditSession> => {
    const res = await apiClient.post<AuditSession>(INV.AUDIT_SESSIONS, body);
    return res.data as AuditSession;
  },

  auditSession: async (id: number): Promise<AuditSession> => {
    const res = await apiClient.get<AuditSession>(INV.AUDIT_SESSION(id));
    return res.data as AuditSession;
  },

  /** Returns the one line the scan touched — not the session. A tag that was
   *  not expected comes back as a new line with a zero expectation. */
  auditScan: async (id: number, body: { tag: string; counted_qty?: number; note?: string }): Promise<AuditLine> => {
    const res = await apiClient.post<AuditLine>(INV.AUDIT_SCAN(id), body);
    return res.data as AuditLine;
  },

  closeAuditSession: async (id: number, body?: { notes?: string }): Promise<AuditCloseResult> => {
    const res = await apiClient.post<AuditCloseResult>(INV.AUDIT_CLOSE(id), body ?? {});
    return res.data as AuditCloseResult;
  },

  /* Reports ----------------------------------------------------------- */

  report: async <T>(name: ReportName, params?: Record<string, unknown>): Promise<T[]> => {
    const res = await apiClient.get<T[]>(INV.REPORT(name), { params });
    return list<T>(res.data);
  },
};
