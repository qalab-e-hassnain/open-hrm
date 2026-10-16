import { apiClient } from './api';
import { API_ENDPOINTS } from '../config/constants';
import { type ReimbursementRequest } from '../types';
import { employeeService } from './employeeService';

/**
 * Every claim type and status the API defines.
 *
 * These lists used to be derived from whatever claims were on screen, so the
 * "Claim type" filter offered only "equipment" — because that was the one
 * type among the pending claims — and no filter could find a paid claim
 * because none was loaded to derive "paid" from. A filter built from the
 * current rows can only ever find what is already in front of you.
 *
 * Mirrors ReimbursementTypeEnum and ReimbursementStatusEnum on the server.
 */
export const REIMBURSEMENT_TYPES = [
  { value: 'travel', label: 'Travel' },
  { value: 'food', label: 'Food' },
  { value: 'medical', label: 'Medical' },
  { value: 'equipment', label: 'Equipment' },
  { value: 'other', label: 'Other' },
] as const;

export const REIMBURSEMENT_STATUSES = [
  { value: 'pending', label: 'Awaiting decision' },
  { value: 'approved', label: 'Approved, awaiting payment' },
  { value: 'paid', label: 'Paid' },
  { value: 'rejected', label: 'Rejected' },
] as const;


export interface ApplyReimbursementData {
  reimbursementType: string; // NEW: Type of reimbursement (e.g., 'travel', 'food', 'medical', 'equipment', 'other')
  amount: number;
  dateOfExpense: string; // CHANGED: From dateUsed to match backend field date_of_expense
  description: string; // CHANGED: From reason to match backend field description
  receiptUrl?: string; // NEW: URL to the uploaded receipt file
}

export interface UpdateReimbursementData {
  reimbursementType?: string;
  amount?: number;
  dateOfExpense?: string;
  description?: string;
  receiptUrl?: string;
}

const formatDateForDisplay = (dateStr: string | null | undefined): string => {
  if (!dateStr) return '';
  return dateStr.includes('T') ? dateStr.split('T')[0] : dateStr;
};

// Single-record mapping, matching the shape used by the list endpoints
// (getReimbursementRequests/getReimbursementHistory). The backend's
// enrich_reimbursement_response already includes employee_name/approver_name,
// so this skips the list-level employee-name-lookup fallback those use.
const mapReimbursement = (reimbursement: any): ReimbursementRequest => ({
  id: reimbursement.id?.toString() || '',
  employeeId: reimbursement.employee_id?.toString() || reimbursement.employeeId?.toString() || '',
  employeeName: reimbursement.employee_name || reimbursement.employeeName || 'Unknown Employee',
  employeeEmail: reimbursement.employee_email || reimbursement.employeeEmail || '',
  amount: reimbursement.amount || 0,
  dateUsed: formatDateForDisplay(reimbursement.date_of_expense || reimbursement.dateUsed),
  reason: reimbursement.description || reimbursement.reason || '',
  reimbursementType: reimbursement.reimbursement_type || reimbursement.reimbursementType || 'other',
  status: reimbursement.status || 'pending',
  document: reimbursement.receipt_url || reimbursement.document || '',
  receiptUrl: reimbursement.receipt_url || reimbursement.document || '',
  comments: reimbursement.comments || '',
  rejectionReason: reimbursement.rejection_reason || '',
  approvedBy: reimbursement.approved_by?.toString() || reimbursement.approvedBy || '',
  approvedByName: reimbursement.approver_name || reimbursement.approvedByName || '',
  approvedAt: formatDateForDisplay(reimbursement.approved_at || reimbursement.approvedAt),
  paidAt: formatDateForDisplay(reimbursement.paid_at || reimbursement.paidAt),
  paidVia: reimbursement.paid_via ?? reimbursement.paidVia ?? '',
  payrollId: reimbursement.payroll_id ?? reimbursement.payrollId ?? null,
  appliedDate: formatDateForDisplay(reimbursement.applied_date || reimbursement.appliedDate || reimbursement.created_at) || new Date().toISOString().split('T')[0],
  createdAt: formatDateForDisplay(reimbursement.created_at || reimbursement.createdAt),
  updatedAt: formatDateForDisplay(reimbursement.updated_at || reimbursement.updatedAt),
});

