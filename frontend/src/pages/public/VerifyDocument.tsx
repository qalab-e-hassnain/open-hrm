import { useCallback, useEffect, useState, type FormEvent } from 'react';
import { useParams, useSearchParams } from 'react-router-dom';
import { FiCheckCircle, FiMail, FiSearch, FiShield, FiXCircle } from 'react-icons/fi';
import { lifecycleService, type DocumentVerification } from '../../services/lifecycleService';
import { COMPANY } from '../../config/constants';
import './VerifyPayslip.css';

/**
 * Public verification for a signed contract or NOC.
 *
 * Two answers only, deliberately. `valid: false` covers an unknown code, a
 * document not yet signed, and one that failed its integrity check — the
 * server does not distinguish them, and guessing which on the reader's behalf
 * would be inventing detail it declined to give. One honest message instead.
 *
 * It never returns the document body, so nothing here can leak the contract.
 */
const VerifyDocument = () => {
  const { code: codeFromPath } = useParams<{ code: string }>();
  const [searchParams] = useSearchParams();
  const [code, setCode] = useState(codeFromPath || searchParams.get('code') || '');
  const [result, setResult] = useState<DocumentVerification | null>(null);
  const [isChecking, setIsChecking] = useState(false);
  const [error, setError] = useState('');

  const check = useCallback(async (value: string) => {
    const trimmed = value.trim();
    if (!trimmed) return;
    setIsChecking(true);
    setError('');
    setResult(null);
    try {
      setResult(await lifecycleService.verifyDocument(trimmed));
    } catch {
      setError('The verification service could not be reached. Please try again shortly.');
    } finally {
      setIsChecking(false);
    }
  }, []);

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
    `Document verification${code ? ` — ${code}` : ''}`,
  )}`;

  return (
    <main className="vp-page">
      <section className="vp-card">
        <header className="vp-header">
          <i aria-hidden="true"><FiShield /></i>
          <div>
            <h1>Verify a document</h1>
            <p>
              Confirm that a contract or certificate was issued and signed at {COMPANY.NAME}.
              No account is needed.
            </p>
          </div>
        </header>

        <form className="vp-form" onSubmit={(e: FormEvent) => { e.preventDefault(); void check(code); }}>
          <label htmlFor="vd-code">Verification code</label>
          <div className="vp-input-row">
            <input
              id="vd-code"
              value={code}
              onChange={(event) => setCode(event.target.value)}
              placeholder="The code printed on the document"
              autoComplete="off"
              spellCheck={false}
            />
            <button type="submit" disabled={isChecking || !code.trim()}>
              <FiSearch aria-hidden="true" /> {isChecking ? 'Checking…' : 'Verify'}
            </button>
          </div>
        </form>

        {error && <p className="vp-error" role="alert">{error}</p>}

        {result?.valid && (
          <div className="vp-result vp-valid" role="status">
            <h2><FiCheckCircle aria-hidden="true" /> Signed document confirmed</h2>
            <p>This document was issued by {COMPANY.NAME} and signed by the person named below.</p>
            <dl>
              <div><dt>Document</dt><dd>{result.title || '—'}</dd></div>
              <div><dt>Signed by</dt><dd>{result.employee_name || '—'}</dd></div>
              <div><dt>Signed on</dt><dd>{date(result.signed_at)}</dd></div>
              <div><dt>Checked on</dt><dd>{date(result.verified_at)}</dd></div>
            </dl>
            {result.content_hash && (
              <p className="vp-revision">
                Content reference <code>{result.content_hash.slice(0, 16)}</code> — the document
                has not been altered since it was signed.
              </p>
            )}
          </div>
        )}

        {result && !result.valid && (
          <div className="vp-result vp-invalid" role="status">
            <h2><FiXCircle aria-hidden="true" /> We could not confirm this document</h2>
            <p>
              Nothing signed matches this code. That can mean the code is wrong, the document has
              not been signed yet, or it no longer matches the version that was issued.
            </p>
            <p className="vp-note">
              Email us at <a href={mailto}><FiMail aria-hidden="true" /> {COMPANY.EMAIL}</a> quoting
              the code and we will check it by hand.
            </p>
          </div>
        )}

        <footer className="vp-footer">
          Verification confirms only that a document exists, is signed, and is unaltered. The
          document itself is never disclosed here.
        </footer>
      </section>
    </main>
  );
};

export default VerifyDocument;
