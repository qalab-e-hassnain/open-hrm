import { apiClient } from './api';
import { API_ENDPOINTS } from '../config/constants';
import { type User } from '../types';

export interface LoginCredentials {
  email: string;
  password: string;
  rememberDevice?: boolean;
}

export interface LoginResponse {
  access_token: string;
  // Absent both on older backends and whenever the caller didn't opt into
  // "remember this device" — handled gracefully either way.
  refresh_token?: string | null;
  token_type: string;
  user: {
    id: number;
    email: string;
    full_name: string;
    role: string;
    is_active: boolean;
    employee_id?: string;
    department?: string;
    designation?: string;
  };
}

// Map backend user response to frontend User type
const mapBackendUser = (backendUser: any): User => {
  if (!backendUser) return {} as User;
  
  console.log('📥 mapBackendUser() - Raw backend response:', backendUser);
  
  const mappedUser: User = {
    // 🔵 Core Fields
    id: backendUser.id?.toString() || '',
    name: backendUser.full_name || backendUser.name || '',
    email: backendUser.email || '',
    role: (backendUser.role || 'employee') as 'employee' | 'manager' | 'admin',
    gender: backendUser.gender || undefined,
    maritalStatus: backendUser.marital_status || backendUser.maritalStatus || undefined,
    designation: backendUser.designation || backendUser.role || 'Employee',
    department: backendUser.department || '',
    
    // 🟢 Contact Fields
    phone: backendUser.phone || '',
    personalEmail: backendUser.personal_email || backendUser.personalEmail || '',
    
    // 🟡 Address & CNIC Fields
    residentialAddress: backendUser.address || backendUser.residential_address || backendUser.residentialAddress || '',
    cnic: backendUser.cnic_number || backendUser.cnic || '',
    dateOfBirth: backendUser.date_of_birth || backendUser.dateOfBirth || '',
    fatherName: backendUser.father_name || backendUser.fatherName || '',
    
    // 🔴 Emergency Contact - Flattened from backend schema
    emergencyContactName: backendUser.emergency_contact_name || 
                         backendUser.emergencyContactName || 
                         (backendUser.emergency_contact?.name) || '',
    emergencyContactPhone: backendUser.emergency_contact_phone || 
                          backendUser.emergencyContactPhone || 
                          (backendUser.emergency_contact?.phone) || '',
    
    // 🟣 Employment Details - Flattened from backend schema
    managerId: backendUser.manager_id !== undefined ? backendUser.manager_id : 
               (backendUser.managerId || null),
    isActive: backendUser.is_active !== undefined ? backendUser.is_active : true,
    // HR managers reach the admin area; the backend says so with is_hr.
    isHr: Boolean(backendUser.is_hr ?? backendUser.isHr),
    // Whether this person reaches the HR area at all. The server combines
    // role and is_hr into one field precisely so the client does not
    // recombine them and get it subtly wrong.
    hasHrAccess: Boolean(backendUser.has_hr_access ?? backendUser.hasHrAccess),
    // True while covering full admin authority for an admin on approved leave today.
    isActingAdmin: Boolean(backendUser.is_acting_admin ?? backendUser.isActingAdmin),
    isLabCustodian: Boolean(backendUser.is_lab_custodian ?? backendUser.isLabCustodian),
    hourlyRate: backendUser.hourly_rate || backendUser.hourlyRate || 0,
    overtimeEligible: backendUser.overtime_eligible !== undefined ? backendUser.overtime_eligible : true,
    standardHours: backendUser.standard_hours || backendUser.standardHours || 0,
    
    // 🖼️ Document URLs from backend schema
    profilePictureUrl: backendUser.profile_picture_url || backendUser.profilePictureUrl || 
                      backendUser.avatar || backendUser.profile_picture || '',
    linkedinWelcomePictureUrl: backendUser.linkedin_welcome_picture_url || 
                              backendUser.linkedinWelcomePictureUrl || '',
    cnicFrontUrl: backendUser.cnic_front_url || backendUser.cnicFrontUrl || '',
    cnicBackUrl: backendUser.cnic_back_url || backendUser.cnicBackUrl || '',
    
    // 💳 Banking Information from backend schema
    accountNumber: backendUser.account_number || backendUser.accountNumber || 
                   (backendUser.banking_info?.account_number) || 
                   (backendUser.bankingInfo?.accountNumber) || null,
    bankName: backendUser.bank_number || backendUser.bankName || 
              (backendUser.banking_info?.bank_name) || 
              (backendUser.bankingInfo?.bankName) || null,
    branchCode: backendUser.branch_code || backendUser.branchCode || 
                (backendUser.banking_info?.branch_code) || 
                (backendUser.bankingInfo?.branchCode) || null,
    accountTitle: backendUser.account_title || backendUser.accountTitle || 
                  (backendUser.banking_info?.account_title) || 
                  (backendUser.bankingInfo?.accountTitle) || null,
    
    // Legacy avatar field for backward compatibility
    avatar: backendUser.profile_picture || backendUser.profile_picture_url || 
            backendUser.avatar || '',

    // Employment details, nested. The backend returns these flat, but the
    // profile screen reads them through employmentDetails — leaving it unset
    // made every row there ("Joining date", "Employee ID") read as "Not
    // provided" even though the values had been saved at creation.
    // reportingManager stays out: the payload carries manager_id only, so the
    // name is resolved separately by whoever needs to display it.
    // Printed on the employment letter; the public verify endpoint reads it.
    employmentVerificationCode:
      backendUser.employment_verification_code || backendUser.employmentVerificationCode || '',
    // Computed server-side and attached to every UserResponse, so the
    // sign-in prompt needs no second request.
    profileCompletion: backendUser.profile_completion || backendUser.profileCompletion || undefined,
    employmentDetails: {
      employeeId: backendUser.employee_id || backendUser.employeeId || '',
      joinDate: backendUser.date_of_joining || backendUser.joinDate || '',
      department: backendUser.department || '',
      designation: backendUser.designation || '',
      // No column backs this yet, so it stays empty rather than inventing one.
      employmentType: backendUser.employment_type || backendUser.employmentType || '',
    },
  };
  
  console.log('✅ mapBackendUser() - Transformed user object:', mappedUser);
  
  return mappedUser;
};

