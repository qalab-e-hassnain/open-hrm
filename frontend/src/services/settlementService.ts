import { apiClient } from './api';
import { API_ENDPOINTS } from '../config/constants';

export type SettlementStatus = 'draft' | 'approved' | 'paid';

/** A rendered line. The server filters these to non-zero, so an employee
 *  with no gratuity does not read a page of zeroes. */
export interface SettlementLine {
  key: string;
  label: string;
  amount: number;
  detail?: string | null;
}

export interface Settlement {
  id: number;
  resignation_id: number;
  employee_id: number;
  employee_name?: string | null;
  designation?: string | null;
  status: SettlementStatus;
  last_working_day: string;
  computed_at: string;
  monthly_gross: number;

  final_month_days: number;
  final_month_total_days: number;
  final_month_amount: number;
  leave_encashment_days: number;
  leave_encashment_amount: number;
  gratuity_years: number;
  gratuity_amount: number;
  pending_reimbursements: number;
  other_earning: number;
  other_earning_note?: string | null;

  notice_shortfall_days: number;
  notice_shortfall_amount: number;
  loan_outstanding: number;
  asset_recovery: number;
  tax_deduction: number;
  other_deduction: number;
  other_deduction_note?: string | null;

  gross_payable: number;
  total_deductions: number;
  net_payable: number;
  /** Lines HR overrode. A recompute leaves these alone, so mark them. */
  manual_fields: string[];
  notes?: string | null;
  approved_at?: string | null;
  paid_at?: string | null;
  payment_reference?: string | null;

  earnings: SettlementLine[];
  deductions: SettlementLine[];
  /** The rules that produced these numbers — print it at the foot. */
  policy: Record<string, unknown>;
}

export const SETTLEMENT_STATUS_LABEL: Record<SettlementStatus, string> = {
  draft: 'Draft',
  approved: 'Approved',
  paid: 'Paid',
};

export type SettlementEdit = Partial<{
  final_month_amount: number;
  leave_encashment_days: number;
  leave_encashment_amount: number;
  gratuity_amount: number;
  pending_reimbursements: number;
  other_earning: number;
  other_earning_note: string;
  notice_shortfall_amount: number;
  loan_outstanding: number;
  asset_recovery: number;
  tax_deduction: number;
  other_deduction: number;
  other_deduction_note: string;
  notes: string;
}>;

export const settlementService = {
  /** Compute the draft, or refresh one. Safe to call again — hand-edited
   *  lines survive a recompute. */
  compute: async (resignationId: number): Promise<Settlement> => {
    const res = await apiClient.post<Settlement>(
      API_ENDPOINTS.SETTLEMENT_FOR_RESIGNATION(resignationId), {},
    );
    return res.data as Settlement;
  },

  /** `null` when none has been drafted yet — not an error. */
  forResignation: async (resignationId: number): Promise<Settlement | null> => {
    const res = await apiClient.get<Settlement | null>(
      API_ENDPOINTS.SETTLEMENT_FOR_RESIGNATION(resignationId),
    );
    return (res.data as Settlement | null) ?? null;
  },

  list: async (status?: SettlementStatus): Promise<Settlement[]> => {
    const res = await apiClient.get<Settlement[]>(API_ENDPOINTS.SETTLEMENTS, {
      params: status ? { status_filter: status } : undefined,
    });
    return Array.isArray(res.data) ? res.data : [];
  },

  /** The leaver's own copy. `null` until approved — a draft is never
   *  shown to them. */
  mine: async (): Promise<Settlement | null> => {
    const res = await apiClient.get<Settlement | null>(API_ENDPOINTS.SETTLEMENT_ME);
    return (res.data as Settlement | null) ?? null;
  },

  update: async (id: number, body: SettlementEdit): Promise<Settlement> => {
    const res = await apiClient.put<Settlement>(API_ENDPOINTS.SETTLEMENT(id), body);
    return res.data as Settlement;
  },

  /** Not your own. A negative net needs `notes` first. */
  approve: async (id: number): Promise<Settlement> => {
    const res = await apiClient.post<Settlement>(API_ENDPOINTS.SETTLEMENT_APPROVE(id), {});
    return res.data as Settlement;
  },

  reopen: async (id: number): Promise<Settlement> => {
    const res = await apiClient.post<Settlement>(API_ENDPOINTS.SETTLEMENT_REOPEN(id), {});
    return res.data as Settlement;
  },

  markPaid: async (
    id: number, body: { payment_reference?: string; paid_on?: string },
  ): Promise<Settlement> => {
    const res = await apiClient.post<Settlement>(API_ENDPOINTS.SETTLEMENT_MARK_PAID(id), body);
    return res.data as Settlement;
  },
};
