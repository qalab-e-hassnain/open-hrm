// API Configuration
// Default to a same-origin relative path: in the Docker deployment Nginx
// proxies /api/v1 to the backend container, so no host needs hardcoding.
export const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || '/api/v1';

// API Endpoints
export const API_ENDPOINTS = {
  // Auth
  LOGIN: '/auth/login-json',
  // Tells the server to revoke the refresh token's whole session. Signing out
  // used to be purely local, which left the refresh token this browser had
  // been given working for another week.
  LOGOUT: '/auth/logout',

  // User
  PROFILE: '/users/me',
  UPDATE_PROFILE: '/users/me',
  CHANGE_PASSWORD: '/users/me/change-password',
  // Admin-only: set another user's password without their current one.
  ADMIN_RESET_PASSWORD: (userId: string | number) => `/users/${userId}/reset-password`,
  // Public: confirms an employment letter from the code printed on it.
  VERIFY_EMPLOYMENT: (code: string) => `/users/verify-employment/${code}`,

  // Leave
  LEAVE_APPLICATIONS: '/leaves/',
  LEAVE_HISTORY: '/leaves/history',
  APPLY_LEAVE: '/leaves/',
  UPCOMING_LEAVES: '/leaves/pending',
  // Admin-only: permanently remove a finished record from the history. The
  // backend refuses anything still awaiting a decision, and anything payroll
  // depends on (approved unpaid leave, a claim already on a payslip).
  LEAVE_PURGE: (id: string | number) => `/leaves/${id}/purge`,
  // Withdraw a still-live request (pending/approved). The backend keeps the
  // row and flips it to 'cancelled' rather than deleting it, and restores
  // the balance if it had already been approved.
  LEAVE_CANCEL: (id: string | number) => `/leaves/${id}`,
  REIMBURSEMENT_PURGE: (id: string | number) => `/reimbursements/${id}/purge`,

  // Reimbursement
  REIMBURSEMENT_REQUESTS: '/reimbursements/',
  REIMBURSEMENT_HISTORY: '/reimbursements/history',
  APPLY_REIMBURSEMENT: '/reimbursements/',
  // Records that an approved claim has actually been paid out. The bulk form
  // takes a bare array of ids and is all-or-nothing, so a half-settled batch
  // cannot happen.
  REIMBURSEMENT_MARK_PAID: (id: string | number) => `/reimbursements/${id}/paid`,
  REIMBURSEMENTS_MARK_PAID: '/reimbursements/mark-paid',

  // Payslip — the assembled document (see backend app/api/v1/payslips.py).
  // The legacy /payroll/payslips list is kept for anything still reading the
  // raw payroll records.
  PAYSLIPS: '/payroll/payslips',
  PAYSLIP_BY_ID: (id: string) => `/payroll/${id}`,
  PAYSLIP_LIST: '/payslips/',
  PAYSLIP_DETAIL: (id: number) => `/payslips/${id}`,
  PAYSLIP_PERIOD: '/payslips/period',
  PAYSLIP_YTD: '/payslips/ytd',
  PAYSLIP_VERSIONS: (id: number) => `/payslips/${id}/versions`,
  PAYSLIP_VERIFY: (code: string) => `/payslips/verify/${code}`,
  PAYSLIP_EMAIL: (id: number) => `/payslips/${id}/email`,
  PAYSLIP_PUBLISH: (id: number) => `/payslips/${id}/publish`,
  PAYSLIP_CORRECT: (id: number) => `/payslips/${id}/correct`,
  PAYSLIP_CANCEL: (id: number) => `/payslips/${id}/cancel`,
  PAYSLIP_DISPUTES: (id: number) => `/payslips/${id}/disputes`,
  PAYSLIP_DISPUTE_LIST: '/payslips/disputes/list',
  PAYSLIP_DISPUTE_RESPOND: (id: number) => `/payslips/disputes/${id}/respond`,

  // Tax Certificate (annual salary tax statement)
  TAX_CERTIFICATE_ME: '/payroll/tax-certificate/me',
  TAX_CERTIFICATE_BY_ID: (id: string) => `/payroll/tax-certificate/${id}`,

  // Payroll (Admin)
  PAYROLL: '/payroll/',
  PAYROLL_BY_ID: (id: number) => `/payroll/${id}`,
  PAYROLL_RECOMPUTE: (id: number) => `/payroll/${id}/recompute`,
  PAYROLL_PROCESS: (id: number) => `/payroll/${id}/process`,
  PAYROLL_PAID: (id: number) => `/payroll/${id}/paid`,
  // Payroll Sheet — monthly runs (Admin)
  PAYROLL_RUNS: '/payroll/runs',
  PAYROLL_RUN_BY_ID: (runId: number) => `/payroll/runs/${runId}`,
  PAYROLL_RUN_ENTRIES: (runId: number) => `/payroll/runs/${runId}/entries`,
  PAYROLL_RUN_ENTRY: (runId: number, entryId: number) => `/payroll/runs/${runId}/entries/${entryId}`,
  PAYROLL_RUN_FINALIZE: (runId: number) => `/payroll/runs/${runId}/finalize`,
  PAYROLL_RUN_REOPEN: (runId: number) => `/payroll/runs/${runId}/reopen`,
  PAYROLL_RUN_EXPORT: (runId: number) => `/payroll/runs/${runId}/export`,
  // Overtime (Admin)
  OVERTIME_COMPUTE: '/payroll/overtime/compute',
  OVERTIME_LIST: '/payroll/overtime',
  OVERTIME_APPROVE: (id: number) => `/payroll/overtime/${id}/approve`,

  // Attendance
  ATTENDANCE: '/attendance/',
  CHECK_IN: '/attendance/checkin',
  CHECK_OUT: '/attendance/checkout',
  DAILY_STATUS: '/attendance/daily/status',      // Enhanced: Now includes overtime!
  DAILY_REPORT: '/attendance/daily/report',      // Date range report
  TODAY_ATTENDANCE: '/attendance/today',
  WEEKLY_OVERTIME: '/attendance/weekly/overtime', // Weekly overtime summary
  MONTHLY_OVERTIME: '/attendance/monthly/overtime', // Monthly overtime report
  ATTENDANCE_FILTER: '/attendance/filter',       // New: Flexible filtering endpoint!
  
  // Manager/Admin: View Subordinate Attendance
  EMPLOYEE_TODAY_STATUS: (employeeId: string) => `/attendance/employee/${employeeId}/today`,
  // Everyone's status in one request, instead of one request per person.
  TEAM_TODAY_STATUS: '/attendance/team/today',
  EMPLOYEE_WEEK_SUMMARY: (employeeId: string) => `/attendance/employee/${employeeId}/week`,
  EMPLOYEE_ATTENDANCE_HISTORY: (employeeId: string) => `/attendance/employee/${employeeId}`,
  
  // Announcements
  ANNOUNCEMENTS: '/announcements',
  
  // Employees (Manager/Admin)
  EMPLOYEES: '/users/',
  EMPLOYEES_ADMIN_DASHBOARD: '/users/admin/dashboard',
  EMPLOYEES_ADMIN_ALL: '/users/admin/all',
  EMPLOYEES_ADMIN_ALL_DETAILED: '/users/admin/all-detailed',
  EMPLOYEES_ADMIN_EMPLOYEES: '/users/admin/employees',
  EMPLOYEES_ADMIN_MANAGERS: '/users/admin/managers',
  EMPLOYEE_BY_ID: (id: string) => `/users/${id}`,
  REACTIVATE_EMPLOYEE: (id: string) => `/users/${id}/reactivate`,
  ASSIGN_MANAGER: (userId: string) => `/users/${userId}/assign-manager`,
  TEAM_OVERVIEW: '/users/team',

  // Teams
  TEAMS: '/teams/',
  TEAM_BY_ID: (id: string) => `/teams/${id}`,
  TEAM_MEMBERS: (id: string) => `/teams/${id}/members`,
  ASSIGN_TEAM_MANAGER: (id: string) => `/teams/${id}`,
  
  // Time logs — manually claimed attendance and overtime, behind two signatures
  TIME_LOGS: '/time-logs/',
  TIME_LOG_SUGGEST: '/time-logs/suggest',
  TIME_LOGS_ME: '/time-logs/me',
  TIME_LOGS_PENDING: '/time-logs/pending',
  TIME_LOGS_EMPLOYEE: (id: string | number) => `/time-logs/employee/${id}`,
  TIME_LOG_DECIDE: (id: number) => `/time-logs/${id}/decide`,
  TIME_LOG_BY_ID: (id: number) => `/time-logs/${id}`,

  // Employee lifecycle — signed documents, onboarding, offboarding
  DOCUMENTS_ME: '/documents/me',
  JOB_PROFILES: '/documents/job-profiles',
  JOB_PROFILE: (id: number) => `/documents/job-profiles/${id}`,
  DOC_TEMPLATES: '/documents/templates',
  DOC_PLACEHOLDERS: '/documents/placeholders',
  DOC_STARTERS: '/documents/starters',
  DOC_TEMPLATE: (id: number) => `/documents/templates/${id}`,
  DOC_TEMPLATE_PREVIEW: (templateId: number, employeeId: number) =>
    `/documents/templates/${templateId}/preview/${employeeId}`,
  DOCUMENT_ISSUE: '/documents/issue',
  DOCUMENT_BY_ID: (id: number) => `/documents/${id}`,
  DOCUMENT_SIGN: (id: number) => `/documents/${id}/sign`,
  DOCUMENT_DECLINE: (id: number) => `/documents/${id}/decline`,
  // Public: a third party confirming a contract or NOC.
  DOCUMENT_VERIFY: (code: string) => `/documents/verify/${code}`,
  ONBOARDING_ME: '/onboarding/me',
  ONBOARDING_ALL: '/onboarding/',
  ONBOARDING_TASK: (id: number) => `/onboarding/tasks/${id}`,
  RESIGNATIONS: '/offboarding/resignations',
  RESIGNATION_ME: '/offboarding/resignations/me',
  RESIGNATION_DECIDE: (id: number) => `/offboarding/resignations/${id}/decide`,
  RESIGNATION_WITHDRAW: (id: number) => `/offboarding/resignations/${id}/withdraw`,
  RESIGNATION_COMPLETE: (id: number) => `/offboarding/resignations/${id}/complete`,
  CLEARANCE_ITEM: (id: number) => `/offboarding/clearance/${id}`,

  // Notifications
  // Trailing slash is required: the backend declares this as @router.get("/")
  // under the /notifications prefix, so requesting it without the slash returns
  // a 307 whose Location is an absolute http://host:8000/... URL. Through the
  // dev/nginx proxy that redirect points off-origin, the browser abandons it,
  // and the list silently came back empty while the unread-count (an exact
  // route match) kept working.
  NOTIFICATIONS: '/notifications/',
  MARK_READ: '/notifications/mark-read',
  MARK_ALL_READ: '/notifications/mark-all-read',
  UNREAD_COUNT: '/notifications/unread-count',

  // ---------------------------------------------------------------------
  // Backend v2 — nine features, September 2026. All on the same bearer
  // token. Enums are lowercase snake_case in both directions, and a 400
  // carries a sentence meant for a person in `detail`: surface it as-is
  // rather than mapping it to our own copy.
  // ---------------------------------------------------------------------

  // Scheduled jobs — four sweeps that run off portal traffic. This panel is
  // the only way to tell whether they are running at all.
  ADMIN_JOBS: '/admin/jobs',
  ADMIN_JOB_RUN: (name: string) => `/admin/jobs/${name}/run`,
  // Unhandled exceptions the sweeps (and the API) have recorded. Admin/HR.
  ADMIN_JOB_ERRORS: '/admin/jobs/errors',

  // Fingerprint device. Every route here is admin/HR on the server.
  BIOMETRIC_SCANS: '/biometric/scans',
  BIOMETRIC_IDENTITIES: '/biometric/identities',
  BIOMETRIC_IDENTITY: (id: string | number) => `/biometric/identities/${id}`,
  BIOMETRIC_SYNC: '/biometric/sync',
  BIOMETRIC_RECONCILE: '/biometric/reconcile',

  // What the server sees of this connection — the address to put in
  // OFFICE_IP_RANGES, and whether the current value already covers it.
  GEOFENCING_DEBUG: '/attendance/debug/geofencing',

  // Reimbursements still awaiting a decision (manager/admin).
  REIMBURSEMENTS_PENDING: '/reimbursements/pending',

  // Who reports to the signed-in manager.
  SUBORDINATES: '/users/subordinates/list',
  // The teams the signed-in user belongs to.
  MY_TEAMS: '/teams/my/teams',
  TEAM_MEMBER: (teamId: string | number, userId: string | number) =>
    `/teams/${teamId}/members/${userId}`,

  // Payroll the signed-in employee may read for themselves.
  MY_PAYROLL: '/payroll/me',
  MY_OVERTIME: '/payroll/overtime/me',
  // Admin/HR: somebody else's payroll, and settling a batch of payslips.
  EMPLOYEE_PAYROLL: (employeeId: string | number) => `/payroll/employee/${employeeId}`,
  PAYROLL_MARK_PAID: '/payroll/mark-paid',
  PAYROLL_RUN_AUDIT: (runId: string | number) => `/payroll/runs/${runId}/audit`,

  // Onboarding: start a packet (admin/HR), read somebody's (own, or privileged).
  ONBOARDING_START: '/onboarding/start',
  ONBOARDING_FOR: (employeeId: string | number) => `/onboarding/employee/${employeeId}`,

  // Documents issued to one employee.
  DOCUMENTS_FOR: (employeeId: string | number) => `/documents/employee/${employeeId}`,
  // Attach CNIC scans and the like to a user record.
  USER_DOCUMENTS: (userId: string | number) => `/users/${userId}/documents`,

  // A short-lived link for a private file, for anything an <img> cannot carry
  // an Authorization header to.
  SIGNED_URL: (storedName: string) => `/upload/${storedName}/signed-url`,

  // Final settlement — gates offboarding completion.
  SETTLEMENT_FOR_RESIGNATION: (id: number) => `/settlements/resignation/${id}`,
  SETTLEMENTS: '/settlements',
  SETTLEMENT_ME: '/settlements/me',
  SETTLEMENT: (id: number) => `/settlements/${id}`,
  SETTLEMENT_APPROVE: (id: number) => `/settlements/${id}/approve`,
  SETTLEMENT_REOPEN: (id: number) => `/settlements/${id}/reopen`,
  SETTLEMENT_MARK_PAID: (id: number) => `/settlements/${id}/mark-paid`,

  // Lab inventory (/api/v1/inventory). Replaced the old /assets register,
  // whose deprecation shim the backend is removing — see the frontend guide, §11.
  INVENTORY: {
    ITEMS: '/inventory/items',
    ITEM: (id: number) => `/inventory/items/${id}`,
    // The scan endpoint. One GET returns the item, the permitted actions and
    // whether the caller is the holder, so the scan screen needs nothing else.
    BY_TAG: (tag: string) => `/inventory/items/by-tag/${encodeURIComponent(tag)}`,
    MOVEMENTS: (id: number) => `/inventory/items/${id}/movements`,
    // Every custody action is POST /items/{id}/{verb} returning the updated
    // item, so one builder covers all seventeen of them.
    ACTION: (id: number, verb: string) => `/inventory/items/${id}/${verb}`,

    ME_HOLDINGS: '/inventory/me/holdings',
    EMPLOYEE_HOLDINGS: (id: number) => `/inventory/employees/${id}/holdings`,

    CATEGORIES: '/inventory/categories',
    CATEGORY: (id: number) => `/inventory/categories/${id}`,
    LOCATIONS: '/inventory/locations',
    LOCATION: (id: number) => `/inventory/locations/${id}`,

    REQUESTS: '/inventory/requests',
    REQUEST_DECISION: (id: number) => `/inventory/requests/${id}/decision`,
    REQUEST_ISSUE: (id: number) => `/inventory/requests/${id}/issue`,
    REQUEST_CANCEL: (id: number) => `/inventory/requests/${id}/cancel`,

    AUDIT_SESSIONS: '/inventory/audit/sessions',
    AUDIT_SESSION: (id: number) => `/inventory/audit/sessions/${id}`,
    AUDIT_SCAN: (id: number) => `/inventory/audit/sessions/${id}/scan`,
    AUDIT_CLOSE: (id: number) => `/inventory/audit/sessions/${id}/close`,

    REPORT: (name: string) => `/inventory/reports/${name}`,
  },

  // Probation.
  PROBATION_DUE: '/probation/due',
  PROBATION_ME: '/probation/me',
  PROBATION_EMPLOYEE: (id: number) => `/probation/${id}`,
  PROBATION_DECIDE: (id: number) => `/probation/${id}/decide`,
  PROBATION_SETUP: (id: number) => `/probation/${id}/setup`,
  PROBATION_BULK_CONFIRM: '/probation/bulk-confirm',

  // Org chart — an array of roots, not one root.
  ORG_CHART: '/users/org-chart',

  // Performance.
  PERF_CYCLES: '/performance/cycles',
  PERF_CYCLE_OPEN: (id: number) => `/performance/cycles/${id}/open`,
  PERF_CYCLE_CLOSE: (id: number) => `/performance/cycles/${id}/close`,
  PERF_GOALS: '/performance/goals',
  PERF_GOAL: (id: number) => `/performance/goals/${id}`,
  PERF_GOALS_SEED: '/performance/goals/seed-from-profile',
  PERF_REVIEWS: '/performance/reviews',
  PERF_REVIEW: (id: number) => `/performance/reviews/${id}`,
  PERF_REVIEW_SELF: (id: number) => `/performance/reviews/${id}/self`,
  PERF_REVIEW_MANAGER: (id: number) => `/performance/reviews/${id}/manager`,
  PERF_REVIEW_SHARE: (id: number) => `/performance/reviews/${id}/share`,
  PERF_REVIEW_ACK: (id: number) => `/performance/reviews/${id}/acknowledge`,
  PERF_ONE_ON_ONES: '/performance/one-on-ones',
  PERF_ONE_ON_ONE: (id: number) => `/performance/one-on-ones/${id}`,

  // Advances and loans. Approved is not paid: nothing is recovered until
  // the loan is disbursed.
  LOANS: '/loans',
  LOANS_ME: '/loans/me',
  LOAN: (id: number) => `/loans/${id}`,
  LOAN_DECIDE: (id: number) => `/loans/${id}/decide`,
  LOAN_DISBURSE: (id: number) => `/loans/${id}/disburse`,
  LOAN_INSTALMENT_WAIVE: (id: number, instalmentId: number) =>
    `/loans/${id}/instalments/${instalmentId}/waive`,
  LOAN_OUTSTANDING: (employeeId: number) => `/loans/outstanding/${employeeId}`,

  // Shifts — which hours, not just how many. An assigned shift drives the
  // automatic checkout cap in place of the flat standard_hours.
  SHIFTS: '/shifts',
  SHIFT: (id: number) => `/shifts/${id}`,
  SHIFT_ASSIGNMENTS: '/shifts/assignments',
  SHIFTS_ME: '/shifts/me',
  SHIFTS_EMPLOYEE: (id: number) => `/shifts/employee/${id}`,

  // Performance v2 — the review is a form the server describes, a score it
  // computes, and a monthly award decided beside it.
  PERF_FORM: (id: number) => `/performance/reviews/${id}/form`,
  PERF_SCORE: (id: number) => `/performance/reviews/${id}/score`,
  PERF_RATING_SCALE: '/performance/rating-scale',
  PERF_TEMPLATES: '/performance/templates',
  PERF_TEMPLATE_FOR: (designation: string) =>
    `/performance/templates/for/${encodeURIComponent(designation)}`,
  PERF_TEMPLATE: (id: number) => `/performance/templates/${id}`,
  PERF_LEADERBOARD: '/performance/leaderboard',
  PERF_CHARTS_CYCLE: (id: number) => `/performance/charts/cycle/${id}`,
  PERF_CHARTS_EMPLOYEE: (id: number) => `/performance/charts/employee/${id}`,
  AWARDS: '/performance/awards',
  AWARD_CURRENT: '/performance/awards/current',
  AWARD_NOMINATE: (id: number) => `/performance/awards/${id}/nominate`,
  AWARD_WITHDRAW: (id: number, nomineeId: number) =>
    `/performance/awards/${id}/nominate/${nomineeId}`,
  AWARD_SIGNALS: (id: number) => `/performance/awards/${id}/signals`,
  AWARD_DECIDE: (id: number) => `/performance/awards/${id}/decide`,

  // Analytics — every one of these is privileged.
  ANALYTICS_OVERVIEW: '/analytics/overview',
  ANALYTICS_HEADCOUNT: '/analytics/headcount',
  ANALYTICS_LEAVE: '/analytics/leave',
  ANALYTICS_PAYROLL: '/analytics/payroll',
  ANALYTICS_OVERTIME: '/analytics/overtime',
  ANALYTICS_ATTENDANCE: '/analytics/attendance',
  ANALYTICS_COMPENSATION: '/analytics/compensation',
  ANALYTICS_ATTENTION: '/analytics/attention',
  MY_PROFILE_COMPLETION: '/users/me/profile-completion',
  INCOMPLETE_PROFILES: '/users/incomplete-profiles',
};

