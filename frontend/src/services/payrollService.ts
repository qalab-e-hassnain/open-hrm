import { apiClient } from './api';
import { API_ENDPOINTS } from '../config/constants';
import { type PayrollRecord, type OvertimeRecord, type OvertimeStatus } from '../types';

const mapPayroll = (p: any): PayrollRecord => ({
  id: p.id,
  employeeId: p.employee_id,
  baseSalary: p.base_salary ?? 0,
  allowances: p.allowances ?? 0,
  bonuses: p.bonuses ?? 0,
  arrears: p.arrears ?? 0,
  reimbursementTotal: p.reimbursement_total ?? 0,
  overtimePay: p.overtime_pay ?? 0,
  deductions: p.deductions ?? 0,
  unpaidLeaveDeduction: p.unpaid_leave_deduction ?? 0,
  unapprovedLeaveFine: p.unapproved_leave_fine ?? 0,
  tax: p.tax ?? 0,
  // Set by hand rather than by the slab engine; a recompute preserves it.
  taxIsManual: Boolean(p.tax_is_manual),
  netSalary: p.net_salary ?? 0,
  month: p.month,
  year: p.year,
  status: p.status,
  notes: p.notes ?? '',
});

const mapOvertime = (o: any): OvertimeRecord => ({
  id: o.id,
  employeeId: o.employee_id,
  employeeName: o.employee_name ?? '',
  month: o.month,
  year: o.year,
  overtimeHours: o.overtime_hours ?? 0,
  overtimePay: o.overtime_pay ?? 0,
  hourlyRate: o.hourly_rate ?? 0,
  estimatedPay: o.estimated_pay ?? 0,
  status: o.status,
  // Only an id comes back; the roster on screen turns it into a name.
  approvedBy: o.approved_by ?? null,
  payrollId: o.payroll_id ?? null,
});

export interface CreatePayrollData {
  employeeId: number;
  month: number;
  year: number;
  baseSalary: number;
  allowances?: number;
  bonuses?: number;
  arrears?: number;
  deductions?: number;
  unapprovedLeaveFine?: number;
  notes?: string;
  // Backfilling a past period: trust the entered tax instead of auto-computing.
  isHistorical?: boolean;
  tax?: number;
  // Save this pay as the employee's new standing salary (an increment).
  applyIncrement?: boolean;
}

