import { apiClient } from './api';
import { API_ENDPOINTS } from '../config/constants';

export interface Notification {
  id: string | number; // Backend returns number, but we'll convert to string for consistency
  title: string;
  message: string;
  type: 'success' | 'info' | 'warning' | 'error';
  read: boolean;
  createdAt: string;
  relatedId?: string | number; // ID of related leave/reimbursement
  /**
   * What this notification is about, mirroring RelatedTypeEnum on the server.
   *
   * It listed only two of the six, so a payslip or an award notification was
   * typed as something that could not happen — and the click handler's
   * branches for them looked, to the compiler, like dead code.
   *
   * Most notifications carry none of these: an announcement, a birthday or a
   * probation reminder has no screen of its own.
   */
  relatedType?: 'leave' | 'reimbursement' | 'payslip' | 'payslip_dispute'
    | 'award' | 'inventory_item';
}

type NotificationsResponse = Notification[] | { success?: boolean; data?: Notification[] };

export const notificationService = {
  getNotifications: async () => {
    try {
      const response = await apiClient.get<NotificationsResponse>(API_ENDPOINTS.NOTIFICATIONS);
      // Backend returns: { "success": true, "data": [...] } or array directly
      let notifications: Notification[] = [];
      const data = response.data;
      // The previous third branch (checking `success` as well) was unreachable:
      // the branch above already matches any object with a `data` array.
      if (Array.isArray(data)) {
        notifications = data;
      } else if (data && typeof data === 'object' && Array.isArray((data as { data?: Notification[] }).data)) {
        notifications = (data as { data: Notification[] }).data;
      }
      // Convert IDs to strings for consistency
      return notifications.map(n => ({
        ...n,
        id: String(n.id),
        relatedId: n.relatedId ? String(n.relatedId) : undefined,
      }));
    } catch (error) {
      console.error('Failed to fetch notifications:', error);
      // Rethrow rather than returning []. Swallowing the failure rendered
      // "No notifications", which is a claim about the data — indistinguishable
      // from a genuinely empty inbox — when the truth was that the request
      // never succeeded. The caller shows a retry instead.
      throw error;
    }
  },

  markAsRead: async (notificationId: string) => {
    try {
      const response = await apiClient.post(API_ENDPOINTS.MARK_READ, {
        notification_id: notificationId,
      });
      return response.data;
    } catch (error) {
      console.error('Failed to mark notification as read:', error);
      throw error;
    }
  },

  markAllAsRead: async () => {
    try {
      // Backend has POST /notifications/mark-all-read endpoint
      const response = await apiClient.post(API_ENDPOINTS.MARK_ALL_READ || '/notifications/mark-all-read', {});
      return response.data;
    } catch (error) {
      console.error('Failed to mark all notifications as read:', error);
      // Fallback: mark each unread notification individually
      try {
        const notifications = await notificationService.getNotifications();
        const unreadNotifications = notifications.filter(n => !n.read);
        
        await Promise.all(
          unreadNotifications.map(n => notificationService.markAsRead(String(n.id)))
        );
      } catch (fallbackError) {
        console.error('Fallback mark-all-read also failed:', fallbackError);
        throw error; // Throw original error
      }
    }
  },

  getUnreadCount: async () => {
    try {
      // Backend has GET /notifications/unread-count endpoint
      type UnreadResponse = number | { count?: number; data?: { count?: number } };
      const response = await apiClient.get<UnreadResponse>(API_ENDPOINTS.UNREAD_COUNT || '/notifications/unread-count');
      const data = response.data;
      if (typeof data === 'number') {
        return data;
      }
      if (data && typeof data === 'object' && data.count !== undefined) {
        return data.count;
      }
      if (data && typeof data === 'object' && (data as { data?: { count?: number } }).data?.count !== undefined) {
        return (data as { data: { count: number } }).data.count;
      }
      // Fallback: count manually
      const notifications = await notificationService.getNotifications();
      return notifications.filter(n => !n.read).length;
    } catch (error) {
      console.error('Failed to get unread count:', error);
      // Fallback: count manually
      try {
        const notifications = await notificationService.getNotifications();
        return notifications.filter(n => !n.read).length;
      } catch (fallbackError) {
        console.error('Fallback unread count also failed:', fallbackError);
        // Returning 0 here would clear the bell badge and report "no
        // notifications" when the truth is "we could not check" — a transient
        // 503 from the database pool silently hid real notifications. null means
        // unknown, and the caller keeps whatever it last displayed.
        return null;
      }
    }
  },
};