// Company details for documents the API does not already carry them on.
// Mirrors the backend's app/config.py (COMPANY_NAME / COMPANY_REG_NO /
// COMPANY_ADDRESS / COMPANY_EMAIL) — payslips and tax certificates get these
// from their own endpoints, so only the employment letter reads this copy.
// A small GET /company endpoint would remove the duplication.
/**
 * Who is running this deployment.
 *
 * Every value is read from the build environment, because this system is
 * handed to other companies to run for their own staff — a company name
 * compiled into the source cannot be theirs. Copy .env.example to .env,
 * fill it in, and build; see DEPLOYMENT.md.
 *
 * The fallbacks are what a build with no .env produces, which keeps local
 * development working and makes a missed variable obvious on screen rather
 * than rendering an empty string.
 */
const env = import.meta.env;

export const COMPANY = {
  /** The registered legal name, as it appears on documents. */
  NAME: env.VITE_COMPANY_NAME || 'Your Company (Pvt) Ltd',
  /** Tax or company registration number, printed on letters. */
  NTN: env.VITE_COMPANY_REG_NO || '',
  ADDRESS: env.VITE_COMPANY_ADDRESS || '',
  EMAIL: env.VITE_COMPANY_EMAIL || '',
  // What the company is called in a sentence, as opposed to NAME, which is
  // what it is registered as. Used under the mark on the badge, where the
  // registered name does not fit.
  SHORT_NAME: env.VITE_COMPANY_SHORT_NAME || 'Your Company',
  WEBSITE: env.VITE_COMPANY_WEBSITE || '',
  // Whoever the company's letters are issued over. Configuration rather than
  // a lookup of whoever holds the title in the database: this is the person
  // who signs a company document, which is a decision, not a query.
  SIGNATORY_NAME: env.VITE_SIGNATORY_NAME || '',
  SIGNATORY_TITLE: env.VITE_SIGNATORY_TITLE || '',
};

