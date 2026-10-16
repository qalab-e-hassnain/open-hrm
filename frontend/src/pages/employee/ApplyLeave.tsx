import { useEffect, useMemo, useRef, useState, type FormEvent } from 'react';
import { Link } from 'react-router-dom';
import {
  FiAlertCircle, FiArrowRight, FiCalendar, FiCheck, FiChevronLeft, FiChevronRight,
  FiFile, FiInfo, FiSave, FiTrash2, FiUploadCloud, FiUser, FiX,
} from 'react-icons/fi';
import { leaveService, type LeaveBalanceSummary } from '../../services/leaveService';
import { employeeService } from '../../services/employeeService';
import { isForcedLogoutRedirect } from '../../services/api';
import { useAuthStore } from '../../store/authStore';
import { hoursPerDay, rateBasis } from '../../utils/workHours';
import { ROUTES } from '../../config/constants';
import { type LeaveApplication } from '../../types';
import { type DraftFile, fileToDataUrl, dataUrlToFile } from '../../utils/draftFile';
import './ApplyLeave.css';

type FormState = {
  leaveType: string;
  startDate: string;
  endDate: string;
  duration: number;
  resumptionDate: string;
  reason: string;
  dayMode: 'single' | 'range';
  // Admin-only: who should cover full admin authority while this leave is
  // approved and active. Empty string keeps the default (HR covers).
  actingAdminId: string;
};

type Policy = {
  key: string;
  label: string;
  /** Whether this type has a ceiling at all. The SIZE of the ceiling is never
   *  held here: annual accrues per person and moves monthly, and personal is a
   *  monthly allowance — both come from /leaves/balance. */
  capped: boolean;
  maxDays: number | null;
  noticeDays: number;
  /** Personal leave runs on hours, not days, since the revised policy. */
  noticeHours?: number;
  halfDay: boolean;
  /** Only a fallback while the balance loads — the live figure is
   *  `medicalCertRequiredAfter` on the sick balance. */
  documentAfter?: number;
  approvals: string[];
};

const POLICIES: Record<string, Policy> = {
  'Annual Leave': { key: 'annual', label: 'Annual', capped: true, maxDays: null, noticeDays: 14, halfDay: true, approvals: ['HR / Admin'] },
  'Sick Leave': { key: 'sick', label: 'Sick', capped: true, maxDays: null, noticeDays: 0, halfDay: true, documentAfter: 0, approvals: ['HR / Admin'] },
  'Personal Leave': { key: 'personal', label: 'Personal', capped: true, maxDays: 3, noticeHours: 48, noticeDays: 2, halfDay: true, approvals: ['HR / Admin'] },
  'Maternity Leave': { key: 'maternity', label: 'Maternity', capped: true, maxDays: null, noticeDays: 14, halfDay: false, approvals: ['HR / Admin'] },
  'Paternity Leave': { key: 'paternity', label: 'Paternity', capped: true, maxDays: null, noticeDays: 14, halfDay: false, approvals: ['HR / Admin'] },
  'Bereavement Leave': { key: 'bereavement', label: 'Bereavement', capped: true, maxDays: null, noticeDays: 0, halfDay: false, approvals: ['HR / Admin'] },
  'Unpaid Leave': { key: 'unpaid', label: 'Unpaid', capped: false, maxDays: null, noticeDays: 0, halfDay: false, approvals: ['HR / Admin'] },
};

const DRAFT_KEY = 'hrm-leave-draft';
const emptyForm = (): FormState => ({ leaveType: 'Annual Leave', startDate: '', endDate: '', duration: 0, resumptionDate: '', reason: '', dayMode: 'single', actingAdminId: '' });

type DraftDocument = DraftFile;

