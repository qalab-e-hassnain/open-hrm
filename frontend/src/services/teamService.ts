import { apiClient } from './api';
import { API_ENDPOINTS } from '../config/constants';

export interface Team {
  id: number;
  name: string;
  description?: string;
  manager_id?: number;
  created_at?: string;
}

export interface TeamMember {
  id: number;
  user_id: number;
  team_id: number;
  user: {
    id: number;
    full_name: string;
    email: string;
    role: string;
  };
}

export const teamService = {
  getTeams: async () => {
    const response = await apiClient.get<Team[]>(API_ENDPOINTS.TEAMS);
    return response.data;
  },

  getTeamById: async (id: number) => {
    const response = await apiClient.get<Team>(API_ENDPOINTS.TEAM_BY_ID(String(id)));
    return response.data;
  },

  createTeam: async (data: { name: string; description?: string; manager_id?: number }) => {
    const response = await apiClient.post<Team>(API_ENDPOINTS.TEAMS, data);
    return response.data;
  },

  updateTeam: async (id: number, data: Partial<Team>) => {
    const response = await apiClient.put<Team>(API_ENDPOINTS.TEAM_BY_ID(String(id)), data);
    return response.data;
  },

  deleteTeam: async (id: number) => {
    const response = await apiClient.delete(API_ENDPOINTS.TEAM_BY_ID(String(id)));
    return response.data;
  },

  // Note: there's no GET /teams/{id}/members endpoint — team members come
  // back nested in getTeamById()'s response instead.
  addTeamMember: async (teamId: number, userId: number) => {
    const response = await apiClient.post(API_ENDPOINTS.TEAM_MEMBERS(String(teamId)), { user_id: userId });
    return response.data;
  },

  removeTeamMember: async (teamId: number, userId: number) => {
    const response = await apiClient.delete(`${API_ENDPOINTS.TEAM_MEMBERS(String(teamId))}/${userId}`);
    return response.data;
  },

  assignManager: async (teamId: number, managerId: number) => {
    const response = await apiClient.put<Team>(API_ENDPOINTS.TEAM_BY_ID(String(teamId)), {
      manager_id: managerId
    });
    return response.data;
  },

  /** The teams the signed-in user belongs to. */
  getMyTeams: async () => {
    const res = await apiClient.get<unknown[]>(API_ENDPOINTS.MY_TEAMS);
    return Array.isArray(res.data) ? res.data : [];
  },

  /** Take somebody off a team. Manager-level on the server. */
  removeMember: async (teamId: string | number, userId: string | number) => {
    const res = await apiClient.delete<unknown>(API_ENDPOINTS.TEAM_MEMBER(teamId, userId));
    return res.data;
  },
};
