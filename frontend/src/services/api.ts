import axios, { type AxiosInstance, type AxiosRequestConfig, type AxiosError, type AxiosResponse } from 'axios';
import { API_BASE_URL, API_ENDPOINTS, STORAGE_KEYS } from '../config/constants';
import { type ApiResponse } from '../types';
import { useAuthStore } from '../store/authStore';

// Set the moment a dead session forces a logout redirect, so a page with a
// "you have unsaved changes" beforeunload guard (Apply Leave, Create User,
// Profile edit) can tell this navigation apart from the user actually
// clicking away — otherwise that guard's confirm dialog can block/cancel the
// redirect, leaving the page logged out in memory but stuck on-screen until
// a manual refresh.
export let isForcedLogoutRedirect = false;

// Create axios instance
const api: AxiosInstance = axios.create({
  baseURL: API_BASE_URL,
  timeout: 30000,
  headers: {
    'Content-Type': 'application/json',
  },
});

// Request interceptor
api.interceptors.request.use(
  (config) => {
    const token = localStorage.getItem(STORAGE_KEYS.AUTH_TOKEN);
    if (token) {
      config.headers.Authorization = `Bearer ${token}`;
    }
    // Any write may change what the short-lived read cache is holding, so
    // the cache is emptied here rather than in each of post/put/patch/delete
    // — this catches calls that go straight to axios as well.
    if ((config.method || 'get').toLowerCase() !== 'get') {
      clearReadCache();
    }
    return config;
  },
  (error) => {
    return Promise.reject(error);
  }
);

// ---- Silent access-token refresh --------------------------------------
// When the access token expires (401), exchange the stored refresh token for
// a new one and transparently retry the failed request. Single-flight: many
// simultaneous 401s share ONE refresh call.
let refreshPromise: Promise<string | null> | null = null;

const refreshAccessToken = async (): Promise<string | null> => {
  const refreshToken = localStorage.getItem(STORAGE_KEYS.REFRESH_TOKEN);
  if (!refreshToken) return null;
  try {
    // Bare axios (NOT `api`) so this call skips our interceptors entirely.
    const res = await axios.post(`${API_BASE_URL}/auth/refresh`, {
      refresh_token: refreshToken,
    });
    const newAccess: string | undefined = res.data?.access_token;
    if (!newAccess) return null;
    localStorage.setItem(STORAGE_KEYS.AUTH_TOKEN, newAccess);
    // Rotation: the server issues a fresh refresh token on every exchange.
    if (res.data?.refresh_token) {
      localStorage.setItem(STORAGE_KEYS.REFRESH_TOKEN, res.data.refresh_token);
    }
    return newAccess;
  } catch {
    return null;
  }
};

// ---- In-flight GET de-duplication ---------------------------------------
// Two components asking for the same thing at the same moment is normal —
// the nav badge and a page both wanting the unread count, a panel and its
// parent both wanting the user. Measured live, a hard page load issued 21
// requests where 12 were distinct, and each analytics endpoint was fetched
// twice within 3ms. Identical GETs already in flight now share one response
// instead of racing each other to the same answer.
//
// GET only, and only while the request is actually in flight: this is not a
// cache. A second call a moment later still goes to the server, so nothing
// here can serve a stale figure.
const inFlight = new Map<string, Promise<AxiosResponse>>();

// A handful of lists are read by almost every screen, so moving between
// pages re-fetched them each time — and against a database this far away
// each one costs the best part of a second. These are held for a few seconds
// after they arrive. Everything else is unaffected, and any write empties the
// cache, so nothing here can show a figure from before a change the user has
// just made.
const CACHEABLE: { match: RegExp; ttlMs: number }[] = [
  { match: /^\/notifications\/unread-count/, ttlMs: 15000 },
  { match: /^\/leaves\/history/, ttlMs: 30000 },
  { match: /^\/reimbursements\/history/, ttlMs: 30000 },
  { match: /^\/users\/?(\?|$)/, ttlMs: 30000 },
  { match: /^\/announcements/, ttlMs: 30000 },
];

