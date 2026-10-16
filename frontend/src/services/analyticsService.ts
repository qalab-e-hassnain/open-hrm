import { apiClient } from './api';
import { API_ENDPOINTS } from '../config/constants';

/** A breakdown entry. Already sorted largest first and capped where a cap
 *  makes sense, so render in the order it arrives. */
export interface Point { label: string; value: number }

export interface Overview {
  headcount: number;
  headcount_by_department: Point[];
  headcount_by_role: Point[];
  joiners_this_year: number;
  leavers_this_year: number;
  /** Leavers over average headcount — a rate. In a company of fifteen,
   *  one departure is seven percent and a raw count says nothing. */
  attrition_rate_percent: number;
  open_resignations: number;
  on_probation: number;
  average_tenure_years: number;
  /** The whole year's entitlement — NOT what is owed today. */
  leave_liability_days: number;
  leave_liability_amount: number;
  /** Pro-rated over the part of the leave year each person has worked.
   *  This is the one that belongs on a balance sheet. */
  leave_accrued_days: number;
  leave_accrued_amount: number;
  pending_leave_requests: number;
  pending_overtime_requests: number;
  part_time_count: number;
  remote_count: number;
  tenure_bands: Point[];
  gender_split: Point[];
  advances_outstanding: number;
  assets_issued_count: number;
  assets_issued_value: number;
  currency: string;
  generated_at: string;
}

/** Series come back as parallel arrays indexed by `months`, oldest first. */
export interface HeadcountSeries {
  months: string[]; headcount: number[]; joiners: number[]; leavers: number[];
}

export interface LeaveStats {
  year: number;
  approved_days: number;
  working_days_available: number;
  absence_rate_percent: number;
  by_type: Point[];
  by_month: Point[];
  top_takers: Point[];
  average_decision_days?: number | null;
}

export interface PayrollSeries {
  months: string[]; gross: number[]; net: number[]; tax: number[];
  overtime: number[]; headcount_paid: number[]; currency: string;
  /** The current month is excluded until it has been paid: a month nobody
   *  has been paid for is not a month the payroll was zero. */
  current_month_pending: boolean;
  tax_percent_of_gross?: number | null;
  average_cost_per_head?: number | null;
}

export interface OvertimeSeries {
  months: string[]; approved_hours: number[]; approved_pay: number[];
  by_employee: Point[]; currency: string;
  /** False when there is genuinely nothing — say so in words rather than
   *  drawing a flat line along the axis, which reads as broken. */
  has_data: boolean;
  total_hours: number;
  total_pay: number;
}

/**
 * Attendance. Every series here is nullable on purpose: a month before the
 * biometric sync began is unknown, not zero, and plotting it as zero reads
 * as "nobody came to work".
 */
export interface AttendanceSeries {
  months: string[];
  attendance_rate_percent: (number | null)[];
  average_hours_per_day: (number | null)[];
  days_present: number[];
  incomplete_days: number[];
  late_arrivals: (number | null)[];
  by_employee: Record<string, unknown>[];
  /** Lateness is only answerable where a shift says what "on time" means. */
  punctuality_available: boolean;
  records_begin?: string | null;
}

export interface Compensation {
  monthly_cost: number;
  annual_cost: number;
  average_salary: number;
  median_salary: number;
  lowest: number;
  highest: number;
  spread_ratio?: number | null;
  by_department: Record<string, unknown>[];
  by_designation: Record<string, unknown>[];
  bands: Point[];
  currency: string;
}

/** Everything currently waiting on somebody, worst first. */
export interface AttentionItem {
  key: string;
  label: string;
  count: number;
  severity: 'info' | 'warning' | 'critical';
  detail?: string | null;
  people: string[];
}

export const analyticsService = {
  overview: async (): Promise<Overview> => {
    const res = await apiClient.get<Overview>(API_ENDPOINTS.ANALYTICS_OVERVIEW);
    return res.data as Overview;
  },
  headcount: async (months = 12): Promise<HeadcountSeries> => {
    const res = await apiClient.get<HeadcountSeries>(API_ENDPOINTS.ANALYTICS_HEADCOUNT, { params: { months } });
    return res.data as HeadcountSeries;
  },
  leave: async (year?: number): Promise<LeaveStats> => {
    const res = await apiClient.get<LeaveStats>(API_ENDPOINTS.ANALYTICS_LEAVE, {
      params: year ? { year } : undefined,
    });
    return res.data as LeaveStats;
  },
  payroll: async (months = 12): Promise<PayrollSeries> => {
    const res = await apiClient.get<PayrollSeries>(API_ENDPOINTS.ANALYTICS_PAYROLL, { params: { months } });
    return res.data as PayrollSeries;
  },
  overtime: async (months = 12): Promise<OvertimeSeries> => {
    const res = await apiClient.get<OvertimeSeries>(API_ENDPOINTS.ANALYTICS_OVERTIME, { params: { months } });
    return res.data as OvertimeSeries;
  },
  attendance: async (months = 12): Promise<AttendanceSeries> => {
    const res = await apiClient.get<AttendanceSeries>(API_ENDPOINTS.ANALYTICS_ATTENDANCE, { params: { months } });
    return res.data as AttendanceSeries;
  },
  compensation: async (): Promise<Compensation> => {
    const res = await apiClient.get<Compensation>(API_ENDPOINTS.ANALYTICS_COMPENSATION);
    return res.data as Compensation;
  },
  attention: async (): Promise<AttentionItem[]> => {
    const res = await apiClient.get<AttentionItem[]>(API_ENDPOINTS.ANALYTICS_ATTENTION);
    return Array.isArray(res.data) ? res.data : [];
  },
};

export interface OrgNode {
  id: number;
  full_name: string;
  email: string;
  designation?: string | null;
  department?: string | null;
  role: string;
  is_hr: boolean;
  profile_picture_url?: string | null;
  employee_id?: string | null;
  reports: OrgNode[];
}

export const orgService = {
  /** An array of ROOTS, not a single root — two founders at the top is a
   *  real shape, and so is nobody. Anyone whose manager has left comes back
   *  as a root rather than vanishing. */
  chart: async (includeInactive = false): Promise<OrgNode[]> => {
    const res = await apiClient.get<OrgNode[]>(API_ENDPOINTS.ORG_CHART, {
      params: includeInactive ? { include_inactive: true } : undefined,
    });
    return Array.isArray(res.data) ? res.data : [];
  },
};
