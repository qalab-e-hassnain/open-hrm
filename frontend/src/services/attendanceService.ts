import { apiClient } from './api';
import { API_ENDPOINTS, OFFICE_LOCATION, TESTING } from '../config/constants';
import { type AttendanceRecord } from '../types';

export interface CheckInData {
  /**
   * Optional, because a desktop has no GPS.
   *
   * A machine with no location hardware cannot produce a fix, and this screen
   * used to refuse the check-in locally — so somebody sitting at an office PC
   * was told they were not in the office, by their own browser, without the
   * request ever reaching the server. Only the server can settle that: it sees
   * the source address the connection arrives on, which the office network
   * assigns and the caller cannot choose. Sending without coordinates lets it
   * answer. (The browser cannot help here: there is no API for the LAN address
   * or the gateway, and the WebRTC leak that used to expose them was closed.)
   */
  latitude?: number;
  longitude?: number;
  address?: string;
}

/** What a location attempt produced: a fix, or the reason there isn't one. */
export interface LocationAttempt {
  position: GeolocationPosition | null;
  error: string | null;
}

export const attendanceService = {
  getAttendanceRecords: async () => {
    const response = await apiClient.get<AttendanceRecord[]>(API_ENDPOINTS.ATTENDANCE);
    return response.data;
  },

  // Check-in/check-out are POSTs, so api.ts's generic retry interceptor
  // deliberately skips them (retrying a POST whose response was lost could
  // otherwise create a duplicate). But the backend enforces "one active
  // check-in per employee" as a hard invariant, so a retry here can't
  // actually double-submit: either it goes through, or it hits that guard
  // (400 "active check-in from...") — which only ever means the check-in
  // already exists. That makes it safe to retry specifically on a transient
  // Supabase pool 502/503/504, instead of surfacing "Database connection
  // error" to the user on the first hiccup and making them click again.
  checkIn: async (data: CheckInData) => {
    // If testing with office coordinates, override payload and add test header
    const payload = TESTING.USE_OFFICE_COORDINATES
      ? { latitude: OFFICE_LOCATION.LATITUDE, longitude: OFFICE_LOCATION.LONGITUDE, address: OFFICE_LOCATION.NAME }
      : data;
    const config = TESTING.USE_OFFICE_COORDINATES ? { headers: { 'X-Testing-Location': 'true' } } : undefined;

    const maxRetries = 2;
    for (let attempt = 0; ; attempt += 1) {
      try {
        const response = await apiClient.post<AttendanceRecord>(API_ENDPOINTS.CHECK_IN, payload, config);
        return response.data;
      } catch (error: any) {
        const httpStatus = error?.response?.status;
        if (attempt > 0 && httpStatus === 400) {
          // A prior attempt's write already landed; this is just the
          // duplicate-check-in guard, not a new failure.
          return null as unknown as AttendanceRecord;
        }
        const transient = !error?.response || [502, 503, 504].includes(httpStatus);
        if (!transient || attempt >= maxRetries) throw error;
        await new Promise((resolve) => window.setTimeout(resolve, 700 * (attempt + 1)));
      }
    }
  },

  checkOut: async (data: CheckInData) => {
    // If testing with office coordinates, override payload and add test header
    const payload = TESTING.USE_OFFICE_COORDINATES
      ? { latitude: OFFICE_LOCATION.LATITUDE, longitude: OFFICE_LOCATION.LONGITUDE, address: OFFICE_LOCATION.NAME }
      : data;
    const config = TESTING.USE_OFFICE_COORDINATES ? { headers: { 'X-Testing-Location': 'true' } } : undefined;

    // Same transient-retry as checkIn, but a 400 here is NOT automatically
    // treated as success: "no active check-in found" is a genuine error
    // state (not just a race outcome) and must still reach the user.
    const maxRetries = 2;
    for (let attempt = 0; ; attempt += 1) {
      try {
        const response = await apiClient.post<AttendanceRecord>(API_ENDPOINTS.CHECK_OUT, payload, config);
        return response.data;
      } catch (error: any) {
        const httpStatus = error?.response?.status;
        const transient = !error?.response || [502, 503, 504].includes(httpStatus);
        if (!transient || attempt >= maxRetries) throw error;
        await new Promise((resolve) => window.setTimeout(resolve, 700 * (attempt + 1)));
      }
    }
  },

  getAttendanceStatus: async () => {
    try {
      // Use new daily status endpoint with overtime data
      const response = await apiClient.get<any>(API_ENDPOINTS.DAILY_STATUS);
      const data = response.data;
      
      // User is checked in ONLY if status is 'checked_in' (not 'completed')
      // 'completed' means they already checked out, so they can check in again
      return {
        isCheckedIn: data.status === 'checked_in', // Only true if currently checked in
        checkInTime: data.check_in_time,
        checkOutTime: data.check_out_time,
        workingHours: data.working_hours,
        regularHours: data.regular_hours,
        overtimeHours: data.overtime_hours,
        overtimeBonus: data.overtime_bonus,
        overtimeRate: data.overtime_rate,
        dayType: data.day_type,
        status: data.status, // 'not_checked_in', 'checked_in', or 'completed'
      };
    } catch (error) {
      console.log('Using fallback attendance status check');
      // Fallback to old method
      try {
        const response = await apiClient.get<any[]>(API_ENDPOINTS.ATTENDANCE);
        const today = new Date().toISOString().split('T')[0];
        const todayRecord = response.data?.find((record: any) => 
          record.check_in?.startsWith(today)
        );
        
        return {
          isCheckedIn: todayRecord ? !todayRecord.check_out : false,
          checkInTime: todayRecord?.check_in,
          checkOutTime: todayRecord?.check_out,
        };
      } catch (fallbackError) {
        return { isCheckedIn: false };
      }
    }
  },

  getWeeklyOvertime: async () => {
    try {
      const response = await apiClient.get<any>(API_ENDPOINTS.WEEKLY_OVERTIME);
      return response.data;
    } catch (error) {
      console.error('Failed to fetch weekly overtime:', error);
      return null;
    }
  },

  getMonthlyOvertime: async (month?: number, year?: number) => {
    try {
      const params: any = {};
      if (month) params.month = month;
      if (year) params.year = year;
      
      const response = await apiClient.get<any>(API_ENDPOINTS.MONTHLY_OVERTIME, { params });
      return response.data;
    } catch (error) {
      console.error('Failed to fetch monthly overtime:', error);
      return null;
    }
  },

  // Flexible attendance filtering
  filterAttendance: async (filters: {
    date?: string;
    startDate?: string;
    endDate?: string;
    month?: number;
    year?: number;
    // Two values only, and deliberately not three: the `record_status` query
    // this maps to still asks one question — is there a check-out? — so
    // `completed` here means "checked out", short days included. The row's own
    // three-state `status` field is what tells a completed day from a short
    // one, and screens that need that distinction filter on it themselves
    // (see AttendanceHistory). Sending `short` here would match no branch
    // server-side and silently return everything.
    status?: 'completed' | 'incomplete';
    employeeId?: number;
    // Omit the signed-in user's own records — used when reviewing other
    // people's attendance, so the reviewer's own rows don't appear in the list.
    excludeSelf?: boolean;
    skip?: number;
    limit?: number;
  }) => {
    // Deliberately does not catch: callers (AttendanceHistory, Dashboard) use
    // Promise.allSettled and check the rejection to show a "Sync issue /
    // Retry" state. Swallowing the error here and returning [] used to make
    // a failed fetch indistinguishable from "genuinely zero records" — the
    // sheet just looked empty/wrong with no indication a retry would fix it.
    const params: any = {};

    if (filters.date) params.date = filters.date;
    if (filters.startDate) params.start_date = filters.startDate;
    if (filters.endDate) params.end_date = filters.endDate;
    if (filters.month) params.month = filters.month;
    if (filters.year) params.year = filters.year;
    // Backend's query param is named `record_status`, not `status` — a
    // mismatch here means FastAPI silently ignores it and the filter is a no-op.
    if (filters.status) params.record_status = filters.status;
    if (filters.employeeId) params.employee_id = filters.employeeId;
    if (filters.excludeSelf) params.exclude_self = true;
    if (filters.skip !== undefined) params.skip = filters.skip;
    if (filters.limit) params.limit = filters.limit;

    const response = await apiClient.get<any[]>(API_ENDPOINTS.ATTENDANCE_FILTER, { params });
    return response.data || [];
  },

  getDailyReport: async (startDate?: string, endDate?: string) => {
    const params: any = {};
    if (startDate) params.start_date = startDate;
    if (endDate) params.end_date = endDate;
    
    const response = await apiClient.get<any>(API_ENDPOINTS.DAILY_REPORT, { params });
    return response.data;
  },

  getWeeklyAttendance: async (employeeId?: number) => {
    try {
      // Use new filter endpoint for better performance
      const endDate = new Date();
      const startDate = new Date();
      startDate.setDate(startDate.getDate() - 6); // Last 7 days including today
      
      const records = await attendanceService.filterAttendance({
        startDate: startDate.toISOString().split('T')[0],
        endDate: endDate.toISOString().split('T')[0],
        employeeId: employeeId, // Filter by specific user
        limit: 100,
      });
      
      // Create a map of date -> attendance record
      const weekMap = new Map<string, any>();
      records.forEach((record: any) => {
        const date = record.date || new Date(record.check_in_time).toISOString().split('T')[0];
        weekMap.set(date, {
          check_in: record.check_in_time,
          check_out: record.check_out_time,
          working_hours: record.working_hours,
          // Clock time past the standard day. `overtime_hours` now counts only
          // approved claims, so a weekly strip reading it showed nothing.
          extra_hours: record.extra_hours,
          overtime_hours: record.overtime_hours,
          day_completed: record.day_completed,
          day_total_hours: record.day_total_hours,
          status: record.status,
        });
      });
      
      // Generate 7 days array (starting from 6 days ago to today)
      const weekData = [];
      for (let i = 6; i >= 0; i--) {
        const date = new Date();
        date.setDate(date.getDate() - i);
        const dateStr = date.toISOString().split('T')[0];
        
        const record = weekMap.get(dateStr);
        weekData.push({
          date: dateStr,
          dayName: date.toLocaleDateString('en-US', { weekday: 'short' }),
          checkIn: record?.check_in,
          checkOut: record?.check_out,
          workingHours: record?.working_hours,
          extraHours: record?.extra_hours,
          overtimeHours: record?.overtime_hours,
          dayCompleted: record?.day_completed,
          // The row's own status where the server gave one; the old two-state
          // guess only as a fallback for a pre-rework payload.
          status: record ? (record.status ?? (record.check_out ? 'completed' : 'checked_in')) : 'absent',
        });
      }
      
      return weekData;
    } catch (error) {
      console.error('Failed to fetch weekly attendance:', error);
      return [];
    }
  },

  /** Manager/Admin: today's status for the whole team, in one request.
   *
   *  The dashboard used to ask per person, on a ten-second timer — twenty
   *  requests every tick, racing each other for the database connection pool,
   *  with some coming back as 503s so those rows showed the wrong status.
   *  Returns a map keyed by employee id, in the same shape as the per-person
   *  call, so the two are interchangeable. */
  getTeamTodayStatus: async (): Promise<Record<string, unknown>> => {
    try {
      const response = await apiClient.get<{ employees?: { employee?: { id: number } }[] }>(
        API_ENDPOINTS.TEAM_TODAY_STATUS,
      );
      const out: Record<string, unknown> = {};
      for (const entry of response.data?.employees || []) {
        const id = entry?.employee?.id;
        if (id != null) out[String(id)] = entry;
      }
      return out;
    } catch (error) {
      console.error('Failed to fetch team today status:', error);
      return {};
    }
  },

  // Manager/Admin: Get subordinate's today status
  getEmployeeTodayStatus: async (employeeId: string) => {
    try {
      const response = await apiClient.get<any>(API_ENDPOINTS.EMPLOYEE_TODAY_STATUS(employeeId));
      return response.data;
    } catch (error) {
      console.error('Failed to fetch employee today status:', error);
      return null;
    }
  },

  // Manager/Admin: Get subordinate's weekly summary
  getEmployeeWeekSummary: async (employeeId: string) => {
    try {
      const response = await apiClient.get<any>(API_ENDPOINTS.EMPLOYEE_WEEK_SUMMARY(employeeId));
      return response.data;
    } catch (error) {
      console.error('Failed to fetch employee week summary:', error);
      return null;
    }
  },

  // Manager/Admin: Get subordinate's attendance history
  getEmployeeAttendanceHistory: async (employeeId: string, startDate?: string, endDate?: string) => {
    try {
      const params: any = {};
      if (startDate) params.start_date = startDate;
      if (endDate) params.end_date = endDate;
      
      const response = await apiClient.get<any[]>(
        API_ENDPOINTS.EMPLOYEE_ATTENDANCE_HISTORY(employeeId),
        { params }
      );
      return response.data || [];
    } catch (error) {
      console.error('Failed to fetch employee attendance history:', error);
      return [];
    }
  },

  // Debug: Try fetching office coordinates from common backend endpoints
  fetchServerOfficeCoordinates: async () => {
    const candidates = [
      '/office',
      '/settings/office',
      '/attendance/office',
      '/config/office',
      '/settings',
      '/admin/settings',
    ];
    const found: Array<{ endpoint: string; data: any }> = [];

    for (const ep of candidates) {
      try {
        const resp = await apiClient.get<any>(ep);
        console.log('[DEBUG] Office candidate ->', ep, resp.data);
        found.push({ endpoint: ep, data: resp.data });
      } catch (err: any) {
        // ignore 404s/unavailable endpoints
        // eslint-disable-next-line no-console
        console.debug('[DEBUG] Office candidate not found:', ep, err?.response?.status || err.message);
      }
    }

    return found;
  },

  /**
   * Location if the device can give one, and the reason if it cannot.
   *
   * Deliberately never rejects. The caller still sends the check-in without
   * coordinates and lets the server decide, so the failure reason is kept for
   * the message rather than used to abandon the attempt.
   */
  tryUserLocation: async (): Promise<LocationAttempt> => {
    try {
      return { position: await attendanceService.getUserLocation(), error: null };
    } catch (error) {
      return {
        position: null,
        error: error instanceof Error ? error.message : 'Location is unavailable on this device.',
      };
    }
  },

  // Get user's current location
  getUserLocation: (): Promise<GeolocationPosition> => {
    return new Promise((resolve, reject) => {
      // If testing mode is enabled, use office coordinates
      if (TESTING.USE_OFFICE_COORDINATES) {
        console.log('🧪 Testing Mode: Using office coordinates');
        const mockPosition: GeolocationPosition = {
          coords: {
            latitude: OFFICE_LOCATION.LATITUDE,
            longitude: OFFICE_LOCATION.LONGITUDE,
            accuracy: 10,
            altitude: null,
            altitudeAccuracy: null,
            heading: null,
            speed: null,
            toJSON: () => ({}),
          },
          timestamp: Date.now(),
          toJSON: () => ({}),
        };
        resolve(mockPosition);
        return;
      }

      // Browsers only expose geolocation in secure contexts (HTTPS or localhost).
      // Accessing the app over plain http:// via a LAN IP silently blocks it.
      if (!window.isSecureContext) {
        reject(new Error(
          'Location is blocked because this page is not loaded over HTTPS. ' +
          'Open the app via http://localhost:8080 on this machine, or serve it over HTTPS to use check-in from other devices.'
        ));
        return;
      }

      if (!navigator.geolocation) {
        reject(new Error('Geolocation is not supported by your browser'));
        return;
      }

      const toErrorMessage = (error: GeolocationPositionError): string => {
        if (error.code === error.PERMISSION_DENIED) {
          return 'Location permission denied. Click the location icon in your browser\'s address bar to allow access, then try again.';
        }
        if (error.code === error.POSITION_UNAVAILABLE) {
          return 'Location unavailable. Please check your device\'s location/GPS settings and try again.';
        }
        return 'Location request timed out. Please try again.';
      };

      // First attempt: precise GPS fix. Desktops without GPS hardware often
      // fail this, so fall back to a network-based (low accuracy) lookup that
      // also accepts a recent cached position.
      navigator.geolocation.getCurrentPosition(
        (position) => resolve(position),
        (error) => {
          if (error.code === error.PERMISSION_DENIED) {
            reject(new Error(toErrorMessage(error)));
            return;
          }
          navigator.geolocation.getCurrentPosition(
            (position) => resolve(position),
            (retryError) => reject(new Error(toErrorMessage(retryError))),
            {
              enableHighAccuracy: false,
              timeout: 15000,
              maximumAge: 300000,
            }
          );
        },
        {
          enableHighAccuracy: true,
          timeout: 10000,
          maximumAge: 0,
        }
      );
    });
  },

  /**
   * What the server sees of this connection.
   *
   * Open it from a machine on the office wifi: `source_ip` is the address
   * that belongs in OFFICE_IP_RANGES, and `matches_office_network` says
   * whether the configured value already covers it. That value is what lets
   * a GPS-less desk PC check in at all.
   */
  getGeofencingDebug: async () => {
    const res = await apiClient.get<{
      source_ip: string;
      matches_office_network: boolean;
      configured_ranges: string[];
      enforce_office_ip: boolean;
      office_geofence: { latitude: number; longitude: number; radius_metres: number };
      note: string;
    }>(API_ENDPOINTS.GEOFENCING_DEBUG);
    return res.data;
  },
};

