import { useState, useEffect } from 'react';
import { FiUserPlus, FiUserCheck, FiUsers, FiEdit, FiX, FiRefreshCw, FiCheck } from 'react-icons/fi';
import { employeeService, resendWelcomeEmail } from '../../services/employeeService';
import Card from '../../components/common/Card';
import Input from '../../components/common/Input';
import Select from '../../components/common/Select';
import Button from '../../components/common/Button';
import { type Employee } from '../../types';
import { useAuthStore } from '../../store/authStore';
import CreateUserPanel from './CreateUserPanel';
import AssignManagerPanel from './AssignManagerPanel';
import ManageUsersPanel from './ManageUsersPanel';
import { hourlyRate, rateBasis } from '../../utils/workHours';
import './Employees.css';

type TabType = 'create' | 'assign' | 'manage';

// ---- Shared field validation for the create / edit user forms ----
const NAME_RE = /^[A-Za-z][A-Za-z\s.'-]*$/;   // letters only (spaces . ' - allowed)
const EMAIL_RE = /^\S+@\S+\.\S+$/;            // must contain @ and a domain
const PK_PHONE_RE = /^03\d{9}$/;              // Pakistani mobile: 11 digits, 03XXXXXXXXX
const CNIC_RE = /^(\d{13}|\d{5}-\d{7}-\d)$/;  // 13 digits, with or without dashes

const validateUserFields = (f: {
  name?: string; email?: string; phone?: string; cnic?: string;
  emergencyContactName?: string; emergencyContactPhone?: string;
  baseSalary?: number | string; allowance?: number | string; hourlyRate?: number | string;
}): string[] => {
  const errors: string[] = [];

  const name = (f.name || '').trim();
  if (!name) errors.push('Full Name is required.');
  else if (!NAME_RE.test(name)) errors.push('Full Name must contain letters only — no digits or symbols.');

  const email = (f.email || '').trim();
  if (!email) errors.push('Email is required.');
  else if (!EMAIL_RE.test(email)) errors.push('Email must be a valid address with "@" (e.g. name@company.com).');

  const phone = (f.phone || '').trim();
  if (!phone) errors.push('Phone Number is required.');
  else if (!PK_PHONE_RE.test(phone)) errors.push('Phone Number must be a Pakistani mobile number — exactly 11 digits starting with 03 (e.g. 03001234567).');

  const cnic = (f.cnic || '').trim();
  if (cnic && !CNIC_RE.test(cnic)) errors.push('CNIC must be 13 digits, with or without dashes (e.g. 35201-1234567-8).');

  const ecName = (f.emergencyContactName || '').trim();
  if (ecName && !NAME_RE.test(ecName)) errors.push('Emergency Contact Name must contain letters only.');

  const ecPhone = (f.emergencyContactPhone || '').trim();
  if (ecPhone && !PK_PHONE_RE.test(ecPhone)) errors.push('Emergency Contact Phone must be exactly 11 digits starting with 03.');

  const wholeNumber = (label: string, v: number | string | undefined) => {
    if (v === undefined || v === null || v === '') return;
    const n = Number(v);
    if (!Number.isFinite(n) || n < 0) errors.push(`${label} must be a non-negative number.`);
    else if (!Number.isInteger(n)) errors.push(`${label} must be a whole number (no decimals).`);
  };
  wholeNumber('Base Salary', f.baseSalary);
  wholeNumber('Allowance', f.allowance);
  // Not the hourly rate: it is derived server-side and read-only here, and it
  // is fractional far more often than not (basic 150,000 over a full-time
  // month is 937.50). Validating it rejected edits nobody had made.

  return errors;
};

