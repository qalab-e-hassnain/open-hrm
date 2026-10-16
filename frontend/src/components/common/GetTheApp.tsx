import { APP_LINKS } from '../../config/constants';
import './GetTheApp.css';

/** Google's own badge, drawn rather than fetched: the PNG Google hosts is a
 *  cross-origin image the CSP would have to allow, and it does not adapt to
 *  a dark theme. */
const PlayIcon = () => (
  <svg viewBox="0 0 24 24" width="22" height="22" aria-hidden="true">
    <path d="M3.6 1.8a1 1 0 0 0-.5.9v18.6a1 1 0 0 0 .5.9l.1.05L14 12.05v-.1L3.7 1.75z" fill="#00D2FF" />
    <path d="M17.4 15.5 14 12.05v-.1L17.4 8.5l.08.05 4.06 2.3c1.16.66 1.16 1.74 0 2.4l-4.06 2.3z" fill="#FFCE00" />
    <path d="m17.5 15.5-3.5-3.5L3.6 22.2c.38.4 1 .45 1.72.05l12.2-6.75" fill="#FF3A44" />
    <path d="M17.5 8.5 5.3 1.75c-.71-.4-1.34-.35-1.72.05L14 12z" fill="#00F076" />
  </svg>
);

/**
 * Link to the Android app.
 *
 * Renders nothing until a URL is configured, so an unfinished link never
 * ships as a dead button on sixteen people's dashboards.
 */
const GetTheApp = () => {
  if (!APP_LINKS.PLAY_STORE) return null;

  return (
    <a
      className="gta-badge"
      href={APP_LINKS.PLAY_STORE}
      target="_blank"
      rel="noreferrer noopener"
    >
      <PlayIcon />
      <span>
        <small>Also available on</small>
        <strong>Google Play</strong>
      </span>
    </a>
  );
};

export default GetTheApp;
