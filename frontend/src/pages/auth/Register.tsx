import { useState, type FormEvent } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { useAuthStore } from '../../store/authStore';
import { ROUTES, COMPANY } from '../../config/constants';
import './Register.css';
import Button from '../../components/common/Button';
import qgtLogo from '../../assets/logo.webp';

const Register = () => {
  const [formData, setFormData] = useState({
    fullName: '',
    email: '',
    role: '',
    password: '',
    confirmPassword: '',
  });
  const [agreedToTerms, setAgreedToTerms] = useState(false);
  const [wantsUpdates, setWantsUpdates] = useState(false);
  const [error, setError] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const [submitted, setSubmitted] = useState(false);

  const navigate = useNavigate();

  const handleChange = (field: string, value: string) => {
    setFormData({ ...formData, [field]: value });
    if (submitted) setSubmitted(false);
  };

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    setError('');
    setSubmitted(true);

    // Validate required fields
    if (!formData.fullName || !formData.email || !formData.role || !formData.password || !formData.confirmPassword) {
      return;
    }

    if (formData.password !== formData.confirmPassword) {
      setError('Passwords do not match');
      return;
    }

    if (!agreedToTerms) {
      setError('Please agree to the Terms and Privacy Policy');
      return;
    }

    setIsLoading(true);

    try {
      // For now use a mock registration — replace with real API call when backend endpoint is ready
      await new Promise(resolve => setTimeout(resolve, 1000));

      const demoUser = {
        id: Date.now().toString(),
        name: formData.fullName,
        email: formData.email,
        role: formData.role as 'employee' | 'manager' | 'admin',
        designation: 'New Employee',
      };

      const { login } = useAuthStore.getState();
      login(demoUser, 'demo-token-' + Date.now());
      navigate(ROUTES.DASHBOARD);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Registration failed. Please try again.');
    } finally {
      setIsLoading(false);
    }
  };

  const isDisabled = !formData.fullName || !formData.email || !formData.role || !formData.password || !formData.confirmPassword || isLoading;

  return (
    <div className="register-page-container">
      <div className="register-card-backdrop">
        <div className="register-card">
          <div className="register-logo-section">
            <img src={qgtLogo} alt={`${COMPANY.SHORT_NAME} logo`} className="register-logo-img" />
            <span className="register-logo-name">{COMPANY.SHORT_NAME}</span>
          </div>

          <div className="register-text-section">
            <h1 className="register-title">Register<span className="accent-dot">.</span></h1>
            <p className="register-subtitle">Create your account</p>
          </div>

          <form onSubmit={handleSubmit} className="register-form-modern">
            {/* Full Name */}
            <div className="register-field-group">
              <label className="register-label">Full Name</label>
              <input
                type="text"
                value={formData.fullName}
                onChange={(e) => handleChange('fullName', e.target.value)}
                placeholder="John Doe"
                className={`register-input${submitted && !formData.fullName ? ' register-input-error' : ''}`}
                required
                autoComplete="name"
              />
              {submitted && !formData.fullName && (
                <span className="register-field-error">Full name is required</span>
              )}
            </div>

            {/* Email */}
            <div className="register-field-group">
              <label className="register-label">Email address</label>
              <input
                type="email"
                value={formData.email}
                onChange={(e) => handleChange('email', e.target.value)}
                placeholder="name@example.com"
                className={`register-input${submitted && !formData.email ? ' register-input-error' : ''}`}
                required
                autoComplete="email"
              />
              {submitted && !formData.email && (
                <span className="register-field-error">Email is required</span>
              )}
            </div>

            {/* Role — styled exactly like the other inputs */}
            <div className="register-field-group">
              <label className="register-label">Role</label>
              <select
                value={formData.role}
                onChange={(e) => handleChange('role', e.target.value)}
                className={`register-select${submitted && !formData.role ? ' register-input-error' : ''}`}
                required
              >
                <option value="" disabled hidden></option>
                <option value="employee">Employee</option>
                <option value="manager">Manager</option>
                <option value="admin">Admin</option>
              </select>
              {submitted && !formData.role && (
                <span className="register-field-error">Role is required</span>
              )}
            </div>

            {/* Password */}
            <div className="register-field-group">
              <label className="register-label">Password</label>
              <input
                type="password"
                value={formData.password}
                onChange={(e) => handleChange('password', e.target.value)}
                placeholder="••••••••"
                className="register-input"
                required
                minLength={6}
                autoComplete="new-password"
              />
            </div>

            {/* Confirm Password */}
            <div className="register-field-group">
              <label className="register-label">Confirm Password</label>
              <input
                type="password"
                value={formData.confirmPassword}
                onChange={(e) => handleChange('confirmPassword', e.target.value)}
                placeholder="••••••••"
                className="register-input"
                required
                minLength={6}
                autoComplete="new-password"
              />
              {formData.confirmPassword && formData.password !== formData.confirmPassword && (
                <span className="register-field-error">Passwords do not match</span>
              )}
            </div>

            {/* Checkboxes */}
            <div className="register-checkbox-group">
              <label className="register-checkbox-label">
                <input
                  type="checkbox"
                  checked={wantsUpdates}
                  onChange={(e) => setWantsUpdates(e.target.checked)}
                />
                <span>Yes, I want to receive QGT updates</span>
              </label>

              <label className="register-checkbox-label">
                <input
                  type="checkbox"
                  checked={agreedToTerms}
                  onChange={(e) => setAgreedToTerms(e.target.checked)}
                />
                <span>
                  I agree to all the{' '}
                  <Link to={ROUTES.TERMS} className="register-terms-link">Terms</Link>,{' '}
                  <Link to={ROUTES.TERMS} className="register-terms-link">Privacy Policy</Link>
                </span>
              </label>
            </div>

            {error && <div className="register-error-modern">{error}</div>}

            <Button
              type="submit"
              variant="primary"
              isLoading={isLoading}
              disabled={isDisabled}
              className="sign-in-btn"
            >
              {isLoading ? 'Creating Account...' : 'Create Account'}
            </Button>

            <p className="register-login-prompt">
              Already have an account?{' '}
              <Link to={ROUTES.LOGIN} className="register-login-link">Log In</Link>
            </p>
          </form>
        </div>
      </div>
    </div>
  );
};

export default Register;
