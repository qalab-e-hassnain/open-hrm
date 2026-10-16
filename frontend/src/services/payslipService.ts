import axios from 'axios';
import { apiClient } from './api';
import { API_ENDPOINTS, API_BASE_URL } from '../config/constants';
import { type Payslip, type PayslipItem } from '../types';

const MONTH_NAMES = [
  'January', 'February', 'March', 'April', 'May', 'June',
  'July', 'August', 'September', 'October', 'November', 'December',
];

export const MONTH_LABELS = MONTH_NAMES;

// ---------------------------------------------------------------------------
// Types mirroring app/schemas/payslip.py. The backend assembles the document —
// named lines, totals and identity — so the page renders it rather than
// deriving salary figures in the browser.
// ---------------------------------------------------------------------------

export type PayslipStatus =
  | 'draft' | 'processing' | 'processed' | 'paid' | 'corrected' | 'cancelled';

export interface PayslipLine {
  key: string;
  label: string;
  amount: number;
  taxable: boolean;
  note?: string | null;
  formula?: string | null;
}

export interface PayslipCompany { name: string; reg_no: string; address: string }

export interface PayslipEmployeeInfo {
  name: string;
  employee_id?: string | null;
  department?: string | null;
  designation?: string | null;
  cnic?: string | null;
  date_of_joining?: string | null;
  bank_account_masked?: string | null;
  bank_name?: string | null;
}

export interface PayslipReconciliation {
  gross_earnings: number;
  total_deductions: number;
  expected_net: number;
  net_salary: number;
  balanced: boolean;
}

export interface PayslipRevision {
  id: number;
  payslip_no?: string | null;
  version: number;
  status: PayslipStatus;
  generated_at?: string | null;
  correction_reason?: string | null;
}

export interface PayslipDocument {
  id: number;
  employee_id: number;
  payslip_no?: string | null;
  verification_code?: string | null;
  version: number;
  generated_at?: string | null;
  status: PayslipStatus;
  is_historical: boolean;
  month: number;
  year: number;
  period_label: string;
  period_start: string;
  period_end: string;
  payment_date?: string | null;
  payment_method?: string | null;
  payment_reference?: string | null;
  currency: string;
  company: PayslipCompany;
  employee: PayslipEmployeeInfo;
  earnings: PayslipLine[];
  deductions: PayslipLine[];
  gross_earnings: number;
  total_deductions: number;
  // Basic pay less the approved unpaid-leave deduction — the base income tax is charged on.
  taxable_income: number;
  net_salary: number;
  reconciliation: PayslipReconciliation;
  supersedes?: PayslipRevision | null;
  superseded_by?: PayslipRevision | null;
  correction_reason?: string | null;
  cancellation_reason?: string | null;
  open_dispute_count: number;
}

export interface PayslipSummary {
  id: number;
  month: number;
  year: number;
  month_label: string;
  status: PayslipStatus;
  net_salary: number;
  payslip_no?: string | null;
  payment_date?: string | null;
}

export interface PayslipYTD {
  year: number;
  currency: string;
  gross_earnings: number;
  total_deductions: number;
  taxable_income: number;
  tax_paid: number;
  provident_fund: number;
  overtime_total: number;
  bonus_total: number;
  net_paid: number;
  months_counted: number;
  previous_month_net?: number | null;
}

export type DisputeCategory =
  | 'incorrect_salary' | 'missing_allowance' | 'unexpected_deduction'
  | 'tax_issue' | 'bank_payment_issue' | 'other';

export type DisputeStatus = 'open' | 'in_review' | 'resolved' | 'rejected';

export interface PayslipDispute {
  id: number;
  payroll_id: number;
  employee_id: number;
  employee_name?: string | null;
  payslip_no?: string | null;
  period_label?: string | null;
  category: DisputeCategory;
  comment: string;
  attachment_url?: string | null;
  status: DisputeStatus;
  response?: string | null;
  responder_name?: string | null;
  responded_at?: string | null;
  created_at: string;
}

