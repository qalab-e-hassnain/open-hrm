import { useEffect, useMemo, useRef, useState } from 'react';
import {
  FiAlertTriangle,
  FiCamera,
  FiCheck,
  FiChevronDown,
  FiCopy,
  FiEye,
  FiEyeOff,
  FiInfo,
  FiRefreshCw,
  FiTrash2,
  FiUploadCloud,
  FiUserPlus,
  FiX,
} from 'react-icons/fi';
import { apiClient, isForcedLogoutRedirect } from '../../services/api';
import { employeeService } from '../../services/employeeService';
import type { Employee } from '../../types';
import {
  normaliseDigits,
  formatPakPhone,
  formatCnic,
  sanitizeName,
  validateAddress,
  validatePersonName,
  validateEmail,
  validatePakPhone,
  validateCnic,
} from '../../utils/pkValidation';
import './CreateUserPanel.css';
import { describeDay, rateBasis } from '../../utils/workHours';

// 'hr' is a UI-level choice: it is stored as a manager carrying the HR flag,
// so no new role value was added to the database.
type Role = 'employee' | 'manager' | 'hr' | 'admin';
type Gender = 'male' | 'female' | '';
type Marital = 'single' | 'married' | '';
interface CreateUserForm {
  name: string;
  email: string;
  phone: string;
  gender: Gender;
  dateOfBirth: string;
  maritalStatus: Marital;
  anniversaryDate: string;
  cnic: string;
  dateOfJoining: string;
  employeeId: string;
  department: string;
  designation: string;
  managerId: string;
  residentialAddress: string;
  role: Role;
  password: string;
  baseSalary: string;
  allowance: string;
  standardHours: string;
}

interface CreateUserPanelProps {
  employees: Employee[];
  managers: Employee[];
  onCreated: () => Promise<void> | void;
  onOpenManage: () => void;
}

type FieldName = keyof CreateUserForm;
type FieldErrors = Partial<Record<FieldName | 'form', string>>;

const DRAFT_KEY = 'hrm-create-user-draft';
const REQUIRED_FIELDS: FieldName[] = [
  'name',
  'email',
  'phone',
  'gender',
  'dateOfBirth',
  'maritalStatus',
  'employeeId',
  'department',
  'designation',
  'role',
  'password',
  'baseSalary',
];

const FIELD_LABELS: Partial<Record<FieldName, string>> = {
  name: 'Full name',
  email: 'Email address',
  phone: 'Phone number',
  gender: 'Gender',
  dateOfBirth: 'Date of birth',
  maritalStatus: 'Marital status',
  anniversaryDate: 'Anniversary date',
  cnic: 'CNIC number',
  dateOfJoining: 'Date of joining',
  employeeId: 'Employee ID',
  department: 'Department',
  designation: 'Designation',
  residentialAddress: 'Residential address',
  role: 'Role',
  password: 'Temporary password',
  baseSalary: 'Basic salary',
  allowance: 'Allowance',
  standardHours: 'Contracted hours per day',
};

// Maps the backend's snake_case UserCreate field names back to this form's
// field names, so a server-side validation error can highlight the actual
// input instead of just naming it in a banner.
const BACKEND_FIELD_MAP: Record<string, FieldName> = {
  full_name: 'name',
  email: 'email',
  phone: 'phone',
  gender: 'gender',
  date_of_birth: 'dateOfBirth',
  marital_status: 'maritalStatus',
  anniversary_date: 'anniversaryDate',
  cnic_number: 'cnic',
  date_of_joining: 'dateOfJoining',
  employee_id: 'employeeId',
  department: 'department',
  designation: 'designation',
  address: 'residentialAddress',
  role: 'role',
  password: 'password',
  base_salary: 'baseSalary',
  allowance: 'allowance',
};

const EMPTY_FORM: CreateUserForm = {
  name: '',
  email: '',
  phone: '',
  gender: '',
  dateOfBirth: '',
  maritalStatus: '',
  anniversaryDate: '',
  cnic: '',
  dateOfJoining: '',
  employeeId: '',
  department: '',
  designation: '',
  managerId: '',
  residentialAddress: '',
  role: 'employee',
  password: '',
  baseSalary: '',
  allowance: '',
  // Hours per day. Set at creation so a part-time contract is never
  // recorded as full time and quietly paid on a full-time month.
  standardHours: '8',
};

// Each department carries the C-level title for the function it leads, so the
// CTO is found under Engineering and the CMO under Marketing. The CEO has no
// single function, so Executive holds the company-wide roles. Titles spell the
// role out and keep the abbreviation in parentheses, since the dropdown is
// alphabetical and admins look for "CTO" rather than "Chief Technology...".
const DESIGNATIONS: Record<string, string[]> = {
  Executive: ['Chief Executive Officer (CEO)', 'Managing Director'],
  Engineering: ['Frontend Engineer', 'Backend Engineer', 'Full Stack Engineer', 'Mobile Application Engineer', 'QA Engineer', 'DevOps Engineer', 'Hardware Design Engineer', 'Embedded Systems Engineer', 'Firmware Engineer', 'PCB Design Engineer', 'Electronics Engineer', 'IoT Engineer', 'Mechanical Design Engineer', 'Machine Learning Engineer', 'Data Engineer', 'Solution Architect', 'Technical Lead', 'Engineering Manager', 'VP Engineering', 'Chief Technology Officer (CTO)'],
  Product: ['Product Designer', 'Product Manager', 'Business Analyst', 'Chief Product Officer (CPO)'],
  'Human Resources': ['HR Executive', 'HR Manager', 'Talent Acquisition Specialist', 'Chief Human Resources Officer (CHRO)'],
  Finance: ['Finance Executive', 'Accountant', 'Payroll Officer', 'Finance Manager', 'Chief Financial Officer (CFO)'],
  Operations: ['Operations Executive', 'Operations Manager', 'Office Coordinator', 'Chief Operating Officer (COO)'],
  Marketing: ['Marketing Executive', 'Content Specialist', 'Marketing Manager', 'Chief Marketing Officer (CMO)'],
  Sales: ['Sales Executive', 'Account Manager', 'Sales Manager', 'VP Sales', 'Chief Revenue Officer (CRO)'],
};

