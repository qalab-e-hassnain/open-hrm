import { useState, useEffect, useCallback } from 'react';
import { FiPlus, FiDownload, FiLock, FiUnlock, FiTrash2, FiArrowLeft, FiUserPlus } from 'react-icons/fi';
import Select from '../../components/common/Select';
import Button from '../../components/common/Button';
import {
  payrollSheetService,
  type PayrollRunSummary,
  type PayrollRunDetail,
  type PayrollEntry,
  type EditableField,
} from '../../services/payrollSheetService';
import { type Employee } from '../../types';
import './PayrollSheet.css';

const MONTHS = [
  'January', 'February', 'March', 'April', 'May', 'June',
  'July', 'August', 'September', 'October', 'November', 'December',
];

const now = new Date();

const money = (n: number) =>
  (n || 0).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });

/** "2025-11" -> "November 2025" */
const monthLabel = (month: string) => {
  const [y, m] = month.split('-');
  return `${MONTHS[Number(m) - 1]} ${y}`;
};

/** Mirrors the server's hybrid properties so edits recompute instantly. */
const recompute = (e: PayrollEntry): PayrollEntry => {
  const grossSalary = e.monthlySalary + e.increment;
  const grossTotal = grossSalary + e.allowanceOvertime + e.arrears + e.bonus;
  return { ...e, grossSalary, grossTotal, netPayable: grossTotal - e.taxDeduction - e.otherDeduction };
};

interface Props {
  employees: Employee[];
}

