import { apiClient } from './api';
import { API_ENDPOINTS } from '../config/constants';
import { type LeaveApplication } from '../types';
import { employeeService } from './employeeService';

/**
 * Every leave status the API defines. The approver screen offered only
 * "Awaiting decision", so an approved or rejected request could not be found
 * from the screen that decided it.
 *
 * Mirrors LeaveStatusEnum on the server. manager_approved is included because
 * historical rows still carry it, even though nothing sets it any more.
 */
export const LEAVE_STATUSES = [
  { value: 'pending', label: 'Awaiting decision' },
  { value: 'approved', label: 'Approved' },
  { value: 'rejected', label: 'Rejected' },
  { value: 'cancelled', label: 'Cancelled' },
  { value: 'manager_approved', label: 'Approved by manager (historical)' },
] as const;


export interface ApplyLeaveData {
  leaveType: string;
  startDate: string;
  endDate: string;
  duration: number;
  resumptionDate: string;
  reason: string;
  document?: File;
  documentUrl?: string;
  // Admin-only: who should cover full admin authority while this leave is
  // approved and active. Omitted/undefined keeps the default (HR covers).
  actingAdminId?: string;
}

/**
 * How this year's annual entitlement was arrived at. A month is credited only
 * once it has ended, and the exact figure is rounded UP to a whole day — which
 * is why three months served shows as four days.
 */
export interface AnnualAccrual {
  months_served: number;
  counted_from: string;
  rate_per_month: number;
  rate_per_year: number;
  exact: number;
  rounded_up_to: number;
  resets_on: string;
  explanation: string;
}

// Shape of one leave type inside the /leaves/balance response
export interface LeaveTypeBalance {
  total: number;
  used: number;
  remaining: number;
  accrued?: number;
  carryover?: number;
  /** Zero now: nothing carries into the new year. */
  maxCarryover?: number;
  /** Annual only — the working behind `total`. */
  accrual?: AnnualAccrual;
  /** Sick only. Zero means documentation is expected from the first day. */
  medicalCertRequiredAfter?: number;
  /** Personal only: which month these figures describe, e.g. "2026-09".
   *  Personal leave is a monthly allowance, not a slice of a yearly pool. */
  period?: string;
  /** Personal only, for reporting. NEVER subtract it from anything. */
  usedThisYear?: number;
  accrualRate?: string;
  explanation?: string;
  duration?: string;
  maxDays?: number;
}

/** Policy text served with the balance, so a policy change reaches the screen
 *  without a frontend release. */
export interface LeavePolicyInfo {
  unapprovedLeavePenalty?: string;
  plannedLeaveNotice?: string;
  personalLeaveNotice?: string;
  weekendPolicy?: string;
}

export interface LeaveBalanceSummary {
  employeeId: string;
  employeeName: string;
  year: number;
  probationStatus: string;
  balances: Record<string, LeaveTypeBalance>; // keyed by annual/sick/personal/maternity/paternity/bereavement
  totalUsed: number;
  totalRemaining: number;
  /** Types this employee may request — the server decides, so the form and the
   *  validation cannot disagree. Maternity/paternity appear for married
   *  employees of the matching gender only. */
  eligibleLeaveTypes: string[];
  maritalStatus?: string;
  policyInfo: LeavePolicyInfo;
}

const mapBalanceResponse = (data: any): LeaveBalanceSummary => {
  const balances: Record<string, LeaveTypeBalance> = {};
  let totalUsed = 0;
  let totalRemaining = 0;
  const raw = data?.leave_balances || {};
  Object.keys(raw).forEach((key) => {
    const b = raw[key] || {};
    const entry: LeaveTypeBalance = {
      total: b.total || 0,
      used: b.used || 0,
      // Rendered as sent. `total - used` is not the rule for personal leave,
      // and annual rounds server-side, so recomputing it here gets both wrong.
      remaining: b.remaining || 0,
      accrued: b.accrued,
      carryover: b.carryover,
      maxCarryover: b.max_carryover,
      accrual: b.accrual,
      // Distinguish "not sent" from a real 0 — 0 means a note is expected from
      // the first sick day, which is the opposite of "no note needed".
      medicalCertRequiredAfter: typeof b.medical_cert_required_after === 'number'
        ? b.medical_cert_required_after : undefined,
      period: b.period,
      usedThisYear: b.used_this_year,
      accrualRate: b.accrual_rate,
      explanation: b.explanation,
      duration: b.duration,
      maxDays: b.max_days,
    };
    balances[key] = entry;
    totalUsed += entry.used;
    totalRemaining += entry.remaining;
  });
  return {
    employeeId: data?.employee_id?.toString() || '',
    employeeName: data?.employee_name || '',
    year: data?.year || new Date().getFullYear(),
    probationStatus: data?.probation_status || '',
    eligibleLeaveTypes: Array.isArray(data?.eligible_leave_types) ? data.eligible_leave_types : [],
    maritalStatus: data?.marital_status || undefined,
    balances,
    totalUsed,
    totalRemaining,
    policyInfo: {
      unapprovedLeavePenalty: data?.policy_info?.unapproved_leave_penalty,
      plannedLeaveNotice: data?.policy_info?.planned_leave_notice,
      personalLeaveNotice: data?.policy_info?.personal_leave_notice,
      weekendPolicy: data?.policy_info?.weekend_policy,
    },
  };
};

