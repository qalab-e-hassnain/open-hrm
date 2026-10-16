import { useCallback, useEffect, useState, type FormEvent } from 'react';
import { useParams, useSearchParams } from 'react-router-dom';
import { FiCheckCircle, FiXCircle, FiShield, FiSearch } from 'react-icons/fi';
import { payslipService, type PayslipVerification } from '../../services/payslipService';
import './VerifyPayslip.css';
import { COMPANY } from '../../config/constants';

/**
 * Public payslip verification.
 *
 * Every issued payslip prints a code and this page's address, so the audience
 * is a bank, landlord or prospective employer who has no account here and
 * never will. It therefore sits outside ProtectedRoute and calls the
 * unauthenticated verify endpoint directly.
 *
 * The endpoint returns only what proves the document is genuine — never the
 * salary breakdown — so this page shows exactly what it is given and no more.
 */
const VerifyPayslip = () => {
  // The code arrives either in the path (a link or QR printed on the payslip)
  // or typed into the box by someone reading it off paper.
  const { code: codeFromPath } = useParams<{ code: string }>();
  const [searchParams] = useSearchParams();
  const [code, setCode] = useState(codeFromPath || searchParams.get('code') || '');
  const [result, setResult] = useState<PayslipVerification | null>(null);
  const [isChecking, setIsChecking] = useState(false);
  const [error, setError] = useState('');

  const check = useCallback(async (value: string) => {
    const trimmed = value.trim();
    if (!trimmed) return;
    setIsChecking(true);
    setError('');
    setResult(null);
    try {
      setResult(await payslipService.verify(trimmed));
    } catch {
      setError('The verification service could not be reached. Please try again shortly.');
    } finally {
      setIsChecking(false);
    }
  }, []);

  // A code in the URL is checked on arrival, so a printed link resolves in one
  // step rather than asking the visitor to press a button.
  useEffect(() => {
    const preset = codeFromPath || searchParams.get('code');
    if (preset) void check(preset);
  }, [codeFromPath, searchParams, check]);

  const onSubmit = (event: FormEvent) => {
    event.preventDefault();
    void check(code);
  };

  const money = (value?: number | null) =>
    typeof value === 'number'
      ? `Rs ${value.toLocaleString('en-PK', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
      : '—';

  const date = (value?: string | null) => {
    if (!value) return '—';
    const parsed = new Date(value);
    return Number.isNaN(parsed.getTime())
      ? value
      : parsed.toLocaleDateString('en-GB', { day: 'numeric', month: 'long', year: 'numeric' });
  };

  return (
    <main className="vp-page">
      <section className="vp-card">
        <header className="vp-header">
          <i aria-hidden="true"><FiShield /></i>
          <div>
            <h1>Verify a payslip</h1>
            <p>
              Enter the verification code printed on the payslip to confirm it was issued
              by {COMPANY.NAME}. No account is needed.
            </p>
          </div>
        </header>

        <form className="vp-form" onSubmit={onSubmit}>
          <label htmlFor="vp-code">Verification code</label>
          <div className="vp-input-row">
            <input
              id="vp-code"
              value={code}
              onChange={(event) => setCode(event.target.value)}
              placeholder="e.g. QGT-8F3A-21C7"
              autoComplete="off"
              spellCheck={false}
            />
            <button type="submit" disabled={isChecking || !code.trim()}>
              <FiSearch aria-hidden="true" /> {isChecking ? 'Checking…' : 'Verify'}
            </button>
          </div>
        </form>

        {error && <p className="vp-error" role="alert">{error}</p>}

        {result && result.valid && (
          <div className="vp-result vp-valid" role="status">
            <h2><FiCheckCircle aria-hidden="true" /> Genuine payslip</h2>
            <p>This document was issued by {COMPANY.NAME}.</p>
            <dl>
              <div><dt>Payslip number</dt><dd>{result.payslip_no || '—'}</dd></div>
              <div><dt>Employee</dt><dd>{result.employee_name || '—'}</dd></div>
              <div><dt>Pay period</dt><dd>{result.period_label || '—'}</dd></div>
              <div><dt>Net salary</dt><dd>{money(result.net_salary)}</dd></div>
              <div><dt>Issued</dt><dd>{date(result.generated_at)}</dd></div>
              <div><dt>Status</dt><dd className="vp-status">{result.status || '—'}</dd></div>
            </dl>
            {typeof result.version === 'number' && result.version > 1 && (
              <p className="vp-revision">
                This payslip has been revised — it is version {result.version}. Check that the
                figures above match the copy you were given.
              </p>
            )}
          </div>
        )}

        {result && !result.valid && (
          <div className="vp-result vp-invalid" role="status">
            <h2><FiXCircle aria-hidden="true" /> Not recognised</h2>
            <p>
              No issued payslip matches this code. Check for typing errors — codes are
              case-sensitive. If it still does not match, the document may not have been
              issued by {COMPANY.NAME}, or may not have been published yet.
            </p>
          </div>
        )}

        <footer className="vp-footer">
          Verification confirms only that a payslip is genuine. It never reveals the salary
          breakdown, and no record of your check is shared with the employee.
        </footer>
      </section>
    </main>
  );
};

export default VerifyPayslip;