const ttlFor = (url: string): number =>
  CACHEABLE.find((entry) => entry.match.test(url))?.ttlMs ?? 0;

const cached = new Map<string, { at: number; response: AxiosResponse }>();

/** Called after every write. A change anywhere may affect any of these
 *  lists, and they are only held for seconds, so emptying the lot is both
 *  correct and cheap. */
export const clearReadCache = (): void => { cached.clear(); };

const sharedGet = (url: string, config?: AxiosRequestConfig): Promise<AxiosResponse> => {
  const key = `${url}?${JSON.stringify(config?.params ?? {})}`;

  const ttl = ttlFor(url);
  if (ttl > 0) {
    const hit = cached.get(key);
    if (hit && Date.now() - hit.at < ttl) return Promise.resolve(hit.response);
  }

  const existing = inFlight.get(key);
  if (existing) return existing;
  const request = api.get(url, config)
    .then((response) => {
      if (ttl > 0) cached.set(key, { at: Date.now(), response });
      return response;
    })
    .finally(() => { inFlight.delete(key); });
  inFlight.set(key, request);
  return request;
};

// ---- Transient-failure retry --------------------------------------------
// The live backend/DB occasionally drops a request outright — a Supabase
// connection-pool hiccup, a cold connection, a momentary 502/503/504.
// Confirmed live while testing (real "Database connection error" 503s), not
// hypothetical. This used to surface directly to whichever page happened to
// be making the call, on every page, with no retry. Centralized here so
// every request through `api` benefits, instead of every page re-implementing
// its own retry wrapper. GET only: retrying a read is always safe, but
// retrying a POST/PUT/PATCH/DELETE after a response is lost risks a
// duplicate submission (double check-in, double leave application, etc.) if
// the original actually succeeded server-side — those surface their error
// normally and leave retrying to the user.
const RETRYABLE_STATUSES = new Set([502, 503, 504]);
const MAX_TRANSIENT_RETRIES = 2;
const RETRY_DELAY_MS = 600;

const isRetryableFailure = (error: AxiosError): boolean => {
  const method = (error.config?.method || 'get').toLowerCase();
  if (method !== 'get') return false;
  if (!error.response) return true; // connection dropped, DNS, timeout, etc.
  return RETRYABLE_STATUSES.has(error.response.status);
};

// Response interceptor
api.interceptors.response.use(
  (response) => response,
  async (error: AxiosError) => {
    const original = error.config as
      | (AxiosError['config'] & { _retry?: boolean; _retryCount?: number })
      | undefined;

    if (original && isRetryableFailure(error)) {
      original._retryCount = (original._retryCount ?? 0) + 1;
      if (original._retryCount <= MAX_TRANSIENT_RETRIES) {
        await new Promise((resolve) => window.setTimeout(resolve, RETRY_DELAY_MS * original!._retryCount!));
        return api(original);
      }
    }

    const url = String(original?.url || '');
    // Never try to refresh the auth endpoints themselves.
    const isAuthRequest = url.includes(API_ENDPOINTS.LOGIN) || url.includes('/auth/refresh');

    if (error.response?.status === 401 && original && !isAuthRequest && !original._retry) {
      original._retry = true; // one retry per request — no loops
      refreshPromise = refreshPromise ?? refreshAccessToken();
      const newToken = await refreshPromise;
      refreshPromise = null;

      if (newToken) {
        original.headers.set('Authorization', `Bearer ${newToken}`);
        return api(original); // seamless retry — caller never sees the 401
      }

      // No refresh token / refresh failed: the session is genuinely over.
      // Clear the in-memory store too, not just localStorage — otherwise the
      // still-mounted page keeps rendering as logged in (stale Zustand state)
      // for however long the redirect below takes, and every request it fires
      // in that window goes out with no Authorization header at all (the
      // backend's literal "Not authenticated" reply, not a token error).
      isForcedLogoutRedirect = true;
      useAuthStore.getState().logout();
      (error as AxiosError & { isSessionExpired?: boolean }).isSessionExpired = true;
      window.location.href = '/login';
    }
    return Promise.reject(error);
  }
);

