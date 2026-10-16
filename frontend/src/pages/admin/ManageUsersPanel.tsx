import { useEffect, useMemo, useRef, useState } from 'react';
import {
  FiArchive, FiCheck, FiChevronDown, FiChevronLeft, FiChevronRight, FiColumns,
  FiDownload, FiEdit2, FiEye, FiEyeOff, FiFileText, FiKey, FiMoreHorizontal, FiRefreshCw,
  FiSearch, FiSliders, FiTrash2, FiUser, FiUserCheck, FiUserX, FiUsers, FiX,
} from 'react-icons/fi';
import { employeeService } from '../../services/employeeService';
import { documentFilename, fetchDocumentBlob } from '../../services/documentService';
import { type Employee, type UserRole } from '../../types';
import { describeDay, hourlyRate, monthlyHours, rateBasis } from '../../utils/workHours';
import {
  normaliseDigits,
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
import './ManageUsersPanel.css';

type EditErrors = Partial<Record<keyof Employee, string>>;

// Every field the edit form validates — checked in full on every save.
const EDIT_VALIDATABLE_FIELDS: (keyof Employee)[] = [
  'name', 'email', 'phone', 'cnic', 'dateOfBirth', 'anniversaryDate', 'residentialAddress', 'employeeId',
  'department', 'designation', 'emergencyContactName', 'emergencyContactPhone',
  'accountNumber', 'bankName', 'branchCode', 'accountTitle',
];

const validateEditField = (field: keyof Employee, value: unknown, form: Employee, employees: Employee[]): string => {
  const str = typeof value === 'string' ? value : '';
  switch (field) {
    case 'name':
      return validatePersonName(str, { required: true });
    case 'email': {
      const error = validateEmail(str, { required: true });
      if (error) return error;
      if (employees.some((employee) => employee.id !== form.id && employee.email.toLowerCase() === str.trim().toLowerCase())) {
        return 'A user with this email already exists.';
      }
      return '';
    }
    case 'phone': {
      const error = validatePakPhone(str);
      if (error) return error;
      if (str && employees.some((employee) => employee.id !== form.id && normaliseDigits(employee.phone || '') === normaliseDigits(str))) {
        return 'A user with this phone number already exists.';
      }
      return '';
    }
    case 'cnic': {
      const error = validateCnic(str);
      if (error) return error;
      if (str && employees.some((employee) => employee.id !== form.id && normaliseDigits(employee.cnic || '') === normaliseDigits(str))) {
        return 'A user with this CNIC already exists.';
      }
      return '';
    }
    case 'dateOfBirth':
      return validateDateOfBirth(str);
    case 'anniversaryDate':
      if (str && form.maritalStatus !== 'married') return 'An anniversary applies to married employees only.';
      // Same backend-sends-a-full-timestamp issue as dateOfBirth above: slice
      // to the date portion before appending a time, or an already-timestamped
      // value untouched by the admin this session doubles up into an invalid
      // string and wrongly blocks the save.
      if (str && new Date(`${str.slice(0, 10)}T00:00:00`) > new Date()) return 'The anniversary cannot be in the future.';
      return '';
    case 'residentialAddress':
      return validateAddress(str);
    case 'employeeId':
      if (str && employees.some((employee) => employee.id !== form.id && employee.employeeId?.toLowerCase() === str.trim().toLowerCase())) {
        return 'This Employee ID is already in use.';
      }
      return '';
    case 'department':
    case 'designation':
      return validateOrgName(str);
    case 'emergencyContactName':
    case 'accountTitle':
      return validatePersonName(str);
    case 'emergencyContactPhone':
      return validatePakPhone(str);
    case 'accountNumber':
      return validateAccountNumber(str);
    case 'bankName':
      return validateOrgName(str);
    case 'branchCode':
      return validateBranchCode(str);
    default:
      return '';
  }
};

type SortKey = 'name' | 'role' | 'department' | 'manager' | 'status' | 'joinDate';
type SortDirection = 'asc' | 'desc';
type FilterKey = 'role' | 'department' | 'manager' | 'status';
type Status = 'active' | 'inactive';

interface Props {
  employees: Employee[];
  managers: Employee[];
  onRefresh: () => Promise<void>;
  onPatchEmployee: (id: string, patch: Partial<Employee>) => void;
}

const FILTER_KEY = 'hrm-manage-user-filters';
const RECENT_KEY = 'hrm-manage-user-searches';

const ManageUsersPanel = ({ employees, managers, onRefresh, onPatchEmployee }: Props) => {
  const searchRef = useRef<HTMLInputElement>(null);
  const searchBoxRef = useRef<HTMLDivElement>(null);
  const [search, setSearch] = useState('');
  const [searchHistoryOpen, setSearchHistoryOpen] = useState(false);
  const [debouncedSearch, setDebouncedSearch] = useState('');
  const [recentSearches, setRecentSearches] = useState<string[]>(() => readStored(RECENT_KEY, []));
  // Defaults to Active-only: the employee list now includes suspended
  // accounts (needed so they can be found and reactivated — see
  // getAllUsers(true) in Employees.tsx), and this production DB has ~130
  // old inactive test/seed accounts alongside the handful of real ones. An
  // admin opening this for the first time should see their real roster, not
  // that backlog; switching the Status filter to Inactive still reaches it.
  const [filters, setFilters] = useState<Record<FilterKey, string[]>>(() => readStored(FILTER_KEY, {
    role: [], department: [], manager: [], status: ['active'],
  }));
  const [openFilter, setOpenFilter] = useState<FilterKey | null>(null);
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [sort, setSort] = useState<{ key: SortKey; direction: SortDirection }>({ key: 'name', direction: 'asc' });
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(25);
  const [drawerUser, setDrawerUser] = useState<Employee | null>(null);
  const [editing, setEditing] = useState(false);
  const [editForm, setEditForm] = useState<Employee | null>(null);
  const [editErrors, setEditErrors] = useState<EditErrors>({});
  const [editTouched, setEditTouched] = useState<Set<keyof Employee>>(new Set());
  const [saving, setSaving] = useState(false);
  const [actionMenu, setActionMenu] = useState<string | null>(null);
  const [notice, setNotice] = useState('');
  const [columnsOpen, setColumnsOpen] = useState(false);
  const [extraColumns, setExtraColumns] = useState<string[]>([]);
  const [removeConfirm, setRemoveConfirm] = useState<Employee | null>(null);
  const [passwordTarget, setPasswordTarget] = useState<Employee | null>(null);
  const [newPassword, setNewPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [passwordError, setPasswordError] = useState('');
  const [showPassword, setShowPassword] = useState(false);

  const employeeMap = useMemo(() => new Map(employees.map((employee) => [String(employee.id), employee])), [employees]);
  const departments = useMemo(() => unique(employees.map((employee) => clean(employee.department))), [employees]);
  const roles = useMemo(() => unique(employees.map((employee) => employee.role || 'employee')), [employees]);

  useEffect(() => {
    const timer = window.setTimeout(() => setDebouncedSearch(search.trim().toLowerCase()), 260);
    return () => window.clearTimeout(timer);
  }, [search]);

  useEffect(() => {
    localStorage.setItem(FILTER_KEY, JSON.stringify(filters));
    setPage(1);
  }, [filters, debouncedSearch]);

  useEffect(() => {
    const focusSearch = (event: KeyboardEvent) => {
      if (event.key === '/' && !(event.target instanceof HTMLInputElement) && !(event.target instanceof HTMLTextAreaElement)) {
        event.preventDefault();
        searchRef.current?.focus();
      }
      if (event.key === 'Escape') {
        setOpenFilter(null);
        setActionMenu(null);
        setSearchHistoryOpen(false);
      }
    };
    window.addEventListener('keydown', focusSearch);
    return () => window.removeEventListener('keydown', focusSearch);
  }, []);

  useEffect(() => {
    if (!searchHistoryOpen) return;
    const closeSearchHistory = (event: MouseEvent | TouchEvent) => {
      if (searchBoxRef.current && !searchBoxRef.current.contains(event.target as Node)) {
        setSearchHistoryOpen(false);
      }
    };
    document.addEventListener('mousedown', closeSearchHistory);
    document.addEventListener('touchstart', closeSearchHistory);
    return () => {
      document.removeEventListener('mousedown', closeSearchHistory);
      document.removeEventListener('touchstart', closeSearchHistory);
    };
  }, [searchHistoryOpen]);

  const filtered = useMemo(() => employees.filter((employee) => {
    const manager = employee.managerId ? employeeMap.get(String(employee.managerId)) : undefined;
    // Deliberately excludes the assigned manager's name: including it made
    // searching "manager" surface every employee who happens to report to
    // someone with "manager" in their name, not just people who are
    // managers — confusing when it doesn't match the searched person's own
    // identity. "Who reports to X" is already covered by the Manager filter
    // dropdown, so free-text search only needs to match the employee's own
    // fields.
    const haystack = [
      employee.name, employee.email, employee.employeeId, employee.department,
      employee.designation,
    ].map(clean).join(' ').toLowerCase();
    const status: Status = employee.isActive === false || employee.status === 'inactive' ? 'inactive' : 'active';
    return (!debouncedSearch || haystack.includes(debouncedSearch))
      && includesFilter(filters.role, employee.role || 'employee')
      && includesFilter(filters.department, clean(employee.department))
      && includesFilter(filters.manager, manager?.id || 'none')
      && includesFilter(filters.status, status);
  }), [employees, employeeMap, debouncedSearch, filters]);

  const sorted = useMemo(() => [...filtered].sort((a, b) => {
    const managerName = (employee: Employee) => employee.managerId ? employeeMap.get(String(employee.managerId))?.name || '' : '';
    const value = (employee: Employee) => {
      if (sort.key === 'manager') return managerName(employee);
      if (sort.key === 'status') return employee.isActive === false ? 'inactive' : 'active';
      return clean(employee[sort.key as keyof Employee]);
    };
    return value(a).localeCompare(value(b), undefined, { numeric: true }) * (sort.direction === 'asc' ? 1 : -1);
  }), [filtered, employeeMap, sort]);

  const pageCount = Math.max(1, Math.ceil(sorted.length / pageSize));
  const visible = sorted.slice((page - 1) * pageSize, page * pageSize);
  const activeFilterCount = Object.values(filters).reduce((total, values) => total + values.length, 0);
  const selectedUsers = employees.filter((employee) => selectedIds.has(employee.id));

  useEffect(() => { if (page > pageCount) setPage(pageCount); }, [page, pageCount]);

  const commitSearch = () => {
    const value = search.trim();
    if (!value) return;
    const next = [value, ...recentSearches.filter((item) => item !== value)].slice(0, 5);
    setRecentSearches(next);
    localStorage.setItem(RECENT_KEY, JSON.stringify(next));
  };

  const removeRecentSearch = (item: string) => {
    const next = recentSearches.filter((entry) => entry !== item);
    setRecentSearches(next);
    localStorage.setItem(RECENT_KEY, JSON.stringify(next));
  };

  const toggleFilter = (key: FilterKey, value: string) => {
    setFilters((current) => ({
      ...current,
      [key]: current[key].includes(value) ? current[key].filter((item) => item !== value) : [...current[key], value],
    }));
  };

  const removeFilter = (key: FilterKey, value: string) => setFilters((current) => ({
    ...current, [key]: current[key].filter((item) => item !== value),
  }));

  const clearFilters = () => setFilters({ role: [], department: [], manager: [], status: [] });

  const toggleSort = (key: SortKey) => setSort((current) => ({
    key,
    direction: current.key === key && current.direction === 'asc' ? 'desc' : 'asc',
  }));

  const toggleVisible = () => {
    const allSelected = visible.length > 0 && visible.every((employee) => selectedIds.has(employee.id));
    setSelectedIds((current) => {
      const next = new Set(current);
      visible.forEach((employee) => allSelected ? next.delete(employee.id) : next.add(employee.id));
      return next;
    });
  };

  const openDrawer = (employee: Employee, edit = false) => {
    setDrawerUser(employee);
    setEditForm({ ...employee });
    setEditing(edit);
    setActionMenu(null);
    setEditErrors({});
    setEditTouched(new Set());
  };

  const updateEditField = (field: keyof Employee, value: unknown) => {
    if (!editForm) return;
    let nextValue = value;
    if (typeof nextValue === 'string') {
      let formatted = nextValue;
      if (field === 'phone' || field === 'emergencyContactPhone') formatted = formatPakPhone(formatted);
      if (field === 'cnic') formatted = formatCnic(formatted);
      if (field === 'name' || field === 'emergencyContactName' || field === 'accountTitle') formatted = sanitizeName(formatted);
      if (field === 'department' || field === 'designation' || field === 'bankName') formatted = sanitizeOrgName(formatted);
      if (field === 'accountNumber') formatted = sanitizeAccountNumber(formatted);
      if (field === 'branchCode') formatted = sanitizeBranchCode(formatted);
      nextValue = formatted;
    }
    const nextForm = { ...editForm, [field]: nextValue } as Employee;
    setEditForm(nextForm);
    setEditErrors((current) => ({ ...current, [field]: validateEditField(field, nextValue, nextForm, employees) }));
  };

  const markEditTouched = (field: keyof Employee) => {
    setEditTouched((current) => (current.has(field) ? current : new Set(current).add(field)));
    if (editForm) {
      setEditErrors((current) => ({ ...current, [field]: validateEditField(field, editForm[field], editForm, employees) }));
    }
  };

  const errorForEdit = (field: keyof Employee) => (editTouched.has(field) ? editErrors[field] : undefined);

  // The entire record is checked on every save, not just fields the admin
  // touched this session — a record full of pre-existing garbage data now
  // has to be cleaned up before any other change to it can be saved.
  const validateEditForm = (): boolean => {
    if (!editForm) return true;
    let firstInvalid: keyof Employee | undefined;
    const updates: EditErrors = {};
    EDIT_VALIDATABLE_FIELDS.forEach((field) => {
      const message = validateEditField(field, editForm[field], editForm, employees);
      updates[field] = message;
      if (message && !firstInvalid) firstInvalid = field;
    });
    setEditErrors((current) => ({ ...current, ...updates }));
    setEditTouched((current) => {
      const next = new Set(current);
      EDIT_VALIDATABLE_FIELDS.forEach((field) => next.add(field));
      return next;
    });
    return !firstInvalid;
  };

  const saveUser = async () => {
    if (!editForm) return;
    if (!validateEditForm()) {
      setNotice('Fix the highlighted fields before saving.');
      return;
    }
    setSaving(true);
    try {
      // Reactivating (Inactive -> Active in the status dropdown) has to go
      // through the dedicated reactivate endpoint first: the generic update
      // below 404s on an already-inactive user for ANY field, not just
      // status. Once reactivated, the rest of the edited fields can go
      // through normally.
      if (editForm.isActive && drawerUser?.isActive === false) {
        await employeeService.reactivateEmployee(editForm.id);
      }
      const updated = await employeeService.updateEmployee(editForm.id, editForm);
      setDrawerUser({ ...editForm, ...updated });
      setEditForm({ ...editForm, ...updated });
      setEditing(false);
      setEditErrors({});
      setEditTouched(new Set());
      setNotice('User changes saved.');
      await onRefresh();
    } catch {
      setNotice('We could not save these changes. Please try again.');
    } finally {
      setSaving(false);
    }
  };

  const removeUser = async () => {
    if (!removeConfirm) return;
    setSaving(true);
    try {
      await employeeService.deleteEmployee(removeConfirm.id);
      setNotice(`${removeConfirm.name} was removed.`);
      setRemoveConfirm(null);
      setDrawerUser(null);
      await onRefresh();
    } catch {
      setNotice('The user could not be removed. Please try again.');
    } finally {
      setSaving(false);
    }
  };

  const openPasswordReset = (employee: Employee) => {
    setPasswordTarget(employee);
    setNewPassword('');
    setConfirmPassword('');
    setPasswordError('');
    setShowPassword(false);
    setActionMenu(null);
  };

  // Mirrors the backend rules so the admin is told what is wrong before the
  // request goes out, rather than getting a 422 back.
  const passwordProblem = (value: string): string => {
    if (value.length < 8) return 'Use at least 8 characters.';
    if (!/[A-Z]/.test(value)) return 'Include an uppercase letter.';
    if (!/[0-9]/.test(value)) return 'Include a number.';
    if (!/[^A-Za-z0-9]/.test(value)) return 'Include a special character.';
    return '';
  };

  const submitPasswordReset = async () => {
    if (!passwordTarget) return;
    const problem = passwordProblem(newPassword);
    if (problem) {
      setPasswordError(problem);
      return;
    }
    if (newPassword !== confirmPassword) {
      setPasswordError('The two passwords do not match.');
      return;
    }

    setSaving(true);
    setPasswordError('');
    try {
      await employeeService.resetPassword(passwordTarget.id, newPassword, confirmPassword);
      setNotice(`Password updated for ${passwordTarget.name}. Share it with them securely — it is not emailed.`);
      setPasswordTarget(null);
      setNewPassword('');
      setConfirmPassword('');
    } catch (error) {
      const detail = (error as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail;
      setPasswordError(
        typeof detail === 'string'
          ? detail
          : 'The password could not be updated. Please try again.',
      );
    } finally {
      setSaving(false);
    }
  };

  const exportCsv = (rows: Employee[]) => {
    const headers = ['Name', 'Email', 'Role', 'Department', 'Designation', 'Manager', 'Status'];
    const values = rows.map((employee) => [
      employee.name, employee.email, employee.role || 'employee', safe(employee.department),
      safe(employee.designation), employee.managerId ? employeeMap.get(String(employee.managerId))?.name || 'No manager' : 'No manager',
      employee.isActive === false ? 'Inactive' : 'Active',
    ]);
    const csv = [headers, ...values].map((row) => row.map(csvCell).join(',')).join('\n');
    const link = document.createElement('a');
    link.href = URL.createObjectURL(new Blob([csv], { type: 'text/csv;charset=utf-8' }));
    link.download = `users-${new Date().toISOString().slice(0, 10)}.csv`;
    link.click();
    URL.revokeObjectURL(link.href);
  };

  // One click from the row menu, no drawer required. Updates the row in
  // place via onPatchEmployee instead of waiting on a full onRefresh() (two
  // full-table GETs) — that round trip was most of what made suspending an
  // account feel slow. Reverts the optimistic update if the request fails.
  const toggleUserStatus = async (employee: Employee) => {
    const active = employee.isActive !== false && employee.status !== 'inactive';
    const nextActive = !active;
    setActionMenu(null);
    onPatchEmployee(employee.id, { isActive: nextActive, status: nextActive ? 'active' : 'inactive' });
    try {
      // Reactivating an inactive user 404s on the generic update endpoint
      // (the backend treats "inactive" as "not found" for any other edit),
      // so it needs the dedicated reactivate endpoint instead.
      if (nextActive) await employeeService.reactivateEmployee(employee.id);
      else await employeeService.updateEmployee(employee.id, { isActive: false });
      setNotice(`${employee.name} ${nextActive ? 'activated' : 'suspended'}.`);
    } catch {
      onPatchEmployee(employee.id, { isActive: active, status: active ? 'active' : 'inactive' });
      setNotice(`We could not update ${employee.name}'s status. Please try again.`);
    }
  };

  const performBulk = async (action: 'activate' | 'suspend') => {
    setSaving(true);
    const nextActive = action === 'activate';
    selectedUsers.forEach((employee) => onPatchEmployee(employee.id, { isActive: nextActive, status: nextActive ? 'active' : 'inactive' }));
    try {
      await Promise.all(selectedUsers.map((employee) => (
        nextActive
          ? employeeService.reactivateEmployee(employee.id)
          : employeeService.updateEmployee(employee.id, { isActive: false })
      )));
      setNotice(`${selectedUsers.length} account${selectedUsers.length === 1 ? '' : 's'} updated.`);
      setSelectedIds(new Set());
    } catch {
      selectedUsers.forEach((employee) => onPatchEmployee(employee.id, {
        isActive: employee.isActive, status: employee.isActive === false ? 'inactive' : 'active',
      }));
      setNotice('Some accounts could not be updated. Refresh and try again.');
    } finally {
      setSaving(false);
    }
  };

  const filterOptions: Record<FilterKey, Array<{ value: string; label: string }>> = {
    role: roles.map((value) => ({ value, label: capitalize(value) })),
    department: departments.map((value) => ({ value, label: value })),
    manager: [{ value: 'none', label: 'No manager' }, ...managers.map((manager) => ({ value: manager.id, label: manager.name }))],
    status: [{ value: 'active', label: 'Active' }, { value: 'inactive', label: 'Inactive' }],
  };

  return (
    <section className="manage-users" aria-label="Manage users">
      {notice && <div className="manage-notice" role="status"><FiCheck /><span>{notice}</span><button onClick={() => setNotice('')} aria-label="Dismiss"><FiX /></button></div>}

      <header className="manage-toolbar">
        {selectedIds.size > 0 ? (
          <div className="bulk-toolbar">
            <strong>{selectedIds.size} selected</strong>
            <button onClick={() => performBulk('activate')} disabled={saving}><FiUserCheck /> Activate</button>
            <button onClick={() => performBulk('suspend')} disabled={saving}><FiUserX /> Suspend</button>
            <button onClick={() => exportCsv(selectedUsers)}><FiDownload /> Export selected</button>
            <button className="bulk-clear" onClick={() => setSelectedIds(new Set())}>Clear selection</button>
          </div>
        ) : (
          <>
            <div className="manage-search" ref={searchBoxRef}>
              <FiSearch />
              <input ref={searchRef} value={search} onFocus={() => setSearchHistoryOpen(true)} onChange={(event) => { setSearch(event.target.value); setSearchHistoryOpen(!event.target.value); }} onBlur={commitSearch} onKeyDown={(event) => event.key === 'Enter' && commitSearch()} placeholder="Search by name, email, ID, department, or designation" aria-label="Search users" />
              {search ? <button onClick={() => { setSearch(''); setSearchHistoryOpen(false); }} aria-label="Clear search"><FiX /></button> : <kbd>/</kbd>}
              {searchHistoryOpen && !search && recentSearches.length > 0 && (
                <div className="recent-searches">
                  <small>Recent searches</small>
                  {recentSearches.map((item) => (
                    <div className="recent-search-row" key={item}>
                      <button onMouseDown={(event) => { event.preventDefault(); setSearch(item); setSearchHistoryOpen(false); }}>{item}</button>
                      <button
                        type="button"
                        className="recent-search-remove"
                        aria-label={`Remove "${item}" from recent searches`}
                        onMouseDown={(event) => { event.preventDefault(); event.stopPropagation(); removeRecentSearch(item); }}
                      >
                        <FiX />
                      </button>
                    </div>
                  ))}
                </div>
              )}
            </div>
            <div className="manage-toolbar-actions">
              <div className="column-control">
                <button onClick={() => setColumnsOpen(!columnsOpen)} aria-expanded={columnsOpen}><FiColumns /> Columns</button>
                {columnsOpen && <div className="column-menu">{['employeeId', 'designation', 'joinDate'].map((column) => <label key={column}><input type="checkbox" checked={extraColumns.includes(column)} onChange={() => setExtraColumns((current) => current.includes(column) ? current.filter((item) => item !== column) : [...current, column])} />{columnLabel(column)}</label>)}</div>}
              </div>
              <button onClick={() => exportCsv(filtered)}><FiDownload /> Export</button>
              <button onClick={onRefresh} title="Refresh users"><FiRefreshCw /></button>
            </div>
          </>
        )}
      </header>

      <div className="manage-filters">
        <span><FiSliders /> Filters {activeFilterCount > 0 && <b>{activeFilterCount}</b>}</span>
        {(Object.keys(filterOptions) as FilterKey[]).map((key) => (
          <div className="filter-control" key={key}>
            <button onClick={() => setOpenFilter(openFilter === key ? null : key)} aria-expanded={openFilter === key}>{capitalize(key)}{filters[key].length > 0 && <b>{filters[key].length}</b>}<FiChevronDown /></button>
            {openFilter === key && <div className="filter-menu">{filterOptions[key].map((option) => <label key={option.value}><input type="checkbox" checked={filters[key].includes(option.value)} onChange={() => toggleFilter(key, option.value)} />{option.label}</label>)}</div>}
          </div>
        ))}
        {activeFilterCount > 0 && <button className="clear-filters" onClick={clearFilters}>Clear all</button>}
      </div>

      {activeFilterCount > 0 && <div className="filter-chips">{(Object.keys(filters) as FilterKey[]).flatMap((key) => filters[key].map((value) => <button key={`${key}-${value}`} onClick={() => removeFilter(key, value)}><span>{capitalize(key)}:</span> {labelFor(filterOptions[key], value)} <FiX /></button>))}</div>}

      <div className="users-table-wrap">
        <table className="users-table">
          <thead><tr>
            <th className="select-cell"><input type="checkbox" checked={visible.length > 0 && visible.every((employee) => selectedIds.has(employee.id))} onChange={toggleVisible} aria-label="Select visible users" /></th>
            <Sortable label="User" sortKey="name" current={sort} onSort={toggleSort} />
            {extraColumns.includes('employeeId') && <th>Employee ID</th>}
            {extraColumns.includes('designation') && <th>Designation</th>}
            <Sortable label="Role" sortKey="role" current={sort} onSort={toggleSort} />
            <Sortable label="Department" sortKey="department" current={sort} onSort={toggleSort} />
            <Sortable label="Manager" sortKey="manager" current={sort} onSort={toggleSort} />
            <Sortable label="Status" sortKey="status" current={sort} onSort={toggleSort} />
            {extraColumns.includes('joinDate') && <Sortable label="Date joined" sortKey="joinDate" current={sort} onSort={toggleSort} />}
            <th className="actions-cell">Actions</th>
          </tr></thead>
          <tbody>
            {visible.map((employee) => {
              const manager = employee.managerId ? employeeMap.get(String(employee.managerId)) : undefined;
              const active = employee.isActive !== false && employee.status !== 'inactive';
              return <tr key={employee.id} className={selectedIds.has(employee.id) ? 'selected' : ''} onClick={() => openDrawer(employee)}>
                <td className="select-cell"><input type="checkbox" checked={selectedIds.has(employee.id)} onChange={() => setSelectedIds((current) => toggleSet(current, employee.id))} onClick={(event) => event.stopPropagation()} aria-label={`Select ${employee.name}`} /></td>
                <td><div className="user-identity"><Avatar employee={employee} /><span><strong>{employee.name}</strong><small>{employee.email}</small></span></div></td>
                {extraColumns.includes('employeeId') && <td>{safe(employee.employeeId, 'Not assigned')}</td>}
                {extraColumns.includes('designation') && <td>{safe(employee.designation, 'No designation')}</td>}
                <td><span className={`role-badge role-${employee.role || 'employee'}`}>{capitalize(employee.role || 'employee')}</span></td>
                <td>{clean(employee.department) ? employee.department : <span className="muted" title="No department assigned">— No department</span>}</td>
                <td>{manager ? <span className="manager-name"><Avatar employee={manager} small />{manager.name}</span> : <span className="muted" title="No manager assigned">— No manager</span>}</td>
                <td><span className={`account-status ${active ? 'active' : 'inactive'}`} title={active ? 'User can access the HRM' : 'User access is disabled'}><i />{active ? 'Active' : 'Inactive'}</span></td>
                {extraColumns.includes('joinDate') && <td>{formatDate(employee.joinDate)}</td>}
                <td className="actions-cell" onClick={(event) => event.stopPropagation()}>
                  <div className="row-menu-wrap"><button className="icon-action" onClick={() => setActionMenu(actionMenu === employee.id ? null : employee.id)} title="More actions"><FiMoreHorizontal /></button>
                    {actionMenu === employee.id && <div className="row-menu">
                      <button onClick={() => openDrawer(employee, true)}><FiEdit2 /> Edit user</button>
                      {active
                        ? <button onClick={() => toggleUserStatus(employee)}><FiUserX /> Suspend</button>
                        : <button onClick={() => toggleUserStatus(employee)}><FiUserCheck /> Activate</button>}
                      <button onClick={() => openPasswordReset(employee)}><FiKey /> Reset password</button>
                      <button className="danger" onClick={() => { setRemoveConfirm(employee); setActionMenu(null); }}><FiTrash2 /> Remove user</button>
                      <button onClick={() => openDrawer(employee)}><FiUser /> View profile</button>
                    </div>}
                  </div>
                </td>
              </tr>;
            })}
          </tbody>
        </table>
        {visible.length === 0 && <div className="users-empty"><FiUsers /><strong>{employees.length === 0 ? 'No users yet' : 'No users match your search'}</strong><p>{employees.length === 0 ? 'Create the first employee account to begin.' : 'Clear filters or try a different search term.'}</p>{activeFilterCount > 0 && <button onClick={clearFilters}>Clear filters</button>}</div>}
      </div>

      <footer className="manage-pagination">
        <span>Showing {sorted.length === 0 ? 0 : (page - 1) * pageSize + 1}–{Math.min(page * pageSize, sorted.length)} of {sorted.length} users</span>
        <label>Rows per page <select value={pageSize} onChange={(event) => { setPageSize(Number(event.target.value)); setPage(1); }}><option>10</option><option>25</option><option>50</option></select></label>
        <div>{pageCount > 1 && <button disabled={page === 1} onClick={() => setPage(page - 1)}><FiChevronLeft /> Previous</button>}<span>Page <strong>{page}</strong> of {pageCount}</span>{pageCount > 1 && <button disabled={page === pageCount} onClick={() => setPage(page + 1)}>Next <FiChevronRight /></button>}</div>
      </footer>

      {drawerUser && editForm && <div className="user-drawer-backdrop" onMouseDown={(event) => event.target === event.currentTarget && setDrawerUser(null)}>
        <aside className="user-drawer" aria-label={`${drawerUser.name} details`}>
          <header><div className="user-identity"><Avatar employee={drawerUser} /><span><strong>{drawerUser.name}</strong><small>{drawerUser.email}</small></span></div><button onClick={() => setDrawerUser(null)} aria-label="Close drawer"><FiX /></button></header>
          <div className="drawer-status"><span className={`role-badge role-${drawerUser.role || 'employee'}`}>{capitalize(drawerUser.role || 'employee')}</span><span className={`account-status ${drawerUser.isActive === false ? 'inactive' : 'active'}`}><i />{drawerUser.isActive === false ? 'Inactive' : 'Active'}</span></div>
          {editing ? <div className="drawer-form">
            <FormSection title="Personal information" description="Identity and contact details used throughout the HRM.">
              <FormField label="Full name" required error={errorForEdit('name')}><input value={editForm.name} onChange={(event) => updateEditField('name', event.target.value)} onBlur={() => markEditTouched('name')} /></FormField>
              <FormField label="Email address" required error={errorForEdit('email')}><input type="email" value={editForm.email} onChange={(event) => updateEditField('email', event.target.value)} onBlur={() => markEditTouched('email')} /></FormField>
              <FormField label="Phone number" error={errorForEdit('phone')}><input value={editForm.phone || ''} onChange={(event) => updateEditField('phone', event.target.value)} onBlur={() => markEditTouched('phone')} /></FormField>
              <FormField label="Gender"><select value={editForm.gender || ''} onChange={(event) => setEditForm({ ...editForm, gender: event.target.value as Employee['gender'] })}><option value="">Not specified</option><option value="male">Male</option><option value="female">Female</option></select></FormField>
              <FormField label="Date of birth" error={errorForEdit('dateOfBirth')}><input type="date" value={(editForm.dateOfBirth || '').split('T')[0]} onChange={(event) => updateEditField('dateOfBirth', event.target.value)} onBlur={() => markEditTouched('dateOfBirth')} /></FormField>
              <FormField label="Marital status" hint="Married employees are eligible for maternity or paternity leave"><select value={editForm.maritalStatus || ''} onChange={(event) => { const picked = event.target.value as Employee['maritalStatus']; setEditForm({ ...editForm, maritalStatus: picked || undefined, anniversaryDate: picked === 'married' ? editForm.anniversaryDate : '' }); }}><option value="">Not recorded</option><option value="single">Single</option><option value="married">Married</option></select></FormField>
              {editForm.maritalStatus === 'married' && <FormField label="Anniversary date" error={errorForEdit('anniversaryDate')}><input type="date" value={(editForm.anniversaryDate || '').split('T')[0]} onChange={(event) => updateEditField('anniversaryDate', event.target.value)} onBlur={() => markEditTouched('anniversaryDate')} /></FormField>}
              <FormField label="CNIC" error={errorForEdit('cnic')}><input value={editForm.cnic || ''} onChange={(event) => updateEditField('cnic', event.target.value)} onBlur={() => markEditTouched('cnic')} /></FormField>
              <FormField label="Residential address" wide error={errorForEdit('residentialAddress')}><input value={editForm.residentialAddress || ''} onChange={(event) => updateEditField('residentialAddress', event.target.value)} onBlur={() => markEditTouched('residentialAddress')} /></FormField>
            </FormSection>
            <FormSection title="Employment details" description="Position, reporting line, access level, and account status.">
              <FormField label="Employee ID" error={errorForEdit('employeeId')}><input value={editForm.employeeId || ''} onChange={(event) => updateEditField('employeeId', event.target.value)} onBlur={() => markEditTouched('employeeId')} /></FormField>
              <FormField label="Department" error={errorForEdit('department')}><input value={editForm.department || ''} onChange={(event) => updateEditField('department', event.target.value)} onBlur={() => markEditTouched('department')} /></FormField>
              <FormField label="Designation" error={errorForEdit('designation')}><input value={editForm.designation || ''} onChange={(event) => updateEditField('designation', event.target.value)} onBlur={() => markEditTouched('designation')} /></FormField>
              <FormField label="Date of joining" hint="Set the real hire date when backfilling an employee who predates this system." error={errorForEdit('joinDate')}><input type="date" value={(editForm.joinDate || '').split('T')[0]} onChange={(event) => updateEditField('joinDate', event.target.value)} onBlur={() => markEditTouched('joinDate')} /></FormField>
              <FormField label="Reporting manager"><select value={editForm.managerId || ''} onChange={(event) => setEditForm({ ...editForm, managerId: event.target.value ? Number(event.target.value) : null })}><option value="">No manager</option>{managers.filter((manager) => manager.id !== editForm.id).map((manager) => <option key={manager.id} value={manager.id}>{manager.name}</option>)}</select></FormField>
              <FormField label="Role" hint={editForm.isHr ? 'HR managers reach everything an admin does, except admin accounts.' : undefined}><select value={editForm.isHr ? 'hr' : (editForm.role || 'employee')} onChange={(event) => { const picked = event.target.value; setEditForm({ ...editForm, role: (picked === 'hr' ? 'manager' : picked) as UserRole, isHr: picked === 'hr' }); }}><option value="employee">Employee</option><option value="manager">Manager</option><option value="hr">HR Manager</option><option value="admin">Admin</option></select></FormField>
              <FormField label="Account status"><select value={editForm.isActive === false ? 'inactive' : 'active'} onChange={(event) => setEditForm({ ...editForm, isActive: event.target.value === 'active', status: event.target.value as Status })}><option value="active">Active</option><option value="inactive">Inactive</option></select></FormField>
            </FormSection>
            <FormSection title="Compensation and hours" description="Administrative payroll values. These fields are excluded from user-list exports.">
              <FormField label="Base salary (Rs)"><NumberInput value={editForm.baseSalary} onChange={(value) => setEditForm({ ...editForm, baseSalary: value })} /></FormField>
              <FormField label="Allowance (Rs)"><NumberInput value={editForm.allowance} onChange={(value) => setEditForm({ ...editForm, allowance: value })} /></FormField>
              <FormField label="Hourly rate (Rs)" hint={`Basic salary \u00f7 ${monthlyHours(editForm.standardHours)} working hours`}><input value={editForm.baseSalary ? hourlyRate(editForm.baseSalary, editForm.standardHours).toFixed(2) : '\u2014'} readOnly disabled /></FormField>
              <FormField label="Standard hours per day" hint="4 for a part-time contract; 8 is full time."><NumberInput value={editForm.standardHours} step="0.5" onChange={(value) => setEditForm({ ...editForm, standardHours: value })} /></FormField>
              <label className="drawer-check wide"><input type="checkbox" checked={Boolean(editForm.overtimeEligible)} onChange={(event) => setEditForm({ ...editForm, overtimeEligible: event.target.checked })} /><span><strong>Overtime eligible</strong><small>Include this employee in overtime calculations.</small></span></label>
            </FormSection>
            <FormSection title="Emergency contact" description="Used only when the employee cannot be reached directly.">
              <FormField label="Contact name" error={errorForEdit('emergencyContactName')}><input value={editForm.emergencyContactName || ''} onChange={(event) => updateEditField('emergencyContactName', event.target.value)} onBlur={() => markEditTouched('emergencyContactName')} /></FormField>
              <FormField label="Contact phone" error={errorForEdit('emergencyContactPhone')}><input value={editForm.emergencyContactPhone || ''} onChange={(event) => updateEditField('emergencyContactPhone', event.target.value)} onBlur={() => markEditTouched('emergencyContactPhone')} /></FormField>
            </FormSection>
            <FormSection title="Banking information" description="Account details used by payroll processing.">
              <FormField label="Account title" error={errorForEdit('accountTitle')}><input value={editForm.accountTitle || ''} onChange={(event) => updateEditField('accountTitle', event.target.value)} onBlur={() => markEditTouched('accountTitle')} /></FormField>
              <FormField label="Account number" error={errorForEdit('accountNumber')}><input value={editForm.accountNumber || ''} onChange={(event) => updateEditField('accountNumber', event.target.value)} onBlur={() => markEditTouched('accountNumber')} /></FormField>
              <FormField label="Bank name" error={errorForEdit('bankName')}><input value={editForm.bankName || ''} onChange={(event) => updateEditField('bankName', event.target.value)} onBlur={() => markEditTouched('bankName')} /></FormField>
              <FormField label="Branch code" error={errorForEdit('branchCode')}><input value={editForm.branchCode || ''} onChange={(event) => updateEditField('branchCode', event.target.value)} onBlur={() => markEditTouched('branchCode')} /></FormField>
            </FormSection>
            <div className="password-exclusion"><FiKey /><span><strong>Password is handled separately</strong><small>Resetting a password is a deliberate action of its own, not part of saving this form.</small></span><button type="button" onClick={() => openPasswordReset(drawerUser)}>Reset password</button></div>
          </div> : <div className="drawer-details">
            <section><h4>Personal information</h4>
              <Detail label="Email" value={drawerUser.email} />
              <Detail label="Phone" value={safe(drawerUser.phone, 'Not provided')} />
              <Detail label="Gender" value={drawerUser.gender ? capitalize(drawerUser.gender) : 'Not specified'} />
              <Detail label="Date of birth" value={formatDate(drawerUser.dateOfBirth)} />
              <Detail label="Marital status" value={drawerUser.maritalStatus ? capitalize(drawerUser.maritalStatus) : 'Not recorded'} />
              {drawerUser.maritalStatus === 'married' && <Detail label="Anniversary" value={formatDate(drawerUser.anniversaryDate)} />}
              <Detail label="CNIC" value={safe(drawerUser.cnic, 'Not provided')} />
              <Detail label="Address" value={safe(drawerUser.residentialAddress, 'Not provided')} />
            </section>
            <section><h4>Employment</h4>
              <Detail label="Employee ID" value={safe(drawerUser.employeeId, 'Not assigned')} />
              <Detail label="Department" value={safe(drawerUser.department, 'No department')} />
              <Detail label="Designation" value={safe(drawerUser.designation, 'No designation')} />
              <Detail label="Manager" value={drawerUser.managerId ? employeeMap.get(String(drawerUser.managerId))?.name || 'No manager' : 'No manager'} />
              <Detail label="Date joined" value={formatDate(drawerUser.joinDate)} />
              <Detail label="Role" value={drawerUser.isHr ? 'HR Manager' : capitalize(drawerUser.role || 'employee')} />
              <Detail label="Account status" value={drawerUser.isActive === false ? 'Inactive' : 'Active'} />
            </section>
            <section><h4>Compensation</h4>
              <Detail label="Basic salary" value={money(drawerUser.baseSalary)} />
              <Detail label="Allowance" value={money(drawerUser.allowance)} />
              <Detail label="Hourly rate" value={drawerUser.baseSalary ? money(hourlyRate(drawerUser.baseSalary, drawerUser.standardHours)) + ' \u00b7 ' + rateBasis(drawerUser.standardHours) : money(0)} />
              <Detail label="Standard hours" value={describeDay(drawerUser.standardHours)} />
              <Detail label="Overtime eligible" value={drawerUser.overtimeEligible ? 'Yes' : 'No'} />
            </section>
            <section><h4>Emergency contact</h4>
              <Detail label="Contact name" value={safe(drawerUser.emergencyContactName, 'Not provided')} />
              <Detail label="Contact phone" value={safe(drawerUser.emergencyContactPhone, 'Not provided')} />
            </section>
            <section><h4>Banking information</h4>
              <Detail label="Account title" value={safe(drawerUser.accountTitle || '', 'Not provided')} />
              <Detail label="Account number" value={safe(drawerUser.accountNumber || '', 'Not provided')} />
              <Detail label="Bank" value={safe(drawerUser.bankName || '', 'Not provided')} />
              <Detail label="Branch code" value={safe(drawerUser.branchCode || '', 'Not provided')} />
            </section>
            <Documents employee={drawerUser} />
          </div>}
          <footer>{editing ? <><button onClick={() => { setEditForm({ ...drawerUser }); setEditErrors({}); setEditTouched(new Set()); setEditing(false); }}>Cancel</button><button className="primary" onClick={saveUser} disabled={saving}>{saving ? 'Saving…' : 'Save changes'}</button></> : <button className="primary" onClick={() => { setEditForm({ ...drawerUser }); setEditErrors({}); setEditTouched(new Set()); setEditing(true); }}><FiEdit2 /> Edit user</button>}</footer>
        </aside>
      </div>}

      {removeConfirm && <div className="manage-confirm-backdrop"><div className="manage-confirm"><FiArchive /><h3>Remove user?</h3><p>This will remove <strong>{removeConfirm.name}</strong> from the HRM. Their account and related access will no longer be available.</p><footer><button onClick={() => setRemoveConfirm(null)}>Cancel</button><button className="primary" onClick={removeUser} disabled={saving}>{saving ? 'Removing…' : 'Remove user'}</button></footer></div></div>}

      {passwordTarget && (
        <div className="manage-confirm-backdrop" onMouseDown={(event) => event.target === event.currentTarget && setPasswordTarget(null)}>
          <div className="manage-confirm manage-password-reset">
            <FiKey />
            <h3>Reset password</h3>
            <p>
              Set a new password for <strong>{passwordTarget.name}</strong> ({passwordTarget.email}).
              Their current password is not needed.
            </p>
            <div className="reset-password-fields">
              <label>
                <span>New password</span>
                <div className="reset-password-input">
                  <input
                    type={showPassword ? 'text' : 'password'}
                    value={newPassword}
                    onChange={(event) => { setNewPassword(event.target.value); setPasswordError(''); }}
                    autoComplete="new-password"
                    autoFocus
                  />
                  <button
                    type="button"
                    onClick={() => setShowPassword((visible) => !visible)}
                    aria-label={showPassword ? 'Hide password' : 'Show password'}
                    title={showPassword ? 'Hide password' : 'Show password'}
                  >
                    {showPassword ? <FiEyeOff /> : <FiEye />}
                  </button>
                </div>
              </label>
              <label>
                <span>Confirm password</span>
                <div className="reset-password-input">
                  <input
                    type={showPassword ? 'text' : 'password'}
                    value={confirmPassword}
                    onChange={(event) => { setConfirmPassword(event.target.value); setPasswordError(''); }}
                    autoComplete="new-password"
                  />
                </div>
              </label>
              <small className="reset-password-rules">
                At least 8 characters, with an uppercase letter, a number, and a special character.
              </small>
              {passwordError && <span className="reset-password-error" role="alert">{passwordError}</span>}
              <small className="reset-password-rules">
                {passwordTarget.name.split(' ')[0]} is notified that their password changed. It is
                not emailed — pass it on yourself.
              </small>
            </div>
            <footer>
              <button onClick={() => setPasswordTarget(null)}>Cancel</button>
              <button
                className="primary"
                onClick={submitPasswordReset}
                disabled={saving || !newPassword || !confirmPassword}
              >
                {saving ? 'Updating…' : 'Set password'}
              </button>
            </footer>
          </div>
        </div>
      )}
    </section>
  );
};

const Sortable = ({ label, sortKey, current, onSort }: { label: string; sortKey: SortKey; current: { key: SortKey; direction: SortDirection }; onSort: (key: SortKey) => void }) => (
  <th><button className={current.key === sortKey ? 'sorted' : ''} onClick={() => onSort(sortKey)}>{label}{current.key === sortKey && <span>{current.direction === 'asc' ? '↑' : '↓'}</span>}</button></th>
);
const Avatar = ({ employee, small = false }: { employee: Employee; small?: boolean }) => {
  const [imageFailed, setImageFailed] = useState(false);
  return <span className={`manage-avatar${small ? ' small' : ''}`}>{employee.profilePictureUrl && !imageFailed ? <img src={employee.profilePictureUrl} alt={`${employee.name} profile`} onError={() => setImageFailed(true)} /> : initials(employee.name)}</span>;
};
const money = (value?: number) => `Rs ${(value ?? 0).toLocaleString('en-PK', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

const DOCUMENTS: Array<{ key: 'profilePictureUrl' | 'linkedinWelcomePictureUrl' | 'cnicFrontUrl' | 'cnicBackUrl'; label: string; note: string }> = [
  { key: 'profilePictureUrl', label: 'Profile photo', note: 'Used across the HRM' },
  { key: 'linkedinWelcomePictureUrl', label: 'LinkedIn welcome image', note: 'For the welcome announcement' },
  { key: 'cnicFrontUrl', label: 'CNIC — front', note: 'National ID, front side' },
  { key: 'cnicBackUrl', label: 'CNIC — back', note: 'National ID, back side' },
];

const Documents = ({ employee }: { employee: Employee }) => {
  // Each held document is fetched once with the caller's token and kept as an
  // object URL. View and Download are then plain anchors pointing at bytes the
  // browser already has — no second request that would arrive unauthenticated,
  // and no popup blocked for firing after an await.
  const [files, setFiles] = useState<Partial<Record<string, string>>>({});
  const [failed, setFailed] = useState<Partial<Record<string, boolean>>>({});

  useEffect(() => {
    let cancelled = false;
    const created: string[] = [];
    setFiles({});
    setFailed({});

    void (async () => {
      for (const item of DOCUMENTS) {
        const url = employee[item.key] as string | undefined;
        if (!url) continue;
        try {
          const blob = await fetchDocumentBlob(url);
          if (cancelled) return;
          const objectUrl = URL.createObjectURL(blob);
          created.push(objectUrl);
          setFiles((current) => ({ ...current, [item.key]: objectUrl }));
        } catch {
          if (!cancelled) setFailed((current) => ({ ...current, [item.key]: true }));
        }
      }
    })();

    return () => {
      cancelled = true;
      created.forEach(URL.revokeObjectURL);
    };
  }, [employee]);

  const held = DOCUMENTS.filter((item) => Boolean(employee[item.key]));
  return <section className="drawer-documents">
    <h4>Documents <span>{held.length} of {DOCUMENTS.length}</span></h4>
    {DOCUMENTS.map((item) => {
      const url = employee[item.key] as string | undefined;
      const objectUrl = files[item.key];
      const isImage = !/\.pdf($|\?)/i.test(url || '');
      return <div key={item.key} className={`drawer-document${url ? '' : ' is-missing'}`}>
        <i>{objectUrl && isImage ? <img src={objectUrl} alt="" loading="lazy" /> : <FiFileText />}</i>
        <span>
          <strong>{item.label}</strong>
          <small>{!url ? 'Not uploaded' : failed[item.key] ? 'Could not be loaded' : objectUrl ? item.note : 'Loading…'}</small>
        </span>
        {url && objectUrl && <div className="drawer-document-actions">
          <a href={objectUrl} target="_blank" rel="noopener noreferrer">View</a>
          <a href={objectUrl} download={documentFilename(url, employee.name, item.key.replace('Url', ''))}><FiDownload /> Download</a>
        </div>}
      </div>;
    })}
  </section>;
};

const Detail = ({ label, value }: { label: string; value: string }) => <div className="drawer-detail"><span>{label}</span><strong>{value}</strong></div>;
const FormSection = ({ title, description, children }: { title: string; description: string; children: React.ReactNode }) => <fieldset className="drawer-form-section"><legend>{title}</legend><p>{description}</p><div className="drawer-form-grid">{children}</div></fieldset>;
const FormField = ({ label, required = false, wide = false, hint, error, children }: { label: string; required?: boolean; wide?: boolean; hint?: string; error?: string; children: React.ReactNode }) => <label className={wide ? 'wide' : ''}><span>{label}{required && <b aria-hidden="true"> *</b>}</span>{children}{error ? <small className="field-error" role="alert">{error}</small> : hint && <small className="field-hint">{hint}</small>}</label>;
const NumberInput = ({ value, onChange, step = '1' }: { value?: number; onChange: (value: number | undefined) => void; step?: string }) => <input type="number" min="0" step={step} value={value ?? ''} onChange={(event) => onChange(event.target.value === '' ? undefined : Number(event.target.value))} />;
const clean = (value: unknown) => typeof value === 'string' && value.trim().toLowerCase() !== 'string' ? value.trim() : '';
const safe = (value: unknown, fallback = 'Not provided') => clean(value) || fallback;
const unique = (values: string[]) => Array.from(new Set(values.filter(Boolean))).sort();
const includesFilter = (selected: string[], value: string) => selected.length === 0 || selected.includes(value);
const capitalize = (value: string) => value ? value.charAt(0).toUpperCase() + value.slice(1) : value;
const initials = (name: string) => name.split(/\s+/).slice(0, 2).map((part) => part[0]).join('').toUpperCase();
const toggleSet = (current: Set<string>, value: string) => {
  const next = new Set(current);
  if (next.has(value)) next.delete(value);
  else next.add(value);
  return next;
};
const readStored = <T,>(key: string, fallback: T): T => { try { return JSON.parse(localStorage.getItem(key) || '') as T; } catch { return fallback; } };
const labelFor = (options: Array<{ value: string; label: string }>, value: string) => options.find((option) => option.value === value)?.label || value;
const columnLabel = (value: string) => ({ employeeId: 'Employee ID', designation: 'Designation', joinDate: 'Date joined' }[value] || value);
const formatDate = (value?: string) => value ? new Intl.DateTimeFormat('en-PK', { day: '2-digit', month: 'short', year: 'numeric' }).format(new Date(value)) : 'Not available';
const csvCell = (value: unknown) => `"${String(value ?? '').replaceAll('"', '""')}"`;

export default ManageUsersPanel;
