import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { FiAlertTriangle, FiArrowRight, FiCheck, FiX } from 'react-icons/fi';
import { useAuthStore } from '../../store/authStore';
import type { MissingField } from '../../utils/profileCompletion';
import { ROUTES } from '../../config/constants';
import './CompleteProfilePrompt.css';

/** Dismissed for this sign-in only, so it returns next time they log in. */
const DISMISS_KEY = 'profile_prompt_dismissed';

/**
 * Asks for the missing parts of a profile once per sign-in.
 *
 * Deliberately not a wall. Blocking the portal would also block checking in
 * for attendance and applying for leave — punishing somebody for a missing
 * profile photo by stopping them recording a day's work. It states what is
 * missing, says why each one matters, and marks the two that actually stop
 * the company doing its job.
 */
const CompleteProfilePrompt = () => {
  const { user } = useAuthStore();
  const navigate = useNavigate();
  const [dismissed, setDismissed] = useState(true);

  useEffect(() => {
    try {
      setDismissed(sessionStorage.getItem(DISMISS_KEY) === '1');
    } catch {
      // A private window with storage blocked should still see the prompt.
      setDismissed(false);
    }
  }, []);

  const completion = user?.profileCompletion;
  // prompt_on_login is the server's answer, not ours: it is true only when a
  // REQUIRED field is missing, so a person short of a profile photo alone is
  // never stopped on the way in.
  if (dismissed || !completion?.prompt_on_login) return null;

  const required = completion.missing_required ?? [];
  const recommended = completion.missing_recommended ?? [];

  const close = () => {
    setDismissed(true);
    try { sessionStorage.setItem(DISMISS_KEY, '1'); } catch { /* not worth failing over */ }
  };

  // Payroll and the documents stop outright without these two.
  const PAYROLL_CRITICAL = ['account_number', 'cnic_number'];
  const critical = required.filter((m: MissingField) => PAYROLL_CRITICAL.includes(m.field));

  return (
    <div className="cp-overlay" role="dialog" aria-modal="true" aria-labelledby="cp-title">
      <div className="cp-card">
        <header>
          <div>
            <h2 id="cp-title">Your profile is {completion.percent}% complete</h2>
            <p>
              {required.length} {required.length === 1 ? 'thing is' : 'things are'} still needed.
              It takes a couple of minutes.
            </p>
          </div>
          <button type="button" onClick={close} aria-label="Not now"><FiX /></button>
        </header>

        {critical.length > 0 && (
          <p className="cp-blocking">
            <FiAlertTriangle />
            <span>
              {critical.length === 1
                ? `Without your ${critical[0].label.toLowerCase()}, payroll and your documents stop.`
                : 'Two of these stop payroll and your documents outright.'}
            </span>
          </p>
        )}

        <ul className="cp-list">
          {required.map((item: MissingField) => (
            <li key={item.field} className={PAYROLL_CRITICAL.includes(item.field) ? 'is-blocking' : ''}>
              <span className="cp-tick" aria-hidden="true" />
              <span>
                <strong>{item.label}</strong>
                <small>{item.why}</small>
              </span>
            </li>
          ))}
        </ul>

        {/* Recommended fields block nothing, so they are mentioned rather
            than listed alongside the ones that do. */}
        {recommended.length > 0 && (
          <p className="cp-optional">
            {recommended.length} other {recommended.length === 1 ? 'field is' : 'fields are'} worth
            filling in too — {recommended.slice(0, 3).map((r: MissingField) => r.label.toLowerCase()).join(', ')}
            {recommended.length > 3 ? ' and more' : ''} — but nothing depends on them.
          </p>
        )}

        <footer>
          <button type="button" className="cp-later" onClick={close}>Not now</button>
          <button type="button" className="cp-go" onClick={() => { close(); navigate(ROUTES.PROFILE); }}>
            <FiCheck /> Complete it now <FiArrowRight />
          </button>
        </footer>
      </div>
    </div>
  );
};

export default CompleteProfilePrompt;