const PayrollSheet = ({ employees }: Props) => {
  const [runs, setRuns] = useState<PayrollRunSummary[]>([]);
  const [openRun, setOpenRun] = useState<PayrollRunDetail | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [isBusy, setIsBusy] = useState(false);
  const [newRun, setNewRun] = useState({ month: now.getMonth() + 1, year: now.getFullYear() });
  const [addEmployeeId, setAddEmployeeId] = useState('');

  const loadRuns = useCallback(async () => {
    try {
      setIsLoading(true);
      setRuns(await payrollSheetService.listRuns());
    } catch {
      setRuns([]);
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => { loadRuns(); }, [loadRuns]);

  const isDraft = openRun?.status === 'draft';

  const handleCreateRun = async () => {
    const month = `${newRun.year}-${String(newRun.month).padStart(2, '0')}`;
    try {
      setIsBusy(true);
      const detail = await payrollSheetService.createRun(month);
      await loadRuns();
      setOpenRun(detail); // straight into the editor
    } catch (err: any) {
      alert(err.message);
    } finally {
      setIsBusy(false);
    }
  };

  const handleOpenRun = async (runId: number) => {
    try {
      setIsBusy(true);
      setOpenRun(await payrollSheetService.getRun(runId));
    } catch (err: any) {
      alert(err.message || 'Failed to open payroll run');
    } finally {
      setIsBusy(false);
    }
  };

  const handleDeleteRun = async (run: PayrollRunSummary) => {
    if (!window.confirm(`Delete the ${monthLabel(run.month)} payroll run and all its rows?`)) return;
    try {
      await payrollSheetService.deleteRun(run.id);
      await loadRuns();
    } catch (err: any) {
      alert(err.message);
    }
  };

  /** Commit one cell on blur, but only if the value actually changed. */
  const handleCellCommit = async (entry: PayrollEntry, field: EditableField, raw: string) => {
    if (!openRun) return;
    const value = parseFloat(raw) || 0;
    if (value === entry[field]) return;

    const previous = openRun;
    // Optimistic: show the new number immediately, roll back if the PATCH fails.
    setOpenRun({
      ...openRun,
      entries: openRun.entries.map((e) => (e.id === entry.id ? recompute({ ...e, [field]: value }) : e)),
    });

    try {
      const saved = await payrollSheetService.updateEntry(openRun.id, entry.id, field, value);
      setOpenRun((current) =>
        current ? { ...current, entries: current.entries.map((e) => (e.id === saved.id ? saved : e)) } : current
      );
    } catch (err: any) {
      setOpenRun(previous);
      alert(err.message);
    }
  };

  const handleAddEmployee = async () => {
    if (!openRun || !addEmployeeId) return;
    try {
      setIsBusy(true);
      await payrollSheetService.addEntry(openRun.id, Number(addEmployeeId));
      setAddEmployeeId('');
      setOpenRun(await payrollSheetService.getRun(openRun.id));
    } catch (err: any) {
      alert(err.message);
    } finally {
      setIsBusy(false);
    }
  };

  const handleRemoveEntry = async (entry: PayrollEntry) => {
    if (!openRun) return;
    if (!window.confirm(`Remove ${entry.employeeName} from this payroll run?`)) return;
    try {
      await payrollSheetService.deleteEntry(openRun.id, entry.id);
      setOpenRun(await payrollSheetService.getRun(openRun.id));
    } catch (err: any) {
      alert(err.message);
    }
  };

  const handleFinalize = async () => {
    if (!openRun) return;
    // Finalizing now issues pay, not just a status change: it creates a payslip
    // per entry, which the employee can see immediately, and folds any
    // increment into their standing salary.
    const count = openRun.entries.length;
    const raises = openRun.entries.filter((e) => (e.increment || 0) > 0).length;
    const confirmation = [
      `Finalize ${monthLabel(openRun.month)}?`,
      '',
      `${count} payslip${count === 1 ? '' : 's'} will be issued and become visible to `
        + `employee${count === 1 ? '' : 's'} straight away, with an in-app notification.`,
      raises > 0
        ? `${raises} salary increment${raises === 1 ? '' : 's'} will become the new standing `
          + 'monthly salary from next month.'
        : '',
      '',
      'All rows become read-only until the run is reopened. No email is sent — '
        + 'emailing a payslip stays a separate action.',
    ].filter(Boolean).join('\n');
    if (!window.confirm(confirmation)) return;
    try {
      setIsBusy(true);
      setOpenRun(await payrollSheetService.finalizeRun(openRun.id));
      await loadRuns();
    } catch (err: any) {
      alert(err.message);
    } finally {
      setIsBusy(false);
    }
  };

  const handleReopen = async () => {
    if (!openRun) return;
    // Re-finalizing after a reopen revises the payslips already issued rather
    // than creating duplicates — each gets a new version and a correction
    // reason — so the employee sees figures change under them.
    const issued = openRun.entries.length;
    if (!window.confirm(
      `${issued} payslip${issued === 1 ? ' has' : 's have'} been issued for this run.\n\n`
      + `Reopening and re-finalizing will revise ${issued === 1 ? 'it' : 'them'} in place, and `
      + `employee${issued === 1 ? '' : 's'} will see the change.`,
    )) return;
    const reason = window.prompt('Why are you reopening this finalized run? (recorded in the audit log)');
    if (!reason || !reason.trim()) return;
    try {
      setIsBusy(true);
      setOpenRun(await payrollSheetService.reopenRun(openRun.id, reason.trim()));
      await loadRuns();
    } catch (err: any) {
      alert(err.message);
    } finally {
      setIsBusy(false);
    }
  };

  const handleExport = async () => {
    if (!openRun) return;
    try {
      await payrollSheetService.exportRun(openRun.id, openRun.month);
    } catch (err: any) {
      alert(err.message);
    }
  };

  const yearOptions = Array.from({ length: 5 }, (_, i) => now.getFullYear() - 2 + i);

  // ---------------- Run list ----------------
  if (!openRun) {
    return (
      <div className="pr-tab">
        <h3 className="pr-section-title">New Payroll Run</h3>
        <div className="ps-new-run">
          <Select
            label="Month"
            value={String(newRun.month)}
            onChange={(e) => setNewRun({ ...newRun, month: Number(e.target.value) })}
            options={MONTHS.map((m, i) => ({ value: String(i + 1), label: m }))}
          />
          <Select
            label="Year"
            value={String(newRun.year)}
            onChange={(e) => setNewRun({ ...newRun, year: Number(e.target.value) })}
            options={yearOptions.map((y) => ({ value: String(y), label: String(y) }))}
          />
          <Button variant="primary" onClick={handleCreateRun} isLoading={isBusy}>
            <FiPlus size={16} /> New payroll run
          </Button>
        </div>
        <p className="pr-hint">
          Creates a draft sheet with one row per employee to be paid — including anyone whose last
          working day fell in this month, so a final salary is never skipped. Each row's Monthly
          Salary is a snapshot taken at creation time; later raises won't change this sheet.
        </p>

        <h3 className="pr-section-title pr-mt">Payroll Runs ({runs.length})</h3>
        {isLoading ? (
          <div className="content-loading"><div className="spinner" /></div>
        ) : runs.length === 0 ? (
          <p className="empty-state">No payroll runs yet. Create one above.</p>
        ) : (
          <div className="ps-runs">
            {runs.map((run) => (
              <div key={run.id} className="ps-run-card" onClick={() => handleOpenRun(run.id)}>
                <div className="ps-run-main">
                  <span className="ps-run-month">{monthLabel(run.month)}</span>
                  <span className={`status-badge status-${run.status === 'draft' ? 'pending' : 'approved'}`}>
                    {run.status}
                  </span>
                </div>
                <div className="ps-run-meta">
                  <span>{run.employeeCount} {run.employeeCount === 1 ? 'employee' : 'employees'}</span>
                  <span className="ps-run-net">Rs {money(run.totalNet)}</span>
                </div>
                <div className="ps-run-actions">
                  {run.status === 'draft' && (
                    <button
                      className="pr-btn pr-btn-danger"
                      title="Delete draft run"
                      onClick={(e) => { e.stopPropagation(); handleDeleteRun(run); }}
                    >
                      <FiTrash2 size={14} /> Delete
                    </button>
                  )}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    );
  }

  // ---------------- Editor ----------------
  const totals = openRun.entries.reduce(
    (acc, e) => ({
      monthlySalary: acc.monthlySalary + e.monthlySalary,
      increment: acc.increment + e.increment,
      grossSalary: acc.grossSalary + e.grossSalary,
      allowanceOvertime: acc.allowanceOvertime + e.allowanceOvertime,
      arrears: acc.arrears + e.arrears,
      bonus: acc.bonus + e.bonus,
      grossTotal: acc.grossTotal + e.grossTotal,
      taxDeduction: acc.taxDeduction + e.taxDeduction,
      otherDeduction: acc.otherDeduction + e.otherDeduction,
      netPayable: acc.netPayable + e.netPayable,
    }),
    {
      monthlySalary: 0, increment: 0, grossSalary: 0, allowanceOvertime: 0, arrears: 0,
      bonus: 0, grossTotal: 0, taxDeduction: 0, otherDeduction: 0, netPayable: 0,
    }
  );

  // The roster holds current employees only, so anything on the sheet that
  // is missing from it left during the period.
  const activeIds = new Set(employees.map((e) => String(e.id)));
  const onSheet = new Set(openRun.entries.map((e) => String(e.employeeId)));
  const addableEmployees = employees.filter((e) => !onSheet.has(e.id));

  return (
    <div className="pr-tab">
      <div className="ps-editor-head">
        <button className="ps-back" onClick={() => setOpenRun(null)}>
          <FiArrowLeft size={16} /> All runs
        </button>
        <div className="ps-editor-title">
          <h3 className="pr-section-title">{monthLabel(openRun.month)}</h3>
          <span className={`status-badge status-${isDraft ? 'pending' : 'approved'}`}>{openRun.status}</span>
        </div>
        <div className="ps-editor-actions">
          <button className="pr-btn pr-btn-ghost" onClick={handleExport}>
            <FiDownload size={14} /> Export
          </button>
          {isDraft ? (
            <button className="pr-btn pr-btn-primary" onClick={handleFinalize} disabled={isBusy}>
              <FiLock size={14} /> Finalize
            </button>
          ) : (
            <button className="pr-btn pr-btn-ghost" onClick={handleReopen} disabled={isBusy}>
              <FiUnlock size={14} /> Reopen
            </button>
          )}
        </div>
      </div>

      {!isDraft && (
        <p className="ps-locked-note">
          <FiLock size={13} /> This run is {openRun.status} and read-only. Reopen it to make changes.
        </p>
      )}

      <div className="ps-table-wrap">
        <table className="ps-table">
          <thead>
            <tr>
              <th className="ps-num">Sr#</th>
              <th className="ps-sticky-name">Name</th>
              <th>Designation</th>
              <th className="ps-right">Monthly Salary</th>
              <th className="ps-right ps-edit-col" title="Folded into the employee's standing monthly salary when this run is finalized — it becomes their new base pay from next month, not a one-off payment.">Increment</th>
              <th className="ps-right ps-computed">Gross Salary</th>
              <th className="ps-right ps-edit-col">Allowance/Overtime</th>
              <th className="ps-right ps-edit-col">Arrears</th>
              <th className="ps-right ps-edit-col">Bonus</th>
              <th className="ps-right ps-computed">Gross Total</th>
              <th className="ps-right ps-edit-col" title="Carried over from the previous month rather than recalculated, because the slab engine cannot see pension-scheme exemptions. Edit it if the figure has changed.">Tax Deduction</th>
              <th className="ps-right ps-edit-col">Advance/Other Deduction</th>
              <th className="ps-right ps-computed">Net Payable</th>
              {isDraft && <th className="ps-right" />}
            </tr>
          </thead>
          <tbody>
            {openRun.entries.map((entry, i) => (
              <tr key={entry.id}>
                <td className="ps-num">{i + 1}</td>
                <td className="ps-sticky-name">
                  {entry.employeeName}
                  {/* A run now includes anyone whose last working day fell in
                      the month, so an entry with no active employee behind it
                      is a leaver being paid out — not a data error to hide. */}
                  {!activeIds.has(String(entry.employeeId)) && (
                    <span className="ps-leaver" title="Left during this period — final salary">Leaver</span>
                  )}
                </td>
                <td className="ps-designation">{entry.designation || '—'}</td>
                <td className="ps-right ps-readonly">{money(entry.monthlySalary)}</td>
                <Cell entry={entry} field="increment" disabled={!isDraft} onCommit={handleCellCommit} />
                <td className="ps-right ps-computed">{money(entry.grossSalary)}</td>
                <Cell entry={entry} field="allowanceOvertime" disabled={!isDraft} onCommit={handleCellCommit} />
                <Cell entry={entry} field="arrears" disabled={!isDraft} onCommit={handleCellCommit} />
                <Cell entry={entry} field="bonus" disabled={!isDraft} onCommit={handleCellCommit} />
                <td className="ps-right ps-computed">{money(entry.grossTotal)}</td>
                <Cell entry={entry} field="taxDeduction" disabled={!isDraft} onCommit={handleCellCommit} />
                <Cell entry={entry} field="otherDeduction" disabled={!isDraft} onCommit={handleCellCommit} />
                <td className="ps-right ps-computed ps-net">{money(entry.netPayable)}</td>
                {isDraft && (
                  <td className="ps-right">
                    <button className="ps-row-remove" title="Remove from run" onClick={() => handleRemoveEntry(entry)}>
                      <FiTrash2 size={14} />
                    </button>
                  </td>
                )}
              </tr>
            ))}
          </tbody>
          <tfoot>
            <tr className="ps-totals">
              <td />
              <td className="ps-sticky-name">Total:</td>
              <td />
              <td className="ps-right">{money(totals.monthlySalary)}</td>
              <td className="ps-right">{money(totals.increment)}</td>
              <td className="ps-right">{money(totals.grossSalary)}</td>
              <td className="ps-right">{money(totals.allowanceOvertime)}</td>
              <td className="ps-right">{money(totals.arrears)}</td>
              <td className="ps-right">{money(totals.bonus)}</td>
              <td className="ps-right">{money(totals.grossTotal)}</td>
              <td className="ps-right">{money(totals.taxDeduction)}</td>
              <td className="ps-right">{money(totals.otherDeduction)}</td>
              <td className="ps-right ps-net">{money(totals.netPayable)}</td>
              {isDraft && <td />}
            </tr>
          </tfoot>
        </table>
      </div>

      {isDraft && addableEmployees.length > 0 && (
        <div className="ps-add-row">
          <Select
            label="Add employee to this run"
            value={addEmployeeId}
            onChange={(e) => setAddEmployeeId(e.target.value)}
            options={[
              { value: '', label: 'Select employee' },
              ...addableEmployees.map((e) => ({ value: e.id, label: e.name })),
            ]}
          />
          <Button variant="secondary" onClick={handleAddEmployee} isLoading={isBusy}>
            <FiUserPlus size={16} /> Add
          </Button>
        </div>
      )}
    </div>
  );
};

/** One inline-editable numeric cell. Commits on blur; Enter blurs, Esc reverts. */
const Cell = ({
  entry,
  field,
  disabled,
  onCommit,
}: {
  entry: PayrollEntry;
  field: EditableField;
  disabled: boolean;
  onCommit: (entry: PayrollEntry, field: EditableField, raw: string) => void;
}) => {
  const [draft, setDraft] = useState(String(entry[field] ?? 0));

  // Keep in step when the row is replaced by the server's response.
  useEffect(() => { setDraft(String(entry[field] ?? 0)); }, [entry, field]);

  if (disabled) return <td className="ps-right ps-readonly">{money(entry[field])}</td>;

  return (
    <td className="ps-right ps-edit-col">
      <input
        className="ps-input"
        type="number"
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        onFocus={(e) => e.target.select()}
        onBlur={() => onCommit(entry, field, draft)}
        onKeyDown={(e) => {
          if (e.key === 'Enter') (e.target as HTMLInputElement).blur();
          if (e.key === 'Escape') setDraft(String(entry[field] ?? 0));
        }}
      />
    </td>
  );
};

export default PayrollSheet;