const ROLE_COPY: Record<Role, { title: string; body: string; sensitive?: boolean }> = {
  employee: {
    title: 'Employee',
    body: 'Can view their profile, attendance, leave, documents, and assigned HR services.',
  },
  manager: {
    title: 'Manager',
    body: 'Can view team members, approve leave, and review team attendance.',
  },
  hr: {
    title: 'HR Manager',
    body: 'Full administrator access to people, payroll, and attendance — except administrator accounts.',
    sensitive: true,
  },
  admin: {
    title: 'Administrator',
    body: 'Can manage users, company records, attendance, leave, payroll, and system access.',
    sensitive: true,
  },
};

const generatePassword = () => {
  const chars = 'ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789!@#$%';
  const bytes = new Uint32Array(14);
  crypto.getRandomValues(bytes);
  return Array.from(bytes, (value) => chars[value % chars.length]).join('');
};

type RequestErrorResult = { message: string; fieldErrors: Partial<Record<FieldName, string>> };

// The backend's validation handler (main.py) formats each item as a plain
// string "loc -> path: message" (e.g. "body -> date_of_birth: field
// required"), not the raw FastAPI {msg, loc} object shape. Reading .msg off
// a string is always undefined, which is why every one of these errors used
// to collapse into the generic "not accepted" fallback instead of the real,
// specific reason. This also pulls the field out of that path so the exact
// input can be highlighted instead of just a banner naming it.
const parseRequestError = (error: unknown): RequestErrorResult => {
  const fallback = 'We could not create the user. Your information is still here. Please retry.';
  if (typeof error !== 'object' || error === null) return { message: fallback, fieldErrors: {} };
  const requestError = error as {
    message?: string;
    response?: { data?: { detail?: string | Array<string | { msg?: string; loc?: Array<string | number> }> } };
  };
  const detail = requestError.response?.data?.detail;

  if (typeof detail === 'string') return { message: detail, fieldErrors: {} };

  if (Array.isArray(detail)) {
    const fieldErrors: Partial<Record<FieldName, string>> = {};
    const messages: string[] = [];

    const record = (backendField: string | undefined, message: string) => {
      const mapped = backendField ? BACKEND_FIELD_MAP[backendField] : undefined;
      if (mapped) {
        fieldErrors[mapped] = message;
        messages.push(`${FIELD_LABELS[mapped] ?? backendField}: ${message}`);
      } else {
        messages.push(message);
      }
    };

    detail.forEach((item) => {
      if (typeof item === 'string') {
        // "body -> date_of_birth: field required" -> field "date_of_birth", message "field required"
        const match = item.match(/^(?:.*->\s*)?([\w.]+):\s*(.+)$/);
        if (match) record(match[1].trim(), match[2].trim());
        else messages.push(item);
        return;
      }
      if (item && typeof item === 'object' && item.msg) {
        const backendField = Array.isArray(item.loc) ? String(item.loc[item.loc.length - 1]) : undefined;
        record(backendField, item.msg);
      }
    });

    return {
      message: messages.join(' ') || 'The submitted information was not accepted. Please review the highlighted fields.',
      fieldErrors,
    };
  }

  return { message: requestError.message || fallback, fieldErrors: {} };
};

const formatMoney = (value: string) => {
  const digits = normaliseDigits(value).replace(/^0+(?=\d)/, '').slice(0, 10);
  return digits ? Number(digits).toLocaleString('en-PK') : '';
};

const fieldValue = (form: CreateUserForm, field: FieldName) => String(form[field] ?? '').trim();

