import axios from 'axios';
import { apiClient } from './api';
import { API_ENDPOINTS, API_BASE_URL } from '../config/constants';

/* ----------------------------- Documents ------------------------------ */

export type DocumentStatus = 'issued' | 'viewed' | 'signed' | 'declined' | 'void';

export interface DocumentSummary {
  id: number;
  employee_id: number;
  employee_name?: string | null;
  kind: string;
  title: string;
  status: DocumentStatus;
  requires_signature: boolean;
  issued_at: string;
  due_at?: string | null;
  viewed_at?: string | null;
}

export interface SignatureInfo {
  typed_name: string;
  signed_at: string;
  ip_address?: string | null;
  hash_at_signing: string;
  /** The exact wording the signer was shown when they agreed. */
  intent_statement?: string | null;
}

export interface DocumentDetail extends DocumentSummary {
  body_html: string;
  content_hash: string;
  verification_code?: string | null;
  signature?: SignatureInfo | null;
  /**
   * False means the stored document no longer matches its hash. Never render
   * such a document as if it were valid — it has been altered since issue.
   */
  integrity_ok: boolean;
}

export interface DocumentVerification {
  valid: boolean;
  title?: string | null;
  kind?: string | null;
  employee_name?: string | null;
  signed_at?: string | null;
  content_hash?: string | null;
  verified_at?: string | null;
}

/* ---------------------------- Onboarding ------------------------------ */

export type TaskStatus = 'pending' | 'completed' | 'waived';
export type TaskKind = 'document' | 'data' | 'action';

export interface OnboardingTask {
  id: number;
  kind: TaskKind;
  label: string;
  detail?: string | null;
  status: TaskStatus;
  /** Present on document tasks — the document that completes this one. */
  employee_document_id?: number | null;
  completed_at?: string | null;
}

export interface OnboardingPacket {
  id: number;
  employee_id: number;
  employee_name?: string | null;
  started_at: string;
  completed_at?: string | null;
  tasks: OnboardingTask[];
  total: number;
  done: number;
  percent_complete: number;
}

/* ---------------------------- Offboarding ----------------------------- */

export type ResignationStatus =
  | 'submitted'
  | 'manager_approved'
  | 'ceo_approved'
  | 'cleared'
  | 'completed'
  | 'rejected'
  | 'withdrawn';

export interface ClearanceItem {
  id: number;
  category: string;
  label: string;
  reference?: string | null;
  status: TaskStatus;
  notes?: string | null;
  cleared_at?: string | null;
}

export interface Resignation {
  id: number;
  employee_id: number;
  employee_name?: string | null;
  submitted_at: string;
  reason: string;
  notice_days: number;
  proposed_last_working_day: string;
  agreed_last_working_day?: string | null;
  status: ResignationStatus;
  manager_note?: string | null;
  ceo_note?: string | null;
  rejection_reason?: string | null;
  completed_at?: string | null;
  exit_pack_sent_at?: string | null;
  clearance_items: ClearanceItem[];
  /** HR cannot clear while this is above zero. */
  clearance_outstanding: number;
  /** Whether THIS viewer may decide it now — never re-derive from roles. */
  can_decide: boolean;
  next_approver?: string | null;
  days_remaining?: number | null;
}

export const RESIGNATION_STATUS_LABEL: Record<ResignationStatus, string> = {
  submitted: 'Awaiting line manager',
  manager_approved: 'Awaiting administrator',
  ceo_approved: 'HR clearance in progress',
  cleared: 'Cleared — awaiting last working day',
  completed: 'Completed',
  rejected: 'Rejected',
  withdrawn: 'Withdrawn',
};

/** Still open, and so still withdrawable by the employee. */
export const isResignationOpen = (status: ResignationStatus) =>
  status === 'submitted' || status === 'manager_approved' || status === 'ceo_approved';

/** The one stage that may set the agreed last working day. */
export const isAdminStage = (status: ResignationStatus) => status === 'manager_approved';

/* ------------------------ Job profiles & templates --------------------- */

export interface JobProfile {
  id: number;
  designation: string;
  department?: string | null;
  reports_to_title?: string | null;
  summary?: string | null;
  /** Rendered as the bullet list in Appendix A. */
  responsibilities: string[];
  /** Rendered as the bullet list in Appendix B. */
  kpis: string[];
  is_active: boolean;
  /** How many people currently hold this designation. */
  employee_count: number;
}

export interface DocumentTemplate {
  id: number;
  kind: string;
  title: string;
  version: number;
  body_html: string;
  requires_signature: boolean;
  include_in_onboarding: boolean;
  is_active: boolean;
  created_at: string;
}

export interface TemplatePreview {
  body_html: string;
  /**
   * Fields this employee's record cannot fill. A contract issued with any of
   * these goes out with a blank in it, so issuing is blocked while non-empty.
   */
  missing_placeholders: string[];
}

/** The seeded text every unfilled profile still carries. */
export interface PlaceholderItem {
  key: string;
  description: string;
  /** A worked value from a real employee, not a description of one. */
  example: string;
}

