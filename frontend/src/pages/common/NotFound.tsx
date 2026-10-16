import { Link, useLocation } from 'react-router-dom';
import { FiArrowLeft, FiCompass } from 'react-icons/fi';
import { useAuthStore } from '../../store/authStore';
import { ROUTES } from '../../config/constants';
import './NotFound.css';

/**
 * A page that says the address is wrong.
 *
 * An unknown address used to redirect silently to the login screen. Someone
 * following an out-of-date link was shown a login form with no explanation —
 * and if they were already signed in, they were bounced somewhere they had
 * not asked to go, with nothing to say why.
 */
const NotFound = () => {
  const { pathname } = useLocation();
  const { isAuthenticated } = useAuthStore();
  const home = isAuthenticated ? ROUTES.DASHBOARD : ROUTES.LOGIN;

  return (
    <div className="nf-page">
      <div className="nf-card" role="alert">
        <span className="nf-icon" aria-hidden="true"><FiCompass /></span>
        <p className="nf-code">404</p>
        <h1>That page does not exist</h1>
        <p className="nf-detail">
          Nothing is served at <code>{pathname}</code>. The link may be out of
          date, or the address may have a typo in it.
        </p>
        <Link className="nf-home" to={home}>
          <FiArrowLeft aria-hidden="true" />
          {isAuthenticated ? 'Back to the dashboard' : 'Go to sign in'}
        </Link>
      </div>
    </div>
  );
};

export default NotFound;
