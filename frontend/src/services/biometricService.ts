import { apiClient } from './api';
import { API_ENDPOINTS } from '../config/constants';

/**
 * The office fingerprint device.
 *
 * Scans arrive on their own through the `biometric_sync` sweep, so nothing
 * here is required for attendance to work — this is the panel that answers
 * the questions the sweep cannot: which device names nobody has claimed,
 * whether a day's scans agree with what the portal recorded, and what the
 * device actually saw when somebody insists they were here.
 *
 * Every route is admin/HR on the server (`require_admin_or_hr`). The screen
 * gates on the same thing, so the UI and the API refuse the same people.
 */

/** A name the device knows, and the employee it belongs to — if anyone. */
export interface BiometricIdentity {
  id: number;
  device_name: string;
  employee_id?: number | null;
  employee_name?: string | null;
  /** Deliberately not a member of staff — a visitor, a test enrolment. */
  ignored: boolean;
  note?: string | null;
  first_seen?: string | null;
  last_seen?: string | null;
  scan_count: number;
  /** Mapped to somebody, or deliberately ignored. Anything else needs a decision. */
  is_resolved: boolean;
  /** Scans still attached to nobody — the punches that reach no attendance
   *  record. An ignored name keeps collecting these, which is the only way to
   *  tell a name ignored on purpose from one ignored by mistake. */
  unattached_scans: number;
}

/** One person's day at the device: first touch, last touch. */
export interface BiometricScan {
  device_name: string;
  employee_id?: number | null;
  employee_name?: string | null;
  scan_date: string;
  first_scan_local: string;
  last_scan_local: string;
  /** Touched the device once, so the departure is unknown rather than zero. */
  single_scan: boolean;
}

export interface IdentityUpdate {
  employee_id?: number | null;
  ignored?: boolean;
  note?: string | null;
}

export const biometricService = {
  /** Scans, optionally narrowed to one day or one employee. */
  listScans: async (params?: { on?: string; employeeId?: number }): Promise<BiometricScan[]> => {
    const res = await apiClient.get<BiometricScan[]>(API_ENDPOINTS.BIOMETRIC_SCANS, {
      params: { on: params?.on, employee_id: params?.employeeId },
    });
    return Array.isArray(res.data) ? res.data : [];
  },

  /** Device names. `unresolvedOnly` is the queue worth acting on. */
  /** Device names, filtered by what has been decided about them.
   *  "undecided" is not the same as "unresolved": an ignored name counts as
   *  resolved, which is how thirty-five ignored names stayed invisible. */
  listIdentities: async (
    state: 'all' | 'undecided' | 'ignored' | 'mapped' = 'all',
  ): Promise<BiometricIdentity[]> => {
    const res = await apiClient.get<BiometricIdentity[]>(API_ENDPOINTS.BIOMETRIC_IDENTITIES, {
      params: { state },
    });
    return Array.isArray(res.data) ? res.data : [];
  },

  /** Point a device name at an employee, or mark it ignored. */
  updateIdentity: async (id: number, payload: IdentityUpdate): Promise<BiometricIdentity> => {
    const res = await apiClient.put<BiometricIdentity>(API_ENDPOINTS.BIOMETRIC_IDENTITY(id), payload);
    return res.data as BiometricIdentity;
  },

  /**
   * Pull the log now rather than waiting for the sweep. `month` is YYYY-MM;
   * without it the server uses its own window.
   */
  sync: async (month?: string): Promise<unknown> => {
    const res = await apiClient.post<unknown>(
      API_ENDPOINTS.BIOMETRIC_SYNC, {}, { params: { month } },
    );
    return res.data;
  },

  /** Device scans against portal attendance for a day — who disagrees. */
  reconcile: async (on?: string): Promise<unknown> => {
    const res = await apiClient.get<unknown>(API_ENDPOINTS.BIOMETRIC_RECONCILE, {
      params: { on },
    });
    return res.data;
  },
};
