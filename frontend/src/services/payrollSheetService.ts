import { apiClient } from './api';
import { API_ENDPOINTS } from '../config/constants';

export type PayrollRunStatus = 'draft' | 'finalized' | 'paid';

export interface PayrollRunSummary {
  id: number;
  month: string; // "YYYY-MM"
  status: PayrollRunStatus;
  createdAt: string;
  finalizedAt: string | null;
  employeeCount: number;
  totalGross: number;
  totalNet: number;
}

export interface PayrollEntry {
  id: number;
  payrollRunId: number;
  employeeId: number;
  employeeName: string;
  designation: string;
  monthlySalary: number;
  increment: number;
  allowanceOvertime: number;
  arrears: number;
  bonus: number;
  taxDeduction: number;
  otherDeduction: number;
  notes: string;
  // Computed server-side; recomputed locally for instant feedback while editing.
  grossSalary: number;
  grossTotal: number;
  netPayable: number;
}

export interface PayrollRunDetail {
  id: number;
  month: string;
  status: PayrollRunStatus;
  createdAt: string;
  finalizedAt: string | null;
  entries: PayrollEntry[];
  totalGross: number;
  totalNet: number;
}

/** The six adjustable numeric columns — the only ones PATCH accepts. */
export type EditableField =
  | 'increment'
  | 'allowanceOvertime'
  | 'arrears'
  | 'bonus'
  | 'taxDeduction'
  | 'otherDeduction';

const FIELD_TO_API: Record<EditableField, string> = {
  increment: 'increment',
  allowanceOvertime: 'allowance_overtime',
  arrears: 'arrears',
  bonus: 'bonus',
  taxDeduction: 'tax_deduction',
  otherDeduction: 'other_deduction',
};

const mapEntry = (e: any): PayrollEntry => ({
  id: e.id,
  payrollRunId: e.payroll_run_id,
  employeeId: e.employee_id,
  employeeName: e.employee_name ?? '',
  designation: e.designation ?? '',
  monthlySalary: e.monthly_salary ?? 0,
  increment: e.increment ?? 0,
  allowanceOvertime: e.allowance_overtime ?? 0,
  arrears: e.arrears ?? 0,
  bonus: e.bonus ?? 0,
  taxDeduction: e.tax_deduction ?? 0,
  otherDeduction: e.other_deduction ?? 0,
  notes: e.notes ?? '',
  grossSalary: e.gross_salary ?? 0,
  grossTotal: e.gross_total ?? 0,
  netPayable: e.net_payable ?? 0,
});

const mapSummary = (r: any): PayrollRunSummary => ({
  id: r.id,
  month: r.month,
  status: r.status,
  createdAt: r.created_at,
  finalizedAt: r.finalized_at ?? null,
  employeeCount: r.employee_count ?? 0,
  totalGross: r.total_gross ?? 0,
  totalNet: r.total_net ?? 0,
});

const mapDetail = (r: any): PayrollRunDetail => ({
  id: r.id,
  month: r.month,
  status: r.status,
  createdAt: r.created_at,
  finalizedAt: r.finalized_at ?? null,
  entries: (r.entries || []).map(mapEntry),
  totalGross: r.total_gross ?? 0,
  totalNet: r.total_net ?? 0,
});

/** Surface the backend's `detail` message (e.g. the 409 on a finalized run). */
const toError = (error: any, fallback: string): Error => {
  const detail = error?.response?.data?.detail;
  if (typeof detail === 'string') return new Error(detail);
  if (Array.isArray(detail) && detail.length) return new Error(String(detail[0]?.msg || fallback));
  return new Error(error?.message || fallback);
};

export const payrollSheetService = {
  listRuns: async (): Promise<PayrollRunSummary[]> => {
    const res = await apiClient.get<any[]>(API_ENDPOINTS.PAYROLL_RUNS);
    return (res.data || []).map(mapSummary);
  },

  getRun: async (runId: number): Promise<PayrollRunDetail> => {
    const res = await apiClient.get<any>(API_ENDPOINTS.PAYROLL_RUN_BY_ID(runId));
    return mapDetail(res.data);
  },

  createRun: async (month: string): Promise<PayrollRunDetail> => {
    try {
      const res = await apiClient.post<any>(API_ENDPOINTS.PAYROLL_RUNS, { month });
      return mapDetail(res.data);
    } catch (error: any) {
      throw toError(error, 'Failed to create payroll run');
    }
  },

  updateEntry: async (
    runId: number,
    entryId: number,
    field: EditableField,
    value: number
  ): Promise<PayrollEntry> => {
    try {
      const res = await apiClient.patch<any>(API_ENDPOINTS.PAYROLL_RUN_ENTRY(runId, entryId), {
        [FIELD_TO_API[field]]: value,
      });
      return mapEntry(res.data);
    } catch (error: any) {
      throw toError(error, 'Failed to update entry');
    }
  },

  addEntry: async (runId: number, employeeId: number): Promise<PayrollEntry> => {
    try {
      const res = await apiClient.post<any>(API_ENDPOINTS.PAYROLL_RUN_ENTRIES(runId), {
        employee_id: employeeId,
      });
      return mapEntry(res.data);
    } catch (error: any) {
      throw toError(error, 'Failed to add employee to run');
    }
  },

  deleteEntry: async (runId: number, entryId: number): Promise<void> => {
    try {
      await apiClient.delete(API_ENDPOINTS.PAYROLL_RUN_ENTRY(runId, entryId));
    } catch (error: any) {
      throw toError(error, 'Failed to remove entry');
    }
  },

  finalizeRun: async (runId: number): Promise<PayrollRunDetail> => {
    try {
      const res = await apiClient.post<any>(API_ENDPOINTS.PAYROLL_RUN_FINALIZE(runId), {});
      return mapDetail(res.data);
    } catch (error: any) {
      throw toError(error, 'Failed to finalize run');
    }
  },

  reopenRun: async (runId: number, reason: string): Promise<PayrollRunDetail> => {
    try {
      const res = await apiClient.post<any>(API_ENDPOINTS.PAYROLL_RUN_REOPEN(runId), { reason });
      return mapDetail(res.data);
    } catch (error: any) {
      throw toError(error, 'Failed to reopen run');
    }
  },

  deleteRun: async (runId: number): Promise<void> => {
    try {
      await apiClient.delete(API_ENDPOINTS.PAYROLL_RUN_BY_ID(runId));
    } catch (error: any) {
      throw toError(error, 'Failed to delete run');
    }
  },

  /** Fetches the xlsx as a blob and triggers a browser download. */
  exportRun: async (runId: number, month: string): Promise<void> => {
    try {
      // responseType 'blob' makes axios hand back the raw file as res.data.
      const res = await apiClient.get<Blob>(API_ENDPOINTS.PAYROLL_RUN_EXPORT(runId), {
        params: { format: 'xlsx' },
        responseType: 'blob',
      });
      if (!res.data) throw new Error('Export returned an empty file');
      const url = URL.createObjectURL(new Blob([res.data]));
      const link = document.createElement('a');
      link.href = url;
      link.download = `payroll-${month}.xlsx`;
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
      URL.revokeObjectURL(url);
    } catch (error: any) {
      throw toError(error, 'Failed to export payroll sheet');
    }
  },

  /** Every change made to a payroll run, and by whom. Admin/HR. */
  getRunAudit: async (runId: string | number) => {
    const res = await apiClient.get<unknown[]>(API_ENDPOINTS.PAYROLL_RUN_AUDIT(runId));
    return Array.isArray(res.data) ? res.data : [];
  },
};