export const leaveService = {
  // Current user's leave balance (used/remaining per type, policy-accurate)
  getMyLeaveBalance: async (): Promise<LeaveBalanceSummary | null> => {
    try {
      const response = await apiClient.get<any>('/leaves/balance');
      return mapBalanceResponse(response.data);
    } catch (error) {
      console.error('Failed to fetch leave balance:', error);
      return null;
    }
  },

  // A specific employee's balance (admin or their manager)
  getEmployeeLeaveBalance: async (employeeId: string): Promise<LeaveBalanceSummary | null> => {
    try {
      const response = await apiClient.get<any>(`/leaves/balance/${employeeId}`);
      return mapBalanceResponse(response.data);
    } catch (error) {
      console.error(`Failed to fetch leave balance for employee ${employeeId}:`, error);
      return null;
    }
  },

  // excludeSelf omits the caller's own requests — used when an approver reviews
  // the whole team's history rather than their own.
  getLeaveHistory: async (employeeId?: string, excludeSelf = false) => {
    try {
      const params: Record<string, string | boolean> = {};
      if (employeeId) params.employee_id = employeeId;
      if (excludeSelf) params.exclude_self = true;
      const response = await apiClient.get<any>(API_ENDPOINTS.LEAVE_HISTORY, { params });
      console.log('Raw leave history API response:', response);
      
      // Handle different response structures
      let leaves: any[] = [];
      if (Array.isArray(response.data)) {
        leaves = response.data;
      } else if (response.data?.data && Array.isArray(response.data.data)) {
        leaves = response.data.data;
      } else if (response.data && typeof response.data === 'object') {
        leaves = [response.data];
      }
      
      // Reverse mapping: backend leave_type to frontend display name
      const leaveTypeDisplayMap: Record<string, string> = {
        'annual': 'Annual Leave',
        'sick': 'Sick Leave',
        'casual': 'Casual Leave',
        'personal': 'Personal Leave',
        'maternity': 'Maternity Leave',
        'paternity': 'Paternity Leave',
        'unpaid': 'Unpaid Leave',
        'bereavement': 'Bereavement Leave',
        'compassionate': 'Compassionate Leave',
      };
      
      // Extract date strings and convert ISO to YYYY-MM-DD if needed
      const formatDateForDisplay = (dateStr: string | null | undefined): string => {
        if (!dateStr) return '';
        if (dateStr.includes('T')) {
          return dateStr.split('T')[0];
        }
        return dateStr;
      };
      
      // Map API response to frontend format
      const mappedLeaves = leaves.map((leave: any) => {
        const leaveTypeBackend = (leave.leave_type || leave.leaveType || '').toLowerCase();
        const leaveTypeDisplay = leaveTypeDisplayMap[leaveTypeBackend] || leaveTypeBackend || 'Leave';
        
        return {
          id: leave.id?.toString() || '',
          employeeId: leave.employee_id?.toString() || leave.employeeId?.toString() || '',
          employeeName: leave.employee_name || leave.employeeName || 
                       leave.employee?.name || leave.employee?.full_name ||
                       leave.user?.name || leave.user?.full_name || '',
          leaveType: leaveTypeDisplay,
          startDate: formatDateForDisplay(leave.start_date || leave.startDate),
          endDate: formatDateForDisplay(leave.end_date || leave.endDate),
          duration: leave.total_days || leave.duration || 0,
          resumptionDate: formatDateForDisplay(leave.resumption_date || leave.resumptionDate || leave.end_date || leave.endDate),
          reason: leave.reason || '',
          status: leave.status || 'pending',
          document: leave.document || leave.medical_certificate_url || '',
          // Rejection reason - only populated when status is 'rejected'
          rejectionReason: leave.rejection_reason || '',
          // Policy warnings now ride along on the list and detail responses,
          // not just on create. Dropping them here left the approval queue
          // rendering a field that was always undefined.
          warnings: Array.isArray(leave.warnings) ? leave.warnings : undefined,
          // Approval info - API now includes approver_name
          approvedBy: leave.approved_by?.toString() || leave.approvedBy || '',
          approvedByName: leave.approver_name || leave.approvedByName || '',
          approvedAt: formatDateForDisplay(leave.approved_at || leave.approvedAt),
          appliedDate: formatDateForDisplay(leave.applied_date || leave.appliedDate || leave.created_at) || new Date().toISOString().split('T')[0],
        };
      });
      
      // Fetch employee names if missing (approver_name is now included in API response)
      const employeeIds = mappedLeaves
        .map(leave => leave.employeeId)
        .filter((id, index, self) => id && self.indexOf(id) === index);
      
      if (employeeIds.length > 0) {
        try {
          const employees = await employeeService.getEmployees();
          const employeeMap = new Map(employees.map(emp => [emp.id, emp.name]));
          
          mappedLeaves.forEach(leave => {
            if (!leave.employeeName && leave.employeeId) {
              leave.employeeName = employeeMap.get(leave.employeeId) || `Employee #${leave.employeeId}`;
            } else if (!leave.employeeName) {
              leave.employeeName = 'Unknown Employee';
            }
            
            // Fallback for approver name if not provided by API
            if (leave.approvedBy && !leave.approvedByName) {
              leave.approvedByName = employeeMap.get(leave.approvedBy) || `User #${leave.approvedBy}`;
            }
          });
        } catch (empError) {
          console.warn('Failed to fetch employee names for leave history:', empError);
          mappedLeaves.forEach(leave => {
            if (!leave.employeeName) {
              leave.employeeName = leave.employeeId ? `Employee #${leave.employeeId}` : 'Unknown Employee';
            }
            if (leave.approvedBy && !leave.approvedByName) {
              leave.approvedByName = `User #${leave.approvedBy}`;
            }
          });
        }
      }
      
      return mappedLeaves;
    } catch (error: any) {
      console.error('Error fetching leave history:', error);
      throw error;
    }
  },

  // Lightweight list of IDs of leaves awaiting a decision (for the nav badge
  // dot). The history endpoint is already role-scoped by the backend, so admins
  // see everyone's, managers see their team's, employees see their own.
  getPendingIds: async (): Promise<string[]> => {
    try {
      const response = await apiClient.get<any>(API_ENDPOINTS.LEAVE_HISTORY);
      let leaves: any[] = [];
      if (Array.isArray(response.data)) {
        leaves = response.data;
      } else if (response.data?.data && Array.isArray(response.data.data)) {
        leaves = response.data.data;
      } else if (response.data && typeof response.data === 'object') {
        leaves = [response.data];
      }
      return leaves
        .filter((l) => {
          const s = (l.status || '').toLowerCase();
          return s === 'pending' || s === 'manager_approved';
        })
        .map((l) => String(l.id));
    } catch (error) {
      console.error('Failed to fetch pending leave ids:', error);
      return [];
    }
  },

  getLeaveApplications: async () => {
    // Keep for backward compatibility - delegate to getLeaveHistory with no filter
    // We need to call it after the object is defined, so we'll use the same logic
    try {
      const response = await apiClient.get<any>(API_ENDPOINTS.LEAVE_HISTORY);
      console.log('Raw leave applications API response:', response);
      
      // Handle different response structures
      let leaves: any[] = [];
      if (Array.isArray(response.data)) {
        leaves = response.data;
      } else if (response.data?.data && Array.isArray(response.data.data)) {
        leaves = response.data.data;
      } else if (response.data && typeof response.data === 'object') {
        leaves = [response.data];
      }
      
      // Use the same mapping logic as getLeaveHistory
      const leaveTypeDisplayMap: Record<string, string> = {
        'annual': 'Annual Leave',
        'sick': 'Sick Leave',
        'casual': 'Casual Leave',
        'personal': 'Personal Leave',
        'maternity': 'Maternity Leave',
        'paternity': 'Paternity Leave',
        'unpaid': 'Unpaid Leave',
        'bereavement': 'Bereavement Leave',
        'compassionate': 'Compassionate Leave',
      };
      
      const formatDateForDisplay = (dateStr: string | null | undefined): string => {
        if (!dateStr) return '';
        if (dateStr.includes('T')) {
          return dateStr.split('T')[0];
        }
        return dateStr;
      };
      
      const mappedLeaves = leaves.map((leave: any) => {
        const leaveTypeBackend = (leave.leave_type || leave.leaveType || '').toLowerCase();
        const leaveTypeDisplay = leaveTypeDisplayMap[leaveTypeBackend] || leaveTypeBackend || 'Leave';
        
        return {
          id: leave.id?.toString() || '',
          employeeId: leave.employee_id?.toString() || leave.employeeId?.toString() || '',
          employeeName: leave.employee_name || leave.employeeName || 
                       leave.employee?.name || leave.employee?.full_name ||
                       leave.user?.name || leave.user?.full_name || '',
          leaveType: leaveTypeDisplay,
          startDate: formatDateForDisplay(leave.start_date || leave.startDate),
          endDate: formatDateForDisplay(leave.end_date || leave.endDate),
          duration: leave.total_days || leave.duration || 0,
          resumptionDate: formatDateForDisplay(leave.resumption_date || leave.resumptionDate || leave.end_date || leave.endDate),
          reason: leave.reason || '',
          status: leave.status || 'pending',
          document: leave.document || leave.medical_certificate_url || '',
          // Rejection reason - only populated when status is 'rejected'
          rejectionReason: leave.rejection_reason || '',
          // Policy warnings now ride along on the list and detail responses,
          // not just on create. Dropping them here left the approval queue
          // rendering a field that was always undefined.
          warnings: Array.isArray(leave.warnings) ? leave.warnings : undefined,
          // Manager approval info
          managerApprovedBy: leave.manager_approved_by?.toString() || leave.managerApprovedBy || '',
          managerApproverName: leave.manager_approver_name || leave.managerApproverName || '',
          managerApprovedAt: formatDateForDisplay(leave.manager_approved_at || leave.managerApprovedAt),
          // Final approval info - API now includes approver_name
          approvedBy: leave.approved_by?.toString() || leave.approvedBy || '',
          approvedByName: leave.approver_name || leave.approvedByName || '',
          approvedAt: formatDateForDisplay(leave.approved_at || leave.approvedAt),
          appliedDate: formatDateForDisplay(leave.applied_date || leave.appliedDate || leave.created_at) || new Date().toISOString().split('T')[0],
        };
      });
      
      // Fetch employee names if missing (approver_name is now included in API response)
      const employeeIds = mappedLeaves
        .map(leave => leave.employeeId)
        .filter((id, index, self) => id && self.indexOf(id) === index);
      
      if (employeeIds.length > 0) {
        try {
          const employees = await employeeService.getEmployees();
          const employeeMap = new Map(employees.map(emp => [emp.id, emp.name]));
          
          mappedLeaves.forEach(leave => {
            if (!leave.employeeName && leave.employeeId) {
              leave.employeeName = employeeMap.get(leave.employeeId) || `Employee #${leave.employeeId}`;
            } else if (!leave.employeeName) {
              leave.employeeName = 'Unknown Employee';
            }
            
            // Fallback for manager approver name if not provided by API
            if (leave.managerApprovedBy && !leave.managerApproverName) {
              leave.managerApproverName = employeeMap.get(leave.managerApprovedBy) || `User #${leave.managerApprovedBy}`;
            }
            // Fallback for final approver name if not provided by API
            if (leave.approvedBy && !leave.approvedByName) {
              leave.approvedByName = employeeMap.get(leave.approvedBy) || `User #${leave.approvedBy}`;
            }
          });
        } catch (empError) {
          console.warn('Failed to fetch employee names:', empError);
          mappedLeaves.forEach(leave => {
            if (!leave.employeeName) {
              leave.employeeName = leave.employeeId ? `Employee #${leave.employeeId}` : 'Unknown Employee';
            }
            if (leave.managerApprovedBy && !leave.managerApproverName) {
              leave.managerApproverName = `User #${leave.managerApprovedBy}`;
            }
            if (leave.approvedBy && !leave.approvedByName) {
              leave.approvedByName = `User #${leave.approvedBy}`;
            }
          });
        }
      }
      
      return mappedLeaves;
    } catch (error: any) {
      console.error('Error fetching leave applications:', error);
      throw error;
    }
  },

  applyLeave: async (data: ApplyLeaveData) => {
    try {
      // Map frontend leave type to backend values
      // Backend expects: "sick", "casual", "annual", "maternity", "paternity", "unpaid"
      const leaveTypeMap: Record<string, string> = {
        'Annual Leave': 'annual',
        'Sick Leave': 'sick',
        'Personal Leave': 'casual', // Map Personal Leave to casual
        'Maternity Leave': 'maternity',
        'Paternity Leave': 'paternity',
        'Bereavement Leave': 'bereavement',
        'Unpaid Leave': 'unpaid', // Direct unpaid leave option
        'Casual Leave': 'casual',
        'Compassionate Leave': 'unpaid', // Map Compassionate to unpaid
      };

      // Convert dates from YYYY-MM-DD to ISO 8601 format (e.g., "2025-12-25T00:00:00")
      // HTML date inputs return YYYY-MM-DD format
      const formatDateToISO = (dateString: string): string => {
        if (!dateString) return dateString;
        // If already in ISO format, return as is
        if (dateString.includes('T')) return dateString;
        // Convert YYYY-MM-DD to ISO format with time
        return `${dateString}T00:00:00`;
      };

      // Backend expects ISO 8601 format dates and specific field names
      const leaveData = {
        leave_type: leaveTypeMap[data.leaveType] || 'annual',
        start_date: formatDateToISO(data.startDate),
        end_date: formatDateToISO(data.endDate),
        reason: data.reason || undefined, // Make optional if empty
        medical_certificate_url: data.documentUrl || undefined,
        acting_admin_id: data.actingAdminId ? Number(data.actingAdminId) : undefined,
      };

      console.log('Submitting leave data:', leaveData);

      const response = await apiClient.post<any>(API_ENDPOINTS.APPLY_LEAVE, leaveData);
      
      // Return response with warnings if present
      return {
        ...response.data,
        warnings: response.data?.warnings || null,
      };
    } catch (error: any) {
      // Log detailed error for debugging
      console.error('Leave application error:', error.response?.data || error.message);
      
      // Extract and format errors for better user feedback
      if (error.response?.data) {
        const errorData = error.response.data;
        let errorMessage = '';
        
        // Handle different error response formats
        if (errorData.detail) {
          if (Array.isArray(errorData.detail)) {
            // Pydantic validation errors (422)
            const errors = errorData.detail.map((err: any) => {
              const field = err.loc?.slice(1).join('.') || 'unknown';
              return `${field}: ${err.msg}`;
            }).join('\n');
            errorMessage = `Validation Error:\n${errors}`;
          } else if (typeof errorData.detail === 'string') {
            // Business logic errors or simple validation messages (400, 403, etc.)
            errorMessage = errorData.detail;
          } else {
            errorMessage = JSON.stringify(errorData.detail, null, 2);
          }
        } else if (errorData.message) {
          // Alternative error message field
          errorMessage = errorData.message;
        } else if (typeof errorData === 'string') {
          // Error data is directly a string
          errorMessage = errorData;
        } else {
          errorMessage = 'Please check your input fields.';
        }
        
        // Set the formatted error message
        error.message = errorMessage;
      }
      
      throw error;
    }
  },

  uploadLeaveDocument: async (file: File): Promise<string> => {
    const formData = new FormData();
    formData.append('file', file);
    const response = await apiClient.post<any>('/upload', formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
    });
    const url = response.data?.url;
    if (!url) throw new Error('The document was uploaded but no file URL was returned.');
    return url;
  },

  getUpcomingLeaves: async () => {
    try {
      const response = await apiClient.get<any>(API_ENDPOINTS.UPCOMING_LEAVES);
      console.log('Raw API response:', response);
      console.log('Response data:', response.data);
      
      // Handle different response structures - API returns array directly or wrapped in data
      let leaves: any[] = [];
      if (Array.isArray(response.data)) {
        leaves = response.data;
      } else if (response.data?.data && Array.isArray(response.data.data)) {
        leaves = response.data.data;
      } else if (response.data && typeof response.data === 'object') {
        // Single object, wrap in array
        leaves = [response.data];
      }
      
      console.log('Processed leaves array:', leaves);
      
      // Reverse mapping: backend leave_type to frontend display name
      const leaveTypeDisplayMap: Record<string, string> = {
        'annual': 'Annual Leave',
        'sick': 'Sick Leave',
        'casual': 'Casual Leave',
        'personal': 'Personal Leave',
        'maternity': 'Maternity Leave',
        'paternity': 'Paternity Leave',
        'unpaid': 'Unpaid Leave',
        'bereavement': 'Bereavement Leave',
        'compassionate': 'Compassionate Leave',
      };
      
      // Extract date strings and convert ISO to YYYY-MM-DD if needed
      const formatDateForDisplay = (dateStr: string | null | undefined): string => {
        if (!dateStr) return '';
        // If it's ISO format with time, extract just the date part
        if (dateStr.includes('T')) {
          return dateStr.split('T')[0];
        }
        return dateStr;
      };
      
      // Map API response to frontend format
      const mappedLeaves = leaves.map((leave: any) => {
        const leaveTypeBackend = (leave.leave_type || leave.leaveType || '').toLowerCase();
        const leaveTypeDisplay = leaveTypeDisplayMap[leaveTypeBackend] || leaveTypeBackend || 'Leave';
        
        console.log(`Mapping leave: backend type="${leaveTypeBackend}", display="${leaveTypeDisplay}"`);
        
        return {
          id: leave.id?.toString() || '',
          employeeId: leave.employee_id?.toString() || leave.employeeId?.toString() || '',
          // Will be populated below with employee names
          employeeName: leave.employee_name || leave.employeeName || 
                       leave.employee?.name || leave.employee?.full_name ||
                       leave.user?.name || leave.user?.full_name || '',
          leaveType: leaveTypeDisplay,
          startDate: formatDateForDisplay(leave.start_date || leave.startDate),
          endDate: formatDateForDisplay(leave.end_date || leave.endDate),
          duration: leave.total_days || leave.duration || 0,
          resumptionDate: formatDateForDisplay(leave.resumption_date || leave.resumptionDate || leave.end_date || leave.endDate),
          reason: leave.reason || '',
          status: leave.status || 'pending',
          document: leave.document || leave.medical_certificate_url || '',
          // Rejection reason - only populated when status is 'rejected'
          rejectionReason: leave.rejection_reason || '',
          // Policy warnings now ride along on the list and detail responses,
          // not just on create. Dropping them here left the approval queue
          // rendering a field that was always undefined.
          warnings: Array.isArray(leave.warnings) ? leave.warnings : undefined,
          // Manager approval info
          managerApprovedBy: leave.manager_approved_by?.toString() || leave.managerApprovedBy || '',
          managerApproverName: leave.manager_approver_name || leave.managerApproverName || '',
          managerApprovedAt: formatDateForDisplay(leave.manager_approved_at || leave.managerApprovedAt),
          // Final approval info - API now includes approver_name
          approvedBy: leave.approved_by?.toString() || leave.approvedBy || '',
          approvedByName: leave.approver_name || leave.approvedByName || '',
          approvedAt: formatDateForDisplay(leave.approved_at || leave.approvedAt),
          appliedDate: formatDateForDisplay(leave.applied_date || leave.appliedDate || leave.created_at) || new Date().toISOString().split('T')[0],
        };
      });
      
      // Fetch employee names if missing (approver_name is now included in API response)
      const employeeIds = mappedLeaves
        .map(leave => leave.employeeId)
        .filter((id, index, self) => id && self.indexOf(id) === index); // Unique IDs
      
      if (employeeIds.length > 0) {
        try {
          // Fetch all employees to create a lookup map
          const employees = await employeeService.getEmployees();
          const employeeMap = new Map(employees.map(emp => [emp.id, emp.name]));
          
          // Populate employee names
          mappedLeaves.forEach(leave => {
            if (!leave.employeeName && leave.employeeId) {
              leave.employeeName = employeeMap.get(leave.employeeId) || `Employee #${leave.employeeId}`;
            } else if (!leave.employeeName) {
              leave.employeeName = 'Unknown Employee';
            }
            
            // Fallback for manager approver name if not provided by API
            if (leave.managerApprovedBy && !leave.managerApproverName) {
              leave.managerApproverName = employeeMap.get(leave.managerApprovedBy) || `User #${leave.managerApprovedBy}`;
            }
            // Fallback for final approver name if not provided by API
            if (leave.approvedBy && !leave.approvedByName) {
              leave.approvedByName = employeeMap.get(leave.approvedBy) || `User #${leave.approvedBy}`;
            }
          });
        } catch (empError) {
          console.warn('Failed to fetch employee names, using fallback:', empError);
          // Fallback: use employee ID if name not available
          mappedLeaves.forEach(leave => {
            if (!leave.employeeName) {
              leave.employeeName = leave.employeeId ? `Employee #${leave.employeeId}` : 'Unknown Employee';
            }
            if (leave.managerApprovedBy && !leave.managerApproverName) {
              leave.managerApproverName = `User #${leave.managerApprovedBy}`;
            }
            if (leave.approvedBy && !leave.approvedByName) {
              leave.approvedByName = `User #${leave.approvedBy}`;
            }
          });
        }
      }
      
      return mappedLeaves;
    } catch (error: any) {
      console.error('Error fetching upcoming leaves:', error);
      throw error;
    }
  },

  // `comments` is the approver's optional note to the employee, stored by the
  // backend's LeaveApproval.comments field.
  approveLeave: async (leaveId: string, comments?: string) => {
    try {
      // Backend expects PUT /leaves/{id}/approve with status='approved' and rejection_reason=null
      const response = await apiClient.put<LeaveApplication>(
        `/leaves/${leaveId}/approve`,
        {
          status: 'approved',
          rejection_reason: null,
          comments: comments?.trim() || null,
        }
      );
      return response.data;
    } catch (error: any) {
      // Handle 403 Forbidden - manager trying to approve their own request
      if (error.response?.status === 403) {
        const errorMessage = error.response?.data?.detail || 
                            error.response?.data?.message || 
                            'You cannot approve your own leave requests. Only subordinates\' requests can be approved.';
        error.message = errorMessage;
      }
      throw error;
    }
  },

  rejectLeave: async (leaveId: string, rejectionReason: string) => {
    try {
      // Backend expects PUT /leaves/{id}/approve with status='rejected' and rejection_reason field (mandatory)
      const response = await apiClient.put<LeaveApplication>(
        `/leaves/${leaveId}/approve`,
        { 
          status: 'rejected',
          rejection_reason: rejectionReason
        }
      );
      return response.data;
    } catch (error: any) {
      // Handle 403 Forbidden - manager trying to reject their own request
      if (error.response?.status === 403) {
        const errorMessage = error.response?.data?.detail ||
                            error.response?.data?.message ||
                            'You cannot reject your own leave requests. Only subordinates\' requests can be rejected.';
        error.message = errorMessage;
      }
      throw error;
    }
  },

  // Withdraw a still-live request. The employee can cancel their own
  // (pending or approved); an admin/HR manager can cancel anyone's. A plain
  // manager gets a 403 for a subordinate's request — cancelling isn't the
  // same as rejecting, so that isn't part of their approval authority.
  cancelLeave: async (leaveId: string | number) => {
    try {
      const response = await apiClient.delete(API_ENDPOINTS.LEAVE_CANCEL(leaveId));
      return response.data;
    } catch (error: any) {
      if (error.response?.status === 403) {
        error.message = error.response?.data?.detail
          || 'You can only cancel your own leave requests.';
      }
      throw error;
    }
  },

  // Admin-only: erase a finished leave record from the history. The backend
  // refuses a request still awaiting a decision, and approved unpaid leave
  // (payroll derives the unpaid-leave deduction from it).
  purgeLeave: async (leaveId: string | number) => {
    try {
      const response = await apiClient.delete(API_ENDPOINTS.LEAVE_PURGE(leaveId));
      return response.data;
    } catch (error) {
      // Surface the backend's `detail` — it explains *why* a record cannot go —
      // instead of Axios's generic "Request failed with status code 409".
      const detail = (error as { response?: { data?: { detail?: unknown } } })
        .response?.data?.detail;
      if (typeof detail === 'string' && error instanceof Error) error.message = detail;
      throw error;
    }
  },
};