export interface PlaceholderCatalogue {
  sample_employee?: string | null;
  groups: { name: string; placeholders: PlaceholderItem[] }[];
}

export interface TemplateStarter {
  key: string;
  kind: string;
  title: string;
  requires_signature: boolean;
  /** A caution the author needs before writing, where one applies. */
  note?: string | null;
  body_html: string;
}

export const PROFILE_PLACEHOLDER = 'To be completed by HR.';

/** True while a profile is still the seeded stub rather than real content. */
export const isProfileUnfilled = (profile: JobProfile) =>
  !profile.responsibilities.length
  || profile.responsibilities.some((line) => line.trim() === PROFILE_PLACEHOLDER)
  || (profile.summary || '').trim() === PROFILE_PLACEHOLDER;

export const lifecycleService = {
  /* -- job profiles & templates (admin/HR) -- */
  /**
   * Start a profile for a designation that has none.
   *
   * The list only ever showed existing rows, so a role nobody had written up
   * — which is every role created since — was invisible on the page meant for
   * writing them up. Created empty on purpose: the draft comes from the
   * Suggest button, which the person reviews before saving.
   */
  createJobProfile: async (designation: string): Promise<JobProfile> => {
    const res = await apiClient.post<JobProfile>(API_ENDPOINTS.JOB_PROFILES, {
      designation,
      responsibilities: [],
      kpis: [],
    });
    return res.data as JobProfile;
  },

  jobProfiles: async (): Promise<JobProfile[]> => {
    const res = await apiClient.get<JobProfile[]>(API_ENDPOINTS.JOB_PROFILES);
    return res.data || [];
  },

  updateJobProfile: async (
    id: number,
    body: Partial<Pick<JobProfile,
      'department' | 'reports_to_title' | 'summary' | 'responsibilities' | 'kpis' | 'is_active'>>,
  ): Promise<JobProfile> => {
    const res = await apiClient.put<JobProfile>(API_ENDPOINTS.JOB_PROFILE(id), body);
    return res.data as JobProfile;
  },

  /**
   * Every placeholder a template may use, grouped, each with a worked value.
   * Served from the same context builder the renderer uses, so it cannot drift
   * from what actually substitutes — never hardcode these keys here.
   */
  placeholders: async (employeeId?: number): Promise<PlaceholderCatalogue> => {
    const res = await apiClient.get<PlaceholderCatalogue>(API_ENDPOINTS.DOC_PLACEHOLDERS, {
      params: employeeId ? { employee_id: employeeId } : undefined,
    });
    return (res.data as PlaceholderCatalogue) ?? { groups: [] };
  },

  /** Skeletons to start from. Deliberately not finished legal text. */
  starters: async (): Promise<TemplateStarter[]> => {
    const res = await apiClient.get<TemplateStarter[]>(API_ENDPOINTS.DOC_STARTERS);
    return res.data || [];
  },

  createTemplate: async (body: {
    kind: string;
    title: string;
    body_html: string;
    requires_signature: boolean;
    include_in_onboarding: boolean;
    is_active?: boolean;
  }): Promise<DocumentTemplate> => {
    const res = await apiClient.post<DocumentTemplate>(API_ENDPOINTS.DOC_TEMPLATES, body);
    return res.data as DocumentTemplate;
  },

  templates: async (includeInactive = false): Promise<DocumentTemplate[]> => {
    const res = await apiClient.get<DocumentTemplate[]>(API_ENDPOINTS.DOC_TEMPLATES, {
      params: includeInactive ? { include_inactive: true } : undefined,
    });
    return res.data || [];
  },

  /**
   * Editing publishes a NEW version and retires the old one — the response
   * carries a new id and an incremented version, and documents already issued
   * keep the text they were issued with.
   */
  updateTemplate: async (
    id: number,
    body: Partial<Pick<DocumentTemplate,
      'title' | 'body_html' | 'requires_signature' | 'include_in_onboarding'>>,
  ): Promise<DocumentTemplate> => {
    const res = await apiClient.put<DocumentTemplate>(API_ENDPOINTS.DOC_TEMPLATE(id), body);
    return res.data as DocumentTemplate;
  },

  /** Render a template against one employee before committing to issue it. */
  previewTemplate: async (templateId: number, employeeId: number): Promise<TemplatePreview> => {
    const res = await apiClient.get<TemplatePreview>(
      API_ENDPOINTS.DOC_TEMPLATE_PREVIEW(templateId, employeeId),
    );
    return res.data as TemplatePreview;
  },

  issueDocument: async (body: {
    employee_id: number;
    template_id: number;
    due_at?: string;
  }): Promise<DocumentDetail> => {
    const res = await apiClient.post<DocumentDetail>(API_ENDPOINTS.DOCUMENT_ISSUE, body);
    return res.data as DocumentDetail;
  },

  /* -- documents -- */
  myDocuments: async (): Promise<DocumentSummary[]> => {
    const res = await apiClient.get<DocumentSummary[]>(API_ENDPOINTS.DOCUMENTS_ME);
    return res.data || [];
  },

  /** Fetching the full document also marks it viewed, server-side. */
  getDocument: async (id: number): Promise<DocumentDetail> => {
    const res = await apiClient.get<DocumentDetail>(API_ENDPOINTS.DOCUMENT_BY_ID(id));
    return res.data as DocumentDetail;
  },

  signDocument: async (
    id: number,
    body: { typed_name: string; agreed: boolean; intent_statement: string },
  ): Promise<DocumentDetail> => {
    const res = await apiClient.post<DocumentDetail>(API_ENDPOINTS.DOCUMENT_SIGN(id), body);
    return res.data as DocumentDetail;
  },

  declineDocument: async (id: number, reason: string): Promise<DocumentDetail> => {
    const res = await apiClient.post<DocumentDetail>(API_ENDPOINTS.DOCUMENT_DECLINE(id), { reason });
    return res.data as DocumentDetail;
  },

  /**
   * Public: confirm a signed document. Plain axios rather than the shared
   * client, which attaches a token and forces a logout on 401 — neither suits
   * a third party with no account here.
   */
  verifyDocument: async (code: string): Promise<DocumentVerification> => {
    const res = await axios.get<DocumentVerification>(
      `${API_BASE_URL}${API_ENDPOINTS.DOCUMENT_VERIFY(encodeURIComponent(code))}`,
    );
    return res.data;
  },

  /* -- onboarding -- */
  /** Returns null (not a 404) when the employee has no packet. */
  myOnboarding: async (): Promise<OnboardingPacket | null> => {
    const res = await apiClient.get<OnboardingPacket | null>(API_ENDPOINTS.ONBOARDING_ME);
    return res.data ?? null;
  },

  /** Every packet — admin and HR only; the server enforces that. */
  allOnboarding: async (): Promise<OnboardingPacket[]> => {
    const res = await apiClient.get<OnboardingPacket[]>(API_ENDPOINTS.ONBOARDING_ALL);
    return res.data || [];
  },

  updateTask: async (
    taskId: number,
    body: { status: TaskStatus },
  ): Promise<OnboardingPacket> => {
    const res = await apiClient.put<OnboardingPacket>(API_ENDPOINTS.ONBOARDING_TASK(taskId), body);
    return res.data as OnboardingPacket;
  },

  /* -- offboarding -- */
  myResignation: async (): Promise<Resignation | null> => {
    const res = await apiClient.get<Resignation | null>(API_ENDPOINTS.RESIGNATION_ME);
    return res.data ?? null;
  },

  /** Everything the caller may see — the server filters, not the client. */
  resignations: async (): Promise<Resignation[]> => {
    const res = await apiClient.get<Resignation[]>(API_ENDPOINTS.RESIGNATIONS);
    return res.data || [];
  },

  resign: async (body: {
    reason: string;
    proposed_last_working_day?: string;
  }): Promise<Resignation> => {
    const res = await apiClient.post<Resignation>(API_ENDPOINTS.RESIGNATIONS, body);
    return res.data as Resignation;
  },

  /**
   * One endpoint for all three stages — the server decides which one this
   * caller is completing. `agreed_last_working_day` is only read at the
   * administrator stage and ignored elsewhere.
   */
  decide: async (
    id: number,
    body: {
      approved: boolean;
      note?: string;
      agreed_last_working_day?: string;
      rejection_reason?: string;
    },
  ): Promise<Resignation> => {
    const res = await apiClient.put<Resignation>(API_ENDPOINTS.RESIGNATION_DECIDE(id), body);
    return res.data as Resignation;
  },

  withdraw: async (id: number): Promise<Resignation> => {
    const res = await apiClient.put<Resignation>(API_ENDPOINTS.RESIGNATION_WITHDRAW(id), {});
    return res.data as Resignation;
  },

  updateClearance: async (
    itemId: number,
    body: { status: TaskStatus; notes?: string; reference?: string },
  ): Promise<Resignation> => {
    const res = await apiClient.put<Resignation>(API_ENDPOINTS.CLEARANCE_ITEM(itemId), body);
    return res.data as Resignation;
  },

  /** Irreversible: issues the NOC, emails the exit pack, closes the account. */
  completeExit: async (id: number): Promise<Resignation> => {
    const res = await apiClient.post<Resignation>(API_ENDPOINTS.RESIGNATION_COMPLETE(id), {});
    return res.data as Resignation;
  },

  /** Open an onboarding packet for a new joiner. Admin/HR on the server. */
  startOnboarding: async (employeeId: string | number) => {
    const res = await apiClient.post<unknown>(API_ENDPOINTS.ONBOARDING_START, {
      employee_id: Number(employeeId),
    });
    return res.data;
  },

  /** One person's packet. Your own, or anybody's with admin/HR reach. */
  getOnboardingFor: async (employeeId: string | number) => {
    const res = await apiClient.get<unknown>(API_ENDPOINTS.ONBOARDING_FOR(employeeId));
    return res.data;
  },
};