const ApplyLeave = ({ onReviewPolicy }: { onReviewPolicy?: () => void }) => {
  const { user } = useAuthStore();
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [form, setForm] = useState<FormState>(() => {
    // Spread over emptyForm() defaults so a draft saved before a new field
    // (e.g. actingAdminId) existed doesn't leave it undefined.
    try { return { ...emptyForm(), ...(JSON.parse(localStorage.getItem(DRAFT_KEY) || '')?.form || {}) }; } catch { return emptyForm(); }
  });
  const [balance, setBalance] = useState<LeaveBalanceSummary | null>(null);
  const [history, setHistory] = useState<LeaveApplication[]>([]);
  const [managerName, setManagerName] = useState('Your reporting manager');
  const [actingAdminOptions, setActingAdminOptions] = useState<{ id: string; name: string }[]>([]);
  const [document, setDocument] = useState<File | null>(null);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [warnings, setWarnings] = useState<string[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [isUploading, setIsUploading] = useState(false);
  const [reviewOpen, setReviewOpen] = useState(false);
  const [success, setSuccess] = useState(false);
  const [draftSavedAt, setDraftSavedAt] = useState<Date | null>(() => {
    try { const value = JSON.parse(localStorage.getItem(DRAFT_KEY) || '')?.savedAt; return value ? new Date(value) : null; } catch { return null; }
  });
  const [calendarMonth, setCalendarMonth] = useState(() => new Date(new Date().getFullYear(), new Date().getMonth(), 1));
  // Restoring the draft's document on mount sets `document` state, which would
  // otherwise immediately re-trigger the auto-save effect below and re-encode
  // the same file back to localStorage for no reason. This flag tells that
  // effect to skip the one save that follows a restore.
  const skipNextAutoSaveRef = useRef(false);

  // The draft's text fields are restored synchronously above (localStorage is
  // already a string). The document needs an async decode step, so it's
  // restored separately, once, after mount.
  useEffect(() => {
    try {
      const draft = JSON.parse(localStorage.getItem(DRAFT_KEY) || '');
      if (draft?.document) {
        dataUrlToFile(draft.document).then((file) => {
          skipNextAutoSaveRef.current = true;
          setDocument(file);
        }).catch(() => {});
      }
    } catch {
      /* no draft, or malformed — nothing to restore */
    }
    // Runs once: this only ever restores the draft that existed at mount time.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    Promise.allSettled([
      leaveService.getMyLeaveBalance(),
      leaveService.getLeaveHistory(user?.id),
      employeeService.getManagers(),
    ]).then(([balanceResult, historyResult, managersResult]) => {
      if (balanceResult.status === 'fulfilled') setBalance(balanceResult.value);
      if (historyResult.status === 'fulfilled') setHistory(historyResult.value);
      if (managersResult.status === 'fulfilled' && user?.managerId) {
        const manager = managersResult.value.find((item) => Number(item.id) === Number(user.managerId));
        if (manager) setManagerName(manager.name);
      }
      setIsLoading(false);
    });
  }, [user?.id, user?.managerId]);

  // Only an admin can name an acting admin, so this list is only worth
  // fetching for one.
  useEffect(() => {
    if (user?.role !== 'admin') return;
    employeeService.getDirectory()
      .then((list) => setActingAdminOptions(
        (list || [])
          .filter((employee) => String(employee.id) !== String(user.id))
          .map((employee) => ({ id: String(employee.id), name: employee.name }))
          .sort((a, b) => a.name.localeCompare(b.name)),
      ))
      .catch(() => {});
  }, [user?.id, user?.role]);

  const saveDraft = async (nextForm: FormState, nextDocument: File | null) => {
    const savedAt = new Date();
    const payload: { form: FormState; savedAt: string; document?: DraftDocument } = {
      form: nextForm,
      savedAt: savedAt.toISOString(),
    };
    try {
      if (nextDocument) {
        payload.document = {
          name: nextDocument.name,
          type: nextDocument.type,
          size: nextDocument.size,
          dataUrl: await fileToDataUrl(nextDocument),
        };
      }
      localStorage.setItem(DRAFT_KEY, JSON.stringify(payload));
    } catch {
      // Quota exceeded (a large file pushes the base64 draft over localStorage's
      // limit) or a serialization failure — still save the text fields, which
      // is strictly better than losing the whole draft over one large file.
      try {
        localStorage.setItem(DRAFT_KEY, JSON.stringify({ form: nextForm, savedAt: savedAt.toISOString() }));
      } catch {
        /* localStorage unavailable entirely — nothing more we can do */
      }
    }
    setDraftSavedAt(savedAt);
  };

  useEffect(() => {
    if (!form.startDate && !form.reason && !document) return;
    if (skipNextAutoSaveRef.current) {
      skipNextAutoSaveRef.current = false;
      return;
    }
    const timer = window.setTimeout(() => { void saveDraft(form, document); }, 800);
    return () => window.clearTimeout(timer);
  }, [form, document]);

  useEffect(() => {
    const hasChanges = Boolean(form.startDate || form.reason || document);
    const protectDraft = (event: BeforeUnloadEvent) => {
      if (!hasChanges || success || isForcedLogoutRedirect) return;
      event.preventDefault();
      event.returnValue = '';
    };
    window.addEventListener('beforeunload', protectDraft);
    return () => window.removeEventListener('beforeunload', protectDraft);
  }, [document, form.reason, form.startDate, success]);

  const policy = POLICIES[form.leaveType];
  const balanceEntry = balance?.balances[policy.key];
  // Whatever the server says is left. Never total minus used: personal leave
  // is scoped to the month and annual rounds server-side.
  const remaining = !policy.capped ? Infinity : balanceEntry?.remaining ?? 0;
  // Annual and personal have different notice periods, and both are stated by
  // the server rather than assumed here.
  const noticeText = policy.key === 'personal'
    ? balance?.policyInfo?.personalLeaveNotice ?? '48 hours in advance'
    : policy.noticeDays
      ? balance?.policyInfo?.plannedLeaveNotice ?? `${policy.noticeDays} days in advance`
      : 'No advance notice';
  // Documentation is *expected*, never blocking: the server accepts the
  // request either way and warns that undocumented sick leave is adjusted
  // against personal or annual leave.
  //
  // The threshold comes from the balance, not from the table above — it moved
  // from 2 days to 0 without a frontend release, and it will move again.
  // Guard on undefined rather than truthiness: 0 is the live value and the one
  // Boolean() would throw away.
  const documentAfter = balanceEntry?.medicalCertRequiredAfter ?? policy.documentAfter;
  const documentExpected = documentAfter !== undefined && form.duration > documentAfter;
  const today = isoDate(new Date());

  // The server states which types this employee may request (parental leave is
  // for married employees of the matching gender). Falling back to the gender
  // rule only while the balance is still loading keeps the list stable.
  const visiblePolicies = useMemo(() => Object.values(POLICIES).filter((item) => {
    const parental = item.key === 'maternity' || item.key === 'paternity';
    if (!parental) return true;
    if (balance?.eligibleLeaveTypes?.length) return balance.eligibleLeaveTypes.includes(item.key);
    if (item.key === 'maternity' && user?.gender === 'male') return false;
    if (item.key === 'paternity' && user?.gender === 'female') return false;
    return true;
  }), [balance?.eligibleLeaveTypes, user?.gender]);

  const dateBreakdown = useMemo(() => calculateDays(form.startDate, form.endDate), [form.startDate, form.endDate]);
  const overlapping = useMemo(() => history.find((leave) => ['pending', 'manager_approved', 'approved'].includes(leave.status)
    && form.startDate && form.endDate && rangesOverlap(form.startDate, form.endDate, leave.startDate, leave.endDate)), [form.endDate, form.startDate, history]);

  const updateDates = (field: 'startDate' | 'endDate' | 'dayMode', value: string) => {
    const next = { ...form, [field]: value } as FormState;
    if (field === 'dayMode' && value === 'single' && next.startDate) next.endDate = next.startDate;
    if (field === 'startDate' && next.dayMode === 'single') next.endDate = value;
    if (field === 'startDate' && next.endDate && value > next.endDate) next.endDate = value;
    const calculation = calculateDays(next.startDate, next.endDate);
    next.duration = calculation.working;
    next.resumptionDate = calculateResumption(next.endDate);
    setForm(next);
    setErrors((current) => ({ ...current, startDate: '', endDate: '', duration: '' }));
  };

  const validate = () => {
    const next: Record<string, string> = {};
    if (!form.startDate) next.startDate = 'Select the first day of leave.';
    if (!form.endDate) next.endDate = 'Select the final day of leave.';
    if (form.startDate && form.startDate < today) next.startDate = 'Past dates cannot be selected.';
    if (form.startDate && form.endDate && form.endDate < form.startDate) next.endDate = 'End date cannot be before start date.';
    if (form.duration <= 0) next.duration = 'The selected range contains no working days.';
    if (Number.isFinite(remaining) && form.duration > remaining) next.duration = `You have ${remaining} ${policy.label.toLowerCase()}-leave day${remaining === 1 ? '' : 's'} remaining, but this request requires ${form.duration}.`;
    if (policy.maxDays && form.duration > policy.maxDays) next.duration = `${policy.label} leave is limited to ${policy.maxDays} working days per request.`;
    if (overlapping) next.startDate = `This request overlaps your ${overlapping.leaveType.toLowerCase()} request from ${shortDate(overlapping.startDate)} to ${shortDate(overlapping.endDate)}.`;
    if (form.reason.trim().length < 5) next.reason = 'Add a brief reason of at least 5 characters.';
    setErrors(next);
    return Object.keys(next).length === 0;
  };

  const openReview = (event: FormEvent) => {
    event.preventDefault();
    if (validate()) setReviewOpen(true);
  };

  const submit = async () => {
    setIsSubmitting(true);
    setWarnings([]);
    try {
      let documentUrl: string | undefined;
      if (document) {
        setIsUploading(true);
        documentUrl = await leaveService.uploadLeaveDocument(document);
        setIsUploading(false);
      }
      const response = await leaveService.applyLeave({ ...form, actingAdminId: form.actingAdminId || undefined, document: document || undefined, documentUrl });
      setWarnings(Array.isArray(response?.warnings) ? response.warnings : []);
      localStorage.removeItem(DRAFT_KEY);
      setReviewOpen(false);
      setSuccess(true);
    } catch (error: unknown) {
      setReviewOpen(false);
      const message = error instanceof Error ? error.message : '';
      setErrors({ submit: message || 'The leave request could not be submitted. Please try again.' });
    } finally {
      setIsSubmitting(false);
      setIsUploading(false);
    }
  };

  const reset = () => {
    setForm(emptyForm());
    setDocument(null);
    setErrors({});
    setWarnings([]);
    setSuccess(false);
    setDraftSavedAt(null);
    localStorage.removeItem(DRAFT_KEY);
  };

  const handleFile = (file?: File) => {
    if (!file) return;
    const allowed = ['application/pdf', 'image/jpeg', 'image/png', 'image/gif', 'image/webp'];
    if (!allowed.includes(file.type)) return setErrors((current) => ({ ...current, document: 'Use PDF, JPG, PNG, GIF, or WEBP.' }));
    if (file.size > 5 * 1024 * 1024) return setErrors((current) => ({ ...current, document: 'The document must be 5 MB or smaller.' }));
    setDocument(file);
    setErrors((current) => ({ ...current, document: '' }));
  };

  if (success) return <SuccessPanel form={form} warnings={warnings} onAnother={reset} />;

  return <div className="leave-apply-page">
    <aside className="leave-policy-caution" aria-label="Leave policy reminder">
      <FiInfo />
      <div>
        <strong>Review the leave policy before applying</strong>
        <span>
          Approved unpaid leave deducts {hoursPerDay(user?.standardHours)} hours at your {rateBasis(user?.standardHours)} rate per working day.
          An unapproved absence may receive a manually applied Rs 5,000/day fine.
        </span>
      </div>
      {onReviewPolicy && <button type="button" onClick={onReviewPolicy}>Review Leave Policy</button>}
    </aside>

    {warnings.length > 0 && <div className="leave-warning" role="status"><FiAlertCircle /><div><strong>Policy notes</strong>{warnings.map((warning) => <span key={warning}>{warning}</span>)}</div><button onClick={() => setWarnings([])} aria-label="Dismiss"><FiX /></button></div>}

    {isLoading ? <BalanceCardsSkeleton /> : balance && <BalanceCards balance={balance} policies={visiblePolicies} />}

    <div className="leave-main-grid">
      <section className="leave-request-card">
        <header><div><h2>Leave application</h2><p>Complete the details below to submit your request.</p></div><small>Fields marked * are required</small></header>
        <form onSubmit={openReview}>
          <FormBlock number="1" title="Leave details" helper="Select the leave policy and dates for your request.">
            <label className="leave-field wide">Leave type *<select value={form.leaveType} onChange={(event) => setForm({ ...form, leaveType: event.target.value })}>{visiblePolicies.map((item) => { const entry = balance?.balances[item.key]; const value = item.capped ? entry?.remaining ?? 0 : null; const unavailable = item.capped && value === 0; return <option key={item.key} value={`${item.label} Leave`} disabled={unavailable}>{item.label} Leave — {value === null ? 'Unlimited' : `${value} ${value === 1 ? 'day' : 'days'} remaining`}</option>; })}</select><small>{!policy.capped ? 'No quota' : `${remaining} remaining of ${balanceEntry?.total ?? 0}${policy.key === 'personal' && balanceEntry?.period ? ` this month (${balanceEntry.period})` : ''}`} · {noticeText}</small></label>
            <div className="leave-mode-control wide"><span>Duration</span><div className="leave-day-mode"><button type="button" className={form.dayMode === 'single' ? 'active' : ''} onClick={() => updateDates('dayMode', 'single')}>Single day</button><button type="button" className={form.dayMode === 'range' ? 'active' : ''} onClick={() => updateDates('dayMode', 'range')}>Date range</button></div></div>
            <label className="leave-field">Start date *<input type="date" min={today} value={form.startDate} onChange={(event) => updateDates('startDate', event.target.value)} aria-invalid={Boolean(errors.startDate)} />{errors.startDate && <small className="error">{errors.startDate}</small>}</label>
            {form.dayMode === 'range' && <label className="leave-field">End date *<input type="date" min={form.startDate || today} value={form.endDate} onChange={(event) => updateDates('endDate', event.target.value)} aria-invalid={Boolean(errors.endDate)} />{errors.endDate && <small className="error">{errors.endDate}</small>}</label>}
            <ReadOnlyValue label="Working days" value={`${form.duration} ${form.duration === 1 ? 'day' : 'days'}`} note={form.startDate ? `${dateBreakdown.calendar} calendar days − ${dateBreakdown.weekends} weekend days` : 'Select a leave date'} error={errors.duration} />
            <ReadOnlyValue label="Resumption date" value={form.resumptionDate ? longDate(form.resumptionDate) : 'Not calculated'} note="Next working day" />
            {(remaining <= 2 || documentExpected) && <div className="leave-inline-policy wide"><FiAlertCircle /><span>{remaining <= 2 ? `Only ${remaining} ${policy.label.toLowerCase()} day${remaining === 1 ? '' : 's'} remain.` : ''}{remaining <= 2 && documentExpected ? ' ' : ''}{documentExpected ? 'Attach medical documentation — undocumented sick leave is adjusted against your personal or annual leave.' : ''}</span></div>}
          </FormBlock>

          <FormBlock number="2" title="Request details" helper="Give your approver enough context to review the request.">
            <label className="leave-field wide">Reason for leave *<textarea maxLength={500} value={form.reason} onChange={(event) => setForm({ ...form, reason: event.target.value })} placeholder="Briefly explain why you are requesting time off" rows={4} aria-invalid={Boolean(errors.reason)} /><small className={errors.reason ? 'error counter' : 'counter'}>{errors.reason || `${form.reason.length}/500`}</small></label>
            <div className="leave-reason-chips wide">{['Family commitment', 'Medical appointment', 'Personal work'].map((reason) => <button type="button" key={reason} onClick={() => setForm({ ...form, reason })}>{reason}</button>)}</div>
          </FormBlock>

          <FormBlock number="3" title="Coverage and documents" helper="Coverage is coordinated automatically; attach evidence when needed.">
            <div className="leave-handover-row wide"><FiUser /><span><strong>Coverage</strong>{managerName === 'Your reporting manager' ? 'Your reporting manager will coordinate cover.' : `${managerName}, your reporting manager, will coordinate cover.`}</span></div>
            {user?.role === 'admin' && (
              <label className="leave-field wide">
                Acting admin during this leave
                <select value={form.actingAdminId} onChange={(event) => setForm({ ...form, actingAdminId: event.target.value })}>
                  <option value="">HR team (default)</option>
                  {actingAdminOptions.map((option) => <option key={option.id} value={option.id}>{option.name}</option>)}
                </select>
                <small>While this leave is approved and active, they get admin access — except editing another admin's account or granting the admin role. Leave unset and HR covers automatically.</small>
              </label>
            )}
            <div className={`leave-upload wide${errors.document ? ' invalid' : ''}`} onDragOver={(event) => event.preventDefault()} onDrop={(event) => { event.preventDefault(); handleFile(event.dataTransfer.files[0]); }}>
            <input ref={fileInputRef} type="file" accept=".pdf,.jpg,.jpeg,.png,.gif,.webp" onChange={(event) => handleFile(event.target.files?.[0])} />
            {document ? <div className="leave-file-row"><FiFile /><span><strong>{document.name}</strong><small>{formatFileSize(document.size)} · Ready</small></span><button type="button" onClick={() => setDocument(null)} aria-label="Remove document"><FiTrash2 /></button></div>
              : <button type="button" className="leave-upload-prompt" onClick={() => fileInputRef.current?.click()}><FiUploadCloud /><span><strong>{documentExpected ? 'Drop your medical documentation here' : 'Drop a supporting document here'}</strong><small>PDF, JPG, PNG, GIF, WEBP · Maximum 5 MB</small></span><em>Choose file</em></button>}
            </div>
            {errors.document && <small className="leave-document-error wide">{errors.document}</small>}
          </FormBlock>
          {errors.submit && <div className="leave-submit-error"><FiAlertCircle />{errors.submit}</div>}

          <footer className="leave-form-footer"><span>{draftSavedAt ? `Draft saved at ${draftSavedAt.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}` : 'Draft saves automatically'}</span><button type="button" className="ghost" onClick={reset}>Cancel</button><button type="button" className="outline" onClick={() => void saveDraft(form, document)}><FiSave /> Save draft</button><button className="primary" disabled={isSubmitting}>Review request <FiArrowRight /></button></footer>
        </form>
      </section>

      <aside className="leave-context-rail">
        <LeaveCalendar month={calendarMonth} form={form} history={history} onMonth={setCalendarMonth} onDate={(date) => updateDates('startDate', date)} />
        <UpcomingLeave history={history} />
      </aside>
    </div>

    {reviewOpen && <ReviewDialog form={form} uploading={isUploading} submitting={isSubmitting} onCancel={() => setReviewOpen(false)} onSubmit={submit} actingAdminLabel={user?.role === 'admin' ? (actingAdminOptions.find((option) => option.id === form.actingAdminId)?.name || 'HR team (default)') : undefined} />}
  </div>;
};

