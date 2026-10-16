import { useCallback, useEffect, useMemo, useState, type ChangeEvent, type FormEvent, type ReactNode } from 'react';
import { describeDay, hourlyRate, rateBasis } from '../../utils/workHours';
import AssetsHeldCard from '../../components/common/AssetsHeldCard';
import { sectionFor, formFieldFor, type MissingField } from '../../utils/profileCompletion';
import EmployeeCard from '../../components/common/EmployeeCard';
import {
  FiAlertCircle,
  FiBriefcase,
  FiCalendar,
  FiCamera,
  FiCheck,
  FiCheckCircle,
  FiChevronRight,
  FiCopy,
  FiCreditCard,
  FiEdit2,
  FiEye,
  FiEyeOff,
  FiFileText,
  FiHash,
  FiLock,
  FiMail,
  FiMapPin,
  FiPhone,
  FiShield,
  FiUser,
  FiUsers,
  FiX,
} from 'react-icons/fi';
import { useAuthStore } from '../../store/authStore';
import { authService } from '../../services/authService';
import { employeeService } from '../../services/employeeService';
import { apiClient, isForcedLogoutRedirect } from '../../services/api';
import { useDocumentImage } from '../../hooks/useDocumentImage';
import Card from '../../components/common/Card';
import Input from '../../components/common/Input';
import Select from '../../components/common/Select';
import Button from '../../components/common/Button';
import { type User } from '../../types';
import { OFFICE_LOCATION } from '../../config/constants';
import {
  formatPakPhone,
  formatCnic,
  sanitizeName,
  sanitizeOrgName,
  sanitizeAccountNumber,
  sanitizeBranchCode,
  validatePersonName,
  validateOrgName,
  validateEmail,
  validatePakPhone,
  validateCnic,
  validateDateOfBirth,
  validateAddress,
  validateAccountNumber,
  validateBranchCode,
} from '../../utils/pkValidation';
import './Profile.css';

type ProfileSection = 'personal' | 'employment' | 'banking' | 'documents' | 'security';
type EditSection = ProfileSection;
type Notice = { type: 'success' | 'error' | 'info'; text: string } | null;

const DOCUMENT_FIELDS = [
  { key: 'profilePictureUrl', label: 'Profile picture', description: 'JPG, PNG or WEBP, up to 5 MB' },
  { key: 'linkedinWelcomePictureUrl', label: 'Welcome photo', description: 'Image used for employee announcements' },
  { key: 'cnicFrontUrl', label: 'CNIC — front', description: 'Secure identity document' },
  { key: 'cnicBackUrl', label: 'CNIC — back', description: 'Secure identity document' },
] as const;

const TABS: { key: ProfileSection; label: string }[] = [
  { key: 'personal', label: 'Personal' },
  { key: 'employment', label: 'Employment' },
  { key: 'banking', label: 'Banking' },
  { key: 'documents', label: 'Documents' },
  { key: 'security', label: 'Security' },
];

const formatDate = (value?: string) => {
  if (!value) return 'Not provided';
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? value
    : date.toLocaleDateString('en-GB', { day: '2-digit', month: 'short', year: 'numeric' });
};

const formatDateForInput = (value?: string) => {
  if (!value) return '';
  // The backend sends a full timestamp ("1990-01-27T00:00:00"), which used to
  // fall through to `new Date(...).toISOString()` below — that reparses it as
  // LOCAL time then converts to UTC, shifting the date shown in the picker
  // back a day for any timezone ahead of UTC. A straight slice sidesteps
  // timezone conversion entirely for the common "already has a date prefix"
  // case.
  if (/^\d{4}-\d{2}-\d{2}/.test(value)) return value.slice(0, 10);
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? '' : date.toISOString().split('T')[0];
};

const maskIdentity = (value?: string) => {
  if (!value) return 'Not provided';
  const visible = value.slice(-4);
  return `•••••••••${visible}`;
};

const maskAccount = (value?: string | null) => {
  if (!value) return 'Not provided';
  return `•••• •••• ${value.slice(-4)}`;
};

const initials = (name?: string) =>
  (name || 'User')
    .split(/\s+/)
    .slice(0, 2)
    .map((part) => part[0])
    .join('')
    .toUpperCase();

type FieldErrors = Partial<Record<keyof User, string>>;

// Which editor tab each validated field lives in, so a save that fails
// on a field outside the current tab can jump the user to it.
const FIELD_SECTION: Partial<Record<keyof User, EditSection>> = {
  name: 'personal',
  fatherName: 'personal',
  dateOfBirth: 'personal',
  cnic: 'personal',
  personalEmail: 'personal',
  phone: 'personal',
  residentialAddress: 'personal',
  emergencyContactName: 'personal',
  emergencyContactPhone: 'personal',
  department: 'employment',
  designation: 'employment',
  managerId: 'employment',
  standardHours: 'employment',
  accountNumber: 'banking',
  bankName: 'banking',
  branchCode: 'banking',
  accountTitle: 'banking',
};

// Fields required for a "complete" profile — matches the set already tracked
// by the completion-ring/missingItems checklist below, plus the personal-
// identity fields shown alongside them on the same tab. Deliberately leaves
// department/designation optional: those are HR-assigned employment fields
// (not personal completion), and at least one real account on this system
// already has an empty department — making it required would lock that
// account out of saving ANY change until HR fills it in, which is a much
// bigger behavior change than "validate what the user is filling in now".
const REQUIRED_PROFILE_FIELDS = new Set<keyof User>([
  'name', 'fatherName', 'dateOfBirth', 'cnic', 'personalEmail', 'phone',
  'residentialAddress', 'emergencyContactName', 'emergencyContactPhone',
  'accountNumber', 'bankName', 'branchCode', 'accountTitle',
]);

const validateProfileField = (field: keyof User, value: unknown): string => {
  const str = typeof value === 'string' ? value : '';
  const required = REQUIRED_PROFILE_FIELDS.has(field);
  switch (field) {
    case 'name':
      return validatePersonName(str, { required: true });
    case 'fatherName':
    case 'accountTitle':
    case 'emergencyContactName':
      return validatePersonName(str, { required });
    case 'dateOfBirth':
      return validateDateOfBirth(str, { required });
    case 'cnic':
      return validateCnic(str, { required });
    case 'personalEmail':
      return validateEmail(str, { required });
    case 'phone':
    case 'emergencyContactPhone':
      return validatePakPhone(str, { required });
    case 'residentialAddress':
      return validateAddress(str, { required });
    case 'department':
    case 'designation':
      return validateOrgName(str);
    case 'managerId': {
      if (value === undefined || value === null || value === '' || Number(value) === 0) return '';
      const num = Number(value);
      if (!Number.isFinite(num) || num <= 0) return 'Enter a valid manager ID.';
      return '';
    }
    case 'standardHours': {
      if (value === undefined || value === null || value === '') return '';
      const num = Number(value);
      if (!Number.isFinite(num) || num < 1 || num > 12) return 'Enter hours per day, between 1 and 12.';
      return '';
    }
    case 'accountNumber':
      return validateAccountNumber(str, { required });
    case 'bankName':
      return validateOrgName(str, { required });
    case 'branchCode':
      return validateBranchCode(str, { required });
    default:
      return '';
  }
};

