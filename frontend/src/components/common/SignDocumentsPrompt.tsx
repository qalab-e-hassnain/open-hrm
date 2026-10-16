import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { FiArrowRight, FiClock, FiFileText, FiX } from 'react-icons/fi';
import { lifecycleService, type DocumentSummary } from '../../services/lifecycleService';
import { useAuthStore } from '../../store/authStore';
import { ROUTES } from '../../config/constants';
import './SignDocumentsPrompt.css';

/** Dismissed for this sign-in only, so it returns next time they log in. */
const DISMISS_KEY = 'sign_documents_prompt_dismissed';

/**
 * Asks for a signature on anything issued and still unsigned, once per sign-in.
 *
 * Eighteen people were holding an unsigned employment agreement and twelve of
 * them had never opened it. Nothing told them: the document sat on a page
 * they had no reason to visit, and the notification that announced it was one
 * line in a bell that fills up.
 *
 * Deliberately not a wall, for the same reason the profile prompt is not one:
 * blocking the portal would also block checking in for attendance and
 * applying for leave, which punishes somebody for not having signed by
 * stopping them recording a day's work. It says what is waiting, how long it
 * has been waiting, and takes them to it.
 */
/** The profile prompt asks for the things payroll and the documents need to
 *  exist at all, so it goes first. Two overlays on one screen read as two
 *  unrelated problems and get both dismissed. */
const profilePromptIsShowing = (user: { profileCompletion?: { prompt_on_login?: boolean } } | null) => {
  if (!user?.profileCompletion?.prompt_on_login) return false;
  try {
    return sessionStorage.getItem('profile_prompt_dismissed') !== '1';
  } catch {
    return true;
  }
};

const SignDocumentsPrompt = () => {
  const { isAuthenticated, user } = useAuthStore();
  const navigate = useNavigate();
  const [waiting, setWaiting] = useState<DocumentSummary[]>([]);
  const [dismissed, setDismissed] = useState(true);

  useEffect(() => {
    try {
      setDismissed(sessionStorage.getItem(DISMISS_KEY) === '1');
    } catch {
      // A private window with storage blocked should still see the prompt.
      setDismissed(false);
    }
  }, []);

  useEffect(() => {
    if (!isAuthenticated || dismissed) return;
    let live = true;
    lifecycleService.myDocuments()
      .then((docs) => {
        if (!live) return;
        // Issued or opened, needs a signature, not yet signed. A declined or
        // voided document is not waiting on them.
        setWaiting(docs.filter((d) => d.requires_signature
          && (d.status === 'issued' || d.status === 'viewed')));
      })
      // A prompt that cannot load its own reason to exist stays quiet.
      .catch(() => { if (live) setWaiting([]); });
    return () => { live = false; };
  }, [isAuthenticated, dismissed]);

  if (dismissed || waiting.length === 0) return null;
  if (profilePromptIsShowing(user)) return null;

  const close = () => {
    setDismissed(true);
    try { sessionStorage.setItem(DISMISS_KEY, '1'); } catch { /* not worth failing over */ }
  };

  const daysSince = (iso: string) => {
    const days = Math.floor((Date.now() - new Date(iso).getTime()) / 86_400_000);
    if (!Number.isFinite(days) || days < 0) return null;
    if (days === 0) return 'issued today';
    return `waiting ${days} day${days === 1 ? '' : 's'}`;
  };

  const overdue = waiting.filter((d) => d.due_at && new Date(d.due_at) < new Date());

  return (
    <div className="sd-overlay" role="dialog" aria-modal="true" aria-labelledby="sd-title">
      <div className="sd-card">
        <header>
          <div>
            <h2 id="sd-title">
              {waiting.length === 1
                ? 'A document is waiting for your signature'
                : `${waiting.length} documents are waiting for your signature`}
            </h2>
            <p>
              Signing takes a minute. Until then the company has no signed copy
              of {waiting.length === 1 ? 'it' : 'them'} on file.
            </p>
          </div>
          <button type="button" onClick={close} aria-label="Not now"><FiX /></button>
        </header>

        {overdue.length > 0 && (
          <p className="sd-overdue">
            <FiClock aria-hidden="true" />
            <span>
              {overdue.length === 1
                ? 'One of these is past its due date.'
                : `${overdue.length} of these are past their due date.`}
            </span>
          </p>
        )}

        <ul className="sd-list">
          {waiting.map((doc) => (
            <li key={doc.id}>
              <FiFileText aria-hidden="true" />
              <span>
                <strong>{doc.title}</strong>
                <small>
                  {daysSince(doc.issued_at)}
                  {doc.status === 'viewed' ? ' · you have opened it' : ' · not opened yet'}
                </small>
              </span>
            </li>
          ))}
        </ul>

        <footer>
          <button type="button" className="sd-later" onClick={close}>Not now</button>
          <button type="button" className="sd-go"
            onClick={() => { close(); navigate(ROUTES.DOCUMENTS); }}>
            Read and sign <FiArrowRight />
          </button>
        </footer>
      </div>
    </div>
  );
};

export default SignDocumentsPrompt;