// The legend names the group for assistive tech but is taken out of the visual
// flow: a rendered legend sits in the fieldset's top border area, where the
// block's padding never reaches, which left the step number on the divider
// above it. The visible heading below is an ordinary box the padding acts on.
const FormBlock = ({ number, title, helper, children }: { number: string; title: string; helper: string; children: React.ReactNode }) => <fieldset className="leave-form-block"><legend>{`Step ${number}: ${title}`}</legend><div className="leave-form-heading"><i aria-hidden="true">{number}</i><span><strong>{title}</strong><small>{helper}</small></span></div><div className="leave-form-fields">{children}</div></fieldset>;
const ReadOnlyValue = ({ label, value, note, error }: { label: string; value: string; note: string; error?: string }) => <div className="leave-readonly"><small>{label}</small><div className="leave-readonly-box"><strong>{value}</strong><span>{note}</span></div>{error && <em>{error}</em>}</div>;

const BalanceCards = ({ balance, policies }: { balance: LeaveBalanceSummary; policies: Policy[] }) => <section className="leave-balance-cards"><header><div><h2>Leave balances</h2><p>Your available leave for {balance.year}</p></div><span>{balance.totalUsed} days used</span></header><div>{policies.map((item) => { const entry = balance.balances[item.key]; const total = entry?.total ?? 0; const remaining = item.capped ? entry?.remaining ?? 0 : null; const used = entry?.used ?? 0; const percentage = total ? Math.min(100, used / total * 100) : 0; const state = remaining === 0 ? 'exhausted' : remaining !== null && remaining <= 2 ? 'low' : remaining === null ? 'unlimited' : ''; return <article key={item.key} className={state}><span>{item.label} leave</span><strong>{remaining === null ? 'Unlimited' : `${remaining} ${remaining === 1 ? 'day' : 'days'} left`}</strong><small>{remaining === null ? 'No quota' : `${used} of ${total} used${item.key === 'personal' && entry?.period ? ` this month` : ''}`}</small>{remaining !== null && <i><b style={{ width: `${percentage}%` }} /></i>}{state === 'low' && <em>Low balance</em>}{state === 'exhausted' && <em>Unavailable</em>}</article>; })}</div></section>;
const BalanceCardsSkeleton = () => <section className="leave-balance-cards leave-balance-skeleton"><header /><div>{Array.from({ length: 7 }, (_, index) => <article key={index} />)}</div></section>;