const Profile = () => {
  const { user, setUser } = useAuthStore();
  const [activeSection, setActiveSection] = useState<ProfileSection>('personal');
  const [profileData, setProfileData] = useState<User | null>(user);
  const [savedProfile, setSavedProfile] = useState<User | null>(user);
  const [isLoading, setIsLoading] = useState(!user);
  const [isSaving, setIsSaving] = useState(false);
  const [isEditing, setIsEditing] = useState(false);
  const [editSection, setEditSection] = useState<EditSection>('personal');
  const [showDiscardConfirm, setShowDiscardConfirm] = useState(false);
  const [dirtyFields, setDirtyFields] = useState<Set<string>>(new Set());
  const [fieldErrors, setFieldErrors] = useState<FieldErrors>({});
  const [touchedFields, setTouchedFields] = useState<Set<keyof User>>(new Set());
  const [uploadingDoc, setUploadingDoc] = useState<string | null>(null);
  const [notice, setNotice] = useState<Notice>(null);
  const [passwordForm, setPasswordForm] = useState({ currentPassword: '', newPassword: '', confirmPassword: '' });
  const [passwordVisibility, setPasswordVisibility] = useState({ current: false, next: false, confirm: false });
  const [isChangingPassword, setIsChangingPassword] = useState(false);
  const [passwordMessage, setPasswordMessage] = useState<{ type: 'success' | 'error'; text: string } | null>(null);
  const storedPhotoUrl = profileData?.profilePictureUrl || profileData?.avatar;
  const { imageUrl: photoImageUrl } = useDocumentImage(storedPhotoUrl);

  const loadProfileData = useCallback(async () => {
    try {
      setIsLoading(true);
      const data = await authService.getProfile();
      if (data) {
        setProfileData(data);
        setSavedProfile(data);
        setUser(data);
      }
    } catch (error) {
      console.error('Failed to load profile:', error);
      setNotice({ type: 'error', text: 'We could not refresh your profile. Showing the last available information.' });
    } finally {
      setIsLoading(false);
    }
  }, [setUser]);

  useEffect(() => {
    void loadProfileData();
  }, [loadProfileData]);

  useEffect(() => {
    const warnBeforeLeaving = (event: BeforeUnloadEvent) => {
      if (!isEditing || dirtyFields.size === 0 || isForcedLogoutRedirect) return;
      event.preventDefault();
      event.returnValue = '';
    };
    window.addEventListener('beforeunload', warnBeforeLeaving);
    return () => window.removeEventListener('beforeunload', warnBeforeLeaving);
  }, [dirtyFields, isEditing]);

  const handleInputChange = (field: keyof User, value: string | boolean | number) => {
    if (!profileData) return;
    let nextValue = value;
    if (typeof nextValue === 'string') {
      let formatted = nextValue;
      if (field === 'phone' || field === 'emergencyContactPhone') formatted = formatPakPhone(formatted);
      if (field === 'cnic') formatted = formatCnic(formatted);
      if (field === 'name' || field === 'fatherName' || field === 'emergencyContactName' || field === 'accountTitle') formatted = sanitizeName(formatted);
      if (field === 'department' || field === 'designation' || field === 'bankName') formatted = sanitizeOrgName(formatted);
      if (field === 'accountNumber') formatted = sanitizeAccountNumber(formatted);
      if (field === 'branchCode') formatted = sanitizeBranchCode(formatted);
      nextValue = formatted;
    }
    setProfileData({ ...profileData, [field]: nextValue });
    setDirtyFields((current) => new Set(current).add(field));
    setNotice(null);
    setFieldErrors((current) => ({ ...current, [field]: validateProfileField(field, nextValue) }));
  };

  const handleFieldBlur = (field: keyof User) => {
    setTouchedFields((current) => (current.has(field) ? current : new Set(current).add(field)));
    setFieldErrors((current) => ({ ...current, [field]: validateProfileField(field, profileData?.[field]) }));
  };

  const errorFor = (field: keyof User) => (touchedFields.has(field) ? fieldErrors[field] : undefined);

  // The whole record is checked on every save, not just the fields touched
  // this session — a save doesn't go through while any editable field is
  // invalid, even pre-existing bad data left over from before these rules
  // existed.
  const validateEditableFields = (): boolean => {
    if (!profileData) return true;
    const fields = (Object.keys(FIELD_SECTION) as (keyof User)[]).filter((field) => {
      const section = FIELD_SECTION[field];
      if (section === 'employment' && !canEditEmployment) return false;
      return true;
    });
    let firstInvalidField: keyof User | undefined;
    const updates: FieldErrors = {};
    fields.forEach((field) => {
      const message = validateProfileField(field, profileData[field]);
      updates[field] = message;
      if (message && !firstInvalidField) firstInvalidField = field;
    });
    setFieldErrors((current) => ({ ...current, ...updates }));
    setTouchedFields((current) => {
      const next = new Set(current);
      fields.forEach((field) => next.add(field));
      return next;
    });
    if (firstInvalidField) {
      const section = FIELD_SECTION[firstInvalidField];
      if (section) setEditSection(section);
    }
    return !firstInvalidField;
  };

  const cancelEditing = () => {
    setProfileData(savedProfile);
    setDirtyFields(new Set());
    setFieldErrors({});
    setTouchedFields(new Set());
    setIsEditing(false);
    setShowDiscardConfirm(false);
    if (savedProfile) setUser(savedProfile);
    setNotice({ type: 'info', text: 'Your unsaved changes were discarded.' });
  };

  const handleSave = async () => {
    if (!profileData) return;
    if (!validateEditableFields()) {
      setNotice({ type: 'error', text: 'Fix the highlighted fields before saving.' });
      return;
    }
    try {
      setIsSaving(true);
      setNotice(null);
      const updatedUser = await authService.updateProfile(profileData, { allowEmployment: canEditEmployment });
      if (updatedUser) {
        setUser(updatedUser);
        setProfileData(updatedUser);
        setSavedProfile(updatedUser);
        setDirtyFields(new Set());
        setFieldErrors({});
        setTouchedFields(new Set());
        setIsEditing(false);
        setShowDiscardConfirm(false);
        setNotice({ type: 'success', text: 'Profile changes saved.' });
      }
    } catch (error: unknown) {
      const apiError = error as {
        response?: { data?: { detail?: string; message?: string } };
        message?: string;
      };
      setNotice({
        type: 'error',
        text:
          apiError.response?.data?.detail ||
          apiError.response?.data?.message ||
          apiError.message ||
          'We could not save your profile. Please try again.',
      });
    } finally {
      setIsSaving(false);
    }
  };

  const uploadFile = async (field: string, file: File) => {
    const isPhoto = field === 'profilePictureUrl';
    const allowed = isPhoto
      ? ['image/jpeg', 'image/png', 'image/webp']
      : ['image/jpeg', 'image/png', 'image/gif', 'image/webp', 'application/pdf'];

    if (!allowed.includes(file.type)) {
      setNotice({ type: 'error', text: isPhoto ? 'Choose a JPG, PNG or WEBP image.' : 'Choose an image or PDF file.' });
      return;
    }
    if (file.size > 5 * 1024 * 1024) {
      setNotice({ type: 'error', text: 'The selected file is larger than 5 MB.' });
      return;
    }

    try {
      setUploadingDoc(field);
      setNotice(null);
      const formData = new FormData();
      formData.append('file', file);
      // A profile picture is drawn at about forty pixels and is fetched on
      // every page load, so the server cuts it right down and converts it to
      // WebP. A CNIC scan is only capped — its small print has to stay
      // readable.
      const response = await apiClient.post<{ url: string }>('/upload', formData, {
        headers: { 'Content-Type': 'multipart/form-data' },
        params: isPhoto ? { purpose: 'avatar' } : undefined,
      });
      if (response.data?.url && profileData) {
        const nextProfile = {
          ...profileData,
          [field]: response.data.url,
          ...(isPhoto ? { avatar: response.data.url } : {}),
        };
        setProfileData(nextProfile);
        if (isPhoto) setUser(nextProfile);
        setDirtyFields((current) => new Set(current).add(field));
        setIsEditing(true);
        setEditSection(field === 'profilePictureUrl' ? 'personal' : 'documents');
        setNotice({ type: 'success', text: `${file.name} uploaded. Save your profile to keep this change.` });
      }
    } catch (error: unknown) {
      const uploadError = error as { message?: string };
      setNotice({ type: 'error', text: uploadError.message || 'Upload failed. Please try again.' });
    } finally {
      setUploadingDoc(null);
    }
  };

  const handleDocumentPick = (field: string, event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    event.target.value = '';
    if (file) void uploadFile(field, file);
  };

  // The profile payload carries manager_id but not the manager's name, so
  // resolve it separately rather than showing a raw "Employee #3".
  const [managerName, setManagerName] = useState('');
  useEffect(() => {
    const managerId = profileData?.managerId;
    if (!managerId) {
      setManagerName('');
      return;
    }
    let cancelled = false;
    void employeeService
      .getEmployeeById(String(managerId))
      .then((record) => {
        if (!cancelled && record?.name) setManagerName(record.name);
      })
      .catch(() => {
        // Fall back to the id-based label below; a missing name is not worth
        // an error banner on a screen the user came to for something else.
      });
    return () => {
      cancelled = true;
    };
  }, [profileData?.managerId]);

  // The server decides what a complete profile is and ships the answer on
  // every response. Computing it again here produced a ring that read 100%
  // while HR's own report was chasing the same person for a CNIC.
  const missingItems = useMemo<MissingField[]>(
    () => profileData?.profileCompletion?.missing_required ?? [],
    [profileData],
  );

  const completion = profileData?.profileCompletion?.percent ?? 0;

  const normalizedDepartment = (profileData?.department || '').trim().toLowerCase();
  const isHrManager = profileData?.role === 'manager' && [
    'hr',
    'human resources',
    'people',
    'people operations',
    'people & culture',
    'people and culture',
  ].includes(normalizedDepartment);
  const canEditEmployment = profileData?.role === 'admin' || isHrManager;

  // Pick a reporting manager by name rather than typing a database id. The
  // endpoint is admin/HR-only, and so is this editor, so the two line up.
  const [managerOptions, setManagerOptions] = useState<Array<{ value: string; label: string }>>([]);
  useEffect(() => {
    if (!canEditEmployment) return;
    let cancelled = false;
    void employeeService
      .getManagers()
      .then((list) => {
        if (cancelled) return;
        setManagerOptions(
          list
            // Nobody reports to themselves.
            .filter((candidate) => String(candidate.id) !== String(profileData?.id))
            .map((candidate) => ({
              value: String(candidate.id),
              label: candidate.designation ? `${candidate.name} · ${candidate.designation}` : candidate.name,
            })),
        );
      })
      .catch(() => {
        // Leave the list empty; the field still shows the current manager.
      });
    return () => {
      cancelled = true;
    };
  }, [canEditEmployment, profileData?.id]);
  const openEditor = (section: ProfileSection = activeSection) => {
    setEditSection(section);
    setShowDiscardConfirm(false);
    setNotice(null);
    setIsEditing(true);
  };

  const requestCloseEditor = useCallback(() => {
    if (dirtyFields.size > 0) {
      setShowDiscardConfirm(true);
      return;
    }
    setIsEditing(false);
  }, [dirtyFields.size]);

  useEffect(() => {
    if (!isEditing) return;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') requestCloseEditor();
    };
    window.addEventListener('keydown', closeOnEscape);
    return () => {
      document.body.style.overflow = previousOverflow;
      window.removeEventListener('keydown', closeOnEscape);
    };
  }, [isEditing, requestCloseEditor]);

  const goToMissingField = (section: ProfileSection, field: string) => {
    setEditSection(section);
    setShowDiscardConfirm(false);
    setIsEditing(true);
    window.setTimeout(() => {
      const target = document.querySelector<HTMLElement>(`[id*="drawer-${field}"] input, [id*="drawer-${field}"] select`);
      target?.focus();
    }, 180);
  };

  const passwordChecks = {
    length: passwordForm.newPassword.length >= 8,
    uppercase: /[A-Z]/.test(passwordForm.newPassword),
    number: /\d/.test(passwordForm.newPassword),
    special: /[^A-Za-z0-9]/.test(passwordForm.newPassword),
    matches: Boolean(passwordForm.confirmPassword) && passwordForm.newPassword === passwordForm.confirmPassword,
  };
  const canChangePassword =
    Boolean(passwordForm.currentPassword) && Object.values(passwordChecks).every(Boolean) && !isChangingPassword;

  const handleChangePassword = async (event: FormEvent) => {
    event.preventDefault();
    if (!canChangePassword) return;
    setPasswordMessage(null);
    setIsChangingPassword(true);
    try {
      await authService.changePassword(passwordForm);
      setPasswordForm({ currentPassword: '', newPassword: '', confirmPassword: '' });
      setPasswordMessage({ type: 'success', text: 'Your password has been changed.' });
    } catch (error: unknown) {
      const passwordError = error as { response?: { data?: { detail?: string } }; message?: string };
      setPasswordMessage({
        type: 'error',
        text: passwordError.response?.data?.detail || passwordError.message || 'We could not change your password. Please try again.',
      });
    } finally {
      setIsChangingPassword(false);
    }
  };

  if (isLoading) {
    return (
      <div className="profile-page profile-loading" aria-label="Loading profile">
        <div className="profile-skeleton skeleton-hero" />
        <div className="profile-skeleton skeleton-tabs" />
        <div className="skeleton-grid">
          <div className="profile-skeleton skeleton-content" />
          <div className="profile-skeleton skeleton-aside" />
        </div>
      </div>
    );
  }

  const employeeId = profileData?.employmentDetails?.employeeId || profileData?.id || 'Not assigned';
  const joiningDate = profileData?.employmentDetails?.joinDate;
  const manager = profileData?.employmentDetails?.reportingManager || managerName || (profileData?.managerId ? `Employee #${profileData.managerId}` : 'Not assigned');

  return (
    <div className="profile-page">
      <header className="profile-page-heading">
        <div>
          <span className="profile-eyebrow">Employee profile</span>
          <h1>Profile</h1>
          <p>Manage your personal information and account preferences.</p>
        </div>
        {isEditing && <span className="editing-indicator"><span /> Editing profile</span>}
      </header>

      {/* While the editor drawer is open, this notice is covered by the
          drawer/backdrop (fixed, full-viewport, higher stacking context) so
          it renders again inside the drawer itself below instead. */}
      {notice && !isEditing && (
        <div className={`profile-notice ${notice.type}`} role="status">
          {notice.type === 'success' ? <FiCheckCircle /> : notice.type === 'error' ? <FiAlertCircle /> : <FiShield />}
          <span>{notice.text}</span>
          <button type="button" onClick={() => setNotice(null)} aria-label="Dismiss message"><FiX /></button>
        </div>
      )}

      <Card className="profile-identity-card">
        <div className="profile-identity">
          <div className="avatar-wrap">
            <div className="profile-avatar">
              {photoImageUrl ? (
                <img src={photoImageUrl} alt={`${profileData?.name || 'Employee'} profile`} />
              ) : (
                <span aria-label={`${profileData?.name || 'Employee'} initials`}>{initials(profileData?.name)}</span>
              )}
            </div>
            <input
              id="profile-photo-upload"
              className="visually-hidden"
              type="file"
              accept=".jpg,.jpeg,.png,.webp"
              onChange={(event) => handleDocumentPick('profilePictureUrl', event)}
            />
            <label className="avatar-camera" htmlFor="profile-photo-upload" aria-label="Change profile photo">
              {uploadingDoc === 'profilePictureUrl' ? <span className="button-spinner" /> : <FiCamera />}
            </label>
          </div>

          <div className="identity-copy">
            <div className="identity-title-row">
              <div>
                <h2>{profileData?.name || 'Employee'}</h2>
                <div className="identity-role">
                  <span>{profileData?.designation || profileData?.role || 'Employee'}</span>
                  <span className={`status-badge ${profileData?.isActive === false ? 'inactive' : ''}`}>
                    {profileData?.isActive === false ? 'Inactive' : 'Active'}
                  </span>
                </div>
              </div>
            </div>

            <div className="identity-metadata">
              <MetaItem icon={<FiHash />} label="Employee ID" value={employeeId} />
              <MetaItem icon={<FiUsers />} label="Department" value={profileData?.department || 'Not assigned'} />
              <MetaItem icon={<FiMail />} label="Work email" value={profileData?.email || 'Not provided'} />
              <MetaItem icon={<FiPhone />} label="Phone" value={profileData?.phone || 'Not provided'} />
              <MetaItem icon={<FiUser />} label="Reporting manager" value={manager} />
            </div>
          </div>

          <div className="identity-actions">
            <Button variant="primary" onClick={() => openEditor()}>
              <FiEdit2 /> Edit profile
            </Button>
            <label className="secondary-action" htmlFor="profile-photo-upload"><FiCamera /> Change photo</label>
          </div>
        </div>
      </Card>

      <nav className="profile-tabs" aria-label="Profile sections">
        {TABS.map((tab) => (
          <button
            key={tab.key}
            type="button"
            className={activeSection === tab.key ? 'active' : ''}
            onClick={() => setActiveSection(tab.key)}
            aria-current={activeSection === tab.key ? 'page' : undefined}
          >
            {tab.label}
          </button>
        ))}
      </nav>

      <div className="profile-layout">
        <main className="profile-main" id="profile-main">
          {activeSection === 'personal' && (
            <>
              <SectionCard icon={<FiUser />} title="Personal information" description="Your contact and identity details. Sensitive identity numbers stay masked in view mode.">
                <div className="profile-fields-grid">
                  <EditableField id="name" label="Full name" value={profileData?.name} editing={false} dirty={false}>
                    <Input label="Full name" value={profileData?.name || ''} onChange={(event) => handleInputChange('name', event.target.value)} />
                  </EditableField>
                  <EditableField id="gender" label="Gender" value={profileData?.gender ? profileData.gender[0].toUpperCase() + profileData.gender.slice(1) : undefined} editing={false} dirty={false}>
                    <Select label="Gender" value={profileData?.gender || ''} onChange={(event) => handleInputChange('gender', event.target.value)} options={[{ value: 'male', label: 'Male' }, { value: 'female', label: 'Female' }]} />
                  </EditableField>
                  <EditableField id="dateOfBirth" label="Date of birth" value={formatDate(profileData?.dateOfBirth)} editing={false} dirty={false}>
                    <Input label="Date of birth" type="date" value={formatDateForInput(profileData?.dateOfBirth)} onChange={(event) => handleInputChange('dateOfBirth', event.target.value)} />
                  </EditableField>
                  <EditableField id="fatherName" label="Father's name" value={profileData?.fatherName} editing={false} dirty={false}>
                    <Input label="Father's name" value={profileData?.fatherName || ''} onChange={(event) => handleInputChange('fatherName', event.target.value)} />
                  </EditableField>
                  <EditableField id="cnic" label="CNIC number" value={maskIdentity(profileData?.cnic)} editing={false} dirty={false}>
                    <Input label="CNIC number" value={profileData?.cnic || ''} onChange={(event) => handleInputChange('cnic', event.target.value)} />
                  </EditableField>
                  <EditableField id="personalEmail" label="Personal email" value={profileData?.personalEmail} editing={false} dirty={false}>
                    <Input label="Personal email" type="email" value={profileData?.personalEmail || ''} onChange={(event) => handleInputChange('personalEmail', event.target.value)} />
                  </EditableField>
                  <EditableField id="phone" label="Phone number" value={profileData?.phone} editing={false} dirty={false}>
                    <Input label="Phone number" type="tel" value={profileData?.phone || ''} onChange={(event) => handleInputChange('phone', event.target.value)} />
                  </EditableField>
                  <EditableField id="residentialAddress" label="Residential address" value={profileData?.residentialAddress} editing={false} dirty={false} wide>
                    <Input label="Residential address" value={profileData?.residentialAddress || ''} onChange={(event) => handleInputChange('residentialAddress', event.target.value)} />
                  </EditableField>
                </div>
              </SectionCard>

              <SectionCard icon={<FiPhone />} title="Emergency contact" description="The person we should contact in a workplace emergency.">
                {!profileData?.emergencyContactName && !profileData?.emergencyContactPhone ? (
                  <div className="profile-empty-state">
                    <span><FiPhone /></span>
                    <div><h3>No emergency contact added</h3><p>Add a contact for workplace emergencies.</p></div>
                    <button type="button" onClick={() => openEditor('personal')}>Add contact</button>
                  </div>
                ) : (
                  <div className="profile-fields-grid">
                    <EditableField id="emergencyContactName" label="Contact name" value={profileData?.emergencyContactName} editing={false} dirty={false}>
                      <Input label="Contact name" value={profileData?.emergencyContactName || ''} onChange={(event) => handleInputChange('emergencyContactName', event.target.value)} />
                    </EditableField>
                    <EditableField id="emergencyContactPhone" label="Phone number" value={profileData?.emergencyContactPhone} editing={false} dirty={false}>
                      <Input label="Phone number" type="tel" value={profileData?.emergencyContactPhone || ''} onChange={(event) => handleInputChange('emergencyContactPhone', event.target.value)} />
                    </EditableField>
                  </div>
                )}
              </SectionCard>
            </>
          )}

          {activeSection === 'employment' && (
            <>
            <SectionCard icon={<FiCreditCard />} title="Your employee card" description="The portal's own identity card. The QR on the back resolves against the public verification page, which is the only thing on it that proves anything.">
              <EmployeeCard
                name={profileData?.name}
                designation={profileData?.designation}
                department={profileData?.department}
                employeeId={employeeId}
                photoUrl={photoImageUrl}
                joiningDate={joiningDate}
                email={profileData?.email}
                phone={profileData?.phone}
                verificationCode={profileData?.employmentVerificationCode}
              />
            </SectionCard>

            <SectionCard icon={<FiBriefcase />} title="Employment information" description="Employment details are managed by HR and cannot be changed from your profile.">
              <div className="profile-fields-grid">
                <ReadField label="Employee ID" value={employeeId} locked copyable />
                <ReadField label="Department" value={profileData?.department} locked />
                <ReadField label="Designation" value={profileData?.designation} locked />
                <ReadField label="Reporting manager" value={manager} locked />
                <ReadField label="Joining date" value={formatDate(joiningDate)} locked />
                <ReadField label="Employment type" value={profileData?.employmentDetails?.employmentType} locked />
                <ReadField label="Standard hours" value={profileData?.standardHours ? describeDay(profileData.standardHours) : undefined} locked />
                <ReadField label="Overtime eligibility" value={profileData?.overtimeEligible ? 'Eligible' : 'Not eligible'} locked />
              </div>
              <AssetsHeldCard />
              <div className="permission-note"><FiLock /> Only authorized HR or Admin users can change these fields.</div>
            </SectionCard>
            </>
          )}

          {activeSection === 'banking' && (
            <SectionCard icon={<FiCreditCard />} title="Banking information" description="Bank details are masked in view mode. Confirm changes carefully before saving.">
              <div className="profile-fields-grid">
                <EditableField id="accountNumber" label="Account number" value={maskAccount(profileData?.accountNumber)} editing={false} dirty={false}>
                  <Input label="Account number" value={profileData?.accountNumber || ''} onChange={(event) => handleInputChange('accountNumber', event.target.value)} />
                </EditableField>
                <EditableField id="bankName" label="Bank name" value={profileData?.bankName || undefined} editing={false} dirty={false}>
                  <Input label="Bank name" value={profileData?.bankName || ''} onChange={(event) => handleInputChange('bankName', event.target.value)} />
                </EditableField>
                <EditableField id="branchCode" label="Branch code" value={profileData?.branchCode || undefined} editing={false} dirty={false}>
                  <Input label="Branch code" value={profileData?.branchCode || ''} onChange={(event) => handleInputChange('branchCode', event.target.value)} />
                </EditableField>
                <EditableField id="accountTitle" label="Account title" value={profileData?.accountTitle || undefined} editing={false} dirty={false}>
                  <Input label="Account title" value={profileData?.accountTitle || ''} onChange={(event) => handleInputChange('accountTitle', event.target.value)} />
                </EditableField>
              </div>
              <div className="permission-note banking"><FiShield /> Banking changes are sent over a secure connection and may require HR verification.</div>
            </SectionCard>
          )}

          {activeSection === 'documents' && (
            <SectionCard icon={<FiFileText />} title="Documents" description="Secure profile files. Supported formats: JPG, PNG, WEBP or PDF, up to 5 MB.">
              <div className="documents-list">
                {DOCUMENT_FIELDS.map((document) => {
                  const url = (profileData?.[document.key as keyof User] as string) || '';
                  const isUploading = uploadingDoc === document.key;
                  return (
                    <div className="document-row" key={document.key}>
                      <span className="document-icon"><FiFileText /></span>
                      <div className="document-copy">
                        <strong>{document.label}</strong>
                        <span>{document.description}</span>
                      </div>
                      <span className={`document-status ${url ? 'complete' : 'pending'}`}>{url ? 'Uploaded' : 'Missing'}</span>
                      <div className="document-actions">
                        {url && <a href={url} target="_blank" rel="noreferrer">View</a>}
                        {isEditing && (
                          <>
                            <input id={`doc-${document.key}`} className="visually-hidden" type="file" accept=".jpg,.jpeg,.png,.gif,.webp,.pdf" onChange={(event) => handleDocumentPick(document.key, event)} disabled={isUploading} />
                            <label htmlFor={`doc-${document.key}`}>{isUploading ? 'Uploading…' : url ? 'Replace' : 'Upload'}</label>
                          </>
                        )}
                      </div>
                    </div>
                  );
                })}
              </div>
            </SectionCard>
          )}

          {activeSection === 'security' && (
            <>
              <SectionCard icon={<FiShield />} title="Account security" description="Review the security controls connected to your account.">
                <div className="security-overview">
                  <ReadField label="Password" value="Protected" />
                  <ReadField label="Two-factor authentication" value="Not configured" />
                  <ReadField label="Active session" value="This device" />
                  <ReadField label="Account status" value={profileData?.isActive === false ? 'Inactive' : 'Active'} />
                </div>
              </SectionCard>
              <SectionCard icon={<FiLock />} title="Change password" description="Use at least eight characters and a mix of letters, numbers and symbols.">
                <form className="password-change-form" onSubmit={handleChangePassword}>
                  <PasswordField id="current-password" label="Current password" value={passwordForm.currentPassword} visible={passwordVisibility.current} onChange={(value) => setPasswordForm({ ...passwordForm, currentPassword: value })} onToggle={() => setPasswordVisibility({ ...passwordVisibility, current: !passwordVisibility.current })} autoComplete="current-password" />
                  <PasswordField id="new-password" label="New password" value={passwordForm.newPassword} visible={passwordVisibility.next} onChange={(value) => setPasswordForm({ ...passwordForm, newPassword: value })} onToggle={() => setPasswordVisibility({ ...passwordVisibility, next: !passwordVisibility.next })} autoComplete="new-password" />
                  <PasswordField id="confirm-password" label="Confirm new password" value={passwordForm.confirmPassword} visible={passwordVisibility.confirm} onChange={(value) => setPasswordForm({ ...passwordForm, confirmPassword: value })} onToggle={() => setPasswordVisibility({ ...passwordVisibility, confirm: !passwordVisibility.confirm })} autoComplete="new-password" />
                  <ul className="profile-password-requirements" aria-label="Password requirements">
                    <Requirement met={passwordChecks.length}>At least 8 characters</Requirement>
                    <Requirement met={passwordChecks.uppercase}>One uppercase letter</Requirement>
                    <Requirement met={passwordChecks.number}>One number</Requirement>
                    <Requirement met={passwordChecks.special}>One special character</Requirement>
                    <Requirement met={passwordChecks.matches}>Passwords match</Requirement>
                  </ul>
                  {passwordMessage && <div className={`password-message ${passwordMessage.type}`} role="status">{passwordMessage.type === 'success' && <FiCheck />}{passwordMessage.text}</div>}
                  <div className="password-form-actions">
                    <Button variant="primary" type="submit" disabled={!canChangePassword} isLoading={isChangingPassword}>Change password</Button>
                  </div>
                </form>
              </SectionCard>
            </>
          )}
        </main>

        <aside className="profile-aside">
          <Card className="summary-card completion-card">
            <div className="summary-heading"><FiCheckCircle /><h2>Profile completion</h2></div>
            <div className="completion-body">
              <div className="completion-ring" style={{ '--completion': `${completion * 3.6}deg` } as React.CSSProperties}>
                <div><strong>{completion}%</strong><span>complete</span></div>
              </div>
              <div><strong>{missingItems.length ? `${missingItems.length} items missing` : 'Profile complete'}</strong><p>{missingItems.length ? 'Add the details below to finish your profile.' : 'Your essential profile details are up to date.'}</p></div>
            </div>
            {missingItems.length > 0 && (
              <div className="missing-list">
                {missingItems.slice(0, 3).map((item) => (
                  <button key={item.field} type="button" onClick={() => goToMissingField(sectionFor(item.field), formFieldFor(item.field))}>
                    <span>{item.label}</span><FiChevronRight />
                  </button>
                ))}
              </div>
            )}
          </Card>

          <Card className="summary-card">
            <div className="summary-heading"><FiLock /><h2>Account details</h2></div>
            <SummaryRow label="Username" value={profileData?.email?.split('@')[0] || 'Not available'} copyValue={profileData?.email?.split('@')[0]} />
            <SummaryRow label="User role" value={profileData?.role || 'Employee'} />
            <SummaryRow label="Account status" value={profileData?.isActive === false ? 'Inactive' : 'Active'} status={profileData?.isActive === false ? 'inactive' : 'active'} />
            <SummaryRow label="Two-factor auth" value="Not configured" />
          </Card>

          <Card className="summary-card">
            <div className="summary-heading"><FiBriefcase /><h2>Quick information</h2></div>
            <QuickRow icon={<FiCalendar />} label="Joining date" value={formatDate(joiningDate)} />
            <QuickRow icon={<FiUser />} label="Reporting manager" value={manager} />
            <QuickRow icon={<FiMapPin />} label="Office location" value={OFFICE_LOCATION.NAME || 'Not provided'} />
            <QuickRow icon={<FiBriefcase />} label="Employment type" value={profileData?.employmentDetails?.employmentType || 'Not provided'} />
            <QuickRow icon={<FiUsers />} label="Department" value={profileData?.department || 'Not assigned'} />
          </Card>
        </aside>
      </div>

      {isEditing && (
        <div
          className="profile-editor-backdrop"
          onMouseDown={(event) => {
            if (event.target === event.currentTarget) requestCloseEditor();
          }}
        >
          <aside className="profile-editor" role="dialog" aria-modal="true" aria-labelledby="profile-editor-title">
            <header className="profile-editor-header">
              <div>
                <span className="profile-eyebrow">Profile settings</span>
                <h2 id="profile-editor-title">Edit profile</h2>
                <p>Update the fields you have permission to manage.</p>
              </div>
              <button type="button" onClick={requestCloseEditor} aria-label="Close profile editor"><FiX /></button>
            </header>

            <nav className="profile-editor-tabs" aria-label="Editable profile sections">
              {([
                { key: 'personal', label: 'Personal' },
                ...(canEditEmployment ? [{ key: 'employment', label: 'Employment' }] : []),
                { key: 'banking', label: 'Banking' },
                { key: 'documents', label: 'Documents' },
                { key: 'security', label: 'Security' },
              ] as { key: EditSection; label: string }[]).map((tab) => (
                <button
                  key={tab.key}
                  type="button"
                  className={editSection === tab.key ? 'active' : ''}
                  onClick={() => setEditSection(tab.key)}
                >
                  {tab.label}
                </button>
              ))}
            </nav>

            {notice && (
              <div className={`profile-notice profile-editor-notice ${notice.type}`} role="alert">
                {notice.type === 'success' ? <FiCheckCircle /> : notice.type === 'error' ? <FiAlertCircle /> : <FiShield />}
                <span>{notice.text}</span>
                <button type="button" onClick={() => setNotice(null)} aria-label="Dismiss message"><FiX /></button>
              </div>
            )}

            <div className="profile-editor-body">
              {editSection === 'personal' && (
                <section className="editor-section">
                  <div className="editor-section-heading">
                    <span><FiUser /></span>
                    <div><h3>Personal information</h3><p>Changes to contact details may require confirmation.</p></div>
                  </div>
                  <div className="drawer-form-grid">
                    <EditableField id="drawer-name" label="Full name" value={profileData?.name} editing dirty={dirtyFields.has('name')}>
                      <Input label="Full name" value={profileData?.name || ''} onChange={(event) => handleInputChange('name', event.target.value)} onBlur={() => handleFieldBlur('name')} error={errorFor('name')} />
                    </EditableField>
                    <EditableField id="drawer-gender" label="Gender" value={profileData?.gender} editing dirty={dirtyFields.has('gender')}>
                      <Select label="Gender" value={profileData?.gender || ''} onChange={(event) => handleInputChange('gender', event.target.value)} options={[{ value: 'male', label: 'Male' }, { value: 'female', label: 'Female' }]} />
                    </EditableField>
                    <EditableField id="drawer-dateOfBirth" label="Date of birth" value={profileData?.dateOfBirth} editing dirty={dirtyFields.has('dateOfBirth')}>
                      <Input label="Date of birth" type="date" value={formatDateForInput(profileData?.dateOfBirth)} onChange={(event) => handleInputChange('dateOfBirth', event.target.value)} onBlur={() => handleFieldBlur('dateOfBirth')} error={errorFor('dateOfBirth')} />
                    </EditableField>
                    <EditableField id="drawer-fatherName" label="Father's name" value={profileData?.fatherName} editing dirty={dirtyFields.has('fatherName')}>
                      <Input label="Father's name" value={profileData?.fatherName || ''} onChange={(event) => handleInputChange('fatherName', event.target.value)} onBlur={() => handleFieldBlur('fatherName')} error={errorFor('fatherName')} />
                    </EditableField>
                    <EditableField id="drawer-cnic" label="CNIC number" value={profileData?.cnic} editing dirty={dirtyFields.has('cnic')}>
                      <Input
                        label="CNIC number"
                        value={profileData?.cnic || ''}
                        onChange={(event) => handleInputChange('cnic', event.target.value)}
                        onBlur={() => handleFieldBlur('cnic')}
                        error={errorFor('cnic')}
                      />
                    </EditableField>
                    <EditableField id="drawer-personalEmail" label="Personal email" value={profileData?.personalEmail} editing dirty={dirtyFields.has('personalEmail')}>
                      <Input label="Personal email" type="email" value={profileData?.personalEmail || ''} onChange={(event) => handleInputChange('personalEmail', event.target.value)} onBlur={() => handleFieldBlur('personalEmail')} error={errorFor('personalEmail')} />
                    </EditableField>
                    <EditableField id="drawer-phone" label="Phone number" value={profileData?.phone} editing dirty={dirtyFields.has('phone')}>
                      <Input label="Phone number" type="tel" value={profileData?.phone || ''} onChange={(event) => handleInputChange('phone', event.target.value)} onBlur={() => handleFieldBlur('phone')} error={errorFor('phone')} />
                    </EditableField>
                    <EditableField id="drawer-address" label="Residential address" value={profileData?.residentialAddress} editing dirty={dirtyFields.has('residentialAddress')} wide>
                      <Input label="Residential address" value={profileData?.residentialAddress || ''} onChange={(event) => handleInputChange('residentialAddress', event.target.value)} onBlur={() => handleFieldBlur('residentialAddress')} error={errorFor('residentialAddress')} />
                    </EditableField>
                  </div>

                  <div className="editor-subsection-title"><FiPhone /><span>Emergency contact</span></div>
                  <div className="drawer-form-grid">
                    <EditableField id="drawer-emergencyName" label="Contact name" value={profileData?.emergencyContactName} editing dirty={dirtyFields.has('emergencyContactName')}>
                      <Input label="Contact name" value={profileData?.emergencyContactName || ''} onChange={(event) => handleInputChange('emergencyContactName', event.target.value)} onBlur={() => handleFieldBlur('emergencyContactName')} error={errorFor('emergencyContactName')} />
                    </EditableField>
                    <EditableField id="drawer-emergencyPhone" label="Phone number" value={profileData?.emergencyContactPhone} editing dirty={dirtyFields.has('emergencyContactPhone')}>
                      <Input label="Phone number" type="tel" value={profileData?.emergencyContactPhone || ''} onChange={(event) => handleInputChange('emergencyContactPhone', event.target.value)} onBlur={() => handleFieldBlur('emergencyContactPhone')} error={errorFor('emergencyContactPhone')} />
                    </EditableField>
                  </div>
                </section>
              )}

              {editSection === 'employment' && canEditEmployment && (
                <section className="editor-section">
                  <div className="editor-section-heading">
                    <span><FiBriefcase /></span>
                    <div>
                      <h3>Employment information</h3>
                      <p>{profileData?.role === 'admin' ? 'Admin access' : 'HR manager access'} · Changes are permission controlled.</p>
                    </div>
                  </div>
                  <div className="employment-access-note"><FiShield /> You can edit operational employment fields. Role, activation status, and salary remain protected.</div>
                  <div className="drawer-form-grid">
                    <EditableField id="drawer-department" label="Department" value={profileData?.department} editing dirty={dirtyFields.has('department')}>
                      <Input label="Department" value={profileData?.department || ''} onChange={(event) => handleInputChange('department', event.target.value)} onBlur={() => handleFieldBlur('department')} error={errorFor('department')} />
                    </EditableField>
                    <EditableField id="drawer-designation" label="Designation" value={profileData?.designation} editing dirty={dirtyFields.has('designation')}>
                      <Input label="Designation" value={profileData?.designation || ''} onChange={(event) => handleInputChange('designation', event.target.value)} onBlur={() => handleFieldBlur('designation')} error={errorFor('designation')} />
                    </EditableField>
                    <EditableField id="drawer-manager" label="Reporting manager" value={managerName || (profileData?.managerId ? `Employee #${profileData.managerId}` : '')} editing dirty={dirtyFields.has('managerId')}>
                      <Select label="Reporting manager" value={profileData?.managerId?.toString() || ''} onChange={(event) => handleInputChange('managerId', Number(event.target.value) || 0)} onBlur={() => handleFieldBlur('managerId')} error={errorFor('managerId')} options={[{ value: '0', label: 'No manager assigned' }, ...managerOptions]} />
                    </EditableField>
                    <EditableField id="drawer-standardHours" label="Standard hours" value={profileData?.standardHours?.toString()} editing dirty={dirtyFields.has('standardHours')}>
                      <Input label="Standard hours per day" type="number" min="1" max="12" step="0.5" value={profileData?.standardHours?.toString() || ''} onChange={(event) => handleInputChange('standardHours', Number(event.target.value))} onBlur={() => handleFieldBlur('standardHours')} error={errorFor('standardHours')} />
                    </EditableField>
                    {profileData?.role === 'admin' && (
                      /* Derived from the basic salary over this employee's own month, so it is
                         displayed, not edited. Part time divides by fewer hours. */
                      <EditableField id="drawer-hourlyRate" label="Hourly rate" value={hourlyRate(profileData?.baseSalary, profileData?.standardHours).toFixed(2)} editing dirty={false}>
                        <Input label={`Hourly rate (${rateBasis(profileData?.standardHours)})`} type="text" readOnly disabled
                          value={hourlyRate(profileData?.baseSalary, profileData?.standardHours).toFixed(2)} onChange={() => undefined} />
                      </EditableField>
                    )}
                    <label className={`editor-toggle ${dirtyFields.has('overtimeEligible') ? 'is-dirty' : ''}`}>
                      <span><strong>Overtime eligible</strong><small>Allow overtime calculations for this employee.</small></span>
                      <input type="checkbox" checked={Boolean(profileData?.overtimeEligible)} onChange={(event) => handleInputChange('overtimeEligible', event.target.checked)} />
                    </label>
                  </div>
                </section>
              )}

              {editSection === 'banking' && (
                <section className="editor-section">
                  <div className="editor-section-heading">
                    <span><FiCreditCard /></span>
                    <div><h3>Banking information</h3><p>Review account details carefully before saving.</p></div>
                  </div>
                  <div className="employment-access-note banking"><FiLock /> Banking changes are sensitive and may require HR verification.</div>
                  <div className="drawer-form-grid">
                    <EditableField id="drawer-account" label="Account number" value={profileData?.accountNumber} editing dirty={dirtyFields.has('accountNumber')}>
                      <Input label="Account number" value={profileData?.accountNumber || ''} onChange={(event) => handleInputChange('accountNumber', event.target.value)} onBlur={() => handleFieldBlur('accountNumber')} error={errorFor('accountNumber')} />
                    </EditableField>
                    <EditableField id="drawer-bank" label="Bank name" value={profileData?.bankName} editing dirty={dirtyFields.has('bankName')}>
                      <Input label="Bank name" value={profileData?.bankName || ''} onChange={(event) => handleInputChange('bankName', event.target.value)} onBlur={() => handleFieldBlur('bankName')} error={errorFor('bankName')} />
                    </EditableField>
                    <EditableField id="drawer-branch" label="Branch code" value={profileData?.branchCode} editing dirty={dirtyFields.has('branchCode')}>
                      <Input label="Branch code" value={profileData?.branchCode || ''} onChange={(event) => handleInputChange('branchCode', event.target.value)} onBlur={() => handleFieldBlur('branchCode')} error={errorFor('branchCode')} />
                    </EditableField>
                    <EditableField id="drawer-title" label="Account title" value={profileData?.accountTitle} editing dirty={dirtyFields.has('accountTitle')}>
                      <Input label="Account title" value={profileData?.accountTitle || ''} onChange={(event) => handleInputChange('accountTitle', event.target.value)} onBlur={() => handleFieldBlur('accountTitle')} error={errorFor('accountTitle')} />
                    </EditableField>
                  </div>
                </section>
              )}

              {editSection === 'documents' && (
                <section className="editor-section">
                  <div className="editor-section-heading">
                    <span><FiFileText /></span>
                    <div><h3>Profile documents</h3><p>Upload images or PDF files up to 5 MB.</p></div>
                  </div>
                  <div className="drawer-documents">
                    {DOCUMENT_FIELDS.map((document) => {
                      const url = (profileData?.[document.key as keyof User] as string) || '';
                      const isUploading = uploadingDoc === document.key;
                      return (
                        <div className="drawer-document-row" key={document.key}>
                          <span><FiFileText /></span>
                          <div><strong>{document.label}</strong><small>{url ? 'Uploaded' : 'No file uploaded'}</small></div>
                          <input id={`drawer-doc-${document.key}`} className="visually-hidden" type="file" accept=".jpg,.jpeg,.png,.gif,.webp,.pdf" onChange={(event) => handleDocumentPick(document.key, event)} disabled={isUploading} />
                          <label htmlFor={`drawer-doc-${document.key}`}>{isUploading ? 'Uploading…' : url ? 'Replace' : 'Upload'}</label>
                        </div>
                      );
                    })}
                  </div>
                </section>
              )}

              {editSection === 'security' && (
                <section className="editor-section">
                  <div className="editor-section-heading">
                    <span><FiLock /></span>
                    <div><h3>Change password</h3><p>Use at least eight characters and a mix of letters, numbers and symbols.</p></div>
                  </div>
                  <form className="password-change-form drawer-password-form" onSubmit={handleChangePassword}>
                    <PasswordField id="drawer-current-password" label="Current password" value={passwordForm.currentPassword} visible={passwordVisibility.current} onChange={(value) => setPasswordForm({ ...passwordForm, currentPassword: value })} onToggle={() => setPasswordVisibility({ ...passwordVisibility, current: !passwordVisibility.current })} autoComplete="current-password" />
                    <PasswordField id="drawer-new-password" label="New password" value={passwordForm.newPassword} visible={passwordVisibility.next} onChange={(value) => setPasswordForm({ ...passwordForm, newPassword: value })} onToggle={() => setPasswordVisibility({ ...passwordVisibility, next: !passwordVisibility.next })} autoComplete="new-password" />
                    <PasswordField id="drawer-confirm-password" label="Confirm new password" value={passwordForm.confirmPassword} visible={passwordVisibility.confirm} onChange={(value) => setPasswordForm({ ...passwordForm, confirmPassword: value })} onToggle={() => setPasswordVisibility({ ...passwordVisibility, confirm: !passwordVisibility.confirm })} autoComplete="new-password" />
                    <ul className="profile-password-requirements" aria-label="Password requirements">
                      <Requirement met={passwordChecks.length}>At least 8 characters</Requirement>
                      <Requirement met={passwordChecks.uppercase}>One uppercase letter</Requirement>
                      <Requirement met={passwordChecks.number}>One number</Requirement>
                      <Requirement met={passwordChecks.special}>One special character</Requirement>
                      <Requirement met={passwordChecks.matches}>Passwords match</Requirement>
                    </ul>
                    {passwordMessage && <div className={`password-message ${passwordMessage.type}`} role="status">{passwordMessage.type === 'success' && <FiCheck />}{passwordMessage.text}</div>}
                    <div className="password-form-actions">
                      <Button variant="primary" type="submit" disabled={!canChangePassword} isLoading={isChangingPassword}>Change password</Button>
                    </div>
                  </form>
                </section>
              )}
            </div>

            <footer className="profile-editor-footer">
              {showDiscardConfirm ? (
                <div className="discard-confirm" role="alert">
                  <div><strong>Discard unsaved changes?</strong><span>Your profile will return to its last saved state.</span></div>
                  <button type="button" onClick={() => setShowDiscardConfirm(false)}>Keep editing</button>
                  <button type="button" className="danger" onClick={cancelEditing}>Discard</button>
                </div>
              ) : (
                <>
                  <span>{dirtyFields.size ? `${dirtyFields.size} changed ${dirtyFields.size === 1 ? 'field' : 'fields'}` : 'No changes yet'}</span>
                  <Button variant="secondary" onClick={requestCloseEditor} disabled={isSaving}>Cancel</Button>
                  <Button variant="primary" onClick={handleSave} isLoading={isSaving} disabled={dirtyFields.size === 0}>Save changes</Button>
                </>
              )}
            </footer>
          </aside>
        </div>
      )}
    </div>
  );
};

