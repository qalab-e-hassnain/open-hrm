import { useCallback, useEffect, useRef, useState } from 'react';
import {
  FiAlertTriangle, FiCheck, FiChevronLeft, FiEye, FiFileText, FiPlus, FiSave,
} from 'react-icons/fi';
import {
  lifecycleService,
  type DocumentTemplate, type PlaceholderCatalogue, type TemplateStarter, type TemplatePreview,
} from '../../services/lifecycleService';
import { type Employee } from '../../types';

/**
 * Authoring a new template.
 *
 * The order here is forced by the API and worth stating: preview takes a
 * template id, so unsaved text cannot be rendered. The flow therefore saves a
 * draft first, previews that, and only activates once it renders clean —
 * otherwise the author is writing blind and finds out after issuing.
 */
const TemplateAuthor = ({ employees, onDone, onCancel, onError }: {
  employees: Employee[];
  onDone: (text: string) => void;
  onCancel: () => void;
  onError: (text: string) => void;
}) => {
  const [starters, setStarters] = useState<TemplateStarter[]>([]);
  const [catalogue, setCatalogue] = useState<PlaceholderCatalogue | null>(null);
  const [chosen, setChosen] = useState<TemplateStarter | null>(null);

  const [title, setTitle] = useState('');
  const [bodyHtml, setBodyHtml] = useState('');
  const [requiresSignature, setRequiresSignature] = useState(true);
  const [includeInOnboarding, setIncludeInOnboarding] = useState(false);

  const [draft, setDraft] = useState<DocumentTemplate | null>(null);
  const [previewEmployeeId, setPreviewEmployeeId] = useState('');
  const [preview, setPreview] = useState<TemplatePreview | null>(null);
  const [busy, setBusy] = useState<'' | 'saving' | 'previewing' | 'activating'>('');
  const bodyRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    Promise.allSettled([lifecycleService.starters(), lifecycleService.placeholders()])
      .then(([s, c]) => {
        if (s.status === 'fulfilled') setStarters(s.value);
        if (c.status === 'fulfilled') setCatalogue(c.value);
      });
  }, []);

  const pick = (starter: TemplateStarter) => {
    setChosen(starter);
    setTitle(starter.title);
    setBodyHtml(starter.body_html);
    setRequiresSignature(starter.requires_signature);
  };

  // Editing the text invalidates whatever was previewed from it.
  const editBody = (value: string) => {
    setBodyHtml(value);
    setPreview(null);
  };

  /** Insert a placeholder where the cursor is, not at the end. */
  const insert = useCallback((key: string) => {
    const el = bodyRef.current;
    const token = `{{${key}}}`;
    if (!el) { editBody(bodyHtml + token); return; }
    const start = el.selectionStart ?? bodyHtml.length;
    const end = el.selectionEnd ?? start;
    const next = bodyHtml.slice(0, start) + token + bodyHtml.slice(end);
    editBody(next);
    requestAnimationFrame(() => {
      el.focus();
      el.setSelectionRange(start + token.length, start + token.length);
    });
  }, [bodyHtml]);

  const saveDraft = async () => {
    setBusy('saving');
    try {
      if (draft) {
        // Editing an existing draft publishes a new version, so the id moves.
        const next = await lifecycleService.updateTemplate(draft.id, {
          title: title.trim(), body_html: bodyHtml,
          requires_signature: requiresSignature, include_in_onboarding: includeInOnboarding,
        });
        setDraft(next);
      } else {
        const next = await lifecycleService.createTemplate({
          kind: chosen?.kind || 'policy',
          title: title.trim(),
          body_html: bodyHtml,
          requires_signature: requiresSignature,
          include_in_onboarding: includeInOnboarding,
          // Inactive until it has been previewed — a half-written policy that
          // is live is one that gets issued.
          is_active: false,
        });
        setDraft(next);
      }
      setPreview(null);
      onDone('Draft saved. Preview it against a real employee before activating.');
    } catch (err: any) {
      onError(err?.message || 'The draft could not be saved.');
    } finally {
      setBusy('');
    }
  };

  const runPreview = async () => {
    if (!draft || !previewEmployeeId) return;
    setBusy('previewing');
    try {
      setPreview(await lifecycleService.previewTemplate(draft.id, Number(previewEmployeeId)));
    } catch (err: any) {
      onError(err?.message || 'The preview could not be generated.');
    } finally {
      setBusy('');
    }
  };

  const activate = async () => {
    if (!draft) return;
    setBusy('activating');
    try {
      await lifecycleService.updateTemplate(draft.id, { is_active: true } as never);
      onDone(`“${title.trim()}” is active and can now be issued.`);
      onCancel();
    } catch (err: any) {
      onError(err?.message || 'The template could not be activated.');
    } finally {
      setBusy('');
    }
  };

  const missing = preview?.missing_placeholders ?? [];
  const previewClean = Boolean(preview) && missing.length === 0;

  /* -------------------------- Step 1: a starter ------------------------- */
  if (!chosen) {
    return (
      <div className="hrd-card hrd-issue">
        <button className="hrd-back" onClick={onCancel}><FiChevronLeft size={15} /> Back to templates</button>
        <h2 className="hrd-step-title">Start from a skeleton</h2>
        <p className="hrd-step-note">
          These carry the document's shape, the right placeholders and the standard signature
          block — not finished legal text. Each section is a heading over a marker showing where
          the company's own wording belongs, so what is missing stays visible on screen rather
          than being discovered after signing.
        </p>

        <div className="hrd-starters">
          {starters.map((starter) => (
            <button key={starter.key} className="hrd-starter" onClick={() => pick(starter)}>
              <strong>{starter.title}</strong>
              <span className="hrd-starter-kind">{starter.kind}</span>
              {/* Cautions the author needs before writing, not after. */}
              {starter.note && (
                <span className="hrd-starter-note">
                  <FiAlertTriangle size={13} /> {starter.note}
                </span>
              )}
            </button>
          ))}
        </div>
      </div>
    );
  }

  /* --------------------- Step 2: write, preview, activate --------------- */
  return (
    <div className="hrd-card hrd-issue">
      <button className="hrd-back" onClick={onCancel}><FiChevronLeft size={15} /> Back to templates</button>

      <div className="hrd-grid">
        <label className="hrd-field">
          <span>Title</span>
          <input value={title} onChange={(e) => setTitle(e.target.value)} />
        </label>
        <label className="hrd-field">
          <span>Kind</span>
          <input value={chosen.kind} disabled />
        </label>
      </div>

      <div className="hrd-toggles">
        <label className="hrd-toggle">
          <input type="checkbox" checked={requiresSignature}
            onChange={(e) => setRequiresSignature(e.target.checked)} />
          <span>Requires a signature</span>
        </label>
        <label className="hrd-toggle">
          <input type="checkbox" checked={includeInOnboarding}
            onChange={(e) => setIncludeInOnboarding(e.target.checked)} />
          <span>Issue automatically to every new joiner</span>
        </label>
      </div>

      {/* Said plainly, because the consequence lands on a person: a
          half-finished policy set to auto-issue means the next hire signs a
          document that still reads "[ Replace this... ]". */}
      {includeInOnboarding && (
        <p className="hrd-version-note">
          <FiAlertTriangle size={14} />
          Every new joiner will be issued this document automatically, as part of their onboarding
          checklist. Do not turn this on until the wording is final — the next person hired will
          sign whatever it says.
        </p>
      )}

      <div className="hrd-author">
        <label className="hrd-field hrd-author-body">
          <span>Body (HTML)</span>
          <textarea
            ref={bodyRef}
            className="hrd-code"
            rows={20}
            value={bodyHtml}
            onChange={(e) => editBody(e.target.value)}
            spellCheck={false}
          />
        </label>

        {/* Listed from the API, never hardcoded: the catalogue comes from the
            same context builder the renderer uses, so it cannot drift from
            what actually substitutes. */}
        <aside className="hrd-placeholders">
          <span className="hrd-lines-label">
            Placeholders
            {catalogue?.sample_employee && <small> · examples from {catalogue.sample_employee}</small>}
          </span>
          {(catalogue?.groups ?? []).map((group) => (
            <div key={group.name} className="hrd-ph-group">
              <h5>{group.name}</h5>
              {group.placeholders.map((item) => (
                <button
                  key={item.key}
                  type="button"
                  className="hrd-ph"
                  title={item.description}
                  onClick={() => insert(item.key)}
                >
                  <code>{`{{${item.key}}}`}</code>
                  <em>{item.example || item.description}</em>
                </button>
              ))}
            </div>
          ))}
        </aside>
      </div>

      <div className="hrd-actions">
        <button className="hrd-btn hrd-btn-ghost" disabled={!title.trim() || busy !== ''} onClick={saveDraft}>
          <FiSave size={15} /> {busy === 'saving' ? 'Saving…' : draft ? 'Save draft' : 'Save as draft'}
        </button>
      </div>

      {/* Preview needs a saved id, so it stays out of reach until there is a
          draft to render — rather than failing with something cryptic. */}
      {!draft ? (
        <p className="hrd-step-note">
          <FiFileText size={14} /> Save a draft to preview it. Preview renders a stored template,
          so unsaved text cannot be checked.
        </p>
      ) : (
        <>
          <div className="hrd-grid">
            <label className="hrd-field">
              <span>Preview against</span>
              <select value={previewEmployeeId} onChange={(e) => { setPreviewEmployeeId(e.target.value); setPreview(null); }}>
                <option value="">Choose an employee</option>
                {employees.map((e) => (
                  <option key={e.id} value={e.id}>{e.name}{e.designation ? ` — ${e.designation}` : ''}</option>
                ))}
              </select>
            </label>
          </div>

          <div className="hrd-actions">
            <button className="hrd-btn hrd-btn-ghost" disabled={!previewEmployeeId || busy !== ''}
              onClick={runPreview}>
              <FiEye size={15} /> {busy === 'previewing' ? 'Rendering…' : 'Preview'}
            </button>
            <button className="hrd-btn hrd-btn-primary" disabled={!previewClean || busy !== ''}
              onClick={activate}>
              <FiCheck size={15} /> {busy === 'activating' ? 'Activating…' : 'Activate template'}
            </button>
          </div>

          {preview && missing.length > 0 && (
            <div className="hrd-warn" role="alert">
              <FiAlertTriangle size={16} />
              <div>
                <strong>{missing.length} placeholder{missing.length === 1 ? '' : 's'} did not resolve</strong>
                <span>
                  Either the key is misspelled, or this employee's record cannot fill it. Fix it
                  before activating — every document issued from this template would carry the gap.
                </span>
                <ul className="hrd-missing">
                  {missing.map((field) => <li key={field}><code>{field}</code></li>)}
                </ul>
              </div>
            </div>
          )}

          {previewClean && (
            <p className="hrd-ready"><FiCheck size={14} /> Everything resolved. Safe to activate.</p>
          )}

          {preview && (
            <div className="hrd-preview">
              <span className="hrd-preview-label"><FiFileText size={14} /> Preview</span>
              <div className="hrd-preview-body">
                <article className="legal-doc" dangerouslySetInnerHTML={{ __html: preview.body_html }} />
              </div>
            </div>
          )}
        </>
      )}
    </div>
  );
};

export const NewTemplateButton = ({ onClick }: { onClick: () => void }) => (
  <button className="hrd-btn hrd-btn-primary" onClick={onClick}>
    <FiPlus size={15} /> New template
  </button>
);

export default TemplateAuthor;
