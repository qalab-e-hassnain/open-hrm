import { useState, useEffect } from 'react';
import { useSearchParams } from 'react-router-dom';
import {
  FiCheck, FiX, FiRefreshCw, FiTrash2, FiPlus, FiClock, FiGrid, FiFileText,
  FiEdit2, FiMessageSquare, FiPaperclip, FiLock} from 'react-icons/fi';
import Card from '../../components/common/Card';
import Input from '../../components/common/Input';
import Select from '../../components/common/Select';
import Button from '../../components/common/Button';
import { employeeService } from '../../services/employeeService';
import { payrollService, type CreatePayrollData } from '../../services/payrollService';
import {
  payslipService, readApiError, DISPUTE_CATEGORIES,
  type PayslipDispute, type DisputeStatus,
} from '../../services/payslipService';
import PayrollSheet from './PayrollSheet';
import TaxCertificate from '../employee/TaxCertificate';
import { type Employee, type PayrollRecord, type OvertimeRecord } from '../../types';
import './PayrollReimbursement.css';

type TabType = 'payroll' | 'sheet' | 'overtime' | 'certificate' | 'queries';

const MONTHS = [
  'January', 'February', 'March', 'April', 'May', 'June',
  'July', 'August', 'September', 'October', 'November', 'December',
];

const now = new Date();

// Number(), not parseFloat(): parseFloat("2732-232837") silently reads as
// 2732 and submits it as if that's what was typed, hiding the typo entirely.
// Number() returns NaN for anything that isn't a clean number, so garbled
// input gets caught here instead of reaching the backend as a wrong value.
const validateMoney = (raw: string, label: string, { required = false, positive = false } = {}): string => {
  const trimmed = raw.trim();
  if (!trimmed) return required ? `${label} is required.` : '';
  const value = Number(trimmed);
  if (!Number.isFinite(value)) return `${label} must be a valid number.`;
  if (positive ? value <= 0 : value < 0) return `${label} must be ${positive ? 'greater than 0' : '0 or more'}.`;
  return '';
};