const MetaItem = ({ icon, label, value }: { icon: ReactNode; label: string; value: string }) => (
  <div className="meta-item"><span>{icon}</span><div><small>{label}</small><strong>{value}</strong></div></div>
);

const SectionCard = ({ icon, title, description, children }: { icon: ReactNode; title: string; description: string; children: ReactNode }) => (
  <section className="profile-section-card">
    <header><span>{icon}</span><div><h2>{title}</h2><p>{description}</p></div></header>
    {children}
  </section>
);

const EditableField = ({ id, label, value, editing, dirty, wide, children }: { id: string; label: string; value?: string | null; editing: boolean; dirty: boolean; wide?: boolean; children: ReactNode }) => (
  <div id={`profile-field-${id}`} className={`profile-field ${wide ? 'wide' : ''} ${editing ? 'is-editing' : ''} ${dirty ? 'is-dirty' : ''}`}>
    {editing ? children : <><span className="field-label">{label}</span><span className={`field-value ${!value ? 'empty' : ''}`}>{value || 'Not provided'}</span></>}
    {dirty && <small className="changed-label">Changed</small>}
  </div>
);

const ReadField = ({ label, value, locked, copyable }: { label: string; value?: string | null; locked?: boolean; copyable?: boolean }) => (
  <div className="profile-field read-only">
    <span className="field-label">{label}{locked && <FiLock aria-label="Restricted field" />}</span>
    <span className={`field-value ${!value ? 'empty' : ''}`}>
      {value || 'Not provided'}
      {copyable && value && <button type="button" onClick={() => void navigator.clipboard.writeText(value)} aria-label={`Copy ${label}`}><FiCopy /></button>}
    </span>
  </div>
);