/**
 * The product name shown in the sidebar, on the login screen and in the tab
 * title. Separate from COMPANY.NAME: the company is who you work for, this
 * is what the software is called.
 */
export const BRAND_NAME = env.VITE_BRAND_NAME || 'HR Portal';

// Where the mobile app lives. The badge renders nothing if this is empty,
// so a listing that is not live yet never ships as a dead link.
// `pcampaignid=web_share` is dropped: it is Play's share-attribution tag from
// whoever copied the link, and tagging every employee's tap with it would
// attribute our own installs to a share that did not happen.
export const APP_LINKS = {
  // Your own listing, if you have one. Empty renders no badge.
  PLAY_STORE: env.VITE_PLAY_STORE_URL || '',
};

// Local Storage Keys
export const STORAGE_KEYS = {
  AUTH_TOKEN: 'auth_token',
  REFRESH_TOKEN: 'refresh_token',
  USER_DATA: 'user_data',
};

// App Routes
export const ROUTES = {
  // Public
  LOGIN: '/login',
  REGISTER: '/register',
  TERMS: '/terms', // Public: Terms & Privacy Policy
  // Public: a bank, landlord or prospective employer checks a payslip is
  // genuine from the code printed on it, without an account here.
  VERIFY_PAYSLIP: '/verify-payslip',
  EMPLOYMENT_LETTER: '/employment-letter',
  // Public: the target of the QR printed on an employment letter.
  VERIFY_EMPLOYMENT: '/verify-employment',

  // Employee
  DASHBOARD: '/dashboard',
  PROFILE: '/profile',
  LEAVES: '/leaves',
  REIMBURSEMENTS: '/reimbursements',
  PAYSLIPS: '/payslips',
  TAX_CERTIFICATE: '/tax-certificate',
  ATTENDANCE_HISTORY: '/attendance-history',
  LEAVE_POLICY: '/leave-policy',
  HANDBOOK: '/handbook',
  TIME_LOGS: '/time-logs',
  DOCUMENTS: '/documents',
  HR_DOCUMENTS: '/hr/documents',
  ONBOARDING: '/onboarding',
  OFFBOARDING: '/offboarding',
  VERIFY_DOCUMENT: '/verify-document',
  PROBATION: '/probation',
  ADMIN_JOBS: '/admin/jobs',
  BIOMETRIC: '/admin/biometric',
  // Lab inventory. SCAN is the one people reach for; the rest hang off it.
  INVENTORY_SCAN: '/inventory/scan',
  INVENTORY_ITEMS: '/inventory/items',
  INVENTORY_ITEM: (id: number | string) => `/inventory/items/${id}`,
  INVENTORY_RECEIVE: '/inventory/receive',
  INVENTORY_REQUESTS: '/inventory/requests',
  INVENTORY_AUDIT: '/inventory/audit',
  INVENTORY_AUDIT_SESSION: (id: number | string) => `/inventory/audit/${id}`,
  INVENTORY_REPORTS: '/inventory/reports',
  // Office equipment: the same register's other section — laptops, phones,
  // SIMs, access cards. Admin and HR only.
  OFFICE_EQUIPMENT: '/office-equipment',
  OFFICE_EQUIPMENT_NEW: '/office-equipment/new',
  OFFICE_EQUIPMENT_REPORTS: '/office-equipment/reports',
  MY_EQUIPMENT: '/my-equipment',
  ORG_CHART: '/org-chart',
  ANALYTICS: '/analytics',
  LOANS: '/advances',
  SHIFTS: '/shifts',
  PERFORMANCE: '/performance',
  AWARDS: '/awards',
  LEADERBOARD: '/leaderboard',
  
  // Backward compatibility routes (redirect to consolidated pages)
  APPLY_LEAVE: '/apply-leave',
  REIMBURSEMENT: '/reimbursement',
  
  // Manager (kept for backward compatibility, but redirects to LEAVES/REIMBURSEMENTS)
  MANAGE_LEAVES: '/manage-leaves',
  MANAGE_REIMBURSEMENT: '/manage-reimbursement',
  
  // Admin
  EMPLOYEES: '/employees',
  PAYROLL: '/payroll',
  
  // Common
  NOT_FOUND: '/404',
};

// Leave Types
export const LEAVE_TYPES = {
  ANNUAL: 'Annual Leave',
  SICK: 'Sick Leave',
  COMPASSIONATE: 'Compassionate Leave',
  CASUAL: 'Casual Leave',
  MATERNITY: 'Maternity Leave',
  PATERNITY: 'Paternity Leave',
};

// Status Colors
export const STATUS_COLORS = {
  pending: '#FFA500',
  approved: '#4CAF50',
  rejected: '#F44336',
  active: '#4CAF50',
  inactive: '#9E9E9E',
};

// Pagination
export const PAGINATION = {
  DEFAULT_PAGE: 1,
  DEFAULT_LIMIT: 10,
};

// Office Location (for geofencing)
export const OFFICE_LOCATION = {
  LATITUDE: 33.64604610906695,
  LONGITUDE: 72.99645801770376,
  NAME: 'NSTP Islamabad Office',
};

// Testing Configuration
export const TESTING = {
  // Set to true to use MOCK office coordinates for testing attendance check-in/out
  // When true: Uses pre-defined office location (23.646159, 72.996943)
  // When false: Uses actual browser geolocation (real GPS)
  USE_OFFICE_COORDINATES: false // Use real browser geolocation
};
