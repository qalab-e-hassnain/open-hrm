import { apiClient } from './api';
import { API_ENDPOINTS } from '../config/constants';

export type TimeLogKind = 'attendance' | 'overtime';
export type TimeLogStatus =
  | 'pending'
  | 'manager_approved'
  | 'approved'
  | 'rejected'
  | 'cancelled';

export interface TimeLog {
  id: number;
  employee_id: number;
  employee_name?: string | null;
  kind: TimeLogKind;
  work_date: string;
  check_in_time?: string | null;
  check_out_time?: string | null;
  hours: number;
  amount?: number | null;
  /** What the formula would pay. Shown beside `amount` when they differ. */
  suggested_amount?: number | null;
  reason: string;
  status: TimeLogStatus;
  manager_approver_name?: string | null;
  manager_approved_at?: string | null;
  approver_name?: string | null;
  approved_at?: string | null;
  rejection_reason?: string | null;
  /**
   * Whether THIS viewer may sign it right now. The server already enforces
   * stage order, role and self-approval; gate the buttons on this rather than
   * re-deriving those rules here and drifting out of step with them.
   */
  can_approve: boolean;
  /** Who it is waiting on, for everyone who cannot act. */
  next_approver?: string | null;
  created_at: string;
}

export interface TimeLogSuggestion {
  hours: number;
  hourly_rate: number;
  suggested_amount: number;
}

export interface AttendanceClaim {
  work_date: string;
  check_in_time: string;
  check_out_time: string;
  reason: string;
}

export interface OvertimeClaim {
  work_date: string;
  hours: number;
  reason: string;
}
/*
 * No `amount` here on purpose. The API accepts one, but the employee does not
 * set what they are paid: the figure is pro rata from their own salary, and
 * the server derives it from `hours` when none is sent. An approver may still
 * correct it as they sign — see `decide`.
 */

/** Human labels for the chain: pending -> manager_approved -> approved. */
export const TIME_LOG_STATUS_LABEL: Record<TimeLogStatus, string> = {
  pending: 'Awaiting line manager',
  manager_approved: 'Awaiting HR',
  approved: 'Approved',
  rejected: 'Rejected',
  cancelled: 'Withdrawn',
};

/** A request is still open — and so still withdrawable — until it is decided. */
export const isOpen = (status: TimeLogStatus) =>
  status === 'pending' || status === 'manager_approved';

export const timeLogService = {
  /**
   * Pro-rata figure for a given number of hours, for the caller's own salary.
   * Not used by the logging form — the employee submits hours and the server
   * derives the amount. Kept for an approver screen that wants the figure
   * before a claim exists.
   */
  suggest: async (hours: number): Promise<TimeLogSuggestion> => {
    const res = await apiClient.get<TimeLogSuggestion>(API_ENDPOINTS.TIME_LOG_SUGGEST, {
      params: { hours },
    });
    return res.data as TimeLogSuggestion;
  },

  claimAttendance: async (claim: AttendanceClaim): Promise<TimeLog> => {
    const res = await apiClient.post<TimeLog>(API_ENDPOINTS.TIME_LOGS, {
      kind: 'attendance',
      ...claim,
    });
    return res.data as TimeLog;
  },

  claimOvertime: async (claim: OvertimeClaim): Promise<TimeLog> => {
    const res = await apiClient.post<TimeLog>(API_ENDPOINTS.TIME_LOGS, {
      kind: 'overtime',
      ...claim,
    });
    return res.data as TimeLog;
  },

  myLogs: async (): Promise<TimeLog[]> => {
    const res = await apiClient.get<TimeLog[]>(API_ENDPOINTS.TIME_LOGS_ME);
    return res.data || [];
  },

  /** Exactly what is waiting on the caller — already filtered by the server. */
  pending: async (): Promise<TimeLog[]> => {
    const res = await apiClient.get<TimeLog[]>(API_ENDPOINTS.TIME_LOGS_PENDING);
    return res.data || [];
  },

  employeeLogs: async (employeeId: string | number): Promise<TimeLog[]> => {
    const res = await apiClient.get<TimeLog[]>(API_ENDPOINTS.TIME_LOGS_EMPLOYEE(employeeId));
    return res.data || [];
  },

  /**
   * One endpoint for both signatures — the server decides which stage this
   * caller is completing, so the client never names one. `amount` lets an
   * approver correct a figure as they sign; a rejection must carry a reason.
   */
  decide: async (
    id: number,
    decision: { approved: boolean; amount?: number; rejection_reason?: string },
  ): Promise<TimeLog> => {
    const res = await apiClient.put<TimeLog>(API_ENDPOINTS.TIME_LOG_DECIDE(id), decision);
    return res.data as TimeLog;
  },

  /** Withdraw while still open; the server refuses once decided. */
  withdraw: async (id: number): Promise<void> => {
    await apiClient.delete(API_ENDPOINTS.TIME_LOG_BY_ID(id));
  },
};
