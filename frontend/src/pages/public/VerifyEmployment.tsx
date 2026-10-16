import { useCallback, useEffect, useState, type FormEvent } from 'react';
import { useParams, useSearchParams } from 'react-router-dom';
import { FiAlertTriangle, FiCheckCircle, FiMail, FiSearch, FiShield, FiXCircle } from 'react-icons/fi';
import { verifyEmployment, type EmploymentVerification } from '../../services/employeeService';
import { COMPANY } from '../../config/constants';
// Shares the verification-page styling rather than restating it.
import './VerifyPayslip.css';

/**
 * Public employment-letter verification.
 *
 * Reads the code printed on the letter and asks the unauthenticated endpoint
 * whether it is genuine. Three answers matter, and they are deliberately not
 * collapsed into two: a letter can be genuine while the employment it
 * describes has since ended, which is precisely what a bank or landlord needs
 * to be told rather than left to assume.
 */
const VerifyEmployment = () => {
  const { code: codeFromPath } = useParams<{ code: string }>();
  const [searchParams] = useSearchParams();
  const [code, setCode] = useState(codeFromPath || searchParams.get('code') || '');
  const [result, setResult] = useState<EmploymentVerification | null>(null);
  const [isChecking, setIsChecking] = useState(false);
  const [error, setError] = useState('');

  const check = useCallback(async (value: string) => {
    const trimmed = value.trim();
    if (!trimmed) return;
    setIsChecking(true);
    setError('');
    setResult(null);
    try {
      setResult(await verifyEmployment(trimmed));
    } catch {
      setError('The verification service could not be reached. Please try again shortly.');
    } finally {
      setIsChecking(false);
    }
  }, []);

  // A code in the URL is checked on arrival, so a scanned QR resolves without
  // the reader pressing anything.
  useEffect(() => {
    const preset = codeFromPath || searchParams.get('code');
    if (preset) void check(preset);
  }, [codeFromPath, searchParams, check]);

  const date = (value?: string | null) => {
    if (!value) return '—';
    const parsed = new Date(value);
    return Number.isNaN(parsed.getTime())
      ? value
      : parsed.toLocaleDateString('en-GB', { day: 'numeric', month: 'long', year: 'numeric' });
  };

  const mailto = `mailto:${COMPANY.EMAIL}?subject=${encodeURIComponent(
    `Employment verification request${code ? ` — ${code}` : ''}`,
  )}`;

  const confirmed = result?.valid && result.currently_employed;
  const formerlyEmployed = result?.valid && !result.currently_employed;

  const particulars = result?.valid && (
    <dl>
      <div><dt>Employee</dt><dd>{result.employee_name || '—'}</dd></div>
      <div><dt>Employee ID</dt><dd>{result.employee_id || '—'}</dd></div>
      <div><dt>Designation</dt><dd>{result.designation || '—'}</dd></div>
      <div><dt>Department</dt><dd>{result.department || '—'}</dd></div>
      <div><dt>Joined</dt><dd>{date(result.date_of_joining)}</dd></div>
      <div><dt>Checked on</dt><dd>{date(result.verified_at)}</dd></div>
    </dl>
  );

  return (
    <main className="vp-page">
      <section className="vp-card">
        <header className="vp-header">
          <i aria-hidden="true"><FiShield /></i>
          <div>
            <h1>Verify an employment letter</h1>
            <p>
              Enter the verification code printed on the letter to confirm it was issued by{' '}
              {COMPANY.NAME}. No account is needed.
            </p>
          </div>
        </header>

        <form className="vp-form" onSubmit={(e: FormEvent) => { e.preventDefault(); void check(code); }}>
          <label htmlFor="ve-code">Verification code</label>
          <div className="vp-input-row">
            <input
              id="ve-code"
              value={code}
              onChange={(event) => setCode(event.target.value)}
              placeholder="e.g. 8Kq3ra0HyAfb"
              autoComplete="off"
              spellCheck={false}
            />
            <button type="submit" disabled={isChecking || !code.trim()}>
              <FiSearch aria-hidden="true" /> {isChecking ? 'Checking…' : 'Verify'}
            </button>
          </div>
        </form>

        {error && <p className="vp-error" role="alert">{error}</p>}

        {confirmed && (
          <div className="vp-result vp-valid" role="status">
            <h2><FiCheckCircle aria-hidden="true" /> Employment confirmed</h2>
            <p>
              This letter was issued by {COMPANY.NAME}, and the person named below is currently
              employed with the company.
            </p>
            {particulars}
          </div>
        )}

        {formerlyEmployed && (
          <div className="vp-result vp-warning" role="status">
            <h2><FiAlertTriangle aria-hidden="true" /> Letter genuine — employment has since ended</h2>
            <p>
              This letter was genuinely issued by {COMPANY.NAME} and the particulars below were
              correct when it was written. The person named is <strong>no longer employed</strong>{' '}
              with the company. If the letter is being relied on as proof of current employment,
              it is out of date.
            </p>
            {particulars}
          </div>
        )}

        {result && !result.valid && (
          <div className="vp-result vp-invalid" role="status">
            <h2><FiXCircle aria-hidden="true" /> We could not verify this letter</h2>
            <p>
              No letter matches this code. Check for typing errors — codes are case-sensitive.
              If it still does not match, the letter may not have been issued by {COMPANY.NAME}.
            </p>
            <p className="vp-note">
              If you believe the letter is genuine, email us at{' '}
              <a href={mailto}><FiMail aria-hidden="true" /> {COMPANY.EMAIL}</a> quoting the code
              and we will check it by hand.
            </p>
          </div>
        )}

        <footer className="vp-footer">
          Verification confirms only that a letter is genuine and whether the employment
          continues. It never discloses salary, CNIC or contact details, and no record of your
          check is shared with the employee.
        </footer>
      </section>
    </main>
  );
};

export default VerifyEmployment;
