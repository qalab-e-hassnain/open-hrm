import { useEffect, useRef } from 'react';
import {
  FiAlertCircle, FiAlertTriangle, FiArrowRight, FiCheckCircle, FiInfo, FiX,
} from 'react-icons/fi';
import { type Notification } from '../../services/notificationService';
import './NotificationDetail.css';

/**
 * The full text of one notification.
 *
 * Most of what the portal sends has nowhere to go: an announcement, a
 * birthday, "check the equipment listed against your name". Clicking those
 * used to do nothing at all, and the dropdown shows only as much of the
 * message as fits — so a company announcement could be delivered to
 * nineteen people and read by none of them.
 */

const ICONS = {
  success: <FiCheckCircle className="nd-icon success" />,
  error: <FiAlertCircle className="nd-icon error" />,
  warning: <FiAlertTriangle className="nd-icon warning" />,
  info: <FiInfo className="nd-icon info" />,
} as const;

const when = (value: string) => {
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? value : d.toLocaleString('en-GB', {
    weekday: 'short', day: 'numeric', month: 'long', year: 'numeric',
    hour: '2-digit', minute: '2-digit',
  });
};

interface Props {
  notification: Notification;
  /** Where this one leads, when it leads anywhere. */
  destination?: { label: string; go: () => void };
  onClose: () => void;
}

const NotificationDetail = ({ notification, destination, onClose }: Props) => {
  const closeRef = useRef<HTMLButtonElement>(null);

  // Escape closes, and focus starts inside the dialog rather than wherever
  // the list left it.
  useEffect(() => {
    closeRef.current?.focus();
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [onClose]);

  return (
    <div
      className="nd-backdrop"
      onMouseDown={(e) => e.target === e.currentTarget && onClose()}
    >
      <div className="nd-dialog" role="dialog" aria-modal="true"
           aria-label={notification.title}>
        <header className="nd-head">
          {ICONS[notification.type] ?? ICONS.info}
          <h2>{notification.title}</h2>
          <button ref={closeRef} type="button" className="nd-close"
                  onClick={onClose} aria-label="Close">
            <FiX />
          </button>
        </header>

        {/* Announcements are written with blank lines between sections, and
            the server stores them that way. Rendering the string raw would
            run the whole thing together as one paragraph. */}
        <div className="nd-body">
          {notification.message.split(/\n{2,}/).map((para, i) => (
            <p key={i}>
              {para.split('\n').map((line, j, all) => (
                <span key={j}>{line}{j < all.length - 1 && <br />}</span>
              ))}
            </p>
          ))}
        </div>

        <footer className="nd-foot">
          <time dateTime={notification.createdAt}>{when(notification.createdAt)}</time>
          {destination && (
            <button type="button" className="nd-go" onClick={destination.go}>
              {destination.label} <FiArrowRight />
            </button>
          )}
        </footer>
      </div>
    </div>
  );
};

export default NotificationDetail;