// API helper functions
export const apiClient = {
  get: async <T>(url: string, config?: AxiosRequestConfig): Promise<ApiResponse<T>> => {
    try {
      const response = await sharedGet(url, config);
      // Backend doesn't wrap in ApiResponse format, return data directly.
      // Headers come too: paged endpoints report the true total in one.
      return { success: true, data: response.data,
               headers: response.headers as Record<string, string> } as ApiResponse<T>;
    } catch (error) {
      throw handleApiError(error);
    }
  },

  post: async <T>(url: string, data?: unknown, config?: AxiosRequestConfig): Promise<ApiResponse<T>> => {
    try {
      const response = await api.post(url, data, config);
      // Backend doesn't wrap in ApiResponse format, return data directly
      return { success: true, data: response.data } as ApiResponse<T>;
    } catch (error) {
      throw handleApiError(error);
    }
  },

  put: async <T>(url: string, data?: unknown, config?: AxiosRequestConfig): Promise<ApiResponse<T>> => {
    try {
      const response = await api.put(url, data, config);
      // Backend doesn't wrap in ApiResponse format, return data directly
      return { success: true, data: response.data } as ApiResponse<T>;
    } catch (error) {
      throw handleApiError(error);
    }
  },

  patch: async <T>(url: string, data?: unknown, config?: AxiosRequestConfig): Promise<ApiResponse<T>> => {
    try {
      const response = await api.patch(url, data, config);
      // Backend doesn't wrap in ApiResponse format, return data directly
      return { success: true, data: response.data } as ApiResponse<T>;
    } catch (error) {
      throw handleApiError(error);
    }
  },

  delete: async <T>(url: string, config?: AxiosRequestConfig): Promise<ApiResponse<T>> => {
    try {
      const response = await api.delete(url, config);
      // Backend doesn't wrap in ApiResponse format, return data directly
      return { success: true, data: response.data } as ApiResponse<T>;
    } catch (error) {
      throw handleApiError(error);
    }
  },

  upload: async <T>(url: string, formData: FormData, config?: AxiosRequestConfig): Promise<ApiResponse<T>> => {
    try {
      const response = await api.post(url, formData, {
        ...config,
        headers: {
          'Content-Type': 'multipart/form-data',
          ...config?.headers,
        },
      });
      // Backend doesn't wrap in ApiResponse format, return data directly
      return { success: true, data: response.data } as ApiResponse<T>;
    } catch (error) {
      throw handleApiError(error);
    }
  },
};

// Generic, friendly copy for status codes when the backend didn't include its
// own detail/message. Keeps the raw HTTP status out of the user's face while
// still being specific enough to act on.
const STATUS_FALLBACK_MESSAGES: Record<number, string> = {
  400: 'This request could not be completed. Please check your input and try again.',
  401: 'Your session has expired. Please log in again.',
  403: "You don't have permission to do this.",
  404: 'The requested item could not be found. It may have been removed or moved.',
  405: 'This action is not supported.',
  408: 'The request timed out. Please try again.',
  409: 'This conflicts with existing data. Please refresh the page and try again.',
  413: 'The file you uploaded is too large.',
  415: 'This file type is not supported.',
  422: 'Some fields are invalid. Please check your input and try again.',
  429: 'Too many requests. Please wait a moment and try again.',
  500: 'Something went wrong on our end. Please try again in a moment.',
  502: 'The server is temporarily unavailable. Please try again shortly.',
  503: 'The service is temporarily unavailable. Please try again shortly.',
  504: 'The server took too long to respond. Please try again.',
};

