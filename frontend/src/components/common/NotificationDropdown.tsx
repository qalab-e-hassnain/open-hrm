import { useState, useEffect, useRef } from 'react';
import { FiCheck, FiCheckCircle, FiAlertCircle, FiInfo, FiAlertTriangle } from 'react-icons/fi';
import { useNavigate } from 'react-router-dom';
import { notificationService, type Notification } from '../../services/notificationService';
import { ROUTES } from '../../config/constants';
import NotificationDetail from './NotificationDetail';
import './NotificationDropdown.css';

interface NotificationDropdownProps {
  isOpen: boolean;
  onClose: () => void;
  onUpdate?: () => void; // Callback to refresh notification count
}

const NotificationDropdown = ({ isOpen, onClose, onUpdate }: NotificationDropdownProps) => {
  const [notifications, setNotifications] = useState<Notification[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [loadFailed, setLoadFailed] = useState(false);
  const dropdownRef = useRef<HTMLDivElement>(null);
  const navigate = useNavigate();
  const [detail, setDetail] = useState<Notification | null>(null);

  useEffect(() => {
    if (isOpen) {
      loadNotifications();
    }
  }, [isOpen]);

  // Close dropdown when clicking outside
  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      const target = event.target as Element | null;
      // The detail dialog renders beside this dropdown, not inside it, so
      // every click in it — the close button included — looked like a click
      // outside. On mousedown that closed the dropdown, which unmounted the
      // button before its own click could land: the dialog appeared to
      // close, but nothing cleared it, and it came back on top of the list
      // the next time the bell was opened.
      if (target?.closest('.nd-backdrop')) return;
      if (dropdownRef.current && !dropdownRef.current.contains(event.target as Node)) {
        onClose();
      }
    };

    if (isOpen) {
      document.addEventListener('mousedown', handleClickOutside);
    }

    return () => {
      document.removeEventListener('mousedown', handleClickOutside);
    };
  }, [isOpen, onClose]);

  // This component stays mounted while the bell is shut — `isOpen` only stops
  // it rendering — so state outlives the dropdown unless it is cleared. A
  // dialog left open here would be waiting on the next open regardless of how
  // it was dismissed.
  useEffect(() => {
    if (!isOpen) setDetail(null);
  }, [isOpen]);

  const loadNotifications = async () => {
    try {
      setIsLoading(true);
      setLoadFailed(false);
      const data = await notificationService.getNotifications();
      setNotifications(data || []);
    } catch (error) {
      console.error('Failed to load notifications:', error);
      // Keep whatever was already listed and flag the failure — showing an
      // empty list here would assert there is nothing waiting, which is not
      // what a failed request tells us.
      setLoadFailed(true);
    } finally {
      setIsLoading(false);
    }
  };

  const handleMarkAsRead = async (notificationId: string, e: React.MouseEvent) => {
    e.stopPropagation();
    try {
      await notificationService.markAsRead(notificationId);
      setNotifications(prev =>
        prev.map(n => n.id === notificationId ? { ...n, read: true } : n)
      );
      // Notify parent to refresh count
      if (onUpdate) {
        onUpdate();
      }
    } catch (error) {
      console.error('Failed to mark notification as read:', error);
    }
  };

  /**
   * Where a notification leads, when it leads anywhere.
   *
   * Most do not. Announcements, birthdays, "check the equipment listed
   * against your name", a probation decision — none of those has a screen of
   * its own, and clicking them used to do nothing whatsoever.
   */
  const destinationFor = (notification: Notification):
    { label: string; go: () => void } | undefined => {
    const to = (label: string, path: string) => ({
      label,
      go: () => { navigate(path); onClose(); },
    });
    switch (notification.relatedType) {
      case 'leave': return to('Open leaves', ROUTES.LEAVES);
      case 'reimbursement': return to('Open reimbursements', ROUTES.REIMBURSEMENTS);
      case 'payslip': return to('Open payslips', ROUTES.PAYSLIPS);
      case 'payslip_dispute': return to('Open queries', `${ROUTES.PAYROLL}?tab=queries`);
      case 'award': return to('Open awards', ROUTES.AWARDS);
      case 'inventory_item':
        return notification.relatedId
          ? to('Open item', ROUTES.INVENTORY_ITEM(notification.relatedId))
          : to('Open inventory', ROUTES.INVENTORY_ITEMS);
      default: return undefined;
    }
  };

  const handleNotificationClick = (notification: Notification) => {
    // Reading it is reading it, whichever of the two happens next.
    if (!notification.read) {
      notificationService.markAsRead(String(notification.id)).catch(console.error);
      setNotifications(prev =>
        prev.map(n => n.id === notification.id ? { ...n, read: true } : n)
      );
      // Notify parent to refresh count
      if (onUpdate) {
        onUpdate();
      }
    }

    const destination = destinationFor(notification);
    if (destination) {
      // It has a screen of its own; going there beats a dialog quoting it.
      destination.go();
      return;
    }
    // Nowhere to go, so this dialog is the only place the full text exists —
    // the list truncates it, and an announcement is usually several
    // paragraphs long.
    setDetail({ ...notification, read: true });
  };

  const getNotificationIcon = (type: string) => {
    switch (type) {
      case 'success':
        return <FiCheckCircle size={18} className="notification-icon-success" />;
      case 'error':
        return <FiAlertCircle size={18} className="notification-icon-error" />;
      case 'warning':
        return <FiAlertTriangle size={18} className="notification-icon-warning" />;
      default:
        return <FiInfo size={18} className="notification-icon-info" />;
    }
  };

  const formatTime = (dateString: string) => {
    const date = new Date(dateString);
    const now = new Date();
    const diffMs = now.getTime() - date.getTime();
    const diffMins = Math.floor(diffMs / 60000);
    const diffHours = Math.floor(diffMs / 3600000);
    const diffDays = Math.floor(diffMs / 86400000);

    if (diffMins < 1) return 'Just now';
    if (diffMins < 60) return `${diffMins}m ago`;
    if (diffHours < 24) return `${diffHours}h ago`;
    if (diffDays < 7) return `${diffDays}d ago`;
    return date.toLocaleDateString('en-GB', { day: 'numeric', month: 'short' });
  };

  const unreadCount = notifications.filter(n => !n.read).length;

  if (!isOpen) return null;

  return (
    <>
    <div className="notification-dropdown" ref={dropdownRef}>
      <div className="notification-header">
        <h3 className="notification-title">Notifications</h3>
        {unreadCount > 0 && (
          <button
            className="mark-all-read-btn"
            onClick={async () => {
              try {
                await notificationService.markAllAsRead();
                setNotifications(prev => prev.map(n => ({ ...n, read: true })));
                // Notify parent to refresh count
                if (onUpdate) {
                  onUpdate();
                }
              } catch (error) {
                console.error('Failed to mark all as read:', error);
              }
            }}
            title="Mark all as read"
          >
            Mark all read
          </button>
        )}
      </div>

      <div className="notification-list">
        {isLoading ? (
          <div className="notification-loading">
            <div className="spinner"></div>
            <p>Loading notifications...</p>
          </div>
        ) : loadFailed && notifications.length === 0 ? (
          <div className="notification-empty notification-failed">
            <FiAlertTriangle size={32} />
            <p>Notifications could not be loaded</p>
            <button type="button" className="notification-retry" onClick={loadNotifications}>
              Try again
            </button>
          </div>
        ) : notifications.length === 0 ? (
          <div className="notification-empty">
            <FiInfo size={32} />
            <p>No notifications</p>
          </div>
        ) : (
          notifications.map((notification) => (
            <div
              key={notification.id}
              className={`notification-item ${notification.read ? 'read' : 'unread'}`}
              onClick={() => handleNotificationClick(notification)}
            >
              <div className="notification-icon-wrapper">
                {getNotificationIcon(notification.type)}
              </div>
              <div className="notification-content">
                <div className="notification-header-item">
                  <h4 className="notification-item-title">{notification.title}</h4>
                  {!notification.read && (
                    <button
                      className="mark-read-btn"
                      onClick={(e) => handleMarkAsRead(String(notification.id), e)}
                      title="Mark as read"
                    >
                      <FiCheck size={14} />
                    </button>
                  )}
                </div>
                <p className="notification-message">{notification.message}</p>
                <span className="notification-time">{formatTime(notification.createdAt)}</span>
              </div>
            </div>
          ))
        )}
      </div>

      {notifications.length > 0 && (
        <div className="notification-footer">
          <button
            className="view-all-btn"
            onClick={() => {
              navigate(ROUTES.LEAVES);
              onClose();
            }}
          >
            View All
          </button>
        </div>
      )}
    </div>

      {detail && (
        <NotificationDetail
          notification={detail}
          destination={destinationFor(detail)}
          onClose={() => setDetail(null)}
        />
      )}
    </>
  );
};

export default NotificationDropdown;