const PayrollReimbursement = () => {
  // A notification about a payslip query links here with ?tab=queries, so
  // clicking it lands directly on the right tab instead of just the page.
  const [searchParams] = useSearchParams();
  const initialTab = (searchParams.get('tab') as TabType) || 'payroll';
  const [activeTab, setActiveTab] = useState<TabType>(initialTab);
  const [employees, setEmployees] = useState<Employee[]>([]);
  const [payrolls, setPayrolls] = useState<PayrollRecord[]>([]);
  const [overtime, setOvertime] = useState<OvertimeRecord[]>([]);
  const [disputes, setDisputes] = useState<PayslipDispute[]>([]);
  const [isLoadingDisputes, setIsLoadingDisputes] = useState(false);
  const [isLoading, setIsLoading] = useState(true);
  const [isSaving, setIsSaving] = useState(false);

  const [form, setForm] = useState<{
    employeeId: string; month: number; year: number;
    baseSalary: string; allowances: string; bonuses: string; arrears: string; deductions: string;
    unapprovedLeaveFine: string; notes: string;
    isHistorical: boolean; tax: string; applyIncrement: boolean;
  }>({
    employeeId: '', month: now.getMonth() + 1, year: now.getFullYear(),
    baseSalary: '', allowances: '', bonuses: '', arrears: '', deductions: '', unapprovedLeaveFine: '', notes: '',
    isHistorical: false, tax: '', applyIncrement: false,
  });

  const [errors, setErrors] = useState<Record<string, string>>({});
  const [formError, setFormError] = useState('');

  const [otFilter, setOtFilter] = useState<{ month: number; year: number }>({
    month: now.getMonth() + 1, year: now.getFullYear(),
  });

  // An open edit. `reason` is mandatory: correcting a payslip issues a new
  // version that carries it, so there is always a record of why a figure the
  // employee may already have seen was changed.
  const [editing, setEditing] = useState<{
    record: PayrollRecord;
    baseSalary: string; allowances: string; bonuses: string;
    arrears: string; deductions: string; unapprovedLeaveFine: string; notes: string; reason: string;
    tax: string; taxIsManual: boolean;
  } | null>(null);
  const [editError, setEditError] = useState('');
  const [editErrors, setEditErrors] = useState<Record<string, string>>({});

  const nameFor = (employeeId: number) =>
    employees.find((e) => e.id === String(employeeId))?.name || `Employee #${employeeId}`;

  const money = (n: number) => `Rs ${(n || 0).toLocaleString('en-US', { maximumFractionDigits: 2 })}`;

  useEffect(() => { loadBase(); loadDisputes(); }, []);
  useEffect(() => { if (activeTab === 'overtime') loadOvertime(); }, [activeTab]);

  const loadBase = async () => {
    try {
      setIsLoading(true);
      const [emps, pays] = await Promise.all([
        employeeService.getAllUsers().catch(() => []),
        payrollService.listPayrolls().catch(() => []),
      ]);
      setEmployees(emps);
      setPayrolls(pays);
    } finally {
      setIsLoading(false);
    }
  };

  // Loaded eagerly (not gated to the Queries tab) so the tab button's open-
  // count badge is accurate the moment the page loads, not just after it's
  // clicked into.
  const loadDisputes = async () => {
    setIsLoadingDisputes(true);
    try {
      setDisputes(await payslipService.listDisputes());
    } catch {
      setDisputes([]);
    } finally {
      setIsLoadingDisputes(false);
    }
  };

  const loadOvertime = async () => {
    try {
      const data = await payrollService.listOvertime(otFilter.month, otFilter.year);
      setOvertime(data);
    } catch {
      setOvertime([]);
    }
  };

  /** Prefill pay fields from the employee's saved defaults so the admin doesn't
   *  retype them each period. Both stay editable for one-off adjustments. */
  const handleSelectEmployee = (employeeId: string) => {
    const employee = employees.find((e) => e.id === employeeId);
    setForm((prev) => ({
      ...prev,
      employeeId,
      baseSalary: employee?.baseSalary ? String(employee.baseSalary) : '',
      allowances: employee?.allowance ? String(employee.allowance) : '',
    }));
  };

  /** Validates every field in the Generate Payslip form, setting one inline
   *  message per invalid field. Returns whether the form is submittable. */
  const validateCreateForm = (): boolean => {
    const next: Record<string, string> = {};
    if (!form.employeeId) next.employeeId = 'Select an employee.';
    next.baseSalary = validateMoney(form.baseSalary, 'Base salary', { required: true, positive: true });
    next.allowances = validateMoney(form.allowances, 'Allowances');
    next.bonuses = validateMoney(form.bonuses, 'Bonuses');
    next.arrears = validateMoney(form.arrears, 'Arrears');
    next.deductions = validateMoney(form.deductions, 'Deductions');
    const fineRaw = form.unapprovedLeaveFine.trim();
    if (fineRaw) {
      next.unapprovedLeaveFine = validateMoney(form.unapprovedLeaveFine, 'Unapproved leave fine')
        || (Number(fineRaw) % 5000 !== 0 ? 'Must be Rs 5,000 per day (for example 5,000 or 10,000).' : '');
    }
    if (form.isHistorical) next.tax = validateMoney(form.tax, 'Tax deducted', { required: true });
    Object.keys(next).forEach((key) => { if (!next[key]) delete next[key]; });
    setErrors(next);
    return Object.keys(next).length === 0;
  };

  const handleCreatePayroll = async () => {
    setFormError('');
    if (!validateCreateForm()) return;
    const payload: CreatePayrollData = {
      employeeId: Number(form.employeeId),
      month: form.month,
      year: form.year,
      baseSalary: Number(form.baseSalary),
      allowances: form.allowances.trim() ? Number(form.allowances) : 0,
      bonuses: form.bonuses.trim() ? Number(form.bonuses) : 0,
      arrears: form.arrears.trim() ? Number(form.arrears) : 0,
      deductions: form.deductions.trim() ? Number(form.deductions) : 0,
      unapprovedLeaveFine: form.unapprovedLeaveFine.trim() ? Number(form.unapprovedLeaveFine) : 0,
      notes: form.notes || undefined,
      isHistorical: form.isHistorical,
      tax: form.isHistorical ? Number(form.tax) : undefined,
      applyIncrement: form.applyIncrement,
    };
    try {
      setIsSaving(true);
      await payrollService.createPayroll(payload);
      const wasIncrement = form.applyIncrement;
      setForm({ ...form, employeeId: '', baseSalary: '', allowances: '', bonuses: '', arrears: '', deductions: '', unapprovedLeaveFine: '', notes: '', tax: '', applyIncrement: false });
      setErrors({});
      await loadBase();
      alert(
        form.isHistorical
          ? 'Historical payslip generated and the employee was notified.'
          : wasIncrement
          ? "Payslip generated, the employee was notified, and their salary was updated (increment). Tax re-projects from this month onward."
          : 'Payslip generated and the employee was notified. Approved reimbursements + overtime for the period were pulled in automatically.'
      );
    } catch (err: any) {
      setFormError(err.message || 'Could not generate this payslip. Please check the figures and try again.');
    } finally {
      setIsSaving(false);
    }
  };

  // Issued means the employee has been told what they will be paid, so the
  // server refuses an edit or a delete. Saving still works — it goes through
  // /correct, which supersedes this payslip with a new revision and a reason
  // and leaves the original on the record.
  const isIssued = (status?: string) => status === 'processed' || status === 'paid';

  const openEdit = (record: PayrollRecord) => {
    setEditError('');
    setEditErrors({});
    setEditing({
      record,
      baseSalary: String(record.baseSalary || ''),
      allowances: String(record.allowances || ''),
      bonuses: String(record.bonuses || ''),
      arrears: String(record.arrears || ''),
      deductions: String(record.deductions || ''),
      unapprovedLeaveFine: String(record.unapprovedLeaveFine || ''),
      notes: record.notes || '',
      reason: '',
      tax: String(record.tax || ''),
      taxIsManual: Boolean(record.taxIsManual),
    });
  };

  /**
   * Save an edit as a corrected payslip.
   *
   * Two steps on purpose: /correct issues a NEW version stamped with the
   * reason and moves the original to CORRECTED — the employee may already have
   * downloaded it, so what they were given has to survive — and the figures are
   * then written to that new version, which the server recomputes (tax, swept-in
   * overtime and reimbursements, net pay).
   */
  const handleSaveEdit = async () => {
    if (!editing) return;
    const reason = editing.reason.trim();
    const amount = (value: string) => (value.trim() === '' ? undefined : Number(value));

    const nextErrors: Record<string, string> = {
      baseSalary: validateMoney(editing.baseSalary, 'Base salary', { positive: true }),
      allowances: validateMoney(editing.allowances, 'Allowances'),
      bonuses: validateMoney(editing.bonuses, 'Bonuses'),
      arrears: validateMoney(editing.arrears, 'Arrears'),
      deductions: validateMoney(editing.deductions, 'Deductions'),
      tax: editing.taxIsManual ? validateMoney(editing.tax, 'Tax') : '',
    };
    const fineRaw = editing.unapprovedLeaveFine.trim();
    if (fineRaw) {
      nextErrors.unapprovedLeaveFine = validateMoney(editing.unapprovedLeaveFine, 'Unapproved leave fine')
        || (Number(fineRaw) % 5000 !== 0 ? 'Must be Rs 5,000 per day (for example 5,000 or 10,000).' : '');
    }
    Object.keys(nextErrors).forEach((key) => { if (!nextErrors[key]) delete nextErrors[key]; });
    setEditErrors(nextErrors);
    if (Object.keys(nextErrors).length > 0) return;

    if (reason.length < 5) {
      setEditError('Give a reason for this edit — it is stored on the corrected payslip.');
      return;
    }
    setIsSaving(true);
    setEditError('');
    try {
      const revision = await payslipService.correct(editing.record.id, reason);
      await payrollService.updatePayroll(revision.id, {
        baseSalary: amount(editing.baseSalary),
        allowances: amount(editing.allowances),
        bonuses: amount(editing.bonuses),
        arrears: amount(editing.arrears),
        deductions: amount(editing.deductions),
        unapprovedLeaveFine: amount(editing.unapprovedLeaveFine),
        notes: editing.notes.trim() || undefined,
        // Sending tax pins it; taxIsManual:false hands it back to the engine.
        ...(editing.taxIsManual ? { tax: amount(editing.tax) } : { taxIsManual: false }),
      });
      await payslipService.publish(revision.id);
      await loadBase();
      setEditing(null);
    } catch (err: any) {
      setEditError(err.message || 'Could not save this edit.');
    } finally {
      setIsSaving(false);
    }
  };

  const handleMarkPaid = async (id: number) => {
    if (!window.confirm('Mark this payroll as paid? It can no longer be edited.')) return;
    try {
      const updated = await payrollService.markPaid(id);
      setPayrolls((prev) => prev.map((p) => (p.id === id ? updated : p)));
    } catch (err: any) { alert(err.message || 'Failed to mark paid'); }
  };

  const handleDeletePayroll = async (id: number) => {
    if (!window.confirm('Delete this payroll record?')) return;
    try {
      await payrollService.deletePayroll(id);
      setPayrolls((prev) => prev.filter((p) => p.id !== id));
    } catch (err: any) { alert(err.message || 'Failed to delete'); }
  };

  const handleOvertimeDecision = async (id: number, status: 'approved' | 'rejected') => {
    try {
      const updated = await payrollService.approveOvertime(id, status);
      setOvertime((prev) => prev.map((o) => (o.id === id ? updated : o)));
    } catch (err: any) { alert(err.message || `Failed to ${status} overtime`); }
  };

  const [respondingId, setRespondingId] = useState<number | null>(null);
  const [responseDraft, setResponseDraft] = useState('');
  const [disputeSaving, setDisputeSaving] = useState(false);
  const [disputeError, setDisputeError] = useState('');

  const openRespond = (dispute: PayslipDispute) => {
    setDisputeError('');
    setResponseDraft(dispute.response || '');
    setRespondingId(dispute.id);
  };

  const handleRespondDispute = async (id: number, nextStatus: DisputeStatus) => {
    setDisputeSaving(true);
    setDisputeError('');
    try {
      const updated = await payslipService.respondToDispute(id, nextStatus, responseDraft.trim() || undefined);
      setDisputes((prev) => prev.map((d) => (d.id === id ? updated : d)));
      setRespondingId(null);
      setResponseDraft('');
    } catch (err: unknown) {
      setDisputeError(readApiError(err, 'Could not save this response.'));
    } finally {
      setDisputeSaving(false);
    }
  };

  const openDisputeCount = disputes.filter((d) => d.status === 'open' || d.status === 'in_review').length;

  // Current year +1 down to 10 years back, so old records can be backfilled.
  const yearOptions = Array.from({ length: 12 }, (_, i) => now.getFullYear() + 1 - i);

  if (isLoading) {
    return <div className="payroll-reimbursement-page"><div className="content-loading"><div className="spinner" /></div></div>;
  }

  return (
    <div className="payroll-reimbursement-page">
      <Card className="content-card">
        <h2 className="pr-page-title">Payroll</h2>

        <div className="tab-buttons">
          <button className={`tab-btn ${activeTab === 'payroll' ? 'active' : ''}`} onClick={() => setActiveTab('payroll')}>
            Payslip
          </button>
          <button className={`tab-btn ${activeTab === 'sheet' ? 'active' : ''}`} onClick={() => setActiveTab('sheet')}>
            <FiGrid size={16} /> Payroll Sheet
          </button>
          <button className={`tab-btn ${activeTab === 'overtime' ? 'active' : ''}`} onClick={() => setActiveTab('overtime')}>
            <FiClock size={16} /> Overtime Approval
          </button>
          <button className={`tab-btn ${activeTab === 'certificate' ? 'active' : ''}`} onClick={() => setActiveTab('certificate')}>
            <FiFileText size={16} /> Tax Certificate
          </button>
          <button className={`tab-btn ${activeTab === 'queries' ? 'active' : ''}`} onClick={() => setActiveTab('queries')}>
            <FiMessageSquare size={16} /> Payroll Queries
            {openDisputeCount > 0 && <span className="pr-tab-badge">{openDisputeCount}</span>}
          </button>
        </div>

        {activeTab === 'sheet' && <PayrollSheet employees={employees} />}

        {activeTab === 'certificate' && <TaxCertificate />}

        {activeTab === 'queries' && (
          <div className="pr-tab">
            <h3 className="pr-section-title">Payroll Queries</h3>
            <p className="pr-hint">Issues employees reported from their payslip. Respond to update the employee-visible status.</p>

            {isLoadingDisputes ? (
              <p className="empty-state">Loading…</p>
            ) : disputes.length === 0 ? (
              <p className="empty-state">No payroll queries have been raised.</p>
            ) : (
              <div className="pr-list pr-list-scroll">
                {disputes.map((d) => (
                  <div key={d.id} className="pr-card">
                    <div className="pr-card-head">
                      <div>
                        <span className="pr-emp">{d.employee_name || `Employee #${d.employee_id}`}</span>
                        <span className="pr-period">{d.period_label || ''} · {DISPUTE_CATEGORIES.find((c) => c.value === d.category)?.label || d.category}</span>
                      </div>
                      <div className="pr-head-right">
                        <span className={`status-badge status-${d.status === 'resolved' ? 'approved' : d.status === 'rejected' ? 'rejected' : d.status === 'in_review' ? 'pending' : 'pending'}`}>
                          {d.status.replace('_', ' ')}
                        </span>
                      </div>
                    </div>

                    <p className="pr-dispute-comment">{d.comment}</p>
                    {d.attachment_url && (
                      <a className="pr-dispute-attachment" href={d.attachment_url} target="_blank" rel="noreferrer">
                        <FiPaperclip size={14} /> Supporting document
                      </a>
                    )}

                    {d.response && (
                      <div className="pr-dispute-response">
                        <span>Response{d.responder_name ? ` · ${d.responder_name}` : ''}</span>
                        <p>{d.response}</p>
                      </div>
                    )}

                    {respondingId === d.id ? (
                      <div className="pr-dispute-reply">
                        <textarea
                          rows={3}
                          value={responseDraft}
                          onChange={(e) => setResponseDraft(e.target.value)}
                          placeholder="Explain what you found or what was corrected — the employee sees this."
                        />
                        {disputeError && <p className="pr-edit-error" role="alert">{disputeError}</p>}
                        <div className="pr-actions">
                          <button className="pr-btn pr-btn-ghost" onClick={() => setRespondingId(null)} disabled={disputeSaving}>Cancel</button>
                          <button className="pr-btn pr-btn-ghost" onClick={() => handleRespondDispute(d.id, 'in_review')} disabled={disputeSaving}>
                            Mark in review
                          </button>
                          <button className="pr-btn pr-btn-danger" onClick={() => handleRespondDispute(d.id, 'rejected')} disabled={disputeSaving}>
                            Mark rejected
                          </button>
                          <button className="pr-btn pr-btn-primary" onClick={() => handleRespondDispute(d.id, 'resolved')} disabled={disputeSaving}>
                            <FiCheck size={14} /> Mark resolved
                          </button>
                        </div>
                      </div>
                    ) : (
                      <div className="pr-actions">
                        <button className="pr-btn pr-btn-ghost" onClick={() => openRespond(d)}>
                          <FiMessageSquare size={14} /> {d.response ? 'Update response' : 'Respond'}
                        </button>
                      </div>
                    )}
                  </div>
                ))}
              </div>
            )}
          </div>
        )}

        {activeTab === 'payroll' && (
          <div className="pr-tab">
            {/* Create payslip */}
            <h3 className="pr-section-title">Generate Payslip</h3>
            <div className="pr-form-grid">
              <Select
                label="Employee *"
                value={form.employeeId}
                error={errors.employeeId}
                onChange={(e) => { handleSelectEmployee(e.target.value); setErrors((cur) => ({ ...cur, employeeId: '' })); }}
                options={[
                  { value: '', label: 'Select employee' },
                  ...employees.map((emp) => ({ value: emp.id, label: emp.name })),
                ]}
              />
              <Select
                label="Month *"
                value={String(form.month)}
                onChange={(e) => setForm({ ...form, month: Number(e.target.value) })}
                options={MONTHS.map((m, i) => ({ value: String(i + 1), label: m }))}
              />
              <Select
                label="Year *"
                value={String(form.year)}
                onChange={(e) => setForm({ ...form, year: Number(e.target.value) })}
                options={yearOptions.map((y) => ({ value: String(y), label: String(y) }))}
              />
              <Input label="Base Salary (Rs) *" type="text" inputMode="decimal" value={form.baseSalary}
                error={errors.baseSalary}
                disabled={!form.applyIncrement && !form.isHistorical}
                title={!form.applyIncrement && !form.isHistorical
                  ? "Locked to the employee's current salary — tick 'Apply as increment' below to change it"
                  : undefined}
                onChange={(e) => { setForm({ ...form, baseSalary: e.target.value }); setErrors((cur) => ({ ...cur, baseSalary: '' })); }} />
              <Input label="Allowances (Rs)" type="text" inputMode="decimal" value={form.allowances}
                error={errors.allowances}
                onChange={(e) => { setForm({ ...form, allowances: e.target.value }); setErrors((cur) => ({ ...cur, allowances: '' })); }} />
              <Input label="Bonuses (Rs)" type="text" inputMode="decimal" value={form.bonuses}
                error={errors.bonuses}
                onChange={(e) => { setForm({ ...form, bonuses: e.target.value }); setErrors((cur) => ({ ...cur, bonuses: '' })); }} />
              <Input label="Arrears (Rs)" type="text" inputMode="decimal" value={form.arrears}
                error={errors.arrears}
                onChange={(e) => { setForm({ ...form, arrears: e.target.value }); setErrors((cur) => ({ ...cur, arrears: '' })); }} />
              <Input label="Deductions e.g. Loan (Rs)" type="text" inputMode="decimal" value={form.deductions}
                error={errors.deductions}
                onChange={(e) => { setForm({ ...form, deductions: e.target.value }); setErrors((cur) => ({ ...cur, deductions: '' })); }} />
              <Input label="Unapproved Leave Fine (Rs)" type="text" inputMode="decimal" value={form.unapprovedLeaveFine}
                error={errors.unapprovedLeaveFine}
                title="Enter manually at Rs 5,000 for each unapproved leave day"
                onChange={(e) => { setForm({ ...form, unapprovedLeaveFine: e.target.value }); setErrors((cur) => ({ ...cur, unapprovedLeaveFine: '' })); }} />
              <Input label="Notes" value={form.notes}
                onChange={(e) => setForm({ ...form, notes: e.target.value })} />
              {form.isHistorical && (
                <Input label="Tax Deducted (Rs) *" type="text" inputMode="decimal" value={form.tax}
                  error={errors.tax}
                  onChange={(e) => { setForm({ ...form, tax: e.target.value }); setErrors((cur) => ({ ...cur, tax: '' })); }} />
              )}
            </div>
            {formError && <p className="pr-form-error" role="alert">{formError}</p>}
            {!form.isHistorical && (
              <label className="pr-historical-toggle">
                <input
                  type="checkbox"
                  checked={form.applyIncrement}
                  onChange={(e) => {
                    const on = e.target.checked;
                    const emp = employees.find((x) => x.id === form.employeeId);
                    setForm({
                      ...form,
                      applyIncrement: on,
                      // Turning increment off re-locks the field and restores the
                      // employee's current standing salary.
                      ...(!on && emp?.baseSalary ? { baseSalary: String(emp.baseSalary) } : {}),
                    });
                  }}
                />
                <span>Apply as increment — unlocks Base Salary; the new Base Salary &amp; Allowances become this employee's salary going forward (tax re-projects automatically)</span>
              </label>
            )}
            <label className="pr-historical-toggle">
              <input
                type="checkbox"
                checked={form.isHistorical}
                onChange={(e) => setForm({ ...form, isHistorical: e.target.checked })}
              />
              <span>Historical entry — record actual figures for a period before this system</span>
            </label>
            <p className="pr-hint">
              {form.isHistorical
                ? 'Enter the ACTUAL tax that was deducted — it is stored as-is (not recomputed from slabs). Reimbursements & overtime are not swept in for historical rows.'
                : 'Approved unpaid leave is deducted automatically at the employee\u2019s own rate \u2014 (basic salary \u00f7 their monthly hours) \u00d7 their contracted day, so a part-time day costs a part-time day. Enter any unapproved-leave penalty manually above at Rs 5,000 per day. Income tax is computed on basic pay after the approved unpaid-leave deduction.'}
            </p>
            <div className="pr-form-actions">
              <Button variant="primary" onClick={handleCreatePayroll} isLoading={isSaving}>
                <FiPlus size={16} /> Generate Payslip
              </Button>
            </div>

            {/* Payroll list */}
            <h3 className="pr-section-title pr-mt">Payroll Records ({payrolls.length})</h3>
            {payrolls.length === 0 ? (
              <p className="empty-state">No payroll records yet.</p>
            ) : (
              <div className="pr-list pr-list-scroll">
                {payrolls.map((p) => (
                  <div key={p.id} className="pr-card">
                    <div className="pr-card-head">
                      <div>
                        <span className="pr-emp">{nameFor(p.employeeId)}</span>
                        <span className="pr-period">{MONTHS[p.month - 1]} {p.year}</span>
                      </div>
                      <div className="pr-head-right">
                        <span className={`status-badge status-${p.status === 'paid' ? 'approved' : p.status === 'processed' ? 'pending' : 'pending'}`}>{p.status}</span>
                        <span className="pr-net">{money(p.netSalary)}</span>
                      </div>
                    </div>
                    <div className="pr-breakdown">
                      <div className="pr-col">
                        <span className="pr-col-title">Earnings</span>
                        <Row label="Basic Salary" value={money(p.baseSalary)} />
                        {p.allowances > 0 && <Row label="Allowances" value={money(p.allowances)} />}
                        {p.bonuses > 0 && <Row label="Bonuses" value={money(p.bonuses)} />}
                        {p.reimbursementTotal > 0 && <Row label="Reimbursement" value={money(p.reimbursementTotal)} highlight />}
                        {p.overtimePay > 0 && <Row label="Overtime Pay" value={money(p.overtimePay)} highlight />}
                      </div>
                      <div className="pr-col">
                        <span className="pr-col-title">Deductions</span>
                        <Row label="Income Tax (FY2027)" value={money(p.tax)} />
                        {p.unpaidLeaveDeduction > 0 && <Row label="Approved Unpaid Leave" value={money(p.unpaidLeaveDeduction)} highlight />}
                        {p.unapprovedLeaveFine > 0 && <Row label="Unapproved Leave Fine" value={money(p.unapprovedLeaveFine)} highlight />}
                        {p.deductions > 0 && <Row label="Deductions / Loan" value={money(p.deductions)} />}
                      </div>
                    </div>
                    {/* A draft can still be edited or thrown away. Once a
                        payslip has been issued — processed or paid — it is
                        the record of what somebody was told they would be
                        paid, and the server refuses both. A correction
                        supersedes it with a stated reason and leaves the
                        original standing, which is what an audit needs. */}
                    <div className="pr-actions">
                      {isIssued(p.status) ? (
                        <>
                          <button className="pr-btn pr-btn-ghost"
                            onClick={() => openEdit(p)}
                            title="Issue a corrected payslip, keeping this one on the record">
                            <FiEdit2 size={14} /> Correct
                          </button>
                          <span className="pr-locked" title={`A ${p.status} payslip cannot be edited or deleted`}>
                            <FiLock size={12} /> {p.status === 'paid' ? 'Paid' : 'Issued'}
                          </span>
                        </>
                      ) : (
                        <>
                          <button className="pr-btn pr-btn-ghost" onClick={() => openEdit(p)} title="Edit these figures">
                            <FiEdit2 size={14} /> Edit
                          </button>
                          <button className="pr-btn pr-btn-danger" onClick={() => handleDeletePayroll(p.id)}>
                            <FiTrash2 size={14} /> Delete
                          </button>
                        </>
                      )}
                      {p.status !== 'paid' && (
                        <button className="pr-btn pr-btn-primary" onClick={() => handleMarkPaid(p.id)}>
                          <FiCheck size={14} /> Mark Paid
                        </button>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}

        {editing && (
        <div className="pr-edit-backdrop" onMouseDown={(event) => event.target === event.currentTarget && !isSaving && setEditing(null)}>
          <div className="pr-edit-dialog" role="dialog" aria-modal="true" aria-labelledby="pr-edit-title">
            <header>
              <div>
                <h3 id="pr-edit-title">Edit payslip</h3>
                <p>{nameFor(editing.record.employeeId)} · {MONTHS[editing.record.month - 1]} {editing.record.year}</p>
              </div>
              <button type="button" onClick={() => setEditing(null)} disabled={isSaving} aria-label="Close"><FiX /></button>
            </header>

            <p className="pr-edit-note">
              Saving issues a <strong>corrected payslip</strong> as a new version. The current
              one is kept, marked corrected, so what the employee already received still exists.
              Tax and net pay are recalculated from the figures below.
            </p>

            <div className="pr-form-grid">
              <Input label="Base Salary (Rs) *" type="text" inputMode="decimal" value={editing.baseSalary}
                error={editErrors.baseSalary}
                onChange={(e) => { setEditing({ ...editing, baseSalary: e.target.value }); setEditErrors((cur) => ({ ...cur, baseSalary: '' })); }} />
              <Input label="Allowances (Rs)" type="text" inputMode="decimal" value={editing.allowances}
                error={editErrors.allowances}
                onChange={(e) => { setEditing({ ...editing, allowances: e.target.value }); setEditErrors((cur) => ({ ...cur, allowances: '' })); }} />
              <Input label="Bonuses (Rs)" type="text" inputMode="decimal" value={editing.bonuses}
                error={editErrors.bonuses}
                onChange={(e) => { setEditing({ ...editing, bonuses: e.target.value }); setEditErrors((cur) => ({ ...cur, bonuses: '' })); }} />
              <Input label="Arrears (Rs)" type="text" inputMode="decimal" value={editing.arrears}
                error={editErrors.arrears}
                onChange={(e) => { setEditing({ ...editing, arrears: e.target.value }); setEditErrors((cur) => ({ ...cur, arrears: '' })); }} />
              <Input label="Deductions e.g. Loan (Rs)" type="text" inputMode="decimal" value={editing.deductions}
                error={editErrors.deductions}
                onChange={(e) => { setEditing({ ...editing, deductions: e.target.value }); setEditErrors((cur) => ({ ...cur, deductions: '' })); }} />
              <div className="pr-tax-field">
                <Input label="Tax (Rs)" type="text" inputMode="decimal" value={editing.tax}
                  error={editErrors.tax}
                  disabled={!editing.taxIsManual}
                  onChange={(e) => { setEditing({ ...editing, tax: e.target.value }); setEditErrors((cur) => ({ ...cur, tax: '' })); }} />
                {editing.taxIsManual ? (
                  <p className="pr-tax-note">
                    <FiLock size={12} /> Set by hand — a recompute will keep this figure.
                    <button type="button" onClick={() => setEditing({ ...editing, taxIsManual: false })}>
                      Reset to calculated
                    </button>
                  </p>
                ) : (
                  <p className="pr-tax-note">
                    Calculated from the salary slabs. The slabs see gross salary only, so
                    pension-scheme income that is exempt has to be entered by hand.
                    <button type="button" onClick={() => setEditing({ ...editing, taxIsManual: true })}>
                      Enter tax manually
                    </button>
                  </p>
                )}
              </div>
              <Input label="Unapproved Leave Fine (Rs)" type="text" inputMode="decimal" value={editing.unapprovedLeaveFine}
                error={editErrors.unapprovedLeaveFine}
                title="Enter manually at Rs 5,000 for each unapproved leave day"
                onChange={(e) => { setEditing({ ...editing, unapprovedLeaveFine: e.target.value }); setEditErrors((cur) => ({ ...cur, unapprovedLeaveFine: '' })); }} />
              <Input label="Notes" value={editing.notes}
                onChange={(e) => setEditing({ ...editing, notes: e.target.value })} />
            </div>

            <label className="pr-edit-reason">
              <span>Reason for this edit *</span>
              <textarea
                value={editing.reason}
                onChange={(e) => setEditing({ ...editing, reason: e.target.value })}
                placeholder="e.g. Overtime hours approved after this payslip was issued"
                rows={3}
                maxLength={300}
              />
              <small>Stored on the corrected payslip and visible in its version history.</small>
            </label>

            {editError && <p className="pr-edit-error" role="alert">{editError}</p>}

            <div className="pr-edit-actions">
              <Button variant="secondary" onClick={() => setEditing(null)} disabled={isSaving}>Cancel</Button>
              <Button variant="primary" onClick={handleSaveEdit} isLoading={isSaving}>
                <FiRefreshCw size={16} /> Save &amp; regenerate
              </Button>
            </div>
          </div>
        </div>
      )}

      {activeTab === 'overtime' && (
          <div className="pr-tab">
            <h3 className="pr-section-title">Overtime</h3>
            <div className="pr-form-grid pr-form-grid-3">
              <Select label="Month" value={String(otFilter.month)}
                onChange={(e) => setOtFilter({ ...otFilter, month: Number(e.target.value) })}
                options={MONTHS.map((m, i) => ({ value: String(i + 1), label: m }))} />
              <Select label="Year" value={String(otFilter.year)}
                onChange={(e) => setOtFilter({ ...otFilter, year: Number(e.target.value) })}
                options={yearOptions.map((y) => ({ value: String(y), label: String(y) }))} />
              <div className="pr-inline-actions">
                <Button variant="secondary" onClick={loadOvertime}>Load</Button>
              </div>
            </div>
            <p className="pr-hint">Overtime is claimed per date worked under <strong>Time Logs</strong> and signed there — by the line manager who saw the work, then HR. What reaches payroll is the figure agreed on the claim, which need not match the formula (<strong>basic salary \u00f7 that employee\u2019s monthly hours</strong> \u2014 160 full time, 80 on a 4-hour day). This list shows what those approvals produced for the period.</p>

            <h3 className="pr-section-title pr-mt">Overtime Records ({overtime.length})</h3>
            {overtime.length === 0 ? (
              <p className="empty-state">No overtime for this period. Overtime is claimed by employees under Time Logs and approved there.</p>
            ) : (
              <div className="pr-list">
                {overtime.map((o) => (
                  <div key={o.id} className="pr-ot-row">
                    <div className="pr-ot-info">
                      <span className="pr-emp">{o.employeeName || nameFor(o.employeeId)}</span>
                      <span className="pr-period">{MONTHS[o.month - 1]} {o.year}</span>
                    </div>
                    <span className="pr-ot-hours">{o.overtimeHours}h overtime<small>{o.hourlyRate > 0 ? `${money(o.hourlyRate)}/hour \u00b7 from basic salary` : 'No basic salary on record'}</small></span>
                    <span className={`pr-ot-pay${o.overtimePay > 0 ? ' is-paid' : ''}`}>{money(o.overtimePay > 0 ? o.overtimePay : o.estimatedPay)}<small>{o.overtimePay > 0 ? 'On payslip' : o.status === 'rejected' ? 'Not paid' : 'If approved'}</small></span>
                    <span className={`status-badge status-${o.status === 'approved' ? 'approved' : o.status === 'rejected' ? 'rejected' : 'pending'}`}>{o.status}
                      {/* Who signed it, so a decided record is attributable rather than
                          just "approved" by nobody in particular. */}
                      {o.status !== 'pending' && o.approvedBy ? <small>{o.status === 'approved' ? 'by' : 'by'} {nameFor(o.approvedBy)}</small> : null}
                    </span>
                    {o.status === 'pending' && (
                      <div className="pr-ot-actions">
                        <button className="action-btn-inline action-approve-inline" title="Approve" onClick={() => handleOvertimeDecision(o.id, 'approved')}>
                          <FiCheck size={16} />
                        </button>
                        <button className="action-btn-inline action-reject-inline" title="Reject" onClick={() => handleOvertimeDecision(o.id, 'rejected')}>
                          <FiX size={16} />
                        </button>
                      </div>
                    )}
                  </div>
                ))}
              </div>
            )}
          </div>
        )}
      </Card>
    </div>
  );
};

const Row = ({ label, value, highlight }: { label: string; value: string; highlight?: boolean }) => (
  <div className={`pr-row${highlight ? ' pr-row-highlight' : ''}`}>
    <span className="pr-row-label">{label}</span>
    <span className="pr-row-value">{value}</span>
  </div>
);

export default PayrollReimbursement;