const Employees = () => {
  const { user: currentUser } = useAuthStore();
  // Inline per-card edit + delete. Both are scoped to a single user's card
  // rather than a full-page modal overlay.
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editForm, setEditForm] = useState<Employee | null>(null);
  const [savingEdit, setSavingEdit] = useState(false);
  const [confirmDeleteId, setConfirmDeleteId] = useState<string | null>(null);
  const [deleteReason, setDeleteReason] = useState('');
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const [deleteError, setDeleteError] = useState('');
  const [activeTab, setActiveTab] = useState<TabType>('create');
  const [employees, setEmployees] = useState<Employee[]>([]);
  const [employeesForAssignment, setEmployeesForAssignment] = useState<Employee[]>([]);
  const [managers, setManagers] = useState<Employee[]>([]);
  const [isLoading, setIsLoading] = useState(true);

  const [resetStates, setResetStates] = useState<Record<string, {
    loading?: boolean;
    success?: boolean;
    error?: string;
  }>>({});

  // Define all async functions FIRST (before useEffect)
  const loadEmployees = async () => {
    try {
      // Use admin endpoint to get all users (limit 1000) for Manage Users tab
      // For dashboard, use getDashboardUsers() instead
      const data = await employeeService.getAllUsers(true);
      if (data) setEmployees(data);
    } catch (error) {
      // Deliberately does NOT clear the list on failure. This used to
      // setEmployees([]) here, so a single transient fetch error (a
      // Supabase pool hiccup, a dropped connection) wiped the whole Manage
      // Users table to "no users" even though the data was fine seconds
      // earlier — confusing since nothing on screen explains why. Leaving
      // the last known list up and only logging the error means a stale
      // list in the rare failure case, which is strictly better than an
      // empty one.
      console.error('Failed to load employees:', error);
    }
  };

  const loadEmployeesForAssignment = async () => {
    try {
      // Get employees only (excluding admins) for the assign manager dropdown
      const data = await employeeService.getEmployeesForAssignment();
      console.log('Employees for assignment loaded:', data);
      setEmployeesForAssignment(data || []);
    } catch (error) {
      console.error('Failed to load employees for assignment:', error);
      // Fallback to filtering from all employees
      const filtered = employees.filter(emp => emp.role !== 'admin');
      console.log('Using fallback employees:', filtered);
      setEmployeesForAssignment(filtered);
    }
  };

  const loadManagers = async () => {
    try {
      const managerData = await employeeService.getManagers();
      console.log('Managers loaded:', managerData);
      setManagers(managerData || []);
    } catch (error) {
      console.error('Failed to load managers:', error);
      // Fallback to filtering from employees
      const filtered = employees.filter(emp => 
        emp.role === 'manager' || emp.designation?.toLowerCase().includes('manager')
      );
      console.log('Using fallback managers:', filtered);
      setManagers(filtered);
    }
  };

  const loadData = async () => {
    try {
      setIsLoading(true);
      await Promise.all([loadEmployees(), loadEmployeesForAssignment(), loadManagers()]);
    } catch (error) {
      console.error('Failed to load data:', error);
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    loadData();
  }, []);

  // Reload assignment data when switching to assign tab
  useEffect(() => {
    if (activeTab === 'assign') {
      loadEmployeesForAssignment();
      loadManagers();
    }
  }, [activeTab]);

  const handleResetPassword = async (email?: string, userId?: string) => {
    if (!userId) return;
    if (!email) {
      setResetStates(prev => ({
        ...prev,
        [userId]: { error: 'No email found for user' }
      }));
      setTimeout(() => {
        setResetStates(prev => ({ ...prev, [userId]: {} }));
      }, 3000);
      return;
    }
    setResetStates(prev => ({ ...prev, [userId]: { loading: true } }));
    try {
      await resendWelcomeEmail(email);
      setResetStates(prev => ({ ...prev, [userId]: { success: true } }));
      setTimeout(() => {
        setResetStates(prev => ({ ...prev, [userId]: {} }));
      }, 3000);
    } catch (err: any) {
      setResetStates(prev => ({ ...prev, [userId]: { error: err.message || 'Failed to send email' } }));
      setTimeout(() => {
        setResetStates(prev => ({ ...prev, [userId]: {} }));
      }, 3000);
    }
  };

  if (isLoading) {
    return (
      <div className="employees-loading">
        <div className="spinner"></div>
      </div>
    );
  }

  // Role guard: Only admin can remove users
  const isAdmin = currentUser?.role === 'admin';

  const startEdit = (employee: Employee) => {
    setConfirmDeleteId(null);
    setEditingId(employee.id);
    setEditForm({ ...employee });
  };

  const handleSaveEdit = async () => {
    if (!editingId || !editForm) return;
    const problems = validateUserFields(editForm);
    if (problems.length) {
      alert('Please fix the following before saving:\n\n• ' + problems.join('\n• '));
      return;
    }
    try {
      setSavingEdit(true);
      const updated = await employeeService.updateEmployee(editingId, editForm);
      // Merge rather than replace: the PUT response is authoritative for what
      // it returns, but keep local fields it may omit.
      setEmployees((prev) =>
        prev.map((e) => (e.id === editingId ? { ...e, ...updated } : e))
      );
      setEditingId(null);
      setEditForm(null);
    } catch (err: any) {
      alert(err?.response?.data?.detail || err?.message || 'Failed to update user');
    } finally {
      setSavingEdit(false);
    }
  };

  const handleConfirmDelete = async (employee: Employee) => {
    try {
      setDeletingId(employee.id);
      setDeleteError('');
      const { removeUserById } = await import('../../services/employeeService');
      const response = await removeUserById(employee.id);
      if (response?.success === false || response?.detail) {
        const detail = response.detail;
        setDeleteError(
          Array.isArray(detail) ? (detail[0]?.msg || 'Failed to remove user')
            : typeof detail === 'string' ? detail
            : 'Failed to remove user'
        );
        return;
      }
      setConfirmDeleteId(null);
      setDeleteReason('');
      loadEmployees();
    } catch (err: any) {
      setDeleteError(err?.response?.data?.detail || err?.message || 'Failed to remove user');
    } finally {
      setDeletingId(null);
    }
  };

  return (
    <div className="employees-page">
      <Card className="employees-card">
        <div className="page-header">
          <div>
            <p className="page-breadcrumb">People / User Management</p>
            <h2 className="page-title">
              {activeTab === 'create' ? 'Create User' : activeTab === 'assign' ? 'Assign Manager' : 'Manage Users'}
            </h2>
            <p className="page-subtitle">
              {activeTab === 'create'
                ? 'Create a secure employee account and workforce record.'
                : activeTab === 'assign'
                  ? 'Assign, update, and review reporting relationships across your workforce.'
                  : 'Review and maintain employee accounts and access.'}
            </p>
          </div>
        </div>

        {/* Tabs */}
        <div className="tab-buttons">
          <button
            className={`tab-btn ${activeTab === 'create' ? 'active' : ''}`}
            onClick={() => setActiveTab('create')}
          >
            <FiUserPlus size={18} />
            Create User
          </button>
          <button
            className={`tab-btn ${activeTab === 'assign' ? 'active' : ''}`}
            onClick={() => setActiveTab('assign')}
          >
            <FiUserCheck size={18} />
            Assign Manager
          </button>
          <button
            className={`tab-btn ${activeTab === 'manage' ? 'active' : ''}`}
            onClick={() => setActiveTab('manage')}
          >
            <FiUsers size={18} />
            Manage Users ({employees.length})
          </button>
        </div>

        {/* Tab Content */}
        <div className="tab-content">
          {/* Create User Tab */}
          {activeTab === 'create' && (
            <div className="tab-panel">
              <CreateUserPanel
                employees={employees}
                managers={managers}
                onCreated={async () => {
                  await Promise.all([loadEmployees(), loadManagers()]);
                }}
                onOpenManage={() => setActiveTab('manage')}
              />
            </div>
          )}

          {/* Assign Manager Tab */}
          {activeTab === 'assign' && (
            <div className="tab-panel">
              <AssignManagerPanel
                employees={employeesForAssignment}
                managers={managers}
                onAssigned={async () => {
                  await Promise.all([loadEmployees(), loadEmployeesForAssignment(), loadManagers()]);
                }}
              />
            </div>
          )}

          {/* Manage Users Tab */}
          {activeTab === 'manage' && (
            <div className="tab-panel">
              <ManageUsersPanel
                employees={employees}
                managers={managers}
                onRefresh={async () => {
                  await Promise.all([loadEmployees(), loadManagers()]);
                }}
                onPatchEmployee={(id, patch) => {
                  setEmployees((current) => current.map((employee) => (employee.id === id ? { ...employee, ...patch } : employee)));
                }}
              />
            </div>
          )}

          {/* Legacy card view retained temporarily for rollback safety. */}
          {activeTab === 'manage' && employees.length < 0 && (
            <div className="tab-panel">
              <h3 className="panel-title">All Users ({employees.length})</h3>
              {/* Capped height + internal scroll so a long user list doesn't
                  stretch the page; only the grid scrolls. */}
              <div className="employees-grid employees-grid-scroll">
                {employees.length === 0 ? (
                  <div className="empty-state">
                    <FiUsers size={48} />
                    <p>No employees found</p>
                  </div>
                ) : (
                  employees.map((employee) => (
                    <div key={employee.id} className={`employee-card vertical-card${editingId === employee.id ? ' employee-card-editing' : ''}`}>
                      {editingId === employee.id && editForm ? (
                        /* ---- Inline edit panel (scoped to this card) ---- */
                        <div className="employee-edit-panel">
                          <h4 className="employee-edit-title">Edit {employee.name}</h4>
                          <div className="employee-edit-grid">
                            <Input label="Full Name *" value={editForm.name || ''}
                              onChange={(e) => setEditForm({ ...editForm, name: e.target.value })} />
                            <Input label="Email *" type="email" value={editForm.email || ''}
                              onChange={(e) => setEditForm({ ...editForm, email: e.target.value })} />
                            <Input label="Phone Number *" type="tel" value={editForm.phone || ''}
                              onChange={(e) => setEditForm({ ...editForm, phone: e.target.value })} />
                            <Select label="Gender" value={editForm.gender || ''}
                              onChange={(e) => setEditForm({ ...editForm, gender: e.target.value as any })}
                              options={[
                                { value: '', label: 'Not set' },
                                { value: 'male', label: 'Male' },
                                { value: 'female', label: 'Female' },
                              ]} />
                            <Input label="Department" value={editForm.department || ''}
                              onChange={(e) => setEditForm({ ...editForm, department: e.target.value })} />
                            <Input label="Designation" value={editForm.designation || ''}
                              onChange={(e) => setEditForm({ ...editForm, designation: e.target.value })} />
                            <Select label="Role" value={editForm.role || 'employee'}
                              onChange={(e) => setEditForm({ ...editForm, role: e.target.value as any })}
                              options={[
                                { value: 'employee', label: 'Employee' },
                                { value: 'manager', label: 'Manager' },
                                { value: 'admin', label: 'Admin' },
                              ]} />
                            <Select label="Status" value={editForm.isActive === false ? 'inactive' : 'active'}
                              onChange={(e) => setEditForm({ ...editForm, isActive: e.target.value === 'active' })}
                              options={[
                                { value: 'active', label: 'Active' },
                                { value: 'inactive', label: 'Inactive' },
                              ]} />
                            {/* Numeric fields stay CLEARABLE: an empty box is kept as
                                undefined (field left unchanged on save) instead of being
                                forced back to 0, so backspace works naturally. */}
                            <Input label="Base Salary (Rs)" type="number" min="0" value={editForm.baseSalary ?? ''}
                              onChange={(e) => setEditForm({ ...editForm, baseSalary: e.target.value === '' ? undefined : Number(e.target.value) })} />
                            <Input label="Allowance (Rs)" type="number" min="0" value={editForm.allowance ?? ''}
                              onChange={(e) => setEditForm({ ...editForm, allowance: e.target.value === '' ? undefined : Number(e.target.value) })} />
                            {/* Derived server-side from the basic salary over this employee's own
                                month, so it is shown, not typed. A 4-hour day divides by 80. */}
                            <Input label={`Hourly Rate (Rs) — ${rateBasis(editForm.standardHours)}`} type="text" readOnly disabled
                              value={hourlyRate(editForm.baseSalary, editForm.standardHours).toFixed(2)} onChange={() => undefined} />
                            <Input label="Standard Hours (per day)" type="number" min="1" max="12" step="0.5" value={editForm.standardHours ?? ''}
                              onChange={(e) => setEditForm({ ...editForm, standardHours: e.target.value === '' ? undefined : Number(e.target.value) })} />
                            <Input label="Date of Birth" type="date" value={(editForm.dateOfBirth || '').split('T')[0]}
                              onChange={(e) => setEditForm({ ...editForm, dateOfBirth: e.target.value })} />
                            <Input label="CNIC" value={editForm.cnic || ''}
                              onChange={(e) => setEditForm({ ...editForm, cnic: e.target.value })} />
                            <Input label="Address" value={editForm.residentialAddress || ''}
                              onChange={(e) => setEditForm({ ...editForm, residentialAddress: e.target.value })} />
                            <Input label="Emergency Contact Name" value={editForm.emergencyContactName || ''}
                              onChange={(e) => setEditForm({ ...editForm, emergencyContactName: e.target.value })} />
                            <Input label="Emergency Contact Phone" value={editForm.emergencyContactPhone || ''}
                              onChange={(e) => setEditForm({ ...editForm, emergencyContactPhone: e.target.value })} />
                            <Input label="Account Title" value={editForm.accountTitle || ''}
                              onChange={(e) => setEditForm({ ...editForm, accountTitle: e.target.value })} />
                            <Input label="Account Number" value={editForm.accountNumber || ''}
                              onChange={(e) => setEditForm({ ...editForm, accountNumber: e.target.value })} />
                            <Input label="Bank Name" value={editForm.bankName || ''}
                              onChange={(e) => setEditForm({ ...editForm, bankName: e.target.value })} />
                            <Input label="Branch Code" value={editForm.branchCode || ''}
                              onChange={(e) => setEditForm({ ...editForm, branchCode: e.target.value })} />
                          </div>
                          <div className="employee-edit-actions">
                            <Button variant="secondary" size="sm" onClick={() => { setEditingId(null); setEditForm(null); }} disabled={savingEdit}>
                              Cancel
                            </Button>
                            <Button variant="primary" size="sm" onClick={handleSaveEdit} isLoading={savingEdit}>
                              Save Changes
                            </Button>
                          </div>
                        </div>
                      ) : (
                      <>
                      {/* Employee Info Section */}
                      <div className="employee-info-section">
                        <div className="employee-avatar">
                          <FiUsers size={24} />
                        </div>
                        <div className="employee-info-vertical">
                          <span className="employee-name">{employee.name}</span>
                          <span className="employee-role">{employee.role ? employee.role.charAt(0).toUpperCase() + employee.role.slice(1) : 'Employee'}</span>
                          <div className="employee-details">
                            <div className="employee-detail-item">
                              <span className="detail-label">Email:</span>
                              <span className="detail-value">{employee.email || 'N/A'}</span>
                            </div>
                            {employee.phone && (
                              <div className="employee-detail-item">
                                <span className="detail-label">Phone:</span>
                                <span className="detail-value">{employee.phone}</span>
                              </div>
                            )}
                            <div className="employee-detail-item">
                              <span className="detail-label">Designation:</span>
                              <span className="detail-value">{employee.designation || 'N/A'}</span>
                            </div>
                            <div className="employee-detail-item">
                              <span className="detail-label">Department:</span>
                              <span className="detail-value">{employee.department || 'N/A'}</span>
                            </div>
                            <div className="employee-detail-item">
                              <span className="detail-label">Base Salary:</span>
                              <span className="detail-value">Rs {(employee.baseSalary ?? 0).toLocaleString()}</span>
                            </div>
                            <div className="employee-detail-item">
                              <span className="detail-label">Allowance:</span>
                              <span className="detail-value">Rs {(employee.allowance ?? 0).toLocaleString()}</span>
                            </div>
                            {employee.residentialAddress && (
                              <div className="employee-detail-item">
                                <span className="detail-label">Address:</span>
                                <span className="detail-value">{employee.residentialAddress}</span>
                              </div>
                            )}
                            {employee.dateOfBirth && (
                              <div className="employee-detail-item">
                                <span className="detail-label">DOB:</span>
                                <span className="detail-value">{employee.dateOfBirth}</span>
                              </div>
                            )}
                          </div>
                          <span className={`employee-status status-${employee.status}`}>
                            {employee.status || 'active'}
                          </span>
                        </div>
                      </div>
                      {/* Inline delete confirm — scoped to this card only */}
                      {confirmDeleteId === employee.id && (
                        <div className="employee-delete-confirm">
                          <span className="employee-delete-title">Remove {employee.name}?</span>
                          <textarea
                            className="employee-delete-reason"
                            placeholder="Reason for removal (optional)"
                            value={deleteReason}
                            onChange={(e) => setDeleteReason(e.target.value)}
                            disabled={deletingId === employee.id}
                          />
                          {deleteError && <div className="employee-delete-error">{deleteError}</div>}
                          <div className="employee-delete-actions">
                            <Button variant="secondary" size="sm"
                              onClick={() => { setConfirmDeleteId(null); setDeleteError(''); setDeleteReason(''); }}
                              disabled={deletingId === employee.id}>
                              Cancel
                            </Button>
                            <Button variant="primary" size="sm"
                              onClick={() => handleConfirmDelete(employee)}
                              isLoading={deletingId === employee.id}>
                              Confirm Remove
                            </Button>
                          </div>
                        </div>
                      )}

                      {/* Actions Section */}
                      <div className="employee-actions-section">
                        <div className="employee-actions-group row-actions">
                          <button
                            className="employee-edit-btn"
                            onClick={() => startEdit(employee)}
                            title="Edit User"
                          >
                            <FiEdit size={18} />
                          </button>
                          {isAdmin && (
                            <button
                              className="employee-remove-btn"
                              onClick={() => {
                                setEditingId(null);
                                setDeleteError('');
                                setDeleteReason('');
                                setConfirmDeleteId(confirmDeleteId === employee.id ? null : employee.id);
                              }}
                              title="Remove User"
                              style={{ marginLeft: 8 }}
                            >
                              <FiX size={18} />
                            </button>
                          )}
                        </div>
                        {/* Reset Password Section */}
                        <div className="employee-reset-section">
                          <Button
                            variant="secondary"
                            size="sm"
                            className="reset-password-btn"
                            style={{ width: '100%', marginTop: '1.5rem' }}
                            disabled={resetStates[employee.id]?.loading}
                            onClick={() => handleResetPassword(employee.email, employee.id)}
                          >
                            {resetStates[employee.id]?.loading
                              ? <span className="spin"><FiRefreshCw size={16} /></span>
                              : resetStates[employee.id]?.success
                                ? <span className="tick"><FiCheck size={18} /></span>
                                : 'Reset Password'}
                          </Button>
                          {resetStates[employee.id]?.error && (
                            <div className="reset-error-msg">{resetStates[employee.id]?.error}</div>
                          )}
                        </div>
                      </div>
                      </>
                      )}
                    </div>
                  ))
                )}
              </div>
            </div>
          )}
        </div>
      </Card>
    </div>
  );
};

export default Employees;