const CreateUserPanel = ({ employees, managers, onCreated, onOpenManage }: CreateUserPanelProps) => {
  const [form, setForm] = useState<CreateUserForm>(EMPTY_FORM);
  const [errors, setErrors] = useState<FieldErrors>({});
  const [touched, setTouched] = useState<Partial<Record<FieldName, boolean>>>({});
  const [showPassword, setShowPassword] = useState(false);
  const [openSections, setOpenSections] = useState({ basic: true, work: true, access: true });
  const [photo, setPhoto] = useState<File | null>(null);
  const [photoPreview, setPhotoPreview] = useState('');
  const [uploadProgress, setUploadProgress] = useState(0);
  const [isDragging, setIsDragging] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [showReview, setShowReview] = useState(false);
  const [createdEmployee, setCreatedEmployee] = useState<Employee | null>(null);
  const [draftSavedAt, setDraftSavedAt] = useState('');
  const [hasUnsavedChanges, setHasUnsavedChanges] = useState(false);
  const [showAllMissing, setShowAllMissing] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const fieldRefs = useRef<Partial<Record<FieldName, HTMLElement | null>>>({});

  const departments = useMemo(() => {
    const existing = employees.map((employee) => employee.department).filter(Boolean);
    return Array.from(new Set([...Object.keys(DESIGNATIONS), ...existing])).sort();
  }, [employees]);

  const designations = useMemo(() => {
    const known = DESIGNATIONS[form.department] || [];
    const existing = employees
      .filter((employee) => employee.department === form.department)
      .map((employee) => employee.designation)
      .filter(Boolean);
    return Array.from(new Set([...known, ...existing])).sort();
  }, [employees, form.department]);

  const validationFor = (field: FieldName, current = form): string => {
    const value = fieldValue(current, field);
    if (REQUIRED_FIELDS.includes(field) && !value) return 'This field is required.';

    if (field === 'name' && value) {
      const nameError = validatePersonName(value);
      if (nameError) return nameError;
    }
    if (field === 'email' && value && validateEmail(value)) {
      return 'Enter a valid company email address.';
    }
    if (field === 'email' && employees.some((employee) => employee.email.toLowerCase() === value.toLowerCase())) {
      return 'A user with this email already exists.';
    }
    if (field === 'phone' && value) {
      const phoneError = validatePakPhone(value);
      if (phoneError) return phoneError;
    }
    if (field === 'phone' && employees.some((employee) => normaliseDigits(employee.phone || '') === normaliseDigits(value))) {
      return 'A user with this phone number already exists.';
    }
    if (field === 'cnic' && value) {
      const cnicError = validateCnic(value);
      if (cnicError) return cnicError;
    }
    if (field === 'cnic' && value && employees.some((employee) => normaliseDigits(employee.cnic || '') === normaliseDigits(value))) {
      return 'A user with this CNIC already exists.';
    }
    if (field === 'employeeId' && value && !/^QG-\d{4}-\d{4}$/.test(value)) {
      return 'Use the format QG-2026-0048.';
    }
    if (field === 'employeeId' && employees.some((employee) => employee.employeeId?.toLowerCase() === value.toLowerCase())) {
      return 'This Employee ID is already in use.';
    }
    if (field === 'dateOfBirth' && value) {
      const dob = new Date(`${value}T00:00:00`);
      const eighteenYearsAgo = new Date();
      eighteenYearsAgo.setFullYear(eighteenYearsAgo.getFullYear() - 18);
      if (dob > eighteenYearsAgo) return 'Employee must be at least 18 years old.';
    }
    if (field === 'anniversaryDate' && value) {
      if (current.maritalStatus !== 'married') return 'An anniversary applies to married employees only.';
      if (new Date(`${value}T00:00:00`) > new Date()) return 'The anniversary cannot be in the future.';
    }
    if (field === 'password' && !/^(?=.*[A-Z])(?=.*\d)(?=.*[^A-Za-z0-9]).{8,}$/.test(value)) {
      return 'Use 8+ characters with an uppercase letter, number, and special character.';
    }
    if (field === 'residentialAddress' && value) {
      const addressError = validateAddress(value);
      if (addressError) return addressError;
    }
    return '';
  };

  const validateAll = () => {
    const nextErrors: FieldErrors = {};
    REQUIRED_FIELDS.forEach((field) => {
      const message = validationFor(field);
      if (message) nextErrors[field] = message;
    });
    setErrors(nextErrors);
    setTouched(Object.fromEntries(REQUIRED_FIELDS.map((field) => [field, true])));
    return Object.keys(nextErrors).length === 0;
  };

  const updateField = (field: FieldName, value: string) => {
    // "Select role" is a label, not a choice: keep the current role instead of clearing it.
    if (field === 'role' && !value) return;

    let formatted = value;
    if (field === 'phone') formatted = formatPakPhone(value);
    if (field === 'cnic') formatted = formatCnic(value);
    if (field === 'baseSalary' || field === 'allowance') formatted = formatMoney(value);
    if (field === 'employeeId') formatted = value.toUpperCase();
    if (field === 'name') formatted = sanitizeName(value);

    const next = { ...form, [field]: formatted };
    if (field === 'department') next.designation = '';
    // An anniversary belongs to a marriage; drop it if the status changes.
    if (field === 'maritalStatus' && formatted !== 'married') next.anniversaryDate = '';
    setForm(next);
    setHasUnsavedChanges(true);
    if (touched[field]) setErrors((previous) => ({ ...previous, [field]: validationFor(field, next) }));
  };

  const markTouched = (field: FieldName) => {
    setTouched((previous) => ({ ...previous, [field]: true }));
    setErrors((previous) => ({ ...previous, [field]: validationFor(field) }));
  };

  const generateEmployeeId = () => {
    const year = new Date().getFullYear();
    const usedNumbers = employees
      .map((employee) => employee.employeeId?.match(/^QG-\d{4}-(\d{4})$/)?.[1])
      .filter(Boolean)
      .map(Number);
    let nextNumber = Math.max(0, ...usedNumbers) + 1;
    let candidate = `QG-${year}-${String(nextNumber).padStart(4, '0')}`;
    while (employees.some((employee) => employee.employeeId === candidate)) {
      nextNumber += 1;
      candidate = `QG-${year}-${String(nextNumber).padStart(4, '0')}`;
    }
    updateField('employeeId', candidate);
  };

  const handlePhoto = (file?: File) => {
    if (!file) return;
    if (!['image/jpeg', 'image/png', 'image/webp'].includes(file.type)) {
      setErrors((previous) => ({ ...previous, form: 'Photo must be a JPG, PNG, or WEBP image.' }));
      return;
    }
    if (file.size > 2 * 1024 * 1024) {
      setErrors((previous) => ({ ...previous, form: 'Photo must be 2 MB or smaller.' }));
      return;
    }
    if (photoPreview) URL.revokeObjectURL(photoPreview);
    setPhoto(file);
    setPhotoPreview(URL.createObjectURL(file));
    setHasUnsavedChanges(true);
    setErrors((previous) => ({ ...previous, form: undefined }));
  };

  const saveDraft = () => {
    localStorage.setItem(DRAFT_KEY, JSON.stringify({ form, savedAt: new Date().toISOString() }));
    setDraftSavedAt(new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }));
    setHasUnsavedChanges(false);
  };

  useEffect(() => {
    const stored = localStorage.getItem(DRAFT_KEY);
    if (!stored) return;
    try {
      const draft = JSON.parse(stored);
      if (draft.form) setForm({ ...EMPTY_FORM, ...draft.form });
      if (draft.savedAt) {
        setDraftSavedAt(new Date(draft.savedAt).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }));
      }
    } catch {
      localStorage.removeItem(DRAFT_KEY);
    }
  }, []);

  useEffect(() => {
    if (!hasUnsavedChanges) return;
    const timeout = window.setTimeout(saveDraft, 15000);
    return () => window.clearTimeout(timeout);
  }, [form, hasUnsavedChanges]);

  useEffect(() => {
    const preventLoss = (event: BeforeUnloadEvent) => {
      if (!hasUnsavedChanges || isForcedLogoutRedirect) return;
      event.preventDefault();
    };
    window.addEventListener('beforeunload', preventLoss);
    return () => window.removeEventListener('beforeunload', preventLoss);
  }, [hasUnsavedChanges]);

  useEffect(() => () => {
    if (photoPreview) URL.revokeObjectURL(photoPreview);
  }, [photoPreview]);

  const openReview = () => {
    if (!validateAll()) {
      const firstMissing = REQUIRED_FIELDS.find((field) => validationFor(field));
      if (firstMissing) scrollToField(firstMissing);
      return;
    }
    setShowReview(true);
  };

  const createUser = async () => {
    setIsSubmitting(true);
    setErrors((previous) => ({ ...previous, form: undefined }));
    try {
      const created = await employeeService.createEmployee({
        name: form.name.trim(),
        email: form.email.trim(),
        phone: normaliseDigits(form.phone),
        password: form.password,
        department: form.department,
        designation: form.designation,
        employeeId: form.employeeId,
        role: form.role === 'hr' ? 'manager' : form.role,
        isHr: form.role === 'hr',
        gender: form.gender as 'male' | 'female',
        maritalStatus: form.maritalStatus || undefined,
        anniversaryDate: form.maritalStatus === 'married' && form.anniversaryDate ? form.anniversaryDate : undefined,
        cnic: form.cnic ? normaliseDigits(form.cnic) : undefined,
        dateOfBirth: form.dateOfBirth,
        joinDate: form.dateOfJoining || undefined,
        residentialAddress: form.residentialAddress.trim(),
        baseSalary: form.baseSalary ? Number(normaliseDigits(form.baseSalary)) : undefined,
        allowance: form.allowance ? Number(normaliseDigits(form.allowance)) : undefined,
        standardHours: form.standardHours ? Number(form.standardHours) : undefined,
      });

      if (form.managerId) await employeeService.assignManager(created.id, form.managerId);

      if (photo) {
        setUploadProgress(15);
        const uploadData = new FormData();
        uploadData.append('file', photo);
        const response = await apiClient.post<{ url?: string }>('/upload', uploadData, {
          headers: { 'Content-Type': 'multipart/form-data' },
          // This is the new joiner's profile picture: shrink it on the way in.
          params: { purpose: 'avatar' },
          onUploadProgress: (event) => {
            if (event.total) setUploadProgress(Math.round((event.loaded / event.total) * 100));
          },
        });
        if (response.data?.url) {
          await employeeService.updateEmployee(created.id, { profilePictureUrl: response.data.url });
          created.profilePictureUrl = response.data.url;
        }
      }

      localStorage.removeItem(DRAFT_KEY);
      setHasUnsavedChanges(false);
      setDraftSavedAt('');
      setShowReview(false);
      setCreatedEmployee(created);
      await onCreated();
    } catch (error: unknown) {
      setShowReview(false);
      const { message, fieldErrors } = parseRequestError(error);
      setErrors((previous) => ({ ...previous, ...fieldErrors, form: message }));
      const invalidFields = Object.keys(fieldErrors) as FieldName[];
      if (invalidFields.length) {
        setTouched((previous) => {
          const next = { ...previous };
          invalidFields.forEach((field) => { next[field] = true; });
          return next;
        });
        scrollToField(invalidFields[0]);
      }
    } finally {
      setIsSubmitting(false);
      setUploadProgress(0);
    }
  };

  const resetForm = () => {
    setForm(EMPTY_FORM);
    setTouched({});
    setErrors({});
    setPhoto(null);
    setPhotoPreview('');
    setCreatedEmployee(null);
    setDraftSavedAt('');
    setHasUnsavedChanges(false);
    localStorage.removeItem(DRAFT_KEY);
  };

  const scrollToField = (field: FieldName) => {
    const section = (['name', 'email', 'phone', 'gender', 'dateOfBirth', 'maritalStatus', 'anniversaryDate', 'cnic'] as FieldName[]).includes(field)
      ? 'basic'
      : (['employeeId', 'dateOfJoining', 'department', 'designation', 'managerId', 'residentialAddress', 'baseSalary', 'allowance'] as FieldName[]).includes(field)
        ? 'work'
        : 'access';
    setOpenSections((sections) => ({ ...sections, [section]: true }));
    window.setTimeout(() => {
      fieldRefs.current[field]?.scrollIntoView({ behavior: 'smooth', block: 'center' });
      (fieldRefs.current[field] as HTMLInputElement | null)?.focus?.();
    }, 80);
  };

  const completedRequired = REQUIRED_FIELDS.filter((field) => fieldValue(form, field) && !validationFor(field)).length;
  const isComplete = completedRequired === REQUIRED_FIELDS.length;
  const initials = form.name.trim()
    ? form.name.trim().split(/\s+/).slice(0, 2).map((part) => part[0]).join('').toUpperCase()
    : 'NU';
  const roleCopy = ROLE_COPY[form.role] ?? ROLE_COPY.employee;
  const selectedManager = managers.find((manager) => manager.id === form.managerId);
  const missingFields = REQUIRED_FIELDS.filter((field) => !fieldValue(form, field) || validationFor(field));
  const visibleMissingFields = showAllMissing ? missingFields : missingFields.slice(0, 4);
  const totalMonthlyPackage = Number(normaliseDigits(form.baseSalary)) + Number(normaliseDigits(form.allowance));

  if (createdEmployee) {
    return (
      <section className="create-user-success" aria-live="polite">
        <div className="success-mark"><FiCheck aria-hidden="true" /></div>
        <p className="success-eyebrow">User created</p>
        <h3>{createdEmployee.name} is ready</h3>
        <p>
          The temporary password is active and must be changed after first sign in.
        </p>
        <div className="success-actions">
          <button type="button" className="cu-secondary-button" onClick={onOpenManage}>Go to Manage Users</button>
          <button type="button" className="cu-primary-button" onClick={resetForm}><FiUserPlus /> Create another user</button>
        </div>
      </section>
    );
  }

  return (
    <section className="create-user-workspace">
      <div className="create-user-main">
        <div
          className={`photo-upload-row${isDragging ? ' is-dragging' : ''}`}
          onDragOver={(event) => { event.preventDefault(); setIsDragging(true); }}
          onDragLeave={() => setIsDragging(false)}
          onDrop={(event) => {
            event.preventDefault();
            setIsDragging(false);
            handlePhoto(event.dataTransfer.files[0]);
          }}
        >
          <button type="button" className="photo-preview" onClick={() => fileInputRef.current?.click()} aria-label="Choose profile photo">
            {photoPreview
              ? <img src={photoPreview} alt="Selected profile preview" />
              : form.name.trim()
                ? <span aria-hidden="true">{initials}</span>
                : <FiCamera aria-hidden="true" />}
          </button>
          <div>
            <strong>Profile photo <span className="optional-label">Optional</span></strong>
            <p>Drag and drop or choose a JPG, PNG, or WEBP image up to 2 MB.</p>
            <div className="photo-actions">
              <button type="button" className="cu-text-button" onClick={() => fileInputRef.current?.click()}>
                <FiUploadCloud /> {photo ? 'Replace photo' : 'Choose file'}
              </button>
              {photo && (
                <button type="button" className="cu-text-button danger" onClick={() => { setPhoto(null); setPhotoPreview(''); }}>
                  <FiTrash2 /> Remove
                </button>
              )}
            </div>
          </div>
          <input
            ref={fileInputRef}
            className="visually-hidden"
            type="file"
            accept="image/jpeg,image/png,image/webp"
            onChange={(event) => handlePhoto(event.target.files?.[0])}
          />
        </div>

        {errors.form && (
          <div className="cu-form-alert" role="alert">
            <FiAlertTriangle aria-hidden="true" />
            <span>{String(errors.form)}</span>
            <button type="button" onClick={() => setErrors((previous) => ({ ...previous, form: undefined }))} aria-label="Dismiss error"><FiX /></button>
          </div>
        )}

        <FormSection
          number="1"
          title="Basic information"
          subtitle="Personal and contact details used across HR records."
          expanded={openSections.basic}
          onToggle={() => setOpenSections((sections) => ({ ...sections, basic: !sections.basic }))}
        >
          <div className="cu-field-grid">
            <Field label="Full name" required error={touched.name ? errors.name : ''} fieldRef={(node) => { fieldRefs.current.name = node; }}>
              <input ref={(node) => { fieldRefs.current.name = node; }} value={form.name} onChange={(event) => updateField('name', event.target.value)} onBlur={() => markTouched('name')} placeholder="e.g. Arafay Shah" aria-invalid={Boolean(errors.name)} />
            </Field>
            <Field label="Email address" required error={touched.email ? errors.email : ''}>
              <input ref={(node) => { fieldRefs.current.email = node; }} type="email" autoComplete="off" value={form.email} onChange={(event) => updateField('email', event.target.value)} onBlur={() => markTouched('email')} placeholder="name@example.com" aria-invalid={Boolean(errors.email)} />
            </Field>
            <Field label="Phone number" required error={touched.phone ? errors.phone : ''} hint="Pakistani mobile number">
              <input ref={(node) => { fieldRefs.current.phone = node; }} inputMode="tel" value={form.phone} onChange={(event) => updateField('phone', event.target.value)} onBlur={() => markTouched('phone')} placeholder="0300 123 4567" aria-invalid={Boolean(errors.phone)} />
            </Field>
            <Field label="Gender" required error={touched.gender ? errors.gender : ''}>
              <SelectControl inputRef={(node) => { fieldRefs.current.gender = node; }} value={form.gender} onChange={(value) => updateField('gender', value)} onBlur={() => markTouched('gender')} placeholder="Select gender" options={['Male', 'Female']} optionValues={['male', 'female']} />
            </Field>
            <Field label="Date of birth" required error={touched.dateOfBirth ? errors.dateOfBirth : ''} hint="Admins and HR are reminded on the day">
              <input ref={(node) => { fieldRefs.current.dateOfBirth = node; }} type="date" value={form.dateOfBirth} onChange={(event) => updateField('dateOfBirth', event.target.value)} onBlur={() => markTouched('dateOfBirth')} aria-invalid={Boolean(errors.dateOfBirth)} />
            </Field>
            <Field label="Marital status" required error={touched.maritalStatus ? errors.maritalStatus : ''} hint="Married employees are eligible for maternity or paternity leave">
              <SelectControl inputRef={(node) => { fieldRefs.current.maritalStatus = node; }} value={form.maritalStatus} onChange={(value) => updateField('maritalStatus', value)} onBlur={() => markTouched('maritalStatus')} placeholder="Select marital status" options={['Single', 'Married']} optionValues={['single', 'married']} />
            </Field>
            {form.maritalStatus === 'married' && (
              <Field label="Anniversary date" error={touched.anniversaryDate ? errors.anniversaryDate : ''} hint="Optional. Admins and HR are reminded on the day.">
                <input ref={(node) => { fieldRefs.current.anniversaryDate = node; }} type="date" value={form.anniversaryDate} onChange={(event) => updateField('anniversaryDate', event.target.value)} onBlur={() => markTouched('anniversaryDate')} aria-invalid={Boolean(errors.anniversaryDate)} />
              </Field>
            )}
            <Field label="CNIC" error={touched.cnic ? errors.cnic : ''} hint="Optional unless required for payroll">
              <input ref={(node) => { fieldRefs.current.cnic = node; }} inputMode="numeric" value={form.cnic} onChange={(event) => updateField('cnic', event.target.value)} onBlur={() => markTouched('cnic')} placeholder="35201-1234567-8" aria-invalid={Boolean(errors.cnic)} />
            </Field>
          </div>
        </FormSection>

        <FormSection
          number="2"
          title="Work details"
          subtitle="Position, reporting line, salary, and employee record information."
          expanded={openSections.work}
          onToggle={() => setOpenSections((sections) => ({ ...sections, work: !sections.work }))}
        >
          <div className="cu-field-grid">
            <Field label="Employee ID" required error={touched.employeeId ? errors.employeeId : ''}>
              <div className="cu-input-action">
                <input ref={(node) => { fieldRefs.current.employeeId = node; }} value={form.employeeId} onChange={(event) => updateField('employeeId', event.target.value)} onBlur={() => markTouched('employeeId')} placeholder="QG-2026-0048" aria-invalid={Boolean(errors.employeeId)} />
                <button type="button" onClick={generateEmployeeId}><FiRefreshCw /> Auto</button>
              </div>
            </Field>
            <Field label="Date of joining" error={touched.dateOfJoining ? errors.dateOfJoining : ''} hint="Optional. Defaults to today — set this when backfilling an employee who joined before this system did.">
              <input ref={(node) => { fieldRefs.current.dateOfJoining = node; }} type="date" value={form.dateOfJoining} onChange={(event) => updateField('dateOfJoining', event.target.value)} onBlur={() => markTouched('dateOfJoining')} aria-invalid={Boolean(errors.dateOfJoining)} />
            </Field>
            <Field label="Department" required error={touched.department ? errors.department : ''}>
              <SelectControl inputRef={(node) => { fieldRefs.current.department = node; }} value={form.department} onChange={(value) => updateField('department', value)} onBlur={() => markTouched('department')} placeholder="Select department" options={departments} />
            </Field>
            <Field label="Designation" required error={touched.designation ? errors.designation : ''} hint={!form.department ? 'Select a department first' : ''}>
              <SelectControl inputRef={(node) => { fieldRefs.current.designation = node; }} value={form.designation} onChange={(value) => updateField('designation', value)} onBlur={() => markTouched('designation')} placeholder="Select designation" options={designations} disabled={!form.department} />
            </Field>
            <Field label="Reporting manager" hint="Optional. This can also be assigned later.">
              <SelectControl inputRef={(node) => { fieldRefs.current.managerId = node; }} value={form.managerId} onChange={(value) => updateField('managerId', value)} placeholder="No manager selected" options={managers.map((manager) => `${manager.name} · ${manager.employeeId || manager.designation || 'Manager'}`)} optionValues={managers.map((manager) => manager.id)} />
            </Field>
            <Field label="Residential address" className="cu-field-wide" error={touched.residentialAddress ? errors.residentialAddress : ''} hint="Current residential address">
              <input ref={(node) => { fieldRefs.current.residentialAddress = node; }} value={form.residentialAddress} onChange={(event) => updateField('residentialAddress', event.target.value)} onBlur={() => markTouched('residentialAddress')} placeholder="House, street, city, province" aria-invalid={Boolean(errors.residentialAddress)} />
            </Field>
            <section className="cu-compensation-subsection">
              <header>
                <div><h4>Compensation</h4><p>Monthly salary information visible only to authorized administrators.</p></div>
                <span>Restricted</span>
              </header>
              <div className="cu-compensation-grid">
                <Field label="Basic salary (PKR)" required error={touched.baseSalary ? errors.baseSalary : ''} hint="Required monthly salary">
                  <input ref={(node) => { fieldRefs.current.baseSalary = node; }} inputMode="numeric" value={form.baseSalary} onChange={(event) => updateField('baseSalary', event.target.value)} onBlur={() => markTouched('baseSalary')} placeholder="Enter monthly basic salary" aria-invalid={Boolean(errors.baseSalary)} />
                </Field>
                <Field label="Allowance (PKR)" hint="Optional monthly allowance">
                  <input ref={(node) => { fieldRefs.current.allowance = node; }} inputMode="numeric" value={form.allowance} onChange={(event) => updateField('allowance', event.target.value)} placeholder="Enter allowance, if applicable" />
                </Field>
                <Field label="Contracted hours per day" hint={`${describeDay(Number(form.standardHours))} \u00b7 hourly rate ${rateBasis(Number(form.standardHours))}`}>
                  <select value={form.standardHours} onChange={(event) => updateField('standardHours', event.target.value)}>
                    <option value="8">8 hours \u2014 full time</option>
                    <option value="6">6 hours \u2014 part time</option>
                    <option value="4">4 hours \u2014 part time</option>
                    <option value="2">2 hours \u2014 part time</option>
                  </select>
                </Field>
              </div>
              <div className="cu-package-total"><span>Total monthly package</span><strong>PKR {totalMonthlyPackage.toLocaleString('en-PK')}</strong></div>
            </section>
          </div>
        </FormSection>

        <FormSection
          number="3"
          title="Access and security"
          subtitle="Choose the user's access level and temporary password."
          expanded={openSections.access}
          onToggle={() => setOpenSections((sections) => ({ ...sections, access: !sections.access }))}
        >
          <div className="cu-field-grid">
            <Field label="Role" required error={touched.role ? errors.role : ''}>
              <SelectControl inputRef={(node) => { fieldRefs.current.role = node; }} value={form.role} onChange={(value) => updateField('role', value)} onBlur={() => markTouched('role')} placeholder="Select role" lockPlaceholder options={['Employee', 'Manager', 'HR Manager', 'Administrator']} optionValues={['employee', 'manager', 'hr', 'admin']} />
            </Field>
            <div className={`role-explainer${roleCopy.sensitive ? ' sensitive' : ''}`}>
              {roleCopy.sensitive ? <FiAlertTriangle /> : <FiInfo />}
              <div><strong>{roleCopy.title}</strong><p>{roleCopy.body}</p></div>
            </div>
          </div>

          <div className="temporary-password-panel">
            <Field label="Temporary password" required error={touched.password ? errors.password : ''} hint="The user must change this password after first sign in.">
              <div className="password-entry-row">
                <div className="password-control">
                  <input ref={(node) => { fieldRefs.current.password = node; }} type={showPassword ? 'text' : 'password'} value={form.password} onChange={(event) => updateField('password', event.target.value)} onBlur={() => markTouched('password')} autoComplete="new-password" />
                  <button type="button" onClick={() => setShowPassword((value) => !value)} aria-label={showPassword ? 'Hide password' : 'Show password'}>{showPassword ? <FiEyeOff /> : <FiEye />}</button>
                </div>
                <button type="button" className="password-tool-button" onClick={() => updateField('password', generatePassword())}><FiRefreshCw /> Generate</button>
                {form.password && <button type="button" className="password-tool-button icon-only" onClick={() => navigator.clipboard.writeText(form.password)} aria-label="Copy password"><FiCopy /></button>}
              </div>
            </Field>
            <ul className="password-requirements" aria-label="Password requirements">
              <li className={form.password.length >= 8 ? 'met' : ''}><FiCheck /> At least 8 characters</li>
              <li className={/[A-Z]/.test(form.password) ? 'met' : ''}><FiCheck /> One uppercase letter</li>
              <li className={/\d/.test(form.password) ? 'met' : ''}><FiCheck /> One number</li>
              <li className={/[^A-Za-z0-9]/.test(form.password) ? 'met' : ''}><FiCheck /> One special character</li>
            </ul>
          </div>
        </FormSection>

        <footer className="create-user-actions">
          <div className="draft-status">
            {draftSavedAt ? <><FiCheck /> Draft saved at {draftSavedAt}</> : 'Draft is saved only on this device'}
          </div>
          <div>
            <button type="button" className="cu-secondary-button" onClick={resetForm}>Cancel</button>
            <button type="button" className="cu-secondary-button" onClick={saveDraft}>Save draft</button>
            <button type="button" className="cu-primary-button" onClick={openReview} disabled={!isComplete || isSubmitting} title={!isComplete ? `Complete ${missingFields.length} required field${missingFields.length === 1 ? '' : 's'}` : undefined}>
              <FiUserPlus /> Create user
            </button>
          </div>
        </footer>
      </div>

      <aside className="create-user-aside" aria-label="User creation progress">
        <section className="user-preview-card">
          <div className="preview-card-heading"><h3>Preview</h3></div>
          <div className="preview-identity">
            <div className="preview-avatar">{photoPreview ? <img src={photoPreview} alt="" /> : initials}</div>
            <div><strong>{form.name || 'New user'}</strong><span>{form.email || 'name@example.com'}</span></div>
          </div>
          <div className="preview-tags"><span>{ROLE_COPY[form.role].title}</span>{form.department && <span>{form.department}</span>}</div>
          <dl>
            <div><dt>Employee ID</dt><dd>{form.employeeId || '—'}</dd></div>
            <div><dt>Department</dt><dd>{form.department || '—'}</dd></div>
            <div><dt>Designation</dt><dd>{form.designation || '—'}</dd></div>
            <div><dt>Reports to</dt><dd>{selectedManager?.name || '—'}</dd></div>
            <div><dt>Account status</dt><dd>Pending creation</dd></div>
          </dl>
        </section>

        <section className="progress-card">
          <div className="progress-heading"><div><h3>Form completion</h3><span>{completedRequired} of {REQUIRED_FIELDS.length} required fields complete</span></div><strong>{Math.round((completedRequired / REQUIRED_FIELDS.length) * 100)}%</strong></div>
          <div className="progress-track"><span style={{ width: `${(completedRequired / REQUIRED_FIELDS.length) * 100}%` }} /></div>
          {missingFields.length > 0 ? (
            <div className="missing-fields">
              <strong>Still needed</strong>
              {visibleMissingFields.map((field) => (
                <button key={field} type="button" onClick={() => scrollToField(field)}>
                  <span aria-hidden="true" /> {FIELD_LABELS[field] || field}
                </button>
              ))}
              {missingFields.length > 4 && (
                <button type="button" className="view-missing-toggle" onClick={() => setShowAllMissing((visible) => !visible)}>
                  {showAllMissing ? 'Show fewer fields' : `View all ${missingFields.length} missing fields`}
                </button>
              )}
            </div>
          ) : (
            <div className="form-ready"><FiCheck /> Ready to create</div>
          )}
        </section>
      </aside>

      {showReview && (
        <div className="review-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget && !isSubmitting) setShowReview(false); }}>
          <div className="review-dialog" role="dialog" aria-modal="true" aria-labelledby="review-title">
            <button type="button" className="review-close" onClick={() => setShowReview(false)} disabled={isSubmitting} aria-label="Close review"><FiX /></button>
            <div className="review-icon"><FiUserPlus /></div>
            <p>Final review</p>
            <h3 id="review-title">Create this user?</h3>
            <div className="review-person">
              <strong>{form.name}</strong>
              <span>{form.designation} · {form.department}</span>
              <span>Role: {ROLE_COPY[form.role].title}</span>
              <span>Temporary password will be required at sign in</span>
            </div>
            {uploadProgress > 0 && <div className="upload-progress"><span style={{ width: `${uploadProgress}%` }} /></div>}
            <div className="review-actions">
              <button type="button" className="cu-secondary-button" onClick={() => setShowReview(false)} disabled={isSubmitting}>Go back</button>
              <button type="button" className="cu-primary-button" onClick={createUser} disabled={isSubmitting}>
                {isSubmitting ? 'Creating user…' : 'Create user'}
              </button>
            </div>
          </div>
        </div>
      )}
    </section>
  );
};