const LeaveCalendar = ({ month, form, history, onMonth, onDate }: { month: Date; form: FormState; history: LeaveApplication[]; onMonth: (date: Date) => void; onDate: (date: string) => void }) => {
  const year = month.getFullYear(); const monthIndex = month.getMonth(); const first = new Date(year, monthIndex, 1).getDay(); const count = new Date(year, monthIndex + 1, 0).getDate(); const today = isoDate(new Date());
  return <section className="leave-calendar"><header><h3>Leave calendar</h3><div><button onClick={() => onMonth(new Date(year, monthIndex - 1, 1))} aria-label="Previous month"><FiChevronLeft /></button><button onClick={() => onMonth(new Date(year, monthIndex + 1, 1))} aria-label="Next month"><FiChevronRight /></button></div></header><strong>{month.toLocaleDateString('en-US', { month: 'long', year: 'numeric' })}</strong><div className="leave-calendar-week">{['S','M','T','W','T','F','S'].map((day, index) => <span key={`${day}-${index}`}>{day}</span>)}</div><div className="leave-calendar-grid">{Array.from({ length: first }, (_, index) => <i key={`blank-${index}`} />)}{Array.from({ length: count }, (_, index) => { const date = `${year}-${String(monthIndex + 1).padStart(2,'0')}-${String(index + 1).padStart(2,'0')}`; const selected = form.startDate && form.endDate && date >= form.startDate && date <= form.endDate; const leave = history.find((item) => ['pending', 'manager_approved', 'approved'].includes(item.status) && date >= item.startDate && date <= item.endDate); const weekend = [0,6].includes(new Date(`${date}T00:00:00`).getDay()); return <button key={date} type="button" className={`${selected ? 'selected ' : ''}${date === today ? 'today ' : ''}${weekend ? 'weekend ' : ''}${leave ? leave.status : ''}`} onClick={() => date >= today && onDate(date)} disabled={date < today} title={leave ? `${leave.leaveType} · ${leave.status}` : weekend ? 'Weekend' : longDate(date)}>{index + 1}</button>; })}</div><div className="leave-calendar-legend"><span><i className="approved" />Approved</span><span><i className="pending" />Pending</span><span><i className="today" />Today</span></div></section>;
};

