// Resend Welcome Email (Reset Password)
export const resendWelcomeEmail = async (email: string) => {
  try {
    const response = await apiClient.post('/users/resend-welcome-email', { email });
    return response.data;
  } catch (error: any) {
    if (error.response) {
      const status = error.response.status;
      const detail = error.response.data?.detail;
      if (status === 400) {
        throw new Error(detail || 'Invalid request (400)');
      } else if (status === 401) {
        throw new Error('Unauthorized (401)');
      } else if (status === 500) {
        throw new Error('Server error (500)');
      } else {
        throw new Error(detail || error.message || 'Unknown error');
      }
    }
    throw new Error(error.message || 'Network error');
  }
};

// Remove user by id (DELETE /users/{user_id})
export const removeUserById = async (userId: string) => {
  try {
    const response = await apiClient.delete(API_ENDPOINTS.EMPLOYEE_BY_ID(userId));
    return response.data;
  } catch (error: any) {
    // Return backend error schema
    if (error.response) {
      return error.response.data;
    }
    return { success: false, detail: [{ msg: error.message }] };
  }
};
import axios from 'axios';
import { apiClient } from './api';
import { API_ENDPOINTS, API_BASE_URL } from '../config/constants';
import { type Employee } from '../types';
import { useAuthStore } from '../store/authStore';
import { isPrivileged } from '../utils/permissions';

// Map backend user to frontend Employee format
const mapBackendEmployee = (backendUser: any): Employee => {
  // Log for debugging - check what backend is actually returning
  if (!backendUser.designation || !backendUser.department) {
    console.log('Backend user missing fields:', {
      id: backendUser.id,
      full_name: backendUser.full_name,
      designation: backendUser.designation,
      department: backendUser.department,
      role: backendUser.role,
      all_fields: Object.keys(backendUser),
    });
  }
  
  return {
    id: backendUser.id?.toString() || '',
    name: backendUser.full_name || backendUser.name || '',
    email: backendUser.email || '',
    department: backendUser.department || '',
    designation: backendUser.designation || '',
    employeeId: backendUser.employee_id || '',
    employmentVerificationCode: backendUser.employment_verification_code || '',
    status: backendUser.is_active !== undefined ? (backendUser.is_active ? 'active' : 'inactive') : 'active',
    role: backendUser.role as import('../types').UserRole | undefined,
    gender: backendUser.gender as import('../types').Gender | undefined,
    attendance: backendUser.attendance || 0,
    // `date_of_joining` is the real, editable HR fact (settable by an admin,
    // e.g. to backfill a pre-system hire date); `created_at` is only when
    // the row was inserted, which is wrong for backfilled employees.
    joinDate: backendUser.date_of_joining || backendUser.created_at || '',
    // Include additional fields mapped from backend
    phone: backendUser.phone || '',
    cnic: backendUser.cnic_number || '',
    residentialAddress: backendUser.address || '',
    dateOfBirth: backendUser.date_of_birth || '',
    maritalStatus: backendUser.marital_status || undefined,
    anniversaryDate: backendUser.anniversary_date || '',
    isHr: Boolean(backendUser.is_hr),
    emergencyContactName: backendUser.emergency_contact_name || '',
    emergencyContactPhone: backendUser.emergency_contact_phone || '',
    managerId: backendUser.manager_id || undefined,
    isActive: backendUser.is_active !== undefined ? backendUser.is_active : true,
    baseSalary: backendUser.base_salary || 0,
    allowance: backendUser.allowance || 0,
    hourlyRate: backendUser.hourly_rate || 0,
    overtimeEligible: backendUser.overtime_eligible !== undefined ? backendUser.overtime_eligible : false,
    standardHours: backendUser.standard_hours || 0,
    profilePictureUrl: backendUser.profile_picture_url || '',
    linkedinWelcomePictureUrl: backendUser.linkedin_welcome_picture_url || '',
    cnicFrontUrl: backendUser.cnic_front_url || '',
    cnicBackUrl: backendUser.cnic_back_url || '',
    // Banking Information fields
    accountNumber: backendUser.account_number || null,
    bankName: backendUser.bank_number || null,
    branchCode: backendUser.branch_code || null,
    accountTitle: backendUser.account_title || null,
  };
};