export const authService = {
  login: async (credentials: LoginCredentials) => {
    const response = await apiClient.post<LoginResponse>(API_ENDPOINTS.LOGIN, {
      email: credentials.email,
      password: credentials.password,
      remember_device: credentials.rememberDevice ?? false,
    });
    console.log('Raw backend response:', response);
    
    // Backend returns { access_token, token_type, user }
    // Transform to match our expected format
    const backendUser = response.data?.user;
    const mappedUser = mapBackendUser(backendUser);
    
    console.log('Mapped user:', mappedUser);
    
    return {
      user: mappedUser,
      token: response.data?.access_token || '',
      refreshToken: response.data?.refresh_token || '',
    };
  },


  getProfile: async () => {
    const response = await apiClient.get<any>(API_ENDPOINTS.PROFILE);
    // Handle different response structures
    const userData = response.data?.data || response.data;
    // Map backend user to frontend format
    return mapBackendUser(userData);
  },

  changePassword: async (data: {
    currentPassword: string;
    newPassword: string;
    confirmPassword: string;
  }) => {
    const response = await apiClient.post<{ message: string }>(API_ENDPOINTS.CHANGE_PASSWORD, {
      current_password: data.currentPassword,
      new_password: data.newPassword,
      confirm_password: data.confirmPassword,
    });
    return response.data;
  },

  updateProfile: async (
    data: Partial<User>,
    options: { allowEmployment?: boolean } = {},
  ) => {
    // Transform frontend User format to EXACT backend schema format
    // Backend expects FLAT structure with snake_case keys
    
    console.log('🔄 ===== PROFILE UPDATE - REQUEST PHASE =====');
    console.log('Step 1️⃣ Frontend input (User object):', JSON.stringify(data, null, 2));
    
    const backendData: any = {};
    
    // 🔵 CORE REQUIRED FIELDS
    if (data.name !== undefined && data.name !== '') {
      backendData.full_name = data.name;
    }
    if (data.email !== undefined && data.email !== '') {
      backendData.email = data.email;
    }
    if (data.gender !== undefined) {
      backendData.gender = data.gender;
    }

    // 🟢 CONTACT FIELDS - Match backend schema
    if (data.phone !== undefined && data.phone !== '') {
      backendData.phone = data.phone;
    }
    if (data.personalEmail !== undefined && data.personalEmail !== '') {
      backendData.personal_email = data.personalEmail;
    }
    
    // 🟡 ADDRESS & CNIC FIELDS - Match backend schema
    // CRITICAL: Backend expects 'address' NOT 'residential_address'
    if (data.residentialAddress !== undefined && data.residentialAddress !== '') {
      backendData.address = data.residentialAddress;
    }
    if (data.cnic !== undefined && data.cnic !== '') {
      backendData.cnic_number = data.cnic;
    }
    if (data.dateOfBirth !== undefined && data.dateOfBirth !== '') {
      backendData.date_of_birth = data.dateOfBirth;
    }
    if (data.fatherName !== undefined && data.fatherName !== '') {
      backendData.father_name = data.fatherName;
    }
    
    // 🟡 DEPARTMENT & DESIGNATION - Match backend schema
    if (data.department !== undefined && data.department !== '') {
      backendData.department = data.department;
    }
    if (data.designation !== undefined && data.designation !== '') {
      backendData.designation = data.designation;
    }
    
    // 🔴 EMERGENCY CONTACT - Flattened keys to match backend schema
    if (data.emergencyContactName !== undefined && data.emergencyContactName !== '') {
      backendData.emergency_contact_name = data.emergencyContactName;
    }
    if (data.emergencyContactPhone !== undefined && data.emergencyContactPhone !== '') {
      backendData.emergency_contact_phone = data.emergencyContactPhone;
    }
    
    // 🟣 EMPLOYMENT DETAILS - Flattened keys to match backend schema
    // Manager assignment is only included for Admins and authorized HR
    // managers. The backend remains the source of truth for permission checks.
    if (options.allowEmployment && data.managerId !== undefined) {
      backendData.manager_id = data.managerId;
    }
    if (data.hourlyRate !== undefined && data.hourlyRate > 0) {
      backendData.hourly_rate = data.hourlyRate;
    }
    if (data.overtimeEligible !== undefined) {
      backendData.overtime_eligible = data.overtimeEligible;
    }
    if (data.standardHours !== undefined && data.standardHours > 0) {
      backendData.standard_hours = data.standardHours;
    }
    
    // 🖼️ DOCUMENT URLs - Match backend schema
    if (data.profilePictureUrl !== undefined && data.profilePictureUrl !== '') {
      backendData.profile_picture_url = data.profilePictureUrl;
    }
    if (data.linkedinWelcomePictureUrl !== undefined && data.linkedinWelcomePictureUrl !== '') {
      backendData.linkedin_welcome_picture_url = data.linkedinWelcomePictureUrl;
    }
    if (data.cnicFrontUrl !== undefined && data.cnicFrontUrl !== '') {
      backendData.cnic_front_url = data.cnicFrontUrl;
    }
    if (data.cnicBackUrl !== undefined && data.cnicBackUrl !== '') {
      backendData.cnic_back_url = data.cnicBackUrl;
    }
    
    // 💳 BANKING INFORMATION - Match backend schema
    // CRITICAL: Send banking fields to prevent them from being overwritten as null
    if (data.accountNumber !== undefined && data.accountNumber !== '') {
      backendData.account_number = data.accountNumber;
    }
    if (data.bankName !== undefined && data.bankName !== '') {
      backendData.bank_number = data.bankName;
    }
    if (data.branchCode !== undefined && data.branchCode !== '') {
      backendData.branch_code = data.branchCode;
    }
    if (data.accountTitle !== undefined && data.accountTitle !== '') {
      backendData.account_title = data.accountTitle;
    }
    
    console.log('Step 2️⃣ Transformed backend request payload:', JSON.stringify(backendData, null, 2));
    console.log('Endpoint:', API_ENDPOINTS.UPDATE_PROFILE);
    console.log('Sending PUT request to /users/me...\n');
    
    try {
      const response = await apiClient.put<any>(API_ENDPOINTS.UPDATE_PROFILE, backendData);
      
      console.log('🔄 ===== PROFILE UPDATE - RESPONSE PHASE =====');
      console.log('Step 3️⃣ Raw backend response:', JSON.stringify(response.data, null, 2));
      
      // Handle different response structures
      const userData = response.data?.data || response.data;
      
      // 📊 DEBUG: Compare sent vs received
      console.log('\n📊 FIELD COMPARISON:');
      console.log('Sent:', Object.keys(backendData).sort());
      console.log('Received:', Object.keys(userData).sort());
      
      // Identify missing fields
      const backendKeys = Object.keys(backendData);
      const responseKeys = userData ? Object.keys(userData) : [];
      const missingFields = backendKeys.filter((key: string) => 
        !responseKeys.includes(key) && !['full_name', 'address', 'date_of_birth', 
                                        'emergency_contact_name', 'emergency_contact_phone',
                                        'manager_id', 'profile_picture_url', 
                                        'linkedin_welcome_picture_url', 'cnic_front_url',
                                        'cnic_back_url', 'account_number', 'bank_number',
                                        'branch_code', 'account_title'].includes(key)
      );
      
      if (missingFields.length > 0) {
        console.warn('⚠️ Missing fields in backend response:', missingFields);
      }
      
      // Map backend response to frontend User format
      const mappedUser = mapBackendUser(userData);
      
      console.log('Step 4️⃣ Final transformed frontend object:', JSON.stringify(mappedUser, null, 2));
      console.log('✅ Profile update successful!\n');
      
      return mappedUser;
    } catch (error: any) {
      console.error('❌ ===== PROFILE UPDATE - ERROR PHASE =====');
      console.error('Full Error Object:', error);
      console.error('Error Type:', error.constructor.name);
      console.error('Error Message:', error.message);
      console.error('Status:', error.response?.status);
      console.error('Response Data:', error.response?.data);
      console.error('Request Config:', {
        method: error.config?.method,
        url: error.config?.url,
        data: error.config?.data
      });
      
      // Build detailed error message for user
      let errorMessage = 'Failed to update profile. ';
      
      if (error.response?.status === 422) {
        errorMessage += 'Validation Error: ';
        
        // Check various error response formats the backend might use
        if (error.response?.data?.detail) {
          errorMessage += error.response.data.detail;
        } else if (error.response?.data?.errors) {
          // Handle array of errors
          if (Array.isArray(error.response.data.errors)) {
            errorMessage += error.response.data.errors.map((e: any) => 
              typeof e === 'string' ? e : e.msg || e.message || JSON.stringify(e)
            ).join('; ');
          } else {
            // Handle object of errors
            errorMessage += Object.entries(error.response.data.errors)
              .map(([field, messages]: [string, any]) => {
                const msgs = Array.isArray(messages) ? messages.join(', ') : messages;
                return `${field}: ${msgs}`;
              })
              .join('; ');
          }
        } else if (error.response?.data?.message) {
          errorMessage += error.response.data.message;
        } else if (typeof error.response?.data === 'string') {
          errorMessage += error.response.data;
        } else {
          errorMessage += JSON.stringify(error.response?.data) || 'Invalid request format';
        }
      } else if (error.response?.data?.detail) {
        errorMessage += error.response.data.detail;
      } else if (error.response?.data?.message) {
        errorMessage += error.response.data.message;
      } else if (error.message) {
        errorMessage += error.message;
      }
      
      // Not shown via alert(): a blocking native dialog on top of the profile
      // editor drawer, when the caller (Profile.tsx) already displays this
      // same message inline through its own notice banner.
      console.error('❌ Final error message:', errorMessage);
      throw error;
    }
  },
};