export const payrollService = {
  /** Every payroll record, not just the first page.
   *
   *  The endpoint caps a page at 100 and reports the true total in
   *  X-Total-Count. This used to read one page and present it as the whole
   *  list, so with 168 records on file the payroll screen was quietly missing
   *  68 of them. */
  listPayrolls: async (): Promise<PayrollRecord[]> => {
    const PAGE = 200;
    const rows: unknown[] = [];
    for (let skip = 0; ; skip += PAGE) {
      const res = await apiClient.get<unknown[]>(API_ENDPOINTS.PAYROLL, {
        params: { skip, limit: PAGE },
      });
      const page = res.data || [];
      rows.push(...page);
      const total = Number(res.headers?.['x-total-count']);
      const done = Number.isFinite(total) ? rows.length >= total : page.length < PAGE;
      // The guard matters even if the header is missing or wrong: without it
      // a server that always returns a full page would loop for ever.
      if (done || page.length === 0) break;
    }
    return rows.map(mapPayroll);
  },

  createPayroll: async (data: CreatePayrollData): Promise<PayrollRecord> => {
    const res = await apiClient.post<any>(API_ENDPOINTS.PAYROLL, {
      employee_id: data.employeeId,
      month: data.month,
      year: data.year,
      base_salary: data.baseSalary,
      allowances: data.allowances ?? 0,
      bonuses: data.bonuses ?? 0,
      arrears: data.arrears ?? 0,
      deductions: data.deductions ?? 0,
      unapproved_leave_fine: data.unapprovedLeaveFine ?? 0,
      notes: data.notes || undefined,
      is_historical: data.isHistorical ?? false,
      tax: data.tax ?? 0,
      apply_increment: data.applyIncrement ?? false,
    });
    return mapPayroll(res.data);
  },

  /** Edit a payslip's figures. The server recomputes tax and net pay from them. */
  updatePayroll: async (id: number, data: {
    baseSalary?: number; allowances?: number; bonuses?: number;
    arrears?: number; deductions?: number; unapprovedLeaveFine?: number; notes?: string;
    /** Sending a value pins it — the slab engine will not overwrite it. */
    tax?: number;
    /** Send false to hand the figure back to the slab engine. */
    taxIsManual?: boolean;
  }): Promise<PayrollRecord> => {
    const res = await apiClient.put<any>(API_ENDPOINTS.PAYROLL_BY_ID(id), {
      base_salary: data.baseSalary,
      allowances: data.allowances,
      bonuses: data.bonuses,
      arrears: data.arrears,
      deductions: data.deductions,
      unapproved_leave_fine: data.unapprovedLeaveFine,
      notes: data.notes,
      // Omitted entirely when resetting: the server drops `tax` if
      // tax_is_manual is false, and sending both is contradictory.
      ...(data.taxIsManual === false ? { tax_is_manual: false } : {}),
      ...(data.taxIsManual === false ? {} : { tax: data.tax }),
    });
    return mapPayroll(res.data);
  },

  recompute: async (id: number): Promise<PayrollRecord> => {
    const res = await apiClient.post<any>(API_ENDPOINTS.PAYROLL_RECOMPUTE(id), {});
    return mapPayroll(res.data);
  },

  markPaid: async (id: number): Promise<PayrollRecord> => {
    const res = await apiClient.put<any>(API_ENDPOINTS.PAYROLL_PAID(id), {});
    return mapPayroll(res.data);
  },

  deletePayroll: async (id: number): Promise<void> => {
    await apiClient.delete(API_ENDPOINTS.PAYROLL_BY_ID(id));
  },

  // ---- Overtime ----
  computeOvertime: async (month: number, year: number, employeeId?: number): Promise<OvertimeRecord[]> => {
    const res = await apiClient.post<any[]>(API_ENDPOINTS.OVERTIME_COMPUTE, {
      month, year, employee_id: employeeId ?? null,
    });
    return (res.data || []).map(mapOvertime);
  },

  listOvertime: async (month?: number, year?: number, status?: OvertimeStatus): Promise<OvertimeRecord[]> => {
    const params: any = {};
    if (month) params.month = month;
    if (year) params.year = year;
    if (status) params.status_filter = status;
    const res = await apiClient.get<any[]>(API_ENDPOINTS.OVERTIME_LIST, { params });
    return (res.data || []).map(mapOvertime);
  },

  approveOvertime: async (id: number, status: 'approved' | 'rejected'): Promise<OvertimeRecord> => {
    const res = await apiClient.put<any>(API_ENDPOINTS.OVERTIME_APPROVE(id), { status });
    return mapOvertime(res.data);
  },

  /** The signed-in employee's own payroll records. */
  getMyPayroll: async () => {
    const res = await apiClient.get<unknown[]>(API_ENDPOINTS.MY_PAYROLL);
    return Array.isArray(res.data) ? res.data : [];
  },

  /** The signed-in employee's own approved overtime. */
  getMyOvertime: async () => {
    const res = await apiClient.get<unknown[]>(API_ENDPOINTS.MY_OVERTIME);
    return Array.isArray(res.data) ? res.data : [];
  },

  /** Somebody else's payroll history. Admin/HR on the server. */
  getEmployeePayroll: async (employeeId: string | number) => {
    const res = await apiClient.get<unknown[]>(API_ENDPOINTS.EMPLOYEE_PAYROLL(employeeId));
    return Array.isArray(res.data) ? res.data : [];
  },

  /**
   * Settle a batch of payslips in one act. Admin/HR.
   *
   * `expectedCount` is the server's guard against a stale screen: if the
   * selection no longer matches what is actually unpaid, it refuses rather
   * than marking the wrong set paid.
   */
  markManyPaid: async (payload: {
    payroll_ids?: number[];
    month?: number;
    year?: number;
    payment_date?: string | null;
    payment_method?: string | null;
    payment_reference?: string | null;
    expected_count?: number;
  }) => {
    const res = await apiClient.post<unknown>(API_ENDPOINTS.PAYROLL_MARK_PAID, payload);
    return res.data;
  },
};