export interface EmploymentVerification {
  valid: boolean;
  employee_name?: string | null;
  employee_id?: string | null;
  designation?: string | null;
  department?: string | null;
  date_of_joining?: string | null;
  currently_employed?: boolean | null;
  verified_at?: string | null;
}

/**
 * Public: confirm an employment letter from its printed code. Returns only
 * what proves the letter genuine — never salary, CNIC or contact details.
 */
export const verifyEmployment = async (code: string): Promise<EmploymentVerification> => {
  const res = await axios.get<EmploymentVerification>(
    `${API_BASE_URL}${API_ENDPOINTS.VERIFY_EMPLOYMENT(encodeURIComponent(code))}`,
  );
  return res.data;
};

export const employeeService = {
  getEmployees: async () => {
    // GET /users/ is manager/admin only (see backend's require_manager). A
    // few callers (leave/reimbursement name-enrichment helpers) call this
    // speculatively without checking the caller's role first, which meant a
    // plain employee generated a guaranteed, doomed 403 on every dashboard
    // load. Skip the request entirely for anyone who can't call it — same
    // check Navigation.tsx already uses for its own approver-only calls.
    const user = useAuthStore.getState().user;
    if (!isPrivileged(user) && user?.role !== 'manager') return [];

    try {
      const response = await apiClient.get<any[]>(API_ENDPOINTS.EMPLOYEES);
      // Map backend users to frontend Employee format
      return response.data?.map(mapBackendEmployee) || [];
    } catch (error: any) {
      // Belt-and-suspenders: if role state was stale/wrong, still degrade
      // quietly instead of throwing on every enrichment call site.
      if (error?.response?.status === 403) return [];
      throw error;
    }
  },

  // Admin: Get all users for dashboard (no pagination, all users, ordered by creation date)
  getDashboardUsers: async () => {
    const response = await apiClient.get<any[]>(API_ENDPOINTS.EMPLOYEES_ADMIN_DASHBOARD);
    // Map backend users to frontend Employee format
    return response.data?.map(mapBackendEmployee) || [];
  },

  // Admin: Get all users with pagination (limit 1000). includeInactive
  // defaults to false so payroll/tax-certificate employee pickers keep only
  // seeing active staff; Manage Users passes true so a suspended account
  // stays visible (and reactivatable) instead of disappearing from the list.
  getAllUsers: async (includeInactive = false) => {
    const response = await apiClient.get<any[]>(API_ENDPOINTS.EMPLOYEES_ADMIN_ALL, {
      params: includeInactive ? { include_inactive: true } : undefined,
    });
    // Map backend users to frontend Employee format
    return response.data?.map(mapBackendEmployee) || [];
  },

  // Admin: Get all users with full details and pagination (limit 100)
  getAllUsersDetailed: async () => {
    const response = await apiClient.get<any[]>(API_ENDPOINTS.EMPLOYEES_ADMIN_ALL_DETAILED);
    // Map backend users to frontend Employee format
    return response.data?.map(mapBackendEmployee) || [];
  },

  // Admin: Get employees only (excluding admins) - for assign manager dropdown
  getEmployeesForAssignment: async () => {
    const response = await apiClient.get<any[]>(API_ENDPOINTS.EMPLOYEES_ADMIN_EMPLOYEES);
    // Map backend users to frontend Employee format
    return response.data?.map(mapBackendEmployee) || [];
  },

  // Admin: Get managers only
  getManagers: async () => {
    const response = await apiClient.get<any[]>(API_ENDPOINTS.EMPLOYEES_ADMIN_MANAGERS);
    // Map backend users to frontend Employee format
    return response.data?.map(mapBackendEmployee) || [];
  },

  getEmployeeById: async (id: string) => {
    const response = await apiClient.get<any>(API_ENDPOINTS.EMPLOYEE_BY_ID(id));
    return mapBackendEmployee(response.data);
  },

  /** Names and job titles, for screens that only need to label a row.
   *
   *  GET /users/ returns the whole personnel record — CNIC, salary, bank
   *  details — and several screens were pulling all of it just to turn an
   *  employee id into a name. This reads the directory view instead, which
   *  carries none of that. It also works for an ordinary employee, where
   *  getEmployees() deliberately returns nothing rather than earn a 403. */
  getDirectory: async () => {
    try {
      const response = await apiClient.get<unknown[]>(API_ENDPOINTS.TEAM_OVERVIEW);
      return response.data?.map(mapBackendEmployee) || [];
    } catch {
      return [];
    }
  },

  getTeamOverview: async () => {
    const response = await apiClient.get<any[]>(API_ENDPOINTS.TEAM_OVERVIEW);
    // Map backend users to frontend Employee format
    return response.data?.map(mapBackendEmployee) || [];
  },

  createEmployee: async (data: Partial<Employee> & { password?: string }) => {
    // Transform frontend format to backend format
    // Backend expects: full_name, email, password (required), role (required), department, designation (optional)
    const backendData: any = {
      full_name: data.name,
      email: data.email,
      password: data.password,
      role: data.role || 'employee',
      gender: data.gender,
      phone: data.phone,
    };

    // Add optional fields if provided
    if (data.department) {
      backendData.department = data.department;
    }
    if (data.designation) {
      backendData.designation = data.designation;
    }
    if (data.employeeId) {
      backendData.employee_id = data.employeeId;
    }
    // Personal details
    if (data.residentialAddress) {
      backendData.address = data.residentialAddress;
    }
    if (data.cnic) {
      backendData.cnic_number = data.cnic;
    }
    if (data.dateOfBirth) {
      backendData.date_of_birth = data.dateOfBirth;
    }
    // Omitted, the backend defaults it to today (row-creation time) — only
    // sent when the admin is backfilling a real historical hire date.
    if (data.joinDate) {
      backendData.date_of_joining = data.joinDate;
    }
    if (data.maritalStatus) {
      backendData.marital_status = data.maritalStatus;
    }
    // Only meaningful for a married employee — the backend rejects it otherwise.
    if (data.maritalStatus === 'married' && data.anniversaryDate) {
      backendData.anniversary_date = data.anniversaryDate;
    }
    if (data.isHr) {
      backendData.is_hr = true;
    }
    // Pay defaults — set at creation so payslip generation can prefill them.
    if (data.baseSalary !== undefined) {
      backendData.base_salary = data.baseSalary;
    }
    if (data.allowance !== undefined) {
      backendData.allowance = data.allowance;
    }
    // Hours per day. Without it the backend defaults to 8, which would put a
    // part-time hire on a full-time month and halve their overtime rate.
    if (data.standardHours !== undefined) {
      backendData.standard_hours = data.standardHours;
    }

    const response = await apiClient.post<any>(API_ENDPOINTS.EMPLOYEES, backendData);
    return mapBackendEmployee(response.data);
  },

  assignManager: async (userId: string, managerId: string | null) => {
    const response = await apiClient.put<any>(API_ENDPOINTS.ASSIGN_MANAGER(userId), {
      manager_id: managerId,
    });
    return response.data;
  },

  updateEmployee: async (id: string, data: Partial<Employee & { [key: string]: any }>) => {
    // Transform frontend format to backend format, mapping ALL relevant fields to prevent null overwrites
    // Use spread operator pattern to preserve existing fields on the backend
    const backendData: any = {
      // 🔵 Core Fields
      full_name: data.name || undefined,
      email: data.email || undefined,
      employee_id: data.employeeId || undefined,
      gender: data.gender || undefined,
      // Admin-only server-side; ignored by the backend for non-admin callers.
      role: data.role || undefined,

      // 🟢 Contact Fields
      phone: data.phone || undefined,
      personal_email: data.personalEmail || undefined,
      
      // 🟡 Address & CNIC Fields
      address: data.residentialAddress || undefined,
      cnic_number: data.cnic || undefined,
      date_of_birth: data.dateOfBirth || undefined,
      date_of_joining: data.joinDate || undefined,
      marital_status: data.maritalStatus || undefined,
      anniversary_date: data.maritalStatus === 'married' ? (data.anniversaryDate || undefined) : undefined,
      is_hr: data.isHr !== undefined ? data.isHr : undefined,
      
      // Department & Designation
      department: data.department || undefined,
      designation: data.designation || undefined,
      
      // 🔴 Emergency Contact Fields
      emergency_contact_name: data.emergencyContactName || undefined,
      emergency_contact_phone: data.emergencyContactPhone || undefined,
      
      // 🟣 Employment Details Fields
      base_salary: data.baseSalary !== undefined ? data.baseSalary : undefined,
      allowance: data.allowance !== undefined ? data.allowance : undefined,
      overtime_eligible: data.overtimeEligible !== undefined ? data.overtimeEligible : undefined,
      standard_hours: data.standardHours !== undefined ? data.standardHours : undefined,
      manager_id: data.managerId !== undefined ? data.managerId : undefined,
      is_active: data.status !== undefined ? data.status === 'active' : data.isActive,
      
      // 🖼️ Document URLs
      profile_picture_url: data.profilePictureUrl || undefined,
      linkedin_welcome_picture_url: data.linkedinWelcomePictureUrl || undefined,
      cnic_front_url: data.cnicFrontUrl || undefined,
      cnic_back_url: data.cnicBackUrl || undefined,
      
      // 💳 BANKING INFORMATION - Critical to send to prevent null overwrites
      account_number: data.accountNumber || undefined,
      bank_number: data.bankName || undefined,
      branch_code: data.branchCode || undefined,
      account_title: data.accountTitle || undefined,
    };
    
    // Remove undefined fields to only send provided data
    // Backend will preserve existing fields not in the update payload
    Object.keys(backendData).forEach(key => {
      if (backendData[key] === undefined) {
        delete backendData[key];
      }
    });
    
    console.log('📤 updateEmployee() - Sending payload to backend:', JSON.stringify(backendData, null, 2));
    
    const response = await apiClient.put<any>(API_ENDPOINTS.EMPLOYEE_BY_ID(id), backendData);
    return mapBackendEmployee(response.data);
  },

  deleteEmployee: async (id: string) => {
    const response = await apiClient.delete(API_ENDPOINTS.EMPLOYEE_BY_ID(id));
    return response.data;
  },

  // Suspending (PUT /users/{id} with isActive:false, above) works fine on an
  // already-active user, but the backend's generic update endpoint 404s on
  // ANY update to an already-inactive user — it treats "inactive" as "not
  // found" so a suspended account can't be edited by accident. Reactivating
  // has to go through this dedicated endpoint instead.
  reactivateEmployee: async (id: string) => {
    const response = await apiClient.put<any>(API_ENDPOINTS.REACTIVATE_EMPLOYEE(id));
    return mapBackendEmployee(response.data);
  },

  // Admin-only: set a user's password without their current one, for someone who
  // has forgotten it. The backend notifies the user that it was changed.
  resetPassword: async (id: string, newPassword: string, confirmPassword: string) => {
    const response = await apiClient.post<{ success: boolean; message: string }>(
      API_ENDPOINTS.ADMIN_RESET_PASSWORD(id),
      { new_password: newPassword, confirm_password: confirmPassword },
    );
    return response.data;
  },

  /** Who reports to the signed-in manager. Manager-level on the server. */
  getSubordinates: async () => {
    const res = await apiClient.get<unknown[]>(API_ENDPOINTS.SUBORDINATES);
    return Array.isArray(res.data) ? res.data : [];
  },

  /**
   * Attach identity documents to a user record — CNIC scans, the profile
   * picture, the LinkedIn welcome shot.
   *
   * Send the bare stored URL, never one carrying `?exp=&sig=`. A signature
   * lasts an hour and is minted per response; saving one back is what left
   * four profile pictures permanently broken.
   */
  updateDocuments: async (userId: string | number, payload: {
    profile_picture_url?: string | null;
    linkedin_welcome_picture_url?: string | null;
    cnic_front_url?: string | null;
    cnic_back_url?: string | null;
  }) => {
    const res = await apiClient.put<unknown>(API_ENDPOINTS.USER_DOCUMENTS(userId), payload);
    return res.data;
  },
};

