import { useEffect, useState, type FormEvent, type KeyboardEvent } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  FiArrowRight,
  FiCheck,
  FiEye,
  FiEyeOff,
  FiKey,
  FiLock,
  FiMoon,
  FiSun,
} from 'react-icons/fi';
import { useAuthStore } from '../../store/authStore';
import { authService } from '../../services/authService';
import { ROUTES, COMPANY, BRAND_NAME } from '../../config/constants';
import Button from '../../components/common/Button';
import qgtLogo from '../../assets/logo.webp';
import './Login.css';

const MAX_ATTEMPTS = 5;
const LOCK_DURATION_MS = 2 * 60 * 1000;
const ATTEMPTS_KEY = 'qgt-login-attempts';
const LOCK_KEY = 'qgt-login-lock-until';
const REMEMBERED_EMAIL_KEY = 'qgt-remembered-email';
const THEME_KEY = 'qgt-login-theme';

const features = [
  'Attendance tracking',
  'Leave management',
  'Payroll processing',
  'Performance reviews',
  'Employee directory',
];

const Login = () => {
  const [email, setEmail] = useState(() => localStorage.getItem(REMEMBERED_EMAIL_KEY) ?? '');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const [loadingStage, setLoadingStage] = useState<'signing-in' | 'workspace'>('signing-in');
  const [submitted, setSubmitted] = useState(false);
  const [showPassword, setShowPassword] = useState(false);
  const [capsLockOn, setCapsLockOn] = useState(false);
  const [rememberDevice, setRememberDevice] = useState(
    () => Boolean(localStorage.getItem(REMEMBERED_EMAIL_KEY)),
  );
  const [lockUntil, setLockUntil] = useState(
    () => Number(sessionStorage.getItem(LOCK_KEY) ?? 0),
  );
  const [remainingSeconds, setRemainingSeconds] = useState(0);
  const [theme, setTheme] = useState<'light' | 'dark'>(() => {
    const savedTheme = localStorage.getItem(THEME_KEY);
    if (savedTheme === 'light' || savedTheme === 'dark') return savedTheme;
    return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
  });

  const navigate = useNavigate();
  const { login } = useAuthStore();
  const isLocked = lockUntil > Date.now();
  const emailError = submitted && !email.trim() ? 'Enter your email address.' : '';
  const passwordError = submitted && !password ? 'Enter your password.' : '';

  useEffect(() => {
    localStorage.setItem(THEME_KEY, theme);
  }, [theme]);

  useEffect(() => {
    if (!isLocked) {
      setRemainingSeconds(0);
      return;
    }

    const updateCountdown = () => {
      const seconds = Math.max(0, Math.ceil((lockUntil - Date.now()) / 1000));
      setRemainingSeconds(seconds);
      if (seconds === 0) {
        sessionStorage.removeItem(LOCK_KEY);
        sessionStorage.removeItem(ATTEMPTS_KEY);
        setLockUntil(0);
        setError('');
      }
    };

    updateCountdown();
    const timer = window.setInterval(updateCountdown, 1000);
    return () => window.clearInterval(timer);
  }, [isLocked, lockUntil]);

  const registerFailedAttempt = () => {
    const attempts = Number(sessionStorage.getItem(ATTEMPTS_KEY) ?? 0) + 1;
    sessionStorage.setItem(ATTEMPTS_KEY, String(attempts));

    if (attempts >= MAX_ATTEMPTS) {
      const nextLockUntil = Date.now() + LOCK_DURATION_MS;
      sessionStorage.setItem(LOCK_KEY, String(nextLockUntil));
      setLockUntil(nextLockUntil);
      setError('Too many attempts. Please wait 2 minutes before trying again.');
      return;
    }

    setError('Incorrect email or password.');
  };

  const handleSubmit = async (event: FormEvent) => {
    event.preventDefault();
    setSubmitted(true);
    setError('');

    if (!email.trim() || !password || isLocked) return;

    setIsLoading(true);
    setLoadingStage('signing-in');

    try {
      const response = await authService.login({ email: email.trim(), password, rememberDevice });
      if (!response?.user || !response?.token) {
        throw new Error('Invalid authentication response');
      }

      sessionStorage.removeItem(ATTEMPTS_KEY);
      sessionStorage.removeItem(LOCK_KEY);
      if (rememberDevice) {
        localStorage.setItem(REMEMBERED_EMAIL_KEY, email.trim());
      } else {
        localStorage.removeItem(REMEMBERED_EMAIL_KEY);
      }

      setLoadingStage('workspace');
      // Backend already withholds the refresh token unless remember_device was
      // sent; this re-check is a safety net so an old/misbehaving backend that
      // returns one anyway still can't make an unchecked login persistent.
      login(response.user, response.token, rememberDevice ? response.refreshToken : undefined);
      await new Promise((resolve) => window.setTimeout(resolve, 550));
      navigate(ROUTES.DASHBOARD);
    } catch {
      registerFailedAttempt();
      setIsLoading(false);
      setLoadingStage('signing-in');
    }
  };

  const detectCapsLock = (event: KeyboardEvent<HTMLInputElement>) => {
    setCapsLockOn(event.getModifierState('CapsLock'));
  };

  return (
    <main className={`enterprise-login login-theme-${theme}`}>
      <button
        type="button"
        className="login-theme-toggle"
        onClick={() => setTheme((current) => (current === 'light' ? 'dark' : 'light'))}
        aria-label={`Switch to ${theme === 'light' ? 'dark' : 'light'} mode`}
      >
        {theme === 'light' ? <FiMoon aria-hidden="true" /> : <FiSun aria-hidden="true" />}
      </button>

      <section className="login-brand-panel" aria-labelledby="brand-heading">
        <div className="login-ambient" aria-hidden="true">
          <span />
          <span />
          <span />
        </div>

        <div className="login-brand-content">
          <div className="login-company-lockup">
            <img src={qgtLogo} alt="QGT" className="login-company-logo" />
            <div>
              <strong>{COMPANY.SHORT_NAME}</strong>
              <span>Human Resource Management System</span>
            </div>
          </div>

          <div className="login-brand-message">
            <p className="login-eyebrow">{BRAND_NAME}</p>
            <h1 id="brand-heading">People first.<br /><span>Performance always.</span></h1>
            <p className="login-brand-copy">
              One secure workspace for the people, processes, and decisions that keep our teams moving.
            </p>
          </div>

          <ul className="login-feature-list" aria-label="HRM capabilities">
            {features.map((feature) => (
              <li key={feature}><FiCheck aria-hidden="true" />{feature}</li>
            ))}
          </ul>
        </div>
      </section>

      <section className="login-form-panel" aria-labelledby="login-heading">
        <div className="login-form-shell">
          <div className="login-mobile-lockup">
            <img src={qgtLogo} alt="QGT" />
            <div><strong>{COMPANY.SHORT_NAME}</strong><span>{BRAND_NAME}</span></div>
          </div>

          <header className="login-heading">
            <p className="login-system-label"><FiLock aria-hidden="true" /> Secure workspace</p>
            <h2 id="login-heading">Welcome back<span>.</span></h2>
            <p>Access your HR dashboard securely.</p>
          </header>

          <form onSubmit={handleSubmit} className="login-form-modern" noValidate>
            <div className="login-field-group">
              <label className="login-label" htmlFor="login-email">Email address</label>
              <input
                id="login-email"
                type="email"
                value={email}
                onChange={(event) => {
                  setEmail(event.target.value);
                  setSubmitted(false);
                  setError('');
                }}
                placeholder="name@example.com"
                className={`login-input${emailError ? ' login-input-error' : ''}`}
                required
                autoFocus
                autoComplete="username"
                inputMode="email"
                aria-invalid={Boolean(emailError)}
                aria-describedby={emailError ? 'email-error' : undefined}
              />
              {emailError && <span id="email-error" className="login-field-error">{emailError}</span>}
            </div>

            <div className="login-field-group">
              <label className="login-label" htmlFor="login-password">Password</label>
              <div className="login-password-wrap">
                <input
                  id="login-password"
                  type={showPassword ? 'text' : 'password'}
                  value={password}
                  onChange={(event) => {
                    setPassword(event.target.value);
                    setError('');
                  }}
                  onKeyDown={detectCapsLock}
                  onKeyUp={detectCapsLock}
                  onBlur={() => setCapsLockOn(false)}
                  placeholder="Enter your password"
                  className={`login-input${passwordError ? ' login-input-error' : ''}`}
                  required
                  autoComplete="current-password"
                  aria-invalid={Boolean(passwordError)}
                  aria-describedby={[
                    passwordError ? 'password-error' : '',
                    capsLockOn ? 'caps-lock-warning' : '',
                  ].filter(Boolean).join(' ') || undefined}
                />
                <button
                  type="button"
                  className="password-visibility"
                  onClick={() => setShowPassword((visible) => !visible)}
                  aria-label={showPassword ? 'Hide password' : 'Show password'}
                  aria-pressed={showPassword}
                >
                  {showPassword ? <FiEyeOff aria-hidden="true" /> : <FiEye aria-hidden="true" />}
                  <span>{showPassword ? 'Hide' : 'Show'}</span>
                </button>
              </div>
              {passwordError && <span id="password-error" className="login-field-error">{passwordError}</span>}
              {capsLockOn && <span id="caps-lock-warning" className="login-caps-warning">Caps Lock is on.</span>}
            </div>

            {error && (
              <div id="login-error" className="login-error-modern" role="alert" aria-live="assertive">
                {error}
                {isLocked && remainingSeconds > 0 && (
                  <span> Try again in {Math.ceil(remainingSeconds / 60)} minute{remainingSeconds > 60 ? 's' : ''}.</span>
                )}
              </div>
            )}

            <Button
              type="submit"
              variant="primary"
              disabled={isLoading || isLocked}
              className="sign-in-btn"
              aria-describedby={error ? 'login-error' : undefined}
            >
              {isLoading ? (
                <span className="login-loading-label">
                  <span className="login-spinner" aria-hidden="true" />
                  <span>
                    <strong>{loadingStage === 'signing-in' ? 'Signing you in' : 'Loading workspace...'}</strong>
                    <small>Please wait...</small>
                  </span>
                </span>
              ) : (
                <span className="login-button-label">Sign in <FiArrowRight aria-hidden="true" /></span>
              )}
            </Button>

            <div className="login-options">
              <label className="remember-device">
                <input
                  type="checkbox"
                  checked={rememberDevice}
                  onChange={(event) => setRememberDevice(event.target.checked)}
                />
                <span>Remember this device</span>
              </label>
              {/* There is no self-service reset: an admin sets the new password
                  from User Management, so this points to a person, not a page. */}
              <span className="login-forgot-hint">
                <FiKey aria-hidden="true" /> Forgot password? Contact your administrator.
              </span>
            </div>
          </form>

          <p className="login-access-help">
            Need access? <span>Contact your IT Administrator</span>
          </p>
        </div>

        <footer className="login-footer">© {new Date().getFullYear()} {COMPANY.NAME}</footer>
      </section>
    </main>
  );
};

export default Login;
