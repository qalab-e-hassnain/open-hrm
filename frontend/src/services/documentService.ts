import { API_ENDPOINTS } from '../config/constants';
import { apiClient } from './api';

/**
 * Uploaded documents live behind an authenticated endpoint
 * (`GET /upload/{name}` requires a Bearer token), so they cannot be reached by
 * pointing the browser at their stored URL: with no token the request never
 * reaches the file, the SPA shell is served instead, and the router redirects
 * the viewer to the dashboard. Every read goes through the API client, which
 * attaches the token and refreshes it when needed.
 */

/**
 * The stored object name out of a document URL. Deliberately taken from the
 * URL's last segment rather than trusting the host: rows created against an
 * older API origin still resolve against the current one.
 */
const storedName = (url: string): string => {
  const path = url.split('?')[0];
  const name = path.slice(path.lastIndexOf('/') + 1);
  // Matches the server's own object-name rule (a uuid hex plus an extension).
  return /^[A-Za-z0-9._-]+$/.test(name) ? name : '';
};

/** Fetch a document's bytes with the caller's credentials attached. */
export const fetchDocumentBlob = async (url: string): Promise<Blob> => {
  const name = storedName(url);
  if (name) {
    const response = await apiClient.get<Blob>(`/upload/${name}`, { responseType: 'blob' });
    return response.data as Blob;
  }
  // Not one of ours — an externally hosted file. Try it directly.
  const response = await fetch(url);
  if (!response.ok) throw new Error(`Document request failed (${response.status})`);
  return response.blob();
};

/**
 * A readable filename for a saved copy, e.g. `Arafay-Shah-cnicFront.jpg`.
 * Punctuation is dropped so a label carrying an amount or a dash still yields a
 * clean name on every filesystem.
 */
export const documentFilename = (url: string, ownerName: string, label: string): string => {
  const extension = (url.split('?')[0].split('.').pop() || 'jpg').slice(0, 5);
  const stem = `${ownerName} ${label}`.replace(/[^\w\s-]/g, ' ').trim().replace(/\s+/g, '-');
  return `${stem || 'document'}.${extension}`;
};

/**
 * Documents issued to one employee. Readable for your own record, or for
 * anybody's with HR reach — the server decides which.
 */
export const getDocumentsForEmployee = async (employeeId: string | number) => {
  const response = await apiClient.get<unknown[]>(API_ENDPOINTS.DOCUMENTS_FOR(employeeId));
  return Array.isArray(response.data) ? response.data : [];
};
