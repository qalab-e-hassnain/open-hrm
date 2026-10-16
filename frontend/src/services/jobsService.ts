import { apiClient } from './api';
import { API_ENDPOINTS } from '../config/constants';

/**
 * The four background sweeps. There is no cron: they run off the portal's
 * own traffic, so this panel is the only way anyone can tell whether they
 * are running at all.
 */
export interface ScheduledJob {
  name: string;
  description: string;
  interval_hours: number;
  last_run_at?: string | null;
  last_status?: string | null;
  /** Free-form per job, and genuinely useful — the device sync returns
   *  `{"month":"2026-09","rows":13,"matched":11}`. */
  last_result?: unknown;
  last_error?: string | null;
  last_duration_ms?: number | null;
  running_since?: string | null;
  run_count: number;
  error_count: number;
  is_running: boolean;
  is_due: boolean;
  next_due_at?: string | null;
}

/** An unhandled exception the API recorded, with the short reference quoted
 *  back to whoever hit it. */
export interface JobError {
  id: number;
  reference: string;
  occurred_at: string;
  method?: string | null;
  path?: string | null;
  actor_email?: string | null;
  exception_type?: string | null;
  message?: string | null;
}

export const jobsService = {
  list: async (): Promise<ScheduledJob[]> => {
    const res = await apiClient.get<ScheduledJob[]>(API_ENDPOINTS.ADMIN_JOBS);
    return Array.isArray(res.data) ? res.data : [];
  },

  /** Runs inline and returns the result — the caller is waiting to see it. */
  run: async (name: string): Promise<unknown> => {
    const res = await apiClient.post<unknown>(API_ENDPOINTS.ADMIN_JOB_RUN(name), {});
    return res.data;
  },

  /** Recent unhandled exceptions. Admin/HR. The reference is what an employee
   *  reads off their error screen, so this is how you find their failure. */
  errors: async (limit = 50): Promise<JobError[]> => {
    const res = await apiClient.get<JobError[]>(API_ENDPOINTS.ADMIN_JOB_ERRORS, {
      params: { limit },
    });
    return Array.isArray(res.data) ? res.data : [];
  },
};