const UpcomingLeave = ({ history }: { history: LeaveApplication[] }) => {
  const upcoming = history.filter((item) => item.endDate >= isoDate(new Date())).sort((a, b) => a.startDate.localeCompare(b.startDate)).slice(0, 3);
  return <section className="leave-upcoming"><header><h3>Your scheduled leave</h3><Link to={`${ROUTES.LEAVES}?tab=history`}>View all</Link></header>{upcoming.length ? <ul>{upcoming.map((item) => <li key={item.id}><i>{item.leaveType.slice(0, 1)}</i><span><strong>{item.leaveType}</strong><small>{shortDate(item.startDate)}{item.endDate !== item.startDate ? ` – ${shortDate(item.endDate)}` : ''}</small></span><em>{item.status.replace('_', ' ')}</em></li>)}</ul> : <div className="leave-upcoming-empty"><FiCalendar /><strong>No upcoming leave</strong><span>Your approved and pending requests will appear here.</span></div>}</section>;
};

const ReviewDialog = ({ form, uploading, submitting, onCancel, onSubmit, actingAdminLabel }: { form: FormState; uploading: boolean; submitting: boolean; onCancel: () => void; onSubmit: () => void; actingAdminLabel?: string }) => <div className="leave-review-backdrop"><section className="leave-review"><header><div><p>Final review</p><h2>Submit leave request?</h2></div><button onClick={onCancel} aria-label="Close"><FiX /></button></header><dl><dt>Leave type</dt><dd>{form.leaveType}</dd><dt>Dates</dt><dd>{longDate(form.startDate)}{form.endDate !== form.startDate ? ` – ${longDate(form.endDate)}` : ''}</dd><dt>Working days</dt><dd>{form.duration}</dd><dt>Resumption</dt><dd>{longDate(form.resumptionDate)}</dd><dt>Approver</dt><dd>HR / Admin</dd>{actingAdminLabel && <><dt>Acting admin</dt><dd>{actingAdminLabel}</dd></>}</dl><footer><button onClick={onCancel}>Go back</button><button className="primary" onClick={onSubmit} disabled={submitting}>{uploading ? 'Uploading document…' : submitting ? 'Submitting…' : 'Submit request'}</button></footer></section></div>;