export const reimbursementService = {
  getReimbursementRequests: async () => {
    try {
      const response = await apiClient.get<any>(API_ENDPOINTS.REIMBURSEMENT_REQUESTS);
      console.log('Raw reimbursement API response:', response);
      console.log('Response data:', response.data);
      
      // Handle different response structures - API returns array directly or wrapped in data
      let reimbursements: any[] = [];
      if (Array.isArray(response.data)) {
        reimbursements = response.data;
      } else if (response.data?.data && Array.isArray(response.data.data)) {
        reimbursements = response.data.data;
      } else if (response.data && typeof response.data === 'object') {
        // Single object, wrap in array
        reimbursements = [response.data];
      }
      
      console.log('Processed reimbursements array:', reimbursements);
      
      // Extract date strings and convert ISO to YYYY-MM-DD if needed
      const formatDateForDisplay = (dateStr: string | null | undefined): string => {
        if (!dateStr) return '';
        // If it's ISO format with time, extract just the date part
        if (dateStr.includes('T')) {
          return dateStr.split('T')[0];
        }
        return dateStr;
      };
      
      // Map API response to frontend format - include all available fields
      const mappedReimbursements = reimbursements.map((reimbursement: any) => {
        return {
          id: reimbursement.id?.toString() || '',
          employeeId: reimbursement.employee_id?.toString() || reimbursement.employeeId?.toString() || '',
          // Employee info - API now includes employee_name and employee_email
          employeeName: reimbursement.employee_name || reimbursement.employeeName || 
                       reimbursement.employee?.name || reimbursement.employee?.full_name ||
                       reimbursement.user?.name || reimbursement.user?.full_name || '',
          employeeEmail: reimbursement.employee_email || reimbursement.employeeEmail || 
                        reimbursement.employee?.email || reimbursement.user?.email || '',
          // Reimbursement details
          amount: reimbursement.amount || 0,
          dateUsed: formatDateForDisplay(reimbursement.date_of_expense || reimbursement.dateUsed),
          reason: reimbursement.description || reimbursement.reason || '',
          reimbursementType: reimbursement.reimbursement_type || reimbursement.reimbursementType || 'other',
          status: reimbursement.status || 'pending',
          // Document/receipt
          document: reimbursement.receipt_url || reimbursement.document || '',
          receiptUrl: reimbursement.receipt_url || reimbursement.document || '',
          // Comments and rejection
          comments: reimbursement.comments || '',
          rejectionReason: reimbursement.rejection_reason || '',
          // Approval info - API now includes approver_name
          approvedBy: reimbursement.approved_by?.toString() || reimbursement.approvedBy || '',
          approvedByName: reimbursement.approver_name || reimbursement.approvedByName || '',
          approvedAt: formatDateForDisplay(reimbursement.approved_at || reimbursement.approvedAt),
          // Payment info
          paidAt: formatDateForDisplay(reimbursement.paid_at || reimbursement.paidAt),
          paidVia: reimbursement.paid_via ?? reimbursement.paidVia ?? '',
          payrollId: reimbursement.payroll_id ?? reimbursement.payrollId ?? null,
          // Dates
          appliedDate: formatDateForDisplay(reimbursement.applied_date || reimbursement.appliedDate || reimbursement.created_at) || new Date().toISOString().split('T')[0],
          createdAt: formatDateForDisplay(reimbursement.created_at || reimbursement.createdAt),
          updatedAt: formatDateForDisplay(reimbursement.updated_at || reimbursement.updatedAt),
        };
      });
      
      // Fetch employee names if missing (approver_name is now included in API response)
      const employeeIds = mappedReimbursements
        .map(req => req.employeeId)
        .filter((id, index, self) => id && self.indexOf(id) === index); // Unique IDs
      
      if (employeeIds.length > 0) {
        try {
          // Fetch all employees to create a lookup map
          const employees = await employeeService.getEmployees();
          const employeeMap = new Map(employees.map(emp => [emp.id, emp.name]));
          
          // Populate employee names if missing
          mappedReimbursements.forEach(req => {
            if (!req.employeeName && req.employeeId) {
              req.employeeName = employeeMap.get(req.employeeId) || `Employee #${req.employeeId}`;
            } else if (!req.employeeName) {
              req.employeeName = 'Unknown Employee';
            }
            
            // Fallback for approver name if not provided by API
            if (req.approvedBy && !req.approvedByName) {
              req.approvedByName = employeeMap.get(req.approvedBy) || `User #${req.approvedBy}`;
            }
          });
        } catch (empError: any) {
          // 403 Forbidden is expected for non-admin users trying to fetch all employees
          // Continue with fallback display (use employee IDs instead of names)
          if (empError.response?.status === 403) {
            console.debug('Employee names not accessible (403 - non-admin user), using ID fallback');
          } else {
            console.warn('Failed to fetch employee names for reimbursements, using fallback:', empError);
          }
          // Fallback: use employee ID if name not available
          mappedReimbursements.forEach(req => {
            if (!req.employeeName) {
              req.employeeName = req.employeeId ? `Employee #${req.employeeId}` : 'Unknown Employee';
            }
            if (req.approvedBy && !req.approvedByName) {
              req.approvedByName = `User #${req.approvedBy}`;
            }
          });
        }
      }
      
      return mappedReimbursements;
    } catch (error: any) {
      console.error('Error fetching reimbursement requests:', error);
      throw error;
    }
  },

  // excludeSelf omits the caller's own claims — used when an approver reviews
  // the whole team's history rather than their own.
  getReimbursementHistory: async (employeeId?: string, excludeSelf = false) => {
    try {
      const params: Record<string, string | boolean> = {};
      if (employeeId) params.employee_id = employeeId;
      if (excludeSelf) params.exclude_self = true;
      const response = await apiClient.get<any>(API_ENDPOINTS.REIMBURSEMENT_HISTORY, { params });
      console.log('Raw reimbursement history API response:', response);
      
      // Handle different response structures
      let reimbursements: any[] = [];
      if (Array.isArray(response.data)) {
        reimbursements = response.data;
      } else if (response.data?.data && Array.isArray(response.data.data)) {
        reimbursements = response.data.data;
      } else if (response.data && typeof response.data === 'object') {
        reimbursements = [response.data];
      }
      
      // Extract date strings and convert ISO to YYYY-MM-DD if needed
      const formatDateForDisplay = (dateStr: string | null | undefined): string => {
        if (!dateStr) return '';
        if (dateStr.includes('T')) {
          return dateStr.split('T')[0];
        }
        return dateStr;
      };
      
      // Map API response to frontend format
      const mappedReimbursements = reimbursements.map((reimbursement: any) => {
        return {
          id: reimbursement.id?.toString() || '',
          employeeId: reimbursement.employee_id?.toString() || reimbursement.employeeId?.toString() || '',
          employeeName: reimbursement.employee_name || reimbursement.employeeName || 
                       reimbursement.employee?.name || reimbursement.employee?.full_name ||
                       reimbursement.user?.name || reimbursement.user?.full_name || '',
          employeeEmail: reimbursement.employee_email || reimbursement.employeeEmail || 
                        reimbursement.employee?.email || reimbursement.user?.email || '',
          amount: reimbursement.amount || 0,
          dateUsed: formatDateForDisplay(reimbursement.date_of_expense || reimbursement.dateUsed),
          reason: reimbursement.description || reimbursement.reason || '',
          reimbursementType: reimbursement.reimbursement_type || reimbursement.reimbursementType || 'other',
          status: reimbursement.status || 'pending',
          document: reimbursement.receipt_url || reimbursement.document || '',
          receiptUrl: reimbursement.receipt_url || reimbursement.document || '',
          comments: reimbursement.comments || '',
          rejectionReason: reimbursement.rejection_reason || '',
          // Approval info - API now includes approver_name
          approvedBy: reimbursement.approved_by?.toString() || reimbursement.approvedBy || '',
          approvedByName: reimbursement.approver_name || reimbursement.approvedByName || '',
          approvedAt: formatDateForDisplay(reimbursement.approved_at || reimbursement.approvedAt),
          paidAt: formatDateForDisplay(reimbursement.paid_at || reimbursement.paidAt),
          paidVia: reimbursement.paid_via ?? reimbursement.paidVia ?? '',
          payrollId: reimbursement.payroll_id ?? reimbursement.payrollId ?? null,
          appliedDate: formatDateForDisplay(reimbursement.applied_date || reimbursement.appliedDate || reimbursement.created_at) || new Date().toISOString().split('T')[0],
        };
      });
      
      // Fetch employee names if missing (approver_name is now included in API response)
      const employeeIds = mappedReimbursements
        .map(req => req.employeeId)
        .filter((id, index, self) => id && self.indexOf(id) === index);
      
      if (employeeIds.length > 0) {
        try {
          const employees = await employeeService.getEmployees();
          const employeeMap = new Map(employees.map(emp => [emp.id, emp.name]));
          
          mappedReimbursements.forEach(req => {
            if (!req.employeeName && req.employeeId) {
              req.employeeName = employeeMap.get(req.employeeId) || `Employee #${req.employeeId}`;
            } else if (!req.employeeName) {
              req.employeeName = 'Unknown Employee';
            }
            
            // Fallback for approver name if not provided by API
            if (req.approvedBy && !req.approvedByName) {
              req.approvedByName = employeeMap.get(req.approvedBy) || `User #${req.approvedBy}`;
            }
          });
        } catch (empError: any) {
          // 403 Forbidden is expected for non-admin users trying to fetch all employees
          // Continue with fallback display (use employee IDs instead of names)
          if (empError.response?.status === 403) {
            console.debug('Employee names not accessible (403 - non-admin user), using ID fallback');
          } else {
            console.warn('Failed to fetch employee names for reimbursement history:', empError);
          }
          mappedReimbursements.forEach(req => {
            if (!req.employeeName) {
              req.employeeName = req.employeeId ? `Employee #${req.employeeId}` : 'Unknown Employee';
            }
            if (req.approvedBy && !req.approvedByName) {
              req.approvedByName = `User #${req.approvedBy}`;
            }
          });
        }
      }
      
      return mappedReimbursements;
    } catch (error: any) {
      console.error('Error fetching reimbursement history:', error);
      throw error;
    }
  },

  // Lightweight list of IDs of reimbursements awaiting a decision (for the nav
  // badge dot). The history endpoint is already role-scoped by the backend.
  getPendingIds: async (): Promise<string[]> => {
    try {
      const response = await apiClient.get<any>(API_ENDPOINTS.REIMBURSEMENT_HISTORY);
      let items: any[] = [];
      if (Array.isArray(response.data)) {
        items = response.data;
      } else if (response.data?.data && Array.isArray(response.data.data)) {
        items = response.data.data;
      } else if (response.data && typeof response.data === 'object') {
        items = [response.data];
      }
      return items
        .filter((r) => (r.status || '').toLowerCase() === 'pending')
        .map((r) => String(r.id));
    } catch (error) {
      console.error('Failed to fetch pending reimbursement ids:', error);
      return [];
    }
  },

  applyReimbursement: async (data: ApplyReimbursementData) => {
    try {
      // Build request payload matching backend schema exactly
      const reimbursementData = {
        reimbursement_type: data.reimbursementType, // Required: 'travel', 'food', 'medical', 'equipment', 'other'
        amount: data.amount, // Required: Must be > 0
        description: data.description, // Required: Description field from backend
        date_of_expense: new Date(data.dateOfExpense).toISOString(), // Required: ISO 8601 format
        receipt_url: data.receiptUrl || null, // Optional: URL to uploaded receipt file (can be null if no file)
      };

      console.log('Submitting reimbursement request:', reimbursementData);

      const response = await apiClient.post<any>(API_ENDPOINTS.APPLY_REIMBURSEMENT, reimbursementData);
      return response.data;
    } catch (error: any) {
      console.error('Reimbursement submission error:', error);
      throw error;
    }
  },

  // Backend only allows this while the claim is still pending (and only for
  // the owning employee) — see PUT /reimbursements/{id}.
  updateReimbursement: async (id: string, data: UpdateReimbursementData): Promise<ReimbursementRequest> => {
    const payload: Record<string, unknown> = {};
    if (data.reimbursementType !== undefined) payload.reimbursement_type = data.reimbursementType;
    if (data.amount !== undefined) payload.amount = data.amount;
    if (data.description !== undefined) payload.description = data.description;
    if (data.dateOfExpense !== undefined) payload.date_of_expense = new Date(data.dateOfExpense).toISOString();
    if (data.receiptUrl !== undefined) payload.receipt_url = data.receiptUrl;

    const response = await apiClient.put<any>(`/reimbursements/${id}`, payload);
    return mapReimbursement(response.data);
  },

  // NEW: Upload receipt file to backend and get receipt_url
  uploadReceipt: async (formData: FormData): Promise<string> => {
    try {
      // Upload file to backend - expects multipart/form-data with 'file' field
      // Backend should return response with receipt_url or file path
      const response = await apiClient.post<any>('/upload', formData, {
        headers: {
          'Content-Type': 'multipart/form-data',
        },
      });

      // Extract receipt URL from response
      // Adjust based on actual backend response structure
      const receiptUrl = response.data?.url || response.data?.receipt_url || response.data?.file_url;
      
      if (!receiptUrl) {
        throw new Error('No file URL returned from server');
      }

      console.log('Receipt uploaded successfully:', receiptUrl);
      return receiptUrl;
    } catch (error: any) {
      console.error('Receipt upload error:', error);
      throw new Error(error.response?.data?.detail || error.message || 'Failed to upload receipt');
    }
  },

  approveReimbursement: async (reimbursementId: string, comments?: string) => {
    try {
      // Backend expects PUT /reimbursements/{id}/approve with status and optional comments in body
      const response = await apiClient.put<ReimbursementRequest>(
        `/reimbursements/${reimbursementId}/approve`,
        { 
          status: 'approved',
          rejection_reason: comments || '' 
        }
      );
      return response.data;
    } catch (error: any) {
      // Handle 403 Forbidden - manager trying to approve their own request
      if (error.response?.status === 403) {
        const errorMessage = error.response?.data?.detail || 
                            error.response?.data?.message || 
                            'You cannot approve your own reimbursement requests. Only subordinates\' requests can be approved.';
        error.message = errorMessage;
      }
      throw error;
    }
  },

  rejectReimbursement: async (reimbursementId: string, rejectionReason?: string) => {
    try {
      // Backend expects PUT /reimbursements/{id}/approve with status=rejected and rejection_reason in body
      const response = await apiClient.put<ReimbursementRequest>(
        `/reimbursements/${reimbursementId}/approve`,
        { 
          status: 'rejected',
          rejection_reason: rejectionReason || '' 
        }
      );
      return response.data;
    } catch (error: any) {
      // Handle 403 Forbidden - manager trying to reject their own request
      if (error.response?.status === 403) {
        const errorMessage = error.response?.data?.detail ||
                            error.response?.data?.message ||
                            'You cannot reject your own reimbursement requests. Only subordinates\' requests can be rejected.';
        error.message = errorMessage;
      }
      throw error;
    }
  },

  // Admin-only: erase a finished claim from the history. The backend refuses a
  // pending claim, and any claim already carried onto a payslip.
  purgeReimbursement: async (id: string | number) => {
    try {
      const response = await apiClient.delete(API_ENDPOINTS.REIMBURSEMENT_PURGE(id));
      return response.data;
    } catch (error) {
      // Surface the backend's `detail` — it explains why a record cannot go.
      const detail = (error as { response?: { data?: { detail?: unknown } } })
        .response?.data?.detail;
      if (typeof detail === 'string' && error instanceof Error) error.message = detail;
      throw error;
    }
  },

  /**
   * Record that an approved claim has actually been paid out.
   *
   * Deliberately does NOT catch. The backend refuses this when a payslip
   * already out has carried the claim, and its `detail` names the payslip and
   * the month — api.ts has already put that string on `error.message`, and it
   * is the last check before cash leaves the drawer. Wrapping it in friendlier
   * wording here would hide the one fact the approver needs.
   */
  markPaid: async (id: string | number): Promise<ReimbursementRequest> => {
    const response = await apiClient.put<unknown>(API_ENDPOINTS.REIMBURSEMENT_MARK_PAID(id));
    return mapReimbursement(response.data);
  },

  /** The bulk form. All-or-nothing server-side, so a half-settled batch cannot
   *  happen — and the body is a bare array of ids, not an object. */
  markManyPaid: async (ids: Array<string | number>): Promise<ReimbursementRequest[]> => {
    const response = await apiClient.put<unknown>(
      API_ENDPOINTS.REIMBURSEMENTS_MARK_PAID,
      ids.map((id) => Number(id)),
    );
    const rows = Array.isArray(response.data) ? response.data : [];
    return rows.map(mapReimbursement);
  },

  /**
   * Claims still awaiting a decision, straight from the server.
   *
   * Distinct from getPendingIds(), which pulls the whole history and filters
   * it in the browser — fine for a badge count, wasteful for an approver's
   * queue.
   */
  getPending: async (): Promise<unknown[]> => {
    const res = await apiClient.get<unknown[]>(API_ENDPOINTS.REIMBURSEMENTS_PENDING);
    return Array.isArray(res.data) ? res.data : [];
  },
};

/** Whether the money has actually gone out. `paid_via` is the reliable signal;
 *  `paid_at` alone covers rows recorded before the route was tracked. */
export const isPaidOut = (claim: ReimbursementRequest): boolean =>
  Boolean(claim.paidVia) || Boolean(claim.paidAt);

/**
 * How a paid claim was paid, in words. A row that just says "Paid" is what
 * prompts "paid how?" one payment too late.
 *
 * The month is read off `paid_at` and simply omitted when that is null — the
 * payslip recorded no date, and today's would be a different claim.
 */
export const paidSummary = (claim: ReimbursementRequest): string | null => {
  if (!isPaidOut(claim)) return null;
  const month = claim.paidAt
    ? new Date(`${claim.paidAt}T00:00:00`).toLocaleDateString('en-GB', { month: 'long' })
    : null;
  if (claim.paidVia === 'payroll') {
    return month ? `Paid with ${month} salary` : 'Paid with salary · date not recorded';
  }
  if (claim.paidVia === 'separate') {
    return month ? `Paid separately in ${month}` : 'Paid separately · date not recorded';
  }
  return month ? `Paid in ${month}` : 'Paid · date not recorded';
};

