import { apiClient } from './api';
import { API_ENDPOINTS } from '../config/constants';

/** An advance is this month's salary paid early and recovered next month; a
 *  loan is money lent and recovered over several. They deduct on different
 *  payslip lines, which is why the distinction is stored. */
export type LoanKind = 'advance' | 'loan';

export type LoanStatus =
  | 'requested'
  | 'approved'    // agreed, not yet paid out — nothing is deducted
  | 'disbursed'   // money paid, schedule running
  | 'settled'
  | 'rejected'
  | 'cancelled';

export type InstalmentStatus = 'pending' | 'recovered' | 'waived';

export interface Instalment {
  id: number;
  sequence: number;
  /** "YYYY-MM" */
  due_month: string;
  amount: number;
  status: InstalmentStatus;
  payroll_id?: number | null;
  recovered_at?: string | null;
  waived_reason?: string | null;
}

export interface Loan {
  id: number;
  employee_id: number;
  employee_name?: string | null;
  kind: LoanKind;
  principal: number;
  reason: string;
  status: LoanStatus;
  requested_at: string;
  approved_at?: string | null;
  decision_note?: string | null;
  disbursed_on?: string | null;
  instalment_amount?: number | null;
  instalment_count?: number | null;
  first_recovery_month?: string | null;
  settled_at?: string | null;
  /** Zero until disbursed — approved is not paid. */
  outstanding: number;
  recovered: number;
  instalments: Instalment[];
}

export const LOAN_KIND_LABEL: Record<LoanKind, string> = {
  advance: 'Salary advance',
  loan: 'Loan',
};

export const LOAN_STATUS_LABEL: Record<LoanStatus, string> = {
  requested: 'Awaiting a decision',
  approved: 'Approved, not yet paid',
  disbursed: 'Being recovered',
  settled: 'Settled',
  rejected: 'Rejected',
  cancelled: 'Withdrawn',
};

export const INSTALMENT_STATUS_LABEL: Record<InstalmentStatus, string> = {
  pending: 'Due', recovered: 'Recovered', waived: 'Waived',
};

export const loanService = {
  request: async (body: {
    kind: LoanKind; principal: number; reason: string;
    instalment_count: number; employee_id?: number;
  }): Promise<Loan> => {
    const res = await apiClient.post<Loan>(API_ENDPOINTS.LOANS, body);
    return res.data as Loan;
  },

  list: async (params?: { status?: LoanStatus; employee_id?: number }): Promise<Loan[]> => {
    const res = await apiClient.get<Loan[]>(API_ENDPOINTS.LOANS, { params });
    return Array.isArray(res.data) ? res.data : [];
  },

  mine: async (): Promise<Loan[]> => {
    const res = await apiClient.get<Loan[]>(API_ENDPOINTS.LOANS_ME);
    return Array.isArray(res.data) ? res.data : [];
  },

  /** The approver may agree different terms from the ones asked for.
   *  Never your own. */
  decide: async (id: number, body: {
    approved: boolean; note?: string; principal?: number; instalment_count?: number;
  }): Promise<Loan> => {
    const res = await apiClient.put<Loan>(API_ENDPOINTS.LOAN_DECIDE(id), body);
    return res.data as Loan;
  },

  /** Builds the schedule. Nothing is recovered before this. */
  disburse: async (id: number, body: {
    disbursed_on?: string; first_recovery_month?: string;
  }): Promise<Loan> => {
    const res = await apiClient.post<Loan>(API_ENDPOINTS.LOAN_DISBURSE(id), body);
    return res.data as Loan;
  },

  waive: async (id: number, instalmentId: number, reason: string): Promise<Loan> => {
    const res = await apiClient.put<Loan>(
      API_ENDPOINTS.LOAN_INSTALMENT_WAIVE(id, instalmentId), { reason },
    );
    return res.data as Loan;
  },

  /** Withdraw before disbursement. 400 afterwards — the money moved. */
  withdraw: async (id: number): Promise<void> => {
    await apiClient.delete(API_ENDPOINTS.LOAN(id));
  },
};
