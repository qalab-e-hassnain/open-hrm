// User and Auth Types
export type UserRole = 'employee' | 'manager' | 'admin';
export type MaritalStatus = 'single' | 'married';
export type Gender = 'male' | 'female' ;

export interface User {
  id: string;
  name: string;
  email: string;
  role: UserRole;
  gender?: Gender;
  maritalStatus?: MaritalStatus;
  designation: string;
  department?: string;
  avatar?: string;
  phone?: string;
  cnic?: string;
  dateOfBirth?: string;
  fatherName?: string;
  personalEmail?: string;
  residentialAddress?: string;
  // Emergency Contact - Flattened from nested structure
  emergencyContactName?: string;
  emergencyContactPhone?: string;
  // Employment Details - Flattened from nested structure
  managerId?: number | null;
  isActive?: boolean;
  /** Manager with admin-level reach, except over admin accounts. */
  isHr?: boolean;
  /** Reaches the HR area — true for admins and flagged HR managers. */
  hasHrAccess?: boolean;
  /** Rides along on every UserResponse, login included — see
   *  utils/profileCompletion. */
  profileCompletion?: import('../utils/profileCompletion').ProfileCompletion;
  /** True while covering full admin authority for an admin on approved leave today. */
  isActingAdmin?: boolean;
  /** Keeper of the lab register. A flag, not a role: an admin has the same
   *  reach without carrying it. Gates which inventory *screens* are offered —
   *  what may be done to a given item comes from that item's `actions[]`. */
  isLabCustodian?: boolean;
  baseSalary?: number;
  allowance?: number;
  /** Derived server-side over the employee's own month — read-only. */
  hourlyRate?: number;
  overtimeEligible?: boolean;
  /** Contracted hours per DAY: 8 full time, 4 on a part-time contract. */
  standardHours?: number;
  // Document URLs from backend schema
  profilePictureUrl?: string;
  linkedinWelcomePictureUrl?: string;
  cnicFrontUrl?: string;
  cnicBackUrl?: string;
  // Banking Information - Flattened fields to prevent null overwrites
  accountNumber?: string | null;
  bankName?: string | null;
  branchCode?: string | null;
  accountTitle?: string | null;
  // Legacy nested structures - kept for backward compatibility
  emergencyContact?: EmergencyContact;
  /** Printed on the employment letter; the public verify endpoint reads it. */
  employmentVerificationCode?: string;
  employmentDetails?: EmploymentDetails;
  bankingInfo?: BankingInfo;
}

export interface EmergencyContact {
  name: string;
  phone: string;
  relationship: string;
  maritalStatus: string;
}

export interface EmploymentDetails {
  employeeId: string;
  joinDate: string;
  department: string;
  designation: string;
  reportingManager?: string;
  employmentType: string;
}

export interface BankingInfo {
  accountNumber: string;
  bankName: string;
  branchCode: string;
  accountTitle: string;
}

// Leave Types
export interface LeaveType {
  id: string;
  name: string;
  totalDays: number;
  usedDays: number;
  remainingDays: number;
}

export interface LeaveApplication {
  id: string;
  employeeId: string;
  employeeName: string;
  leaveType: string;
  startDate: string;
  endDate: string;
  duration: number;
  resumptionDate: string;
  reason: string;
  status: 'pending' | 'manager_approved' | 'approved' | 'rejected' | 'cancelled';
  document?: File | string;
  // Rejection reason - only populated when status is 'rejected'
  rejectionReason?: string;
  // Manager approval fields
  managerApprovedBy?: string;
  managerApproverName?: string;
  managerApprovedAt?: string;
  // Final approval fields
  approvedBy?: string;
  approvedByName?: string;
  approvedAt?: string;
  appliedDate: string;
  warnings?: string[]; // Warnings from backend (e.g., probation period, advance notice)
}

// Reimbursement Types
export interface ReimbursementRequest {
  id: string;
  employeeId: string;
  employeeName: string;
  employeeEmail?: string;
  amount: number;
  dateUsed: string;
  reason: string;
  reimbursementType?: string;
  status: 'pending' | 'approved' | 'rejected';
  document?: File | string;
  receiptUrl?: string;
  comments?: string;
  rejectionReason?: string;
  approvedBy?: string;
  approvedByName?: string;
  approvedAt?: string;
  /** Can be null even on a paid claim — the payslip recorded no payment date,
   *  and borrowing today's would be a lie. Render "date not recorded". */
  paidAt?: string;
  /** How the money left: swept into a payslip, or handed over separately.
   *  Empty while the claim is unpaid. */
  paidVia?: 'payroll' | 'separate' | '';
  /** The payslip carrying it, when it went out with salary. */
  payrollId?: number | null;
  appliedDate: string;
  createdAt?: string;
  updatedAt?: string;
}