export const DISPUTE_CATEGORIES: { value: DisputeCategory; label: string }[] = [
  { value: 'incorrect_salary', label: 'Incorrect salary' },
  { value: 'missing_allowance', label: 'Missing allowance' },
  { value: 'unexpected_deduction', label: 'Unexpected deduction' },
  { value: 'tax_issue', label: 'Tax issue' },
  { value: 'bank_payment_issue', label: 'Bank payment issue' },
  { value: 'other', label: 'Other' },
];

// A period query only names an employee when an admin is viewing someone else;
// omitting it lets the backend pin the request to the caller.
const scoped = (employeeId?: number) => (employeeId ? { employee_id: employeeId } : {});

// apiClient returns ApiResponse<T>, whose `data` is optional — so a successful
// call still has to assert the body actually arrived before using it.
const unwrap = <T>(response: { data?: T }, what: string): T => {
  if (response.data === undefined || response.data === null) {
    throw new Error(`The server returned no ${what}.`);
  }
  return response.data;
};

// api.ts's interceptor already rewrites error.message to the backend's
// `detail`, so that is the best message available; the raw body is only read
// when something threw before the interceptor could normalise it.
export const readApiError = (error: unknown, fallback: string): string => {
  const detail = (error as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail;
  if (typeof detail === 'string' && detail.trim()) return detail;
  if (Array.isArray(detail) && typeof detail[0]?.msg === 'string') return detail[0].msg;
  const message = (error as Error)?.message;
  if (message && !/^Request failed with status code/.test(message)) return message;
  return fallback;
};

export interface PayslipVerification {
  valid: boolean;
  payslip_no?: string | null;
  employee_name?: string | null;
  period_label?: string | null;
  status?: string | null;
  version?: number | null;
  generated_at?: string | null;
  net_salary?: number | null;
}

export const payslipService = {
  /** Public: confirm a payslip is genuine from its printed code. Returns only
   *  enough to prove authenticity — never the salary breakdown. */
  verify: async (code: string): Promise<PayslipVerification> => {
    const res = await axios.get<PayslipVerification>(
      `${API_BASE_URL}${API_ENDPOINTS.PAYSLIP_VERIFY(encodeURIComponent(code))}`,
    );
    return res.data;
  },

  listPayslips: async (year?: number, employeeId?: number): Promise<PayslipSummary[]> => {
    const response = await apiClient.get<PayslipSummary[]>(API_ENDPOINTS.PAYSLIP_LIST, {
      params: { ...(year ? { year } : {}), ...scoped(employeeId) },
    });
    return Array.isArray(response.data) ? response.data : [];
  },

  getPayslipForPeriod: async (
    year: number, month: number, employeeId?: number,
  ): Promise<PayslipDocument> => {
    const response = await apiClient.get<PayslipDocument>(API_ENDPOINTS.PAYSLIP_PERIOD, {
      params: { year, month, ...scoped(employeeId) },
    });
    return unwrap(response, 'payslip');
  },

  getPayslip: async (id: number): Promise<PayslipDocument> => {
    const response = await apiClient.get<PayslipDocument>(API_ENDPOINTS.PAYSLIP_DETAIL(id));
    return unwrap(response, 'payslip');
  },

  getYearToDate: async (
    year: number, uptoMonth: number, employeeId?: number,
  ): Promise<PayslipYTD> => {
    const response = await apiClient.get<PayslipYTD>(API_ENDPOINTS.PAYSLIP_YTD, {
      params: { year, upto_month: uptoMonth, ...scoped(employeeId) },
    });
    return unwrap(response, 'year-to-date totals');
  },

  getVersions: async (id: number): Promise<PayslipRevision[]> => {
    const response = await apiClient.get<PayslipRevision[]>(API_ENDPOINTS.PAYSLIP_VERSIONS(id));
    return Array.isArray(response.data) ? response.data : [];
  },

  emailPayslip: async (id: number): Promise<string> => {
    const response = await apiClient.post<{ sent_to: string }>(API_ENDPOINTS.PAYSLIP_EMAIL(id));
    return response.data?.sent_to || '';
  },

  // --- admin ---
  publish: async (id: number): Promise<PayslipDocument> => {
    const response = await apiClient.post<PayslipDocument>(API_ENDPOINTS.PAYSLIP_PUBLISH(id));
    return unwrap(response, 'payslip');
  },

  correct: async (id: number, reason: string): Promise<PayslipDocument> => {
    const response = await apiClient.post<PayslipDocument>(
      API_ENDPOINTS.PAYSLIP_CORRECT(id), { reason },
    );
    return unwrap(response, 'payslip');
  },

  cancel: async (id: number, reason: string): Promise<PayslipDocument> => {
    const response = await apiClient.post<PayslipDocument>(
      API_ENDPOINTS.PAYSLIP_CANCEL(id), { reason },
    );
    return unwrap(response, 'payslip');
  },

  // --- disputes ---
  raiseDispute: async (
    payslipId: number,
    payload: { category: DisputeCategory; comment: string; attachment_url?: string },
  ): Promise<PayslipDispute> => {
    const response = await apiClient.post<PayslipDispute>(
      API_ENDPOINTS.PAYSLIP_DISPUTES(payslipId), payload,
    );
    return unwrap(response, 'query');
  },

  listDisputes: async (): Promise<PayslipDispute[]> => {
    const response = await apiClient.get<PayslipDispute[]>(API_ENDPOINTS.PAYSLIP_DISPUTE_LIST);
    return Array.isArray(response.data) ? response.data : [];
  },

  respondToDispute: async (
    disputeId: number, status: DisputeStatus, response_text?: string,
  ): Promise<PayslipDispute> => {
    const response = await apiClient.put<PayslipDispute>(
      API_ENDPOINTS.PAYSLIP_DISPUTE_RESPOND(disputeId),
      { status, response: response_text },
    );
    return unwrap(response, 'query');
  },

  // --- legacy: raw payroll records, still used by older screens ---
  getPayslips: async (): Promise<Payslip[]> => {
    const response = await apiClient.get<RawPayrollRecord[]>(API_ENDPOINTS.PAYSLIPS);
    const records = Array.isArray(response.data) ? [...response.data] : [];
    records.sort((a, b) =>
      ((b.year || 0) - (a.year || 0)) || ((b.month || 0) - (a.month || 0)));
    return records.map(mapPayrollToPayslip);
  },
};

/** Raw payroll row as the legacy /payroll/payslips endpoint returns it. */
type RawPayrollRecord = {
  id?: number | string;
  employee_id?: number | string;
  base_salary?: number;
  allowances?: number;
  bonuses?: number;
  reimbursement_total?: number;
  overtime_pay?: number;
  deductions?: number;
  tax?: number;
  net_salary?: number;
  month?: number;
  year?: number;
};

// Legacy mapper kept for the older Payslip shape consumed elsewhere.
const mapPayrollToPayslip = (p: RawPayrollRecord): Payslip => {
  const base = p.base_salary || 0;
  const allowances = p.allowances || 0;
  const bonuses = p.bonuses || 0;
  const reimbursement = p.reimbursement_total || 0;
  const overtime = p.overtime_pay || 0;
  const deductions = p.deductions || 0;
  const tax = p.tax || 0;

  const items: PayslipItem[] = [
    { description: 'Basic Salary', gross: base, deduction: 0, net: base },
  ];
  if (allowances > 0) items.push({ description: 'Allowances', gross: allowances, deduction: 0, net: allowances });
  if (bonuses > 0) items.push({ description: 'Bonuses', gross: bonuses, deduction: 0, net: bonuses });
  if (reimbursement > 0) items.push({ description: 'Reimbursement', gross: reimbursement, deduction: 0, net: reimbursement });
  if (overtime > 0) items.push({ description: 'Overtime Pay', gross: overtime, deduction: 0, net: overtime });
  if (deductions > 0) items.push({ description: 'Deductions', gross: 0, deduction: -deductions, net: -deductions });
  if (tax > 0) items.push({ description: 'Income Tax', gross: 0, deduction: -tax, net: -tax });

  const totalGross = base + allowances + bonuses + reimbursement + overtime;
  const totalDeduction = -(deductions + tax);

  return {
    id: p.id?.toString() || '',
    employeeId: p.employee_id?.toString() || '',
    month: MONTH_NAMES[(p.month || 1) - 1] || `Month ${p.month}`,
    year: p.year || new Date().getFullYear(),
    items,
    totalGross,
    totalDeduction,
    totalNet: p.net_salary !== undefined ? p.net_salary : totalGross + totalDeduction,
  };
};
