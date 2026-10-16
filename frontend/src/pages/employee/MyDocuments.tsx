import { useCallback, useEffect, useRef, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import {
  FiAlertTriangle, FiCheckCircle, FiChevronLeft, FiClock, FiFileText, FiShield, FiX,
} from 'react-icons/fi';
import { lifecycleService, type DocumentDetail, type DocumentSummary } from '../../services/lifecycleService';
import { useAuthStore } from '../../store/authStore';
import './legal-doc.css';
import './MyDocuments.css';

/**
 * The exact sentence the signer agrees to.
 *
 * Sent to the server as `intent_statement` and stored with the signature,
 * because what someone was shown matters more than that a box was ticked. Any
 * change to this wording changes what future signatures mean — old signatures
 * keep the wording they were given.
 */
const INTENT_STATEMENT =
  'I have read this document in full and I agree to be bound by it.';

const longDateTime = (value?: string | null) => {
  if (!value) return '—';
  const d = new Date(value);
  return Number.isNaN(d.getTime())
    ? value
    : d.toLocaleString('en-GB', {
      day: 'numeric', month: 'long', year: 'numeric', hour: '2-digit', minute: '2-digit',
    });
};

const longDate = (value?: string | null) => {
  if (!value) return '—';
  const d = new Date(value);
  return Number.isNaN(d.getTime())
    ? value
    : d.toLocaleDateString('en-GB', { day: 'numeric', month: 'long', year: 'numeric' });
};

const STATUS_TONE: Record<string, string> = {
  issued: 'wait', viewed: 'wait', signed: 'ok', declined: 'bad', void: 'muted',
};
const STATUS_LABEL: Record<string, string> = {
  issued: 'Awaiting signature', viewed: 'Awaiting signature',
  signed: 'Signed', declined: 'Declined', void: 'Void',
};

const MyDocuments = () => {
  const { user } = useAuthStore();
  const [params, setParams] = useSearchParams();
  const openId = params.get('doc');

  const [documents, setDocuments] = useState<DocumentSummary[]>([]);
  const [detail, setDetail] = useState<DocumentDetail | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [isOpening, setIsOpening] = useState(false);
  const [error, setError] = useState('');

  const load = useCallback(async () => {
    setIsLoading(true);
    try {
      setDocuments(await lifecycleService.myDocuments());
    } catch {
      setError('Your documents could not be loaded. Please try again.');
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  const open = useCallback(async (id: number) => {
    setIsOpening(true);
    setError('');
    try {
      setDetail(await lifecycleService.getDocument(id));
    } catch (err: any) {
      // 403 here means the signed-in account is not the person named on the
      // document — a different problem from "something went wrong".
      setError(err?.status === 403 || /permission/i.test(err?.message || '')
        ? 'This document belongs to someone else. Sign in as the person named on it.'
        : err?.message || 'This document could not be opened.');
    } finally {
      setIsOpening(false);
    }
  }, []);

  // A ?doc= link — the onboarding checklist deep-links here — opens straight
  // into the document rather than making the employee find it in the list.
  useEffect(() => {
    if (openId) void open(Number(openId));
    else setDetail(null);
  }, [openId, open]);

  const close = () => {
    const next = new URLSearchParams(params);
    next.delete('doc');
    setParams(next, { replace: true });
    setDetail(null);
    void load();
  };

  if (detail) {
    return (
      <DocumentView
        doc={detail}
        signerName={user?.name || ''}
        onBack={close}
        onChanged={setDetail}
      />
    );
  }

  return (
    <div className="doc-page">
      <header className="doc-head">
        <div>
          <h1>My documents</h1>
          <p>Contracts and policies issued to you, and anything awaiting your signature.</p>
        </div>
      </header>

      {error && <div className="doc-notice doc-notice-bad" role="alert">{error}</div>}

      {isLoading || isOpening ? (
        <div className="doc-card doc-empty">Loading…</div>
      ) : !documents.length ? (
        <div className="doc-card doc-empty">
          <FiFileText size={18} /> No documents have been issued to you yet.
        </div>
      ) : (
        <div className="doc-list">
          {documents.map((item) => (
            <article key={item.id} className="doc-card doc-item">
              <div>
                <h3>{item.title}</h3>
                <p className="doc-meta">
                  Issued {longDate(item.issued_at)}
                  {item.due_at ? <> · due {longDate(item.due_at)}</> : null}
                </p>
              </div>
              <div className="doc-item-right">
                <span className={`doc-status doc-status-${STATUS_TONE[item.status] || 'muted'}`}>
                  {STATUS_LABEL[item.status] || item.status}
                </span>
                <button
                  className="doc-btn doc-btn-primary"
                  onClick={() => setParams({ doc: String(item.id) }, { replace: false })}
                >
                  {item.requires_signature && item.status !== 'signed' ? 'Read and sign' : 'Open'}
                </button>
              </div>
            </article>
          ))}
        </div>
      )}
    </div>
  );
};

/* --------------------------- Reading & signing ------------------------- */

const DocumentView = ({ doc, signerName, onBack, onChanged }: {
  doc: DocumentDetail;
  signerName: string;
  onBack: () => void;
  onChanged: (doc: DocumentDetail) => void;
}) => {
  const bodyRef = useRef<HTMLDivElement>(null);
  const [readToEnd, setReadToEnd] = useState(false);
  const [agreed, setAgreed] = useState(false);
  const [typedName, setTypedName] = useState(signerName);
  const [isSaving, setIsSaving] = useState(false);
  const [error, setError] = useState('');
  const [declining, setDeclining] = useState(false);
  const [declineReason, setDeclineReason] = useState('');

  const needsSignature = doc.requires_signature && doc.status !== 'signed' && doc.status !== 'declined';

  // "I read it" is only credible if reading was possible, so the control stays
  // disabled until the document has actually been scrolled to the end. A
  // document short enough not to scroll counts as read on arrival.
  const checkScrolled = useCallback(() => {
    const el = bodyRef.current;
    if (!el) return;
    const atEnd = el.scrollTop + el.clientHeight >= el.scrollHeight - 24;
    if (atEnd) setReadToEnd(true);
  }, []);

  useEffect(() => {
    const el = bodyRef.current;
    if (!el) return;
    if (el.scrollHeight <= el.clientHeight + 24) setReadToEnd(true);
    el.addEventListener('scroll', checkScrolled, { passive: true });
    return () => el.removeEventListener('scroll', checkScrolled);
  }, [checkScrolled, doc.id]);

  const sign = async () => {
    if (!readToEnd || !agreed || typedName.trim().length < 2) return;
    setIsSaving(true);
    setError('');
    try {
      onChanged(await lifecycleService.signDocument(doc.id, {
        typed_name: typedName.trim(),
        agreed: true,
        // The wording actually displayed above the checkbox, not a constant
        // the server assumes.
        intent_statement: INTENT_STATEMENT,
      }));
    } catch (err: any) {
      setError(err?.message || 'This document could not be signed.');
    } finally {
      setIsSaving(false);
    }
  };

  const decline = async () => {
    if (declineReason.trim().length < 3) return;
    setIsSaving(true);
    try {
      onChanged(await lifecycleService.declineDocument(doc.id, declineReason.trim()));
      setDeclining(false);
    } catch (err: any) {
      setError(err?.message || 'This document could not be declined.');
    } finally {
      setIsSaving(false);
    }
  };

  // A document whose stored text no longer matches its hash is not shown at
  // all. Rendering it with a warning invites signing it anyway.
  if (!doc.integrity_ok) {
    return (
      <div className="doc-page">
        <button className="doc-back" onClick={onBack}><FiChevronLeft size={16} /> Back to documents</button>
        <div className="doc-card doc-tampered" role="alert">
          <h2><FiAlertTriangle /> This document failed its integrity check</h2>
          <p>
            <strong>{doc.title}</strong> no longer matches the version that was issued, so it
            cannot be displayed or signed. This needs to be raised with HR — do not sign a
            replacement until they confirm what happened.
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="doc-page doc-reading">
      <button className="doc-back" onClick={onBack}><FiChevronLeft size={16} /> Back to documents</button>

      <header className="doc-head">
        <div>
          <h1>{doc.title}</h1>
          {/* Version and date at the moment of signing: the signer should be
              able to see exactly which text they are agreeing to. */}
          <p className="doc-meta">
            Issued {longDate(doc.issued_at)} · reference{' '}
            <code>{doc.content_hash.slice(0, 12)}</code>
            {doc.due_at ? <> · due {longDate(doc.due_at)}</> : null}
          </p>
        </div>
        <span className={`doc-status doc-status-${STATUS_TONE[doc.status] || 'muted'}`}>
          {STATUS_LABEL[doc.status] || doc.status}
        </span>
      </header>

      {error && <div className="doc-notice doc-notice-bad" role="alert">{error}</div>}

      <div ref={bodyRef} className="doc-card doc-body" tabIndex={0} aria-label="Document text">
        {/* The document's own stylesheet lives here, not inside body_html: the
            content hash covers exactly the bytes that were signed, so folding
            presentation into it would invalidate every existing signature on
            the next CSS tweak. The server sends semantic markup; this supplies
            the paper. */}
        <article
          className="legal-doc"
          // Server-generated and escaped at render time, per the API contract.
          dangerouslySetInnerHTML={{ __html: doc.body_html }}
        />
      </div>

      {doc.status === 'signed' && doc.signature && (
        <div className="doc-card doc-signed" role="status">
          <h2><FiCheckCircle /> Signed</h2>
          <dl>
            <div><dt>Signed by</dt><dd>{doc.signature.typed_name}</dd></div>
            <div><dt>Signed at</dt><dd>{longDateTime(doc.signature.signed_at)}</dd></div>
            {doc.verification_code && (
              <div><dt>Verification code</dt><dd><code>{doc.verification_code}</code></dd></div>
            )}
          </dl>
          {doc.signature.intent_statement && (
            <p className="doc-intent-record">
              <FiShield size={13} /> You agreed to: “{doc.signature.intent_statement}”
            </p>
          )}
        </div>
      )}

      {doc.status === 'declined' && (
        <div className="doc-card doc-notice-bad" role="status">
          <strong>You declined this document.</strong> Speak to HR if that was not intended.
        </div>
      )}

      {needsSignature && (
        <div className="doc-card doc-sign">
          <h2>Sign this document</h2>

          {!readToEnd && (
            <p className="doc-scroll-hint">
              <FiClock size={14} /> Scroll to the end of the document above before signing.
            </p>
          )}

          <label className={`doc-agree${readToEnd ? '' : ' is-locked'}`}>
            <input
              type="checkbox"
              checked={agreed}
              disabled={!readToEnd}
              onChange={(e) => setAgreed(e.target.checked)}
            />
            <span>{INTENT_STATEMENT}</span>
          </label>

          <label className="doc-field">
            <span>Type your full name to sign</span>
            <input
              value={typedName}
              disabled={!readToEnd}
              onChange={(e) => setTypedName(e.target.value)}
              placeholder="Your full name"
            />
          </label>

          <div className="doc-sign-actions">
            <button className="doc-btn doc-btn-ghost" onClick={() => setDeclining(true)}>
              Decline
            </button>
            <button
              className="doc-btn doc-btn-primary"
              disabled={!readToEnd || !agreed || typedName.trim().length < 2 || isSaving}
              onClick={sign}
            >
              {isSaving ? 'Signing…' : 'Sign document'}
            </button>
          </div>
        </div>
      )}

      {declining && (
        <div className="doc-backdrop" onMouseDown={(e) => { if (e.target === e.currentTarget) setDeclining(false); }}>
          <div className="doc-dialog" role="dialog" aria-modal="true" aria-labelledby="doc-decline-title">
            <h2 id="doc-decline-title">Decline this document</h2>
            <p className="doc-meta">Tell HR why. This is recorded against the document.</p>
            <label className="doc-field">
              <span>Reason (required)</span>
              <textarea rows={3} value={declineReason} autoFocus
                onChange={(e) => setDeclineReason(e.target.value)} />
            </label>
            <div className="doc-sign-actions">
              <button className="doc-btn doc-btn-ghost" onClick={() => setDeclining(false)}>
                <FiX size={14} /> Cancel
              </button>
              <button className="doc-btn doc-btn-danger"
                disabled={declineReason.trim().length < 3 || isSaving} onClick={decline}>
                Decline document
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};

export default MyDocuments;