// Payslip Types
export interface PayslipItem {
  description: string;
  gross: number;
  deduction: number;
  net: number;
}

export interface Payslip {
  id: string;
  employeeId: string;
  month: string;
  year: number;
  items: PayslipItem[];
  totalGross: number;
  totalDeduction: number;
  totalNet: number;
}

// Raw payroll record (admin view) — mirrors the backend PayrollResponse.
export type PayrollStatus = 'draft' | 'processed' | 'paid';

export interface PayrollRecord {
  id: number;
  employeeId: number;
  employeeName?: string;
  baseSalary: number;
  allowances: number;
  bonuses: number;
  arrears: number;
  reimbursementTotal: number;
  overtimePay: number;
  deductions: number;
  unpaidLeaveDeduction: number;
  unapprovedLeaveFine: number;
  tax: number;
  /** True when an admin pinned the tax by hand. The slab engine sees only
   *  gross salary, so pension-scheme exemptions have to be entered and
   *  then survive a recompute. */
  taxIsManual?: boolean;
  netSalary: number;
  month: number;
  year: number;
  status: PayrollStatus;
  notes?: string;
}

export type OvertimeStatus = 'pending' | 'approved' | 'rejected';

export interface OvertimeRecord {
  id: number;
  employeeId: number;
  employeeName?: string;
  month: number;
  year: number;
  overtimeHours: number;
  overtimePay: number;
  /** Base salary over the employee's own monthly hours, in PKR. */
  hourlyRate: number;
  /** hourlyRate × overtimeHours — what approval would add to the payslip. */
  estimatedPay: number;
  status: OvertimeStatus;
  /** Who signed it off. An id — the caller resolves the name. */
  approvedBy?: number | null;
  payrollId?: number | null;
}

// Announcement Types
export interface Announcement {
  id: string;
  title: string;
  content: string;
  date: string;
  creatorName?: string;
  isExpanded?: boolean;
}

// Attendance Types
export interface AttendanceRecord {
  id: string;
  employeeId: string;
  date: string;
  checkIn?: string;
  checkOut?: string;
  status: 'present' | 'absent' | 'leave' | 'half-day';
}

// Employee Types (for Manager/Admin)
export interface Employee {
  id: string;
  name: string;
  email: string;
  department: string;
  designation: string;
  status: 'active' | 'inactive';
  employeeId?: string; // Human-facing staff ID, e.g. EMP001 (backend: employee_id)
  role?: UserRole; // Optional: from backend for admin/assign flows
  gender?: Gender;
  attendance?: number;
  avatar?: string;
  joinDate?: string;
  // Additional contact fields
  phone?: string;
  cnic?: string;
  residentialAddress?: string;
  dateOfBirth?: string;
  maritalStatus?: MaritalStatus;
  /** Wedding anniversary — only set when married. */
  anniversaryDate?: string;
  emergencyContactName?: string;
  emergencyContactPhone?: string;
  // Employment details
  managerId?: number | null;
  isActive?: boolean;
  /** Manager with admin-level reach, except over admin accounts. */
  isHr?: boolean;
  baseSalary?: number;
  allowance?: number;
  /** Derived server-side over the employee's own month — read-only. */
  hourlyRate?: number;
  overtimeEligible?: boolean;
  /** Contracted hours per DAY: 8 full time, 4 on a part-time contract. */
  standardHours?: number;
  // Document URLs
  profilePictureUrl?: string;
  linkedinWelcomePictureUrl?: string;
  cnicFrontUrl?: string;
  cnicBackUrl?: string;
  // Banking Information
  accountNumber?: string | null;
  bankName?: string | null;
  branchCode?: string | null;
  accountTitle?: string | null;
  /** Printed on the employment letter; read by the public verify endpoint. */
  employmentVerificationCode?: string;
}

// API Response Types
export interface ApiResponse<T> {
  success: boolean;
  data?: T;
  message?: string;
  error?: string;
  /** Response headers, for the few endpoints that report paging there. */
  headers?: Record<string, string>;
}

export interface PaginatedResponse<T> {
  data: T[];
  total: number;
  page: number;
  limit: number;
  totalPages: number;
}

