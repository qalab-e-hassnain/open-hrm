import { apiClient } from './api';
import { API_ENDPOINTS } from '../config/constants';

export interface Shift {
  id: number;
  name: string;
  /** Local clock time, no timezone: "09:00:00". */
  start_time: string;
  end_time: string;
  break_minutes: number;
  /** Seven characters of 0 or 1, MONDAY FIRST. "1111100" is Mon–Fri.
   *  Use `working_days` for display rather than parsing this. */
  days_of_week: string;
  grace_minutes: number;
  is_active: boolean;
  is_default: boolean;
  hours: number;
  working_days: string[];
  assigned_count: number;
}

export interface ShiftAssignment {
  id: number;
  employee_id: number;
  employee_name?: string | null;
  shift_id: number;
  shift_name?: string | null;
  effective_from: string;
  /** Set when a later assignment closed this one. */
  effective_to?: string | null;
}

export const DAY_INITIALS = ['M', 'T', 'W', 'T', 'F', 'S', 'S'];

/** "09:00:00" → "09:00". */
export const clock = (value?: string | null) => (value ? value.slice(0, 5) : '—');

export interface ShiftInput {
  name: string;
  start_time: string;
  end_time: string;
  break_minutes?: number;
  days_of_week?: string;
  grace_minutes?: number;
  is_default?: boolean;
}

export const shiftService = {
  list: async (includeInactive = false): Promise<Shift[]> => {
    const res = await apiClient.get<Shift[]>(API_ENDPOINTS.SHIFTS, {
      params: includeInactive ? { include_inactive: true } : undefined,
    });
    return Array.isArray(res.data) ? res.data : [];
  },

  create: async (body: ShiftInput): Promise<Shift> => {
    const res = await apiClient.post<Shift>(API_ENDPOINTS.SHIFTS, body);
    return res.data as Shift;
  },

  update: async (id: number, body: Partial<ShiftInput & { is_active: boolean }>): Promise<Shift> => {
    const res = await apiClient.put<Shift>(API_ENDPOINTS.SHIFT(id), body);
    return res.data as Shift;
  },

  /** Deactivates rather than deletes — past attendance was judged against it. */
  deactivate: async (id: number): Promise<void> => {
    await apiClient.delete(API_ENDPOINTS.SHIFT(id));
  },

  assignments: async (currentOnly = true): Promise<ShiftAssignment[]> => {
    const res = await apiClient.get<ShiftAssignment[]>(API_ENDPOINTS.SHIFT_ASSIGNMENTS, {
      params: currentOnly ? undefined : { current_only: false },
    });
    return Array.isArray(res.data) ? res.data : [];
  },

  /** Posting a new assignment ends any running one the day before, so nobody
   *  is ever on two patterns at once. */
  assign: async (body: {
    employee_id: number; shift_id: number; effective_from?: string;
  }): Promise<ShiftAssignment> => {
    const res = await apiClient.post<ShiftAssignment>(API_ENDPOINTS.SHIFT_ASSIGNMENTS, body);
    return res.data as ShiftAssignment;
  },

  mine: async (on?: string): Promise<Shift | null> => {
    const res = await apiClient.get<Shift | null>(API_ENDPOINTS.SHIFTS_ME, {
      params: on ? { on } : undefined,
    });
    return (res.data as Shift | null) ?? null;
  },

  forEmployee: async (employeeId: number, on?: string): Promise<Shift | null> => {
    const res = await apiClient.get<Shift | null>(API_ENDPOINTS.SHIFTS_EMPLOYEE(employeeId), {
      params: on ? { on } : undefined,
    });
    return (res.data as Shift | null) ?? null;
  },
};
