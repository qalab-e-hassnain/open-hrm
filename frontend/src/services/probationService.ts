import { apiClient } from './api';
import { API_ENDPOINTS } from '../config/constants';

export type ProbationStatus =
  | 'in_probation'
  | 'confirmed'
  | 'extended'
  /** Never meant to apply — a contractor, or someone on payroll only. */
  | 'not_applicable';

export type ProbationDecision = 'confirm' | 'extend' | 'not_confirmed';

export interface ProbationReview {
  id: number;
  employee_id: number;
  period_end: string;
  decision: ProbationDecision;
  new_end_date?: string | null;
  comments?: string | null;
  decided_at: string;
  decided_by: number;
  reviewer_name?: string | null;
  /** The confirmation letter, when one was issued. */
  document_id?: number | null;
}

export interface Probation {
  employee_id: number;
  employee_name: string;
  designation?: string | null;
  department?: string | null;
  date_of_joining?: string | null;
  manager_id?: number | null;
  manager_name?: string | null;
  probation_end_date?: string | null;
  status: ProbationStatus;
  days_remaining?: number | null;
  is_overdue: boolean;
  /**
   * Whether THIS viewer may decide it. The server computes it for the caller
   * — gate the buttons on it rather than re-deriving "am I their manager or
   * HR", which is how you get a button that always returns 403.
   */
  can_decide: boolean;
  reviews: ProbationReview[];
}

export const PROBATION_STATUS_LABEL: Record<ProbationStatus, string> = {
  in_probation: 'On probation',
  confirmed: 'Confirmed',
  extended: 'Extended',
  not_applicable: 'Not applicable',
};

export const DECISION_LABEL: Record<ProbationDecision, string> = {
  confirm: 'Confirm',
  extend: 'Extend',
  not_confirmed: 'Not confirmed',
};

export interface DecisionPayload {
  decision: ProbationDecision;
  comments?: string;
  /** Required when extending, and must be later than the current end date. */
  new_end_date?: string;
  /** Confirmation takes effect on the probation end date unless stated. */
  effective_date?: string;
  /** A confirmation letter through the document engine. Default true. */
  issue_letter?: boolean;
}

export interface BulkConfirmPayload {
  /** Defaults server-side to a year ago. */
  ended_before?: string;
  employee_ids?: number[];
  mark_not_applicable?: number[];
  note?: string;
}

export const probationService = {
  /** The queue. A manager sees their own team; HR sees everyone. */
  getDue: async (withinDays = 14): Promise<Probation[]> => {
    const res = await apiClient.get<Probation[]>(API_ENDPOINTS.PROBATION_DUE, {
      params: { within_days: withinDays },
    });
    return Array.isArray(res.data) ? res.data : [];
  },

  getMine: async (): Promise<Probation> => {
    const res = await apiClient.get<Probation>(API_ENDPOINTS.PROBATION_ME);
    return res.data as Probation;
  },

  getFor: async (employeeId: number): Promise<Probation> => {
    const res = await apiClient.get<Probation>(API_ENDPOINTS.PROBATION_EMPLOYEE(employeeId));
    return res.data as Probation;
  },

  decide: async (employeeId: number, payload: DecisionPayload): Promise<Probation> => {
    const res = await apiClient.post<Probation>(
      API_ENDPOINTS.PROBATION_DECIDE(employeeId), payload,
    );
    return res.data as Probation;
  },

  /** Fix the end date, or mark it not applicable. Not a decision. */
  setup: async (
    employeeId: number,
    payload: { probation_end_date?: string; probation_status?: ProbationStatus },
  ): Promise<Probation> => {
    const res = await apiClient.put<Probation>(
      API_ENDPOINTS.PROBATION_SETUP(employeeId), payload,
    );
    return res.data as Probation;
  },

  /**
   * Clears the historical backlog in one action.
   *
   * Turning probation on flags everyone at once, because nobody was ever
   * formally confirmed. This confirms anyone whose probation closed before
   * the cutoff and leaves genuine probationers alone whatever is passed.
   * It issues no letters: a confirmation letter dated today for somebody
   * confirmed in practice years ago would be a fiction.
   */
  bulkConfirm: async (payload: BulkConfirmPayload = {}): Promise<Probation[]> => {
    const res = await apiClient.post<Probation[]>(
      API_ENDPOINTS.PROBATION_BULK_CONFIRM, payload,
    );
    return Array.isArray(res.data) ? res.data : [];
  },
};
