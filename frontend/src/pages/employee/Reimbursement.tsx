import { useEffect, useRef, useState, type FormEvent } from 'react';
import { FiAlertCircle, FiCheck, FiFileText, FiSave, FiSend, FiTrash2, FiUploadCloud } from 'react-icons/fi';
import { reimbursementService } from '../../services/reimbursementService';
import { useAuthStore } from '../../store/authStore';
import { type DraftFile, fileToDataUrl, dataUrlToFile } from '../../utils/draftFile';
import './Reimbursement.css';

const REIMBURSEMENT_TYPES = [
  { value: 'travel', label: 'Travel' },
  { value: 'food', label: 'Food and meals' },
  { value: 'medical', label: 'Medical' },
  { value: 'equipment', label: 'Equipment and supplies' },
  { value: 'other', label: 'Other business expense' },
];

// Mirrors the formats the backend /upload endpoint accepts.
const ACCEPTED_TYPES = ['application/pdf', 'image/jpeg', 'image/png', 'image/gif', 'image/webp'];
const MAX_RECEIPT_BYTES = 5 * 1024 * 1024;

type ReimbursementForm = {
  reimbursementType: string;
  amount: string;
  dateOfExpense: string;
  description: string;
};

const emptyForm: ReimbursementForm = { reimbursementType: '', amount: '', dateOfExpense: '', description: '' };
const DRAFT_KEY = 'hrm-reimbursement-draft';

const today = () => new Date().toISOString().slice(0, 10);

const loadDraft = (): ReimbursementForm => {
  try {
    const saved = JSON.parse(localStorage.getItem(DRAFT_KEY) || 'null');
    // Merge onto the empty form so a partial or stale draft can't leave a
    // field as undefined and break .trim()/.length further down.
    return saved?.form ? { ...emptyForm, ...saved.form } : emptyForm;
  } catch {
    return emptyForm;
  }
};