const SuccessPanel = ({ form, warnings, onAnother }: { form: FormState; warnings: string[]; onAnother: () => void }) => <section className="leave-success"><i><FiCheck /></i><h2>Leave request submitted</h2><p>{form.leaveType} for {longDate(form.startDate)} has been sent for approval.</p>{warnings.map((warning) => <span key={warning}><FiInfo />{warning}</span>)}<div><Link to={`${ROUTES.LEAVES}?tab=history`}>Go to leave history</Link><button onClick={onAnother}>Submit another request</button></div></section>;

const calculateDays = (startValue: string, endValue: string) => {
  if (!startValue || !endValue || endValue < startValue) return { calendar: 0, weekends: 0, working: 0 };
  const start = new Date(`${startValue}T00:00:00`); const end = new Date(`${endValue}T00:00:00`); let calendar = 0; let weekends = 0;
  for (const cursor = new Date(start); cursor <= end; cursor.setDate(cursor.getDate() + 1)) { calendar += 1; if ([0,6].includes(cursor.getDay())) weekends += 1; }
  return { calendar, weekends, working: calendar - weekends };
};
const calculateResumption = (endValue: string) => { if (!endValue) return ''; const date = new Date(`${endValue}T00:00:00`); do { date.setDate(date.getDate() + 1); } while ([0,6].includes(date.getDay())); return isoDate(date); };
const rangesOverlap = (aStart: string, aEnd: string, bStart: string, bEnd: string) => aStart <= bEnd && aEnd >= bStart;
const isoDate = (date: Date) => `${date.getFullYear()}-${String(date.getMonth()+1).padStart(2,'0')}-${String(date.getDate()).padStart(2,'0')}`;
const shortDate = (date: string) => new Date(`${date}T00:00:00`).toLocaleDateString('en-US', { month:'short', day:'numeric' });
const longDate = (date: string) => date ? new Date(`${date}T00:00:00`).toLocaleDateString('en-US', { month:'long', day:'numeric', year:'numeric' }) : 'Not selected';
const formatFileSize = (bytes: number) => `${(bytes / 1024 / 1024).toFixed(1)} MB`;
export default ApplyLeave;