const FormSection = ({
  number,
  title,
  subtitle,
  expanded,
  onToggle,
  children,
}: {
  number: string;
  title: string;
  subtitle: string;
  expanded: boolean;
  onToggle: () => void;
  children: React.ReactNode;
}) => (
  <section className={`cu-form-section${expanded ? ' expanded' : ' collapsed'}`}>
    <button type="button" className="cu-section-toggle" onClick={onToggle} aria-expanded={expanded}>
      <span>{number}</span>
      <div><h3>{title}</h3><p>{subtitle}</p></div>
      <FiChevronDown aria-hidden="true" />
    </button>
    {expanded && children}
  </section>
);

const Field = ({
  label,
  required,
  error,
  hint,
  children,
  className = '',
}: {
  label: string;
  required?: boolean;
  error?: string;
  hint?: string;
  children: React.ReactNode;
  className?: string;
  fieldRef?: (node: HTMLElement | null) => void;
}) => (
  <label className={`cu-field ${className}${error ? ' has-error' : ''}`}>
    <span className="cu-field-label">{label}{required && <><b aria-hidden="true">*</b><em className="visually-hidden"> required</em></>}</span>
    {children}
    {error ? <small className="cu-field-error" role="alert">{error}</small> : hint ? <small className="cu-field-hint">{hint}</small> : null}
  </label>
);

const SelectControl = ({
  inputRef,
  value,
  onChange,
  onBlur,
  placeholder,
  options,
  optionValues,
  disabled,
  lockPlaceholder,
}: {
  inputRef?: (node: HTMLSelectElement | null) => void;
  value: string;
  onChange: (value: string) => void;
  onBlur?: () => void;
  placeholder: string;
  options: string[];
  optionValues?: string[];
  disabled?: boolean;
  lockPlaceholder?: boolean;
}) => (
  <span className="cu-select-control">
    <select ref={inputRef} value={value} onChange={(event) => onChange(event.target.value)} onBlur={onBlur} disabled={disabled}>
      <option value="" disabled={lockPlaceholder}>{placeholder}</option>
      {options.map((option, index) => <option key={optionValues?.[index] || option} value={optionValues?.[index] || option}>{option}</option>)}
    </select>
    <FiChevronDown aria-hidden="true" />
  </span>
);

export default CreateUserPanel;
