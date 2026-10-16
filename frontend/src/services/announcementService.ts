import { apiClient } from './api';
import { API_ENDPOINTS } from '../config/constants';
import { type Announcement } from '../types';

// Map backend snake_case response to the frontend Announcement type
const mapAnnouncement = (a: any): Announcement => ({
  id: a.id?.toString() || '',
  title: a.title || '',
  content: a.content || '',
  date: a.created_at || '',
  creatorName: a.creator_name || '',
  isExpanded: false,
});

export const announcementService = {
  getAnnouncements: async () => {
    const response = await apiClient.get<any[]>(API_ENDPOINTS.ANNOUNCEMENTS);
    const list = Array.isArray(response.data) ? response.data : [];
    return list.map(mapAnnouncement);
  },

  createAnnouncement: async (data: { title: string; content: string }) => {
    const response = await apiClient.post<any>(API_ENDPOINTS.ANNOUNCEMENTS, data);
    return mapAnnouncement(response.data);
  },

  updateAnnouncement: async (id: string, data: Partial<{ title: string; content: string; is_active: boolean }>) => {
    const response = await apiClient.put<any>(`${API_ENDPOINTS.ANNOUNCEMENTS}/${id}`, data);
    return mapAnnouncement(response.data);
  },

  deleteAnnouncement: async (id: string) => {
    const response = await apiClient.delete(`${API_ENDPOINTS.ANNOUNCEMENTS}/${id}`);
    return response.data;
  },
};