// Axios rejects with an Error whose message is "Request failed with status
// code 422"; the message a user can act on is in the FastAPI `detail` body.
const readErrorMessage = (error: unknown, fallback: string): string => {
  const detail = (error as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail;
  if (typeof detail === 'string' && detail.trim()) return detail;
  if (Array.isArray(detail) && typeof detail[0]?.msg === 'string') return detail[0].msg;
  if (error instanceof Error && error.message && !/^Request failed with status code/.test(error.message)) {
    return error.message;
  }
  return fallback;
};

const Reimbursement = () => {
  const { user } = useAuthStore();
  const formRef = useRef<HTMLFormElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [formData, setFormData] = useState<ReimbursementForm>(loadDraft);
  // Own claims still open to a duplicate check — a manager/admin using this
  // same form sees everyone's/their team's claims from this endpoint, so
  // this is filtered to just this user's own before it's used for anything.
  const [ownClaims, setOwnClaims] = useState<{ reimbursementType: string; dateUsed: string; status: string }[]>([]);
  const [receiptFile, setReceiptFile] = useState<File | null>(null);
  const [receiptUrl, setReceiptUrl] = useState('');
  const [isDragging, setIsDragging] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [isUploading, setIsUploading] = useState(false);
  const [message, setMessage] = useState<{ type: 'error' | 'success'; text: string } | null>(null);
  const [validationErrors, setValidationErrors] = useState<Record<string, string>>({});
  const [draftSavedAt, setDraftSavedAt] = useState<Date | null>(() => {
    try { const value = JSON.parse(localStorage.getItem(DRAFT_KEY) || '')?.savedAt; return value ? new Date(value) : null; } catch { return null; }
  });
  // The text fields restore synchronously above (localStorage is already a
  // string); the receipt needs an async decode step, so it restores
  // separately, once, after mount.
  useEffect(() => {
    try {
      const draft = JSON.parse(localStorage.getItem(DRAFT_KEY) || '');
      if (draft?.receipt) {
        dataUrlToFile(draft.receipt).then(setReceiptFile).catch(() => {});
      }
    } catch {
      /* no draft, or malformed — nothing to restore */
    }
  }, []);

  // Fetched once for a fast client-side duplicate warning; the backend
  // enforces this regardless (see create_reimbursement), so a stale list
  // here only affects how early the error shows, never whether it's caught.
  useEffect(() => {
    if (!user?.id) return;
    reimbursementService.getReimbursementRequests()
      .then((list: { employeeId: string; reimbursementType: string; dateUsed: string; status: string }[]) => {
        setOwnClaims(list.filter((claim) => claim.employeeId === String(user.id)));
      })
      .catch(() => setOwnClaims([]));
  }, [user?.id]);

  const update = (field: keyof ReimbursementForm, value: string) => {
    setFormData((current) => ({ ...current, [field]: value }));
    setValidationErrors((current) => ({ ...current, [field]: '' }));
    setMessage(null);
  };

  const acceptFile = (file?: File) => {
    if (!file) return;
    if (!ACCEPTED_TYPES.includes(file.type)) {
      setMessage({ type: 'error', text: 'Use a PDF, JPG, PNG, GIF, or WEBP receipt.' });
      return;
    }
    if (file.size > MAX_RECEIPT_BYTES) {
      setMessage({ type: 'error', text: 'The receipt must be 5 MB or smaller.' });
      return;
    }
    setReceiptFile(file);
    setReceiptUrl('');
    setMessage(null);
  };

  const clearReceipt = () => {
    setReceiptFile(null);
    setReceiptUrl('');
    if (fileInputRef.current) fileInputRef.current.value = '';
  };

  const validate = () => {
    const errors: Record<string, string> = {};
    if (!formData.reimbursementType) errors.reimbursementType = 'Select an expense category.';
    if (!formData.amount || Number(formData.amount) <= 0) errors.amount = 'Enter an amount greater than zero.';
    if (!formData.dateOfExpense) errors.dateOfExpense = 'Select the date of expense.';
    else if (formData.dateOfExpense > today()) errors.dateOfExpense = 'The expense date cannot be in the future.';
    if (formData.description.trim().length < 5) errors.description = 'Add a description of at least 5 characters.';

    // Fast local echo of the backend's own guard (create_reimbursement) —
    // same category, same expense date, anything not already rejected.
    if (!errors.reimbursementType && !errors.dateOfExpense) {
      const duplicate = ownClaims.find((claim) =>
        claim.reimbursementType === formData.reimbursementType
        && claim.dateUsed === formData.dateOfExpense
        && claim.status !== 'rejected');
      if (duplicate) {
        errors.dateOfExpense = `You already have a ${duplicate.status} claim in this category for this date. `
          + 'Edit that request, or use a different category or date.';
      }
    }
    setValidationErrors(errors);

    const firstInvalid = formRef.current?.querySelector<HTMLElement>('[aria-invalid="true"]');
    firstInvalid?.focus();

    return Object.keys(errors).length === 0;
  };

  const handleSubmit = async (event: FormEvent) => {
    event.preventDefault();
    if (!validate()) return;
    setIsSubmitting(true);
    setMessage(null);
    try {
      // A receipt already pushed to storage on a previous failed attempt is
      // reused, so a retry never uploads the same file twice.
      let finalReceiptUrl = receiptUrl || undefined;
      if (receiptFile && !receiptUrl) {
        setIsUploading(true);
        const upload = new FormData();
        upload.append('file', receiptFile);
        finalReceiptUrl = await reimbursementService.uploadReceipt(upload);
        setReceiptUrl(finalReceiptUrl);
        setIsUploading(false);
      }
      await reimbursementService.applyReimbursement({
        reimbursementType: formData.reimbursementType,
        amount: Number(formData.amount),
        dateOfExpense: formData.dateOfExpense,
        description: formData.description.trim(),
        receiptUrl: finalReceiptUrl,
      });
      localStorage.removeItem(DRAFT_KEY);
      setFormData(emptyForm);
      clearReceipt();
      setDraftSavedAt(null);
      setMessage({ type: 'success', text: 'Reimbursement request submitted for review.' });
    } catch (error: unknown) {
      setMessage({
        type: 'error',
        text: readErrorMessage(error, 'We could not submit this reimbursement. Please try again.'),
      });
    } finally {
      setIsUploading(false);
      setIsSubmitting(false);
    }
  };

  const saveDraft = async () => {
    const savedAt = new Date();
    const payload: { form: ReimbursementForm; savedAt: string; receipt?: DraftFile } = {
      form: formData,
      savedAt: savedAt.toISOString(),
    };
    try {
      if (receiptFile) {
        payload.receipt = {
          name: receiptFile.name,
          type: receiptFile.type,
          size: receiptFile.size,
          dataUrl: await fileToDataUrl(receiptFile),
        };
      }
      localStorage.setItem(DRAFT_KEY, JSON.stringify(payload));
    } catch {
      // Quota exceeded (a large receipt pushes the base64 draft over
      // localStorage's limit) — still save the text fields rather than
      // losing the whole draft over one large file.
      try {
        localStorage.setItem(DRAFT_KEY, JSON.stringify({ form: formData, savedAt: savedAt.toISOString() }));
      } catch {
        /* localStorage unavailable entirely — nothing more we can do */
      }
    }
    setDraftSavedAt(savedAt);
    setMessage(null);
  };

  const reset = () => {
    setFormData(emptyForm);
    clearReceipt();
    setValidationErrors({});
    setMessage(null);
    setDraftSavedAt(null);
    localStorage.removeItem(DRAFT_KEY);
  };

  const claimTotal = Number(formData.amount) > 0
    ? `Rs. ${Number(formData.amount).toLocaleString('en-PK', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
    : 'Rs. 0.00';

  return <div className="reimbursement-page">
    <div className="reimbursement-policy-note">
      <FiAlertCircle />
      <span>Submit expenses within 30 days of purchase and attach a readable receipt when available.</span>
    </div>

    <section className="reimbursement-card">
      <header className="reimbursement-form-header">
        <i><FiFileText /></i>
        <div>
          <h2>Reimbursement request</h2>
          <p>Enter the expense information exactly as it appears on your receipt.</p>
        </div>
        <span>Required fields marked <b>*</b></span>
      </header>

      <form ref={formRef} onSubmit={handleSubmit} className="reimbursement-form" noValidate>
        <div className="reimbursement-fields">
          <label className="rb-field" htmlFor="rb-type">Reimbursement type *
            <select
              id="rb-type"
              value={formData.reimbursementType}
              onChange={(event) => update('reimbursementType', event.target.value)}
              aria-invalid={Boolean(validationErrors.reimbursementType)}
              aria-describedby="rb-type-hint"
            >
              <option value="">Select reimbursement type</option>
              {REIMBURSEMENT_TYPES.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
            </select>
            <small id="rb-type-hint" className={validationErrors.reimbursementType ? 'error' : ''}>
              {validationErrors.reimbursementType || 'Choose the category that best describes the expense.'}
            </small>
          </label>

          <label className="rb-field" htmlFor="rb-amount">Total amount *
            <span className="rb-amount-control">
              <b>PKR</b>
              <input
                id="rb-amount"
                type="number"
                inputMode="decimal"
                min="1"
                step="0.01"
                placeholder="0.00"
                value={formData.amount}
                onChange={(event) => update('amount', event.target.value)}
                aria-invalid={Boolean(validationErrors.amount)}
                aria-describedby="rb-amount-hint"
              />
            </span>
            <small id="rb-amount-hint" className={validationErrors.amount ? 'error' : ''}>
              {validationErrors.amount || 'Enter the total amount in Pakistani rupees.'}
            </small>
          </label>

          <label className="rb-field" htmlFor="rb-date">Date of expense *
            <input
              id="rb-date"
              type="date"
              max={today()}
              value={formData.dateOfExpense}
              onChange={(event) => update('dateOfExpense', event.target.value)}
              aria-invalid={Boolean(validationErrors.dateOfExpense)}
              aria-describedby="rb-date-hint"
            />
            <small id="rb-date-hint" className={validationErrors.dateOfExpense ? 'error' : ''}>
              {validationErrors.dateOfExpense || 'Select the date shown on the receipt.'}
            </small>
          </label>

          <div className="rb-expense-summary">
            <span>Claim total</span>
            <div className="rb-summary-box"><strong>{claimTotal}</strong></div>
            <small>This request will be reviewed before reimbursement.</small>
          </div>

          <label className="rb-field wide" htmlFor="rb-description">Description *
            <textarea
              id="rb-description"
              rows={4}
              maxLength={500}
              placeholder="Briefly describe the business expense"
              value={formData.description}
              onChange={(event) => update('description', event.target.value)}
              aria-invalid={Boolean(validationErrors.description)}
              aria-describedby="rb-description-hint"
            />
            <small id="rb-description-hint" className={validationErrors.description ? 'error counter' : 'counter'}>
              {validationErrors.description || `${formData.description.length}/500 characters`}
            </small>
          </label>

          <div className="rb-upload-field wide">
            <label htmlFor="rb-receipt">Attach receipt <span>Optional</span></label>
            <div
              className={`rb-dropzone${isDragging ? ' dragging' : ''}`}
              onDragOver={(event) => { event.preventDefault(); setIsDragging(true); }}
              onDragLeave={() => setIsDragging(false)}
              onDrop={(event) => { event.preventDefault(); setIsDragging(false); acceptFile(event.dataTransfer.files[0]); }}
            >
              <input
                ref={fileInputRef}
                id="rb-receipt"
                type="file"
                accept=".pdf,.jpg,.jpeg,.png,.gif,.webp"
                onChange={(event) => {
                  acceptFile(event.target.files?.[0]);
                  // Clearing lets the same file be picked again after a rejection.
                  event.target.value = '';
                }}
              />
              {receiptFile
                ? <div className="rb-file-preview">
                    <FiFileText />
                    <span>
                      <strong>{receiptFile.name}</strong>
                      <small>{(receiptFile.size / 1024 / 1024).toFixed(2)} MB · {receiptUrl ? 'Uploaded' : 'Ready to upload'}</small>
                    </span>
                    <button type="button" onClick={clearReceipt} aria-label={`Remove receipt ${receiptFile.name}`}><FiTrash2 /></button>
                  </div>
                : <button type="button" onClick={() => fileInputRef.current?.click()}>
                    <FiUploadCloud />
                    <span>
                      <strong>Drop your receipt here, or choose a file</strong>
                      <small>PDF, JPG, PNG, GIF, WEBP · Maximum 5 MB</small>
                    </span>
                    <em>Choose file</em>
                  </button>}
            </div>
          </div>
        </div>

        {message && <div className={`rb-form-message ${message.type}`} role={message.type === 'error' ? 'alert' : 'status'}>
          {message.type === 'success' ? <FiCheck /> : <FiAlertCircle />}
          <span>{message.text}</span>
        </div>}

        <footer className="reimbursement-actions">
          <span>{draftSavedAt
            ? `Draft saved at ${draftSavedAt.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}`
            : 'Receipt uploads when you submit'}</span>
          <button type="button" className="reset" onClick={reset}>Reset</button>
          <button type="button" className="draft" onClick={saveDraft}><FiSave /> Save draft</button>
          <button type="submit" className="submit" disabled={isSubmitting}>
            <FiSend /> {isUploading ? 'Uploading receipt…' : isSubmitting ? 'Submitting…' : 'Submit request'}
          </button>
        </footer>
      </form>
    </section>
  </div>;
};

export default Reimbursement;
