import { apiClient } from '../services/api';
import { API_ENDPOINTS } from '../config/constants';

/**
 * What is missing from a profile — decided by the server, not here.
 *
 * There was a checklist in this file. The backend now owns the definition
 * (ten required fields, seven recommended, each with the reason it is being
 * asked for) and ships it on every UserResponse, including the one embedded
 * in the login payload. Two definitions of "complete" would disagree the
 * first time either side added a field, and the one HR chases people
 * against is the server's.
 */
export interface MissingField {
  /** Server-side attribute name, e.g. `cnic_number`. */
  field: string;
  label: string;
  /** Why it is being asked for. Asking for a national ID with no
   *  explanation is how a form gets abandoned. */
  why: string;
}

export interface ProfileCompletion {
  is_complete: boolean;
  percent: number;
  required_total: number;
  required_missing: number;
  missing_required: MissingField[];
  missing_recommended: MissingField[];
  /** The server's cue to put the completion screen up at sign-in. */
  prompt_on_login: boolean;
}

/** Re-check after a save, rather than refetching the whole user. */
export const fetchProfileCompletion = async (): Promise<ProfileCompletion | null> => {
  const res = await apiClient.get<ProfileCompletion>(API_ENDPOINTS.MY_PROFILE_COMPLETION);
  return (res.data as ProfileCompletion) ?? null;
};

/** Who still has gaps, worst first. HR's chase list. */
export const fetchIncompleteProfiles = async (): Promise<Record<string, unknown>[]> => {
  const res = await apiClient.get<Record<string, unknown>[]>(API_ENDPOINTS.INCOMPLETE_PROFILES);
  return Array.isArray(res.data) ? res.data : [];
};

/**
 * Which profile tab a missing field lives on, so the completion card can
 * jump straight to it. The server names the column; only this screen knows
 * where it is edited.
 */
const SECTION_FOR: Record<string, 'personal' | 'banking' | 'documents'> = {
  phone: 'personal',
  date_of_birth: 'personal',
  cnic_number: 'personal',
  father_name: 'personal',
  address: 'personal',
  emergency_contact_name: 'personal',
  emergency_contact_phone: 'personal',
  personal_email: 'personal',
  marital_status: 'personal',
  account_title: 'banking',
  account_number: 'banking',
  bank_number: 'banking',
  branch_code: 'banking',
  profile_picture_url: 'documents',
  cnic_front_url: 'documents',
  cnic_back_url: 'documents',
  linkedin_welcome_picture_url: 'documents',
};

export const sectionFor = (field: string): 'personal' | 'banking' | 'documents' =>
  SECTION_FOR[field] ?? 'personal';

/** Server columns are snake_case; the edit form keys are camelCase. */
export const formFieldFor = (field: string): string =>
  field.replace(/_([a-z])/g, (_, c: string) => c.toUpperCase())
    .replace(/Url$/, 'Url');