const SummaryRow = ({ label, value, copyValue, status }: { label: string; value: string; copyValue?: string; status?: 'active' | 'inactive' }) => (
  <div className="summary-row"><span>{label}</span><strong className={status ? `summary-status ${status}` : ''}>{value}</strong>{copyValue && <button type="button" onClick={() => void navigator.clipboard.writeText(copyValue)} aria-label={`Copy ${label}`}><FiCopy /></button>}</div>
);

const QuickRow = ({ icon, label, value }: { icon: ReactNode; label: string; value: string }) => (
  <div className="quick-row"><span>{icon}</span><div><small>{label}</small><strong>{value}</strong></div></div>
);

const PasswordField = ({ id, label, value, visible, onChange, onToggle, autoComplete }: {
  id: string; label: string; value: string; visible: boolean; onChange: (value: string) => void; onToggle: () => void; autoComplete: string;
}) => (
  <label className="profile-password-field" htmlFor={id}>
    <span>{label}</span>
    <div><input id={id} type={visible ? 'text' : 'password'} value={value} onChange={(event) => onChange(event.target.value)} autoComplete={autoComplete} required /><button type="button" onClick={onToggle} aria-label={visible ? `Hide ${label.toLowerCase()}` : `Show ${label.toLowerCase()}`}>{visible ? <FiEyeOff /> : <FiEye />}</button></div>
  </label>
);

const Requirement = ({ met, children }: { met: boolean; children: ReactNode }) => <li className={met ? 'met' : ''}><FiCheck />{children}</li>;

export default Profile;