// 422 validation error responses put multiple errors in `detail` as an array.
// This backend's custom validation handler (see app/main.py) already reformats
// each one into a plain string (e.g. "body -> start_date: Field required"),
// but raw/uncaught Pydantic errors elsewhere use { loc, msg, type } objects —
// handle both shapes rather than assuming one.
const formatValidationErrors = (detail: unknown[]): string => {
  return detail
    .map((err) => {
      if (typeof err === 'string') return err;
      if (err && typeof err === 'object') {
        const e = err as { loc?: unknown[]; msg?: string };
        const field = Array.isArray(e.loc) ? e.loc.slice(1).join('.') : '';
        return field ? `${field}: ${e.msg}` : e.msg || 'Invalid value';
      }
      return 'Invalid value';
    })
    .join('\n');
};

// Turns a raw HTTP status + response body into a single, human-readable
// message. Prefers whatever specific text the backend sent (its `detail` /
// `message`), and only falls back to a generic status-based message when the
// backend gave us nothing usable — e.g. an nginx/proxy error page (HTML), a
// plain-text 500 from an unhandled server exception, or an empty body.
const friendlyMessageFor = (status: number, data: unknown): string => {
  if (data && typeof data === 'object') {
    const body = data as { detail?: unknown; message?: unknown };
    if (typeof body.detail === 'string' && body.detail.trim()) {
      return body.detail;
    }
    if (Array.isArray(body.detail) && body.detail.length > 0) {
      return formatValidationErrors(body.detail);
    }
    if (typeof body.message === 'string' && body.message.trim()) {
      return body.message;
    }
  }
  return STATUS_FALLBACK_MESSAGES[status] || `Something went wrong (error ${status}). Please try again.`;
};

// Error handler
const handleApiError = (error: unknown): Error => {
  if (axios.isAxiosError(error)) {
    // Network error - backend is unreachable
    if (!error.response) {
      // This happens when:
      // - Backend server is down/not running
      // - Network is disconnected
      // - CORS or connectivity issue
      const networkError = new Error(
        `Network Error: Cannot reach backend server at ${API_BASE_URL}. ` +
        `Please check your connection or make sure the backend is running.`
      );
      console.error('🔴 NETWORK ERROR:', {
        message: error.message,
        code: error.code,
        endpoint: error.config?.url,
      });
      throw networkError;
    }

    // Server responded with error status.
    //
    // A 404 is usually an answer, not a fault — "this employee has no payslip
    // yet" is a normal state of the world, and shouting about it in the
    // console made an empty screen look like a broken one. It is still
    // recorded, just not as an error.
    const detail = {
      status: error.response.status,
      data: error.response.data,
      message: error.message,
      endpoint: error.config?.url,
    };
    if (error.response.status === 404) {
      console.debug('API 404 (treated as empty):', detail);
    } else {
      console.error('🔴 API Error Response:', detail);
    }

    // Axios's default error.message is a bare "Request failed with status
    // code 404" with no context. Overwrite it with a clean, human-readable
    // message so every call site that does `alert(error.message)` or
    // renders it in the UI shows something useful by default — while still
    // throwing the original AxiosError (not a new Error) so any call site
    // that specifically inspects error.response/.status keeps working.
    //
    // A session the silent-refresh flow already gave up on (see the response
    // interceptor above) would otherwise show the backend's literal
    // "Not authenticated" here — technically accurate (the redirect strips
    // the token before this request's retry goes out) but reads as a bug
    // rather than what it is: log in again.
    error.message = (error as AxiosError & { isSessionExpired?: boolean }).isSessionExpired
      ? 'Your session has expired. Please log in again.'
      : friendlyMessageFor(error.response.status, error.response.data);
    throw error;
  }
  return new Error('An unexpected error occurred. Please try again.');
};

export default api;

