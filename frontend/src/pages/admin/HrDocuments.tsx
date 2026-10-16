import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  FiAlertTriangle, FiCheck, FiChevronRight, FiEye, FiFileText, FiPlus, FiSend, FiTrash2, FiX, FiZap,
} from 'react-icons/fi';
import {
  lifecycleService, isProfileUnfilled, PROFILE_PLACEHOLDER,
  type DocumentTemplate, type JobProfile, type TemplatePreview,
} from '../../services/lifecycleService';
import { employeeService } from '../../services/employeeService';
import { type Employee } from '../../types';
import '../employee/legal-doc.css';
import TemplateAuthor, { NewTemplateButton } from './TemplateAuthor';
import { defaultsForDesignation } from './roleDefaults';
import './HrDocuments.css';

type Tab = 'profiles' | 'templates' | 'issue';

/**
 * HR admin for the variable parts of a contract.
 *
 * The job profiles are where Appendix A and Appendix B come from. They ship
 * seeded with "To be completed by HR.", and a contract issued before someone
 * fills them in carries that sentence into a legal document — which is why
 * unfilled profiles are counted and flagged rather than left to be noticed.
 */
const HrDocuments = () => {
  const [tab, setTab] = useState<Tab>('profiles');
  const [authoring, setAuthoring] = useState(false);
  const [profiles, setProfiles] = useState<JobProfile[]>([]);
  const [templates, setTemplates] = useState<DocumentTemplate[]>([]);
  const [employees, setEmployees] = useState<Employee[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [notice, setNotice] = useState<{ tone: 'ok' | 'bad'; text: string } | null>(null);

  const load = useCallback(async () => {
    setIsLoading(true);
    const [p, t, e] = await Promise.allSettled([
      lifecycleService.jobProfiles(),
      lifecycleService.templates(true),
      employeeService.getEmployees(),
    ]);
    if (p.status === 'fulfilled') setProfiles(p.value);
    if (t.status === 'fulfilled') setTemplates(t.value);
    if (e.status === 'fulfilled') setEmployees(e.value.filter((x) => x.status !== 'inactive'));
    setIsLoading(false);
  }, []);

  useEffect(() => { void load(); }, [load]);

  const unfilled = useMemo(() => profiles.filter(isProfileUnfilled).length, [profiles]);

  // Designations somebody actually holds that have no profile row at all.
  // These never appeared anywhere: the list renders job_profiles, so a role
  // nobody had written up was invisible on the page for writing them up —
  // which is every role added since the first eleven, including both the
  // CFO and the two unit leads.
  const missing = useMemo(() => {
    const have = new Set(profiles.map((x) => x.designation.trim().toLowerCase()));
    const counts = new Map<string, number>();
    for (const e of employees) {
      const d = (e.designation || '').trim();
      if (!d || have.has(d.toLowerCase())) continue;
      counts.set(d, (counts.get(d) || 0) + 1);
    }
    return [...counts.entries()].map(([designation, count]) => ({ designation, count }))
      .sort((a, b) => a.designation.localeCompare(b.designation));
  }, [profiles, employees]);

  return (
    <div className="hrd-page">
      <header className="hrd-head">
        <div>
          <h1>Contract content</h1>
          <p>Job profiles feed Appendices A and B. Templates are the contract itself.</p>
        </div>
        <nav className="hrd-tabs">
          <button className={tab === 'profiles' ? 'is-active' : ''} onClick={() => setTab('profiles')}>
            Job profiles
            {unfilled > 0 && <span className="hrd-count hrd-count-alert">{unfilled}</span>}
          </button>
          <button className={tab === 'templates' ? 'is-active' : ''} onClick={() => setTab('templates')}>
            Templates
          </button>
          <button className={tab === 'issue' ? 'is-active' : ''} onClick={() => setTab('issue')}>
            Issue a document
          </button>
        </nav>
      </header>

      {notice && (
        <div className={`hrd-notice hrd-notice-${notice.tone}`} role="status">
          <span>{notice.text}</span>
          <button onClick={() => setNotice(null)} aria-label="Dismiss"><FiX size={15} /></button>
        </div>
      )}

      {unfilled > 0 && tab === 'profiles' && (
        <div className="hrd-warn" role="alert">
          <FiAlertTriangle size={16} />
          <div>
            <strong>{unfilled} of {profiles.length} profiles still say “{PROFILE_PLACEHOLDER}”</strong>
            <span>
              Any contract issued for those designations carries that sentence into Appendix A and
              Appendix B of a legal document. Fill them before issuing.
            </span>
          </div>
        </div>
      )}

      {isLoading ? (
        <div className="hrd-card hrd-empty">Loading…</div>
      ) : tab === 'profiles' ? (
        <ProfileList
          profiles={profiles}
          missing={missing}
          onSaved={(text) => { setNotice({ tone: 'ok', text }); void load(); }}
          onError={(text) => setNotice({ tone: 'bad', text })}
        />
      ) : tab === 'templates' ? (
        authoring ? (
          <TemplateAuthor
            employees={employees}
            onDone={(text) => { setNotice({ tone: 'ok', text }); void load(); }}
            onCancel={() => setAuthoring(false)}
            onError={(text) => setNotice({ tone: 'bad', text })}
          />
        ) : (
          <TemplateList
            templates={templates}
            onNew={() => setAuthoring(true)}
            onSaved={(text) => { setNotice({ tone: 'ok', text }); void load(); }}
            onError={(text) => setNotice({ tone: 'bad', text })}
          />
        )
      ) : (
        <IssuePanel
          templates={templates}
          employees={employees}
          onIssued={(text) => setNotice({ tone: 'ok', text })}
          onError={(text) => setNotice({ tone: 'bad', text })}
        />
      )}
    </div>
  );
};

/* ---------------------------- Job profiles ----------------------------- */

const ProfileList = ({ profiles, missing, onSaved, onError }: {
  profiles: JobProfile[];
  missing: { designation: string; count: number }[];
  onSaved: (text: string) => void;
  onError: (text: string) => void;
}) => {
  const [openId, setOpenId] = useState<number | null>(null);
  const [creating, setCreating] = useState<string | null>(null);

  const start = async (designation: string) => {
    setCreating(designation);
    try {
      await lifecycleService.createJobProfile(designation);
      onSaved(`Started a profile for ${designation}. Open it and press Suggest.`);
    } catch (err) {
      const detail = (err as { response?: { data?: { detail?: unknown } } })
        ?.response?.data?.detail;
      onError(typeof detail === 'string' ? detail : `Could not start ${designation}.`);
    } finally {
      setCreating(null);
    }
  };

  if (!profiles.length && !missing.length) {
    return <div className="hrd-card hrd-empty">No job profiles exist yet.</div>;
  }

  return (
    <div className="hrd-list">
      {missing.length > 0 && (
        <div className="hrd-card hrd-missing">
          <h3>Roles with nobody's job description written</h3>
          <p>
            {missing.length === 1 ? 'One person holds a role' : `${missing.length} roles are held by people`}
            {' '}with no profile, so their contract would be issued with Appendices A and B empty.
          </p>
          {missing.map((m) => (
            <div key={m.designation} className="hrd-missing-row">
              <span>
                <strong>{m.designation}</strong>
                <small>{m.count} {m.count === 1 ? 'person' : 'people'}</small>
              </span>
              <button type="button" className="hrd-btn hrd-btn-ghost"
                      disabled={creating === m.designation}
                      onClick={() => void start(m.designation)}>
                <FiPlus size={14} /> {creating === m.designation ? 'Starting…' : 'Start a profile'}
              </button>
            </div>
          ))}
        </div>
      )}
      {profiles.map((profile) => (
        <div key={profile.id} className="hrd-card hrd-row">
          <button className="hrd-row-head" onClick={() => setOpenId(openId === profile.id ? null : profile.id)}>
            <span className="hrd-row-name">
              {profile.designation}
              {isProfileUnfilled(profile) && <em className="hrd-tag-warn">Unfilled</em>}
            </span>
            <span className="hrd-row-meta">
              {profile.employee_count} {profile.employee_count === 1 ? 'person' : 'people'}
            </span>
            <FiChevronRight size={15} className={openId === profile.id ? 'is-open' : ''} />
          </button>

          {openId === profile.id && (
            <ProfileEditor profile={profile} onSaved={onSaved} onError={onError} />
          )}
        </div>
      ))}
    </div>
  );
};

const ProfileEditor = ({ profile, onSaved, onError }: {
  profile: JobProfile;
  onSaved: (text: string) => void;
  onError: (text: string) => void;
}) => {
  const [summary, setSummary] = useState(profile.summary || '');
  const [department, setDepartment] = useState(profile.department || '');
  const [reportsTo, setReportsTo] = useState(profile.reports_to_title || '');
  const [responsibilities, setResponsibilities] = useState<string[]>(
    profile.responsibilities.length ? profile.responsibilities : [''],
  );
  const [kpis, setKpis] = useState<string[]>(profile.kpis.length ? profile.kpis : ['']);
  const [isSaving, setIsSaving] = useState(false);
  const [suggested, setSuggested] = useState(false);

  // Fills the form, and saves nothing. Eleven profiles written from an empty
  // box is a job nobody finishes; correcting a plausible draft is one people
  // do. What reaches the contract is whatever survives the editing.
  const suggest = () => {
    const draft = defaultsForDesignation(profile.designation);
    setSummary(draft.summary);
    setResponsibilities(draft.responsibilities);
    setKpis(draft.kpis);
    // Department and reporting line come from the draft too, but never
    // overwrite something already filled in: the suggestion is a starting
    // point, and HR's own answer about who somebody reports to beats a
    // keyword match on their job title.
    if (draft.department && !department.trim()) setDepartment(draft.department);
    if (draft.reportsTo && !reportsTo.trim()) setReportsTo(draft.reportsTo);
    setSuggested(true);
  };

  const save = async () => {
    setIsSaving(true);
    try {
      await lifecycleService.updateJobProfile(profile.id, {
        department: department.trim() || undefined,
        reports_to_title: reportsTo.trim() || undefined,
        summary: summary.trim() || undefined,
        // Blank lines are an artefact of editing, not content — a contract
        // should not render an empty bullet.
        responsibilities: responsibilities.map((l) => l.trim()).filter(Boolean),
        kpis: kpis.map((l) => l.trim()).filter(Boolean),
      });
      onSaved(`${profile.designation} saved.`);
    } catch (err: any) {
      onError(err?.message || 'That profile could not be saved.');
    } finally {
      setIsSaving(false);
    }
  };

  return (
    <div className="hrd-editor">
      <div className="hrd-suggest-bar">
        <button type="button" className="hrd-btn hrd-btn-ghost" onClick={suggest}>
          <FiZap size={14} /> Suggest content for this role
        </button>
        <span>
          Fills this form with a draft for {profile.designation}. Nothing is saved until you
          review it and press Save — edit anything that does not match how the role actually
          works here.
        </span>
      </div>

      {suggested && (
        <p className="hrd-version-note">
          <FiAlertTriangle size={14} />
          This is a generic draft, not a job description anyone has approved. Read it through
          before saving — it becomes Appendix A and Appendix B of a contract someone signs.
        </p>
      )}

      <div className="hrd-grid">
        <label className="hrd-field">
          <span>Department</span>
          <input value={department} onChange={(e) => setDepartment(e.target.value)} />
        </label>
        <label className="hrd-field">
          <span>Reports to</span>
          <input value={reportsTo} onChange={(e) => setReportsTo(e.target.value)}
            placeholder="e.g. Chief Technology Officer" />
        </label>
      </div>

      <label className="hrd-field">
        <span>Role summary</span>
        <textarea rows={3} value={summary} onChange={(e) => setSummary(e.target.value)}
          placeholder="One paragraph describing the role." />
      </label>

      {/* Line editors, not a textarea: these are arrays that render as the
          bullet lists in the appendices, and a textarea would smuggle the
          separator into the content. */}
      <LineEditor
        label="Responsibilities — Appendix A"
        lines={responsibilities}
        onChange={setResponsibilities}
        placeholder="One responsibility per line"
      />
      <LineEditor
        label="KPIs — Appendix B"
        lines={kpis}
        onChange={setKpis}
        placeholder="One measure per line"
      />

      <div className="hrd-actions">
        <button className="hrd-btn hrd-btn-primary" disabled={isSaving} onClick={save}>
          {isSaving ? 'Saving…' : 'Save profile'}
        </button>
      </div>
    </div>
  );
};

const LineEditor = ({ label, lines, onChange, placeholder }: {
  label: string;
  lines: string[];
  onChange: (lines: string[]) => void;
  placeholder: string;
}) => (
  <div className="hrd-lines">
    <span className="hrd-lines-label">{label}</span>
    {lines.map((line, index) => (
      <div key={index} className="hrd-line">
        <input
          value={line}
          placeholder={placeholder}
          onChange={(e) => onChange(lines.map((l, i) => (i === index ? e.target.value : l)))}
        />
        <button
          type="button"
          aria-label="Remove this line"
          onClick={() => onChange(lines.length === 1 ? [''] : lines.filter((_, i) => i !== index))}
        >
          <FiTrash2 size={14} />
        </button>
      </div>
    ))}
    <button type="button" className="hrd-add" onClick={() => onChange([...lines, ''])}>
      <FiPlus size={14} /> Add line
    </button>
  </div>
);

/* ------------------------------ Templates ------------------------------ */

const TemplateList = ({ templates, onNew, onSaved, onError }: {
  templates: DocumentTemplate[];
  onNew: () => void;
  onSaved: (text: string) => void;
  onError: (text: string) => void;
}) => {
  const [openId, setOpenId] = useState<number | null>(null);

  return (
    <div className="hrd-list">
      <div className="hrd-actions"><NewTemplateButton onClick={onNew} /></div>

      {!templates.length && <div className="hrd-card hrd-empty">No templates exist yet.</div>}

      {templates.map((template) => (
        <div key={template.id} className="hrd-card hrd-row">
          <button className="hrd-row-head" onClick={() => setOpenId(openId === template.id ? null : template.id)}>
            <span className="hrd-row-name">
              {template.title}
              {/* A draft is not issuable, and looks identical otherwise. */}
              {!template.is_active && <em className="hrd-tag-warn">Draft</em>}
              {template.include_in_onboarding && <em className="hrd-tag-auto">Auto-issued</em>}
            </span>
            <span className="hrd-row-meta">v{template.version} · {template.kind}</span>
            <FiChevronRight size={15} className={openId === template.id ? 'is-open' : ''} />
          </button>

          {openId === template.id && (
            <TemplateEditor template={template} onSaved={onSaved} onError={onError} />
          )}
        </div>
      ))}
    </div>
  );
};

const TemplateEditor = ({ template, onSaved, onError }: {
  template: DocumentTemplate;
  onSaved: (text: string) => void;
  onError: (text: string) => void;
}) => {
  const [title, setTitle] = useState(template.title);
  const [bodyHtml, setBodyHtml] = useState(template.body_html);
  const [isSaving, setIsSaving] = useState(false);
  const [confirming, setConfirming] = useState(false);

  const save = async () => {
    setIsSaving(true);
    try {
      const next = await lifecycleService.updateTemplate(template.id, {
        title: title.trim(),
        body_html: bodyHtml,
      });
      setConfirming(false);
      onSaved(`Published version ${next.version}. Documents already issued keep their text.`);
    } catch (err: any) {
      setConfirming(false);
      onError(err?.message || 'That template could not be saved.');
    } finally {
      setIsSaving(false);
    }
  };

  return (
    <div className="hrd-editor">
      {/* Without saying this, publishing looks like a save that lost the
          original — the id changes and the old version is retired. */}
      <p className="hrd-version-note">
        <FiAlertTriangle size={14} />
        Saving publishes <strong>version {template.version + 1}</strong> and retires the current
        one. Documents already issued keep the text they were issued with — nothing already signed
        changes.
      </p>

      <label className="hrd-field">
        <span>Title</span>
        <input value={title} onChange={(e) => setTitle(e.target.value)} />
      </label>

      <label className="hrd-field">
        <span>Body (HTML)</span>
        <textarea
          className="hrd-code"
          rows={16}
          value={bodyHtml}
          onChange={(e) => setBodyHtml(e.target.value)}
          spellCheck={false}
        />
      </label>

      <div className="hrd-actions">
        <button className="hrd-btn hrd-btn-primary" onClick={() => setConfirming(true)}>
          Publish new version
        </button>
      </div>

      {confirming && (
        <div className="hrd-backdrop" onMouseDown={(e) => { if (e.target === e.currentTarget) setConfirming(false); }}>
          <div className="hrd-dialog" role="dialog" aria-modal="true">
            <h2>Publish version {template.version + 1}?</h2>
            <p>
              This retires version {template.version}. Contracts issued from now on use the new
              text; everything already issued or signed is untouched.
            </p>
            <div className="hrd-actions">
              <button className="hrd-btn hrd-btn-ghost" onClick={() => setConfirming(false)}>Cancel</button>
              <button className="hrd-btn hrd-btn-primary" disabled={isSaving} onClick={save}>
                {isSaving ? 'Publishing…' : 'Publish'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};

/* ---------------------------- Issue a document -------------------------- */

const IssuePanel = ({ templates, employees, onIssued, onError }: {
  templates: DocumentTemplate[];
  employees: Employee[];
  onIssued: (text: string) => void;
  onError: (text: string) => void;
}) => {
  const [templateId, setTemplateId] = useState('');
  const [employeeId, setEmployeeId] = useState('');
  const [preview, setPreview] = useState<TemplatePreview | null>(null);
  const [isPreviewing, setIsPreviewing] = useState(false);
  const [isIssuing, setIsIssuing] = useState(false);

  // Any change to who or what invalidates the preview — issuing against a
  // stale one is exactly how a wrong contract goes out.
  useEffect(() => { setPreview(null); }, [templateId, employeeId]);

  const runPreview = async () => {
    if (!templateId || !employeeId) return;
    setIsPreviewing(true);
    try {
      setPreview(await lifecycleService.previewTemplate(Number(templateId), Number(employeeId)));
    } catch (err: any) {
      onError(err?.message || 'The preview could not be generated.');
    } finally {
      setIsPreviewing(false);
    }
  };

  const issue = async () => {
    setIsIssuing(true);
    try {
      await lifecycleService.issueDocument({
        employee_id: Number(employeeId),
        template_id: Number(templateId),
      });
      setPreview(null);
      onIssued('Document issued. It is now waiting for their signature.');
    } catch (err: any) {
      onError(err?.message || 'That document could not be issued.');
    } finally {
      setIsIssuing(false);
    }
  };

  const missing = preview?.missing_placeholders ?? [];
  const blocked = !preview || missing.length > 0;

  return (
    <div className="hrd-card hrd-issue">
      <div className="hrd-grid">
        <label className="hrd-field">
          <span>Template</span>
          <select value={templateId} onChange={(e) => setTemplateId(e.target.value)}>
            <option value="">Choose a template</option>
            {templates.map((t) => (
              <option key={t.id} value={t.id}>{t.title} (v{t.version})</option>
            ))}
          </select>
        </label>
        <label className="hrd-field">
          <span>Employee</span>
          <select value={employeeId} onChange={(e) => setEmployeeId(e.target.value)}>
            <option value="">Choose an employee</option>
            {employees.map((e) => (
              <option key={e.id} value={e.id}>{e.name}{e.designation ? ` — ${e.designation}` : ''}</option>
            ))}
          </select>
        </label>
      </div>

      <div className="hrd-actions">
        <button className="hrd-btn hrd-btn-ghost" disabled={!templateId || !employeeId || isPreviewing}
          onClick={runPreview}>
          <FiEye size={15} /> {isPreviewing ? 'Rendering…' : 'Preview'}
        </button>
        {/* Issue is unreachable until a preview has been seen and comes back
            clean. A blank in a contract is not something to discover after
            it has been sent for signature. */}
        <button className="hrd-btn hrd-btn-primary" disabled={blocked || isIssuing} onClick={issue}>
          <FiSend size={15} /> {isIssuing ? 'Issuing…' : 'Issue document'}
        </button>
      </div>

      {preview && missing.length > 0 && (
        <div className="hrd-warn" role="alert">
          <FiAlertTriangle size={16} />
          <div>
            <strong>
              {missing.length} field{missing.length === 1 ? '' : 's'} this employee's record cannot fill
            </strong>
            <span>
              Issuing now would put a blank in the contract. Fill these on their record first.
            </span>
            <ul className="hrd-missing">
              {missing.map((field) => <li key={field}><code>{field}</code></li>)}
            </ul>
          </div>
        </div>
      )}

      {preview && missing.length === 0 && (
        <p className="hrd-ready"><FiCheck size={14} /> Every field resolved. Ready to issue.</p>
      )}

      {preview && (
        <div className="hrd-preview">
          <span className="hrd-preview-label"><FiFileText size={14} /> Preview</span>
          <div className="hrd-preview-body">
            <article className="legal-doc" dangerouslySetInnerHTML={{ __html: preview.body_html }} />
          </div>
        </div>
      )}
    </div>
  );
};

export default HrDocuments;
