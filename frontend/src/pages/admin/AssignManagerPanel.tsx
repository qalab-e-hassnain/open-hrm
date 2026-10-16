import { useMemo, useState } from 'react';
import {
  FiAlertTriangle,
  FiCheck,
  FiChevronDown,
  FiSearch,
  FiUserCheck,
  FiUsers,
  FiX,
} from 'react-icons/fi';
import { employeeService } from '../../services/employeeService';
import type { Employee } from '../../types';
import './AssignManagerPanel.css';

interface AssignManagerPanelProps {
  employees: Employee[];
  managers: Employee[];
  onAssigned: () => Promise<void> | void;
}

type AssignmentResult = {
  completed: number;
  failed: number;
  failedNames: string[];
};

const AssignManagerPanel = ({ employees, managers, onAssigned }: AssignManagerPanelProps) => {
  const [query, setQuery] = useState('');
  const [department, setDepartment] = useState('');
  const [role, setRole] = useState('');
  const [status, setStatus] = useState('active');
  const [currentManager, setCurrentManager] = useState('');
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [showSelectedOnly, setShowSelectedOnly] = useState(false);
  const [managerQuery, setManagerQuery] = useState('');
  const [managerId, setManagerId] = useState('');
  const [showConfirm, setShowConfirm] = useState(false);
  const [isAssigning, setIsAssigning] = useState(false);
  const [result, setResult] = useState<AssignmentResult | null>(null);
  const [undoSnapshot, setUndoSnapshot] = useState<Array<{ employeeId: string; managerId: string | null }>>([]);
  const [reason, setReason] = useState('');

  // Managers as well as employees. The assignable list deliberately excludes
  // admins (you do not reassign an admin from this screen), so looking a
  // manager up in it alone failed for everyone reporting to one — this panel
  // said "No manager assigned" for three people whose manager was recorded
  // perfectly well, and the user list two tabs over showed him by name.
  const employeeMap = useMemo(() => {
    const everyone = new Map<string, Employee>();
    for (const person of [...employees, ...managers]) everyone.set(person.id, person);
    return everyone;
  }, [employees, managers]);
  const departments = useMemo(
    () => Array.from(new Set(employees.map((employee) => cleanValue(employee.department, '')).filter(Boolean))).sort(),
    [employees],
  );

  const selectedEmployees = useMemo(
    () => employees.filter((employee) => selectedIds.has(employee.id)),
    [employees, selectedIds],
  );

  const filteredEmployees = useMemo(() => {
    const term = query.trim().toLowerCase();
    return employees.filter((employee) => {
      if (showSelectedOnly && !selectedIds.has(employee.id)) return false;
      if (department && employee.department !== department) return false;
      if (role && employee.role !== role) return false;
      if (status === 'active' && employee.isActive === false) return false;
      if (status === 'inactive' && employee.isActive !== false) return false;
      if (currentManager === 'none' && employee.managerId) return false;
      if (currentManager && currentManager !== 'none' && String(employee.managerId || '') !== currentManager) return false;
      if (!term) return true;
      return [
        employee.name,
        employee.employeeId,
        employee.email,
        employee.department,
        employee.designation,
      ].some((value) => value?.toLowerCase().includes(term));
    });
  }, [currentManager, department, employees, query, role, selectedIds, showSelectedOnly, status]);

  const availableManagers = useMemo(() => {
    const term = managerQuery.trim().toLowerCase();
    return managers.filter((manager) => {
      if (selectedIds.has(manager.id) || manager.isActive === false) return false;
      return !term || [manager.name, manager.employeeId, manager.department, manager.designation]
        .some((value) => value?.toLowerCase().includes(term));
    });
  }, [managerQuery, managers, selectedIds]);

  const proposedManager = managers.find((manager) => manager.id === managerId);
  const activeFilterCount = [department, role, status !== 'active' ? status : '', currentManager].filter(Boolean).length;
  const createsCircularRelationship = (employee: Employee) => {
    if (!managerId) return false;
    let current = employeeMap.get(managerId);
    const visited = new Set<string>();
    while (current && !visited.has(current.id)) {
      if (current.id === employee.id) return true;
      visited.add(current.id);
      current = current.managerId ? employeeMap.get(String(current.managerId)) : undefined;
    }
    return false;
  };

  const previewRows = selectedEmployees.map((employee) => {
    const current = employee.managerId ? employeeMap.get(String(employee.managerId)) : undefined;
    const selfAssignment = employee.id === managerId;
    const circular = createsCircularRelationship(employee);
    const unchanged = String(employee.managerId || '') === managerId;
    const statusText = selfAssignment ? 'Conflict' : circular ? 'Conflict' : unchanged ? 'No change' : 'Will change';
    return { employee, current, statusText, conflict: selfAssignment || circular, unchanged };
  });

  const affectedRows = previewRows.filter((row) => !row.unchanged && !row.conflict);
  const conflictRows = previewRows.filter((row) => row.conflict);
  const canAssign = selectedEmployees.length > 0 && Boolean(proposedManager) && affectedRows.length > 0 && conflictRows.length === 0 && !isAssigning;

  const toggleEmployee = (id: string) => {
    setResult(null);
    setSelectedIds((previous) => {
      const next = new Set(previous);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const toggleVisible = () => {
    const eligibleVisible = filteredEmployees.filter((employee) => employee.isActive !== false);
    const allSelected = eligibleVisible.length > 0 && eligibleVisible.every((employee) => selectedIds.has(employee.id));
    setSelectedIds((previous) => {
      const next = new Set(previous);
      eligibleVisible.forEach((employee) => allSelected ? next.delete(employee.id) : next.add(employee.id));
      return next;
    });
  };

  const applyAssignments = async () => {
    if (!canAssign || !proposedManager) return;
    setIsAssigning(true);
    setResult(null);
    const snapshot = affectedRows.map(({ employee }) => ({
      employeeId: employee.id,
      managerId: employee.managerId ? String(employee.managerId) : null,
    }));
    const outcomes = await Promise.allSettled(
      affectedRows.map(({ employee }) => employeeService.assignManager(employee.id, proposedManager.id)),
    );
    const failedNames = outcomes.flatMap((outcome, index) =>
      outcome.status === 'rejected' ? [affectedRows[index].employee.name] : [],
    );
    const completed = outcomes.length - failedNames.length;
    setUndoSnapshot(completed ? snapshot.filter((_, index) => outcomes[index].status === 'fulfilled') : []);
    setResult({ completed, failed: failedNames.length, failedNames });
    setShowConfirm(false);
    setIsAssigning(false);
    await onAssigned();
  };

  const undoAssignments = async () => {
    if (!undoSnapshot.length) return;
    setIsAssigning(true);
    await Promise.allSettled(
      undoSnapshot.map((item) => employeeService.assignManager(item.employeeId, item.managerId)),
    );
    setUndoSnapshot([]);
    setResult(null);
    setIsAssigning(false);
    await onAssigned();
  };

  const clearSelection = () => {
    setSelectedIds(new Set());
    setManagerId('');
    setResult(null);
  };

  return (
    <section className="assign-manager-workspace">
      <div className="assignment-employee-panel">
        <header>
          <div><h3>Select employees</h3><p>Choose one or more employees whose reporting manager will change.</p></div>
          <strong>{selectedIds.size} selected</strong>
        </header>

        <label className="assignment-search">
          <FiSearch aria-hidden="true" />
          <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search by name, ID, email, department, or designation" />
          {query && <button type="button" onClick={() => setQuery('')} aria-label="Clear search"><FiX /></button>}
        </label>

        <div className="assignment-filters">
          <FilterSelect label="Department" value={department} onChange={setDepartment} options={departments.map((value) => ({ value, label: value }))} />
          <FilterSelect label="Role" value={role} onChange={setRole} options={[
            { value: 'employee', label: 'Employee' },
            { value: 'manager', label: 'Manager' },
          ]} />
          <FilterSelect label="Status" value={status} onChange={setStatus} options={[
            { value: 'active', label: 'Active' },
            { value: 'inactive', label: 'Inactive' },
          ]} />
          <FilterSelect label="Current manager" value={currentManager} onChange={setCurrentManager} options={[
            { value: 'none', label: 'No manager' },
            ...managers.map((manager) => ({ value: manager.id, label: manager.name })),
          ]} />
          {activeFilterCount > 0 && <button type="button" className="clear-filter-button" onClick={() => { setDepartment(''); setRole(''); setStatus('active'); setCurrentManager(''); }}>Clear {activeFilterCount} filter{activeFilterCount === 1 ? '' : 's'}</button>}
        </div>

        <div className="selection-toolbar">
          <label><input type="checkbox" checked={filteredEmployees.length > 0 && filteredEmployees.filter((employee) => employee.isActive !== false).every((employee) => selectedIds.has(employee.id))} onChange={toggleVisible} /> Select all visible</label>
          {selectedIds.size > 0 && <label><input type="checkbox" checked={showSelectedOnly} onChange={(event) => setShowSelectedOnly(event.target.checked)} /> Show selected only</label>}
          {selectedIds.size > 0 && <button type="button" onClick={clearSelection}>Clear all</button>}
        </div>

        <div className="assignment-employee-list">
          {filteredEmployees.length === 0 ? (
            <div className="assignment-empty"><FiUsers /><strong>No employees found</strong><span>Try clearing or changing the filters.</span></div>
          ) : filteredEmployees.map((employee) => {
            const inactive = employee.isActive === false;
            const current = employee.managerId ? employeeMap.get(String(employee.managerId)) : undefined;
            return (
              <label key={employee.id} className={`assignment-employee-row${selectedIds.has(employee.id) ? ' selected' : ''}${inactive ? ' disabled' : ''}`}>
                <input type="checkbox" checked={selectedIds.has(employee.id)} onChange={() => toggleEmployee(employee.id)} disabled={inactive} />
                <span className="assignment-avatar">{initials(employee.name)}</span>
                <span className="assignment-person">
                  <strong>{employee.name}</strong>
                  <small>{cleanValue(employee.employeeId, 'No employee ID')} · {cleanValue(employee.designation, 'No designation')}</small>
                  <small>{inactive ? 'Inactive account · Cannot be assigned' : `Reports to: ${current?.name || 'No manager assigned'}`}</small>
                </span>
                <span className="assignment-department">{cleanValue(employee.department, 'No department')}</span>
              </label>
            );
          })}
        </div>
      </div>

      <div className="assignment-detail-panel">
        <header className="assignment-detail-header">
          <div><h3>Assign reporting manager</h3><p>{selectedEmployees.length} employee{selectedEmployees.length === 1 ? '' : 's'} selected</p></div>
          {selectedEmployees.length > 0 && (
            <div className="selected-chips">
              {selectedEmployees.slice(0, 4).map((employee) => (
                <button key={employee.id} type="button" onClick={() => toggleEmployee(employee.id)}><span>{initials(employee.name)}</span>{employee.name}<FiX /></button>
              ))}
              {selectedEmployees.length > 4 && <small>+{selectedEmployees.length - 4} more</small>}
            </div>
          )}
        </header>

        <div className="manager-picker">
          <label>Reporting manager <b>*</b></label>
          <div className="manager-search"><FiSearch /><input value={managerQuery} onChange={(event) => setManagerQuery(event.target.value)} placeholder="Search managers by name or employee ID" /></div>
          <div className="manager-results">
            {availableManagers.slice(0, 5).map((manager) => {
              const count = employees.filter((employee) => String(employee.managerId || '') === manager.id).length;
              return (
                <button key={manager.id} type="button" className={managerId === manager.id ? 'selected' : ''} onClick={() => setManagerId(manager.id)}>
                  <span className="assignment-avatar">{initials(manager.name)}</span>
                  <span><strong>{manager.name}</strong><small>{cleanValue(manager.designation, 'Manager')} · {cleanValue(manager.department, 'No department')}</small></span>
                  <em>{count} direct report{count === 1 ? '' : 's'}</em>
                  {managerId === manager.id && <FiCheck />}
                </button>
              );
            })}
            {availableManagers.length === 0 && <p>No eligible managers match this search.</p>}
          </div>
        </div>

        <div className="assignment-meta-grid">
          <label><span>Effective date</span><input type="date" value={today()} min={today()} readOnly /><small>Applied immediately · Pakistan Standard Time</small></label>
          <label><span>Reason for change <em>Optional</em></span><select value={reason} onChange={(event) => setReason(event.target.value)}><option value="">Select reason</option><option>Reorganization</option><option>Promotion</option><option>Manager departure</option><option>Temporary assignment</option><option>Correction</option><option>Other</option></select></label>
        </div>

        {selectedEmployees.length > 0 && (
          <section className="reporting-summary">
            <h4>Current reporting summary</h4>
            <div>
              {Array.from(new Set(selectedEmployees.map((employee) => String(employee.managerId || 'none')))).map((id) => {
                const manager = id === 'none' ? undefined : employeeMap.get(id);
                const count = selectedEmployees.filter((employee) => String(employee.managerId || 'none') === id).length;
                return <span key={id}><FiUsers /><strong>{count}</strong> {count === 1 ? 'employee reports' : 'employees report'} to {manager?.name || 'no manager'}</span>;
              })}
            </div>
          </section>
        )}

        <section className="assignment-preview">
          <div><h4>Preview changes</h4><span>{affectedRows.length} will change · {previewRows.filter((row) => row.unchanged).length} unchanged</span></div>
          {previewRows.length === 0 ? (
            <div className="assignment-preview-empty"><FiUserCheck /><strong>No changes to preview</strong><span>Select at least one employee and a manager.</span></div>
          ) : (
            <div className="assignment-table-wrap">
              <table>
                <thead><tr><th>Employee</th><th>Current manager</th><th>New manager</th><th>Effective date</th><th>Status</th></tr></thead>
                <tbody>{previewRows.map((row) => (
                  <tr key={row.employee.id} className={row.conflict ? 'conflict' : ''}>
                    <td>{row.employee.name}</td><td>{row.current?.name || 'No manager'}</td><td>{proposedManager?.name || 'Not selected'}</td><td>{today()}</td><td><span className={`impact-${row.statusText.toLowerCase().replace(' ', '-')}`}>{row.statusText}</span></td>
                  </tr>
                ))}</tbody>
              </table>
            </div>
          )}
        </section>

        {conflictRows.length > 0 && <div className="assignment-error" role="alert"><FiAlertTriangle /> This assignment would create an invalid or circular reporting relationship.</div>}
        {selectedEmployees.length > 0 && (
          <div className="workflow-warning"><FiAlertTriangle /><span>This change will update leave, attendance, reimbursement, and performance approval routing for {affectedRows.length} employee{affectedRows.length === 1 ? '' : 's'}.</span></div>
        )}
        {result && (
          <div className={`assignment-result${result.failed ? ' partial' : ''}`} role="status">
            <FiCheck /><span><strong>{result.completed} assignment{result.completed === 1 ? '' : 's'} completed.</strong>{result.failed ? ` ${result.failed} failed: ${result.failedNames.join(', ')}.` : ''}</span>
            {undoSnapshot.length > 0 && <button type="button" onClick={undoAssignments} disabled={isAssigning}>Undo</button>}
          </div>
        )}

        <footer className="assignment-actions">
          <span><strong>{selectedEmployees.length}</strong> employee{selectedEmployees.length === 1 ? '' : 's'} selected · {affectedRows.length} will change</span>
          <div><button type="button" className="assignment-cancel" onClick={clearSelection}>Cancel</button>
          <button type="button" className="assignment-primary" disabled={!canAssign} onClick={() => setShowConfirm(true)}>{canAssign && <FiUserCheck />} Assign manager</button></div>
        </footer>
      </div>

      {showConfirm && proposedManager && (
        <div className="assignment-confirm-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget) setShowConfirm(false); }}>
          <div className="assignment-confirm" role="dialog" aria-modal="true" aria-labelledby="assignment-confirm-title">
            <button type="button" onClick={() => setShowConfirm(false)} aria-label="Close"><FiX /></button>
            <span className="confirm-icon"><FiUserCheck /></span>
            <p>Confirm reporting change</p>
            <h3 id="assignment-confirm-title">Assign {proposedManager.name} as manager?</h3>
            <div><strong>{affectedRows.length}</strong> reporting relationship{affectedRows.length === 1 ? '' : 's'} will change immediately.</div>
            {previewRows.some((row) => row.unchanged) && <small>{previewRows.filter((row) => row.unchanged).length} selected employee{previewRows.filter((row) => row.unchanged).length === 1 ? '' : 's'} already report to this manager.</small>}
            <footer><button type="button" onClick={() => setShowConfirm(false)}>Go back</button><button type="button" onClick={applyAssignments} disabled={isAssigning}>{isAssigning ? 'Assigning…' : 'Confirm assignment'}</button></footer>
          </div>
        </div>
      )}
    </section>
  );
};

const FilterSelect = ({ label, value, onChange, options }: { label: string; value: string; onChange: (value: string) => void; options: Array<{ value: string; label: string }> }) => (
  <label><select value={value} onChange={(event) => onChange(event.target.value)}><option value="">{label}</option>{options.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}</select><FiChevronDown /></label>
);

const initials = (name: string) => name.split(/\s+/).slice(0, 2).map((part) => part[0]).join('').toUpperCase();
const today = () => new Date().toISOString().slice(0, 10);
const cleanValue = (value: string | undefined, fallback: string) => {
  const cleaned = value?.trim();
  return !cleaned || cleaned.toLowerCase() === 'string' ? fallback : cleaned;
};

export default AssignManagerPanel;
