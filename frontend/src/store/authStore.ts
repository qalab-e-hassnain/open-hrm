import { create } from 'zustand';
import { type User } from '../types';
import { STORAGE_KEYS } from '../config/constants';

interface AuthState {
  user: User | null;
  token: string | null;
  isAuthenticated: boolean;
  isLoading: boolean;
  isCheckedIn: boolean;
  setUser: (user: User | null) => void;
  setToken: (token: string | null) => void;
  setIsCheckedIn: (status: boolean) => void;
  login: (user: User, token: string, refreshToken?: string) => void;
  logout: () => void;
  initializeAuth: () => void;
}

/**
 * Auth read from localStorage synchronously, as the store's initial state.
 *
 * This used to start as `isLoading: true` and be filled in by an effect in
 * App, which meant every full page load rendered a spinner, then mounted the
 * routes, and the tree settled a second time once the user arrived. Every
 * screen's data therefore loaded twice on a hard load: measured at 21 API
 * calls for a page that needs 12, with all eight analytics endpoints fetched
 * in duplicate. Reading localStorage is synchronous, so there was never a
 * reason to make the first render wait for it.
 */
/**
 * The only parts of the signed-in user that are written to browser storage.
 *
 * The whole user record used to be stored, which meant a CNIC, a date of
 * birth, a salary, bank account details and the URLs of scanned ID documents
 * were all sitting in localStorage on every machine anyone had ever signed
 * in on — readable by any script running on the page, and still there long
 * after they had stopped using it.
 *
 * None of that is needed to restore a session. What is needed is who the
 * person is, what they may see, and enough to draw the header. Everything
 * else stays in memory for the life of the tab and is re-read from the API
 * when a screen actually needs it.
 */
const SESSION_FIELDS = [
  'id', 'name', 'email', 'role', 'designation', 'department', 'employeeId',
  'avatar', 'profilePictureUrl', 'managerId', 'isActive', 'isHr',
  'hasHrAccess', 'isActingAdmin', 'isLabCustodian', 'gender',
] as const;

const forStorage = (user: User): Partial<User> => {
  const slim: Record<string, unknown> = {};
  for (const field of SESSION_FIELDS) {
    const value = (user as unknown as Record<string, unknown>)[field];
    if (value !== undefined) slim[field] = value;
  }
  return slim as Partial<User>;
};

const restoreSession = (): { user: User | null; token: string | null; isAuthenticated: boolean } => {
  try {
    const token = localStorage.getItem(STORAGE_KEYS.AUTH_TOKEN);
    const userData = localStorage.getItem(STORAGE_KEYS.USER_DATA);
    if (!token || !userData) return { user: null, token: null, isAuthenticated: false };
    const stored = JSON.parse(userData) as User;
    // Anyone signed in before the stored copy was trimmed still has their
    // CNIC, salary and bank details sitting in this browser. Rewriting it
    // here clears that on the next load rather than waiting for them to sign
    // out and back in, which some people simply never do.
    const slim = forStorage(stored);
    if (Object.keys(stored).length !== Object.keys(slim).length) {
      try {
        localStorage.setItem(STORAGE_KEYS.USER_DATA, JSON.stringify(slim));
      } catch { /* storage full or blocked; the session still works */ }
    }
    return { user: slim as User, token, isAuthenticated: true };
  } catch {
    // Corrupt or unreadable storage is a signed-out session, not a crash.
    return { user: null, token: null, isAuthenticated: false };
  }
};

export const useAuthStore = create<AuthState>((set) => ({
  ...restoreSession(),
  isLoading: false,
  isCheckedIn: false,

  setUser: (user) => {
    if (user) {
      localStorage.setItem(STORAGE_KEYS.USER_DATA, JSON.stringify(forStorage(user)));
    } else {
      localStorage.removeItem(STORAGE_KEYS.USER_DATA);
    }
    set({ user, isAuthenticated: !!user });
  },
  
  setToken: (token) => set({ token }),
  
  setIsCheckedIn: (status) => set({ isCheckedIn: status }),

  login: (user, token, refreshToken) => {
    // Nothing about the token or the user goes to the console: console output
    // is kept by browser extensions and by anyone screen-sharing a session.
    localStorage.setItem(STORAGE_KEYS.AUTH_TOKEN, token);
    localStorage.setItem(STORAGE_KEYS.USER_DATA, JSON.stringify(forStorage(user)));
    // Older backends don't issue one — only store it when present.
    if (refreshToken) {
      localStorage.setItem(STORAGE_KEYS.REFRESH_TOKEN, refreshToken);
    }
    // The full record stays in memory for this tab; only the slim copy above
    // is written to storage.
    set({ user, token, isAuthenticated: true, isLoading: false });
  },

  logout: () => {
    localStorage.removeItem(STORAGE_KEYS.AUTH_TOKEN);
    localStorage.removeItem(STORAGE_KEYS.USER_DATA);
    localStorage.removeItem(STORAGE_KEYS.REFRESH_TOKEN);
    set({ user: null, token: null, isAuthenticated: false, isCheckedIn: false });
  },

  initializeAuth: () => {
    // The session is already restored in the store's initial state; this
    // remains so existing callers keep working, and re-reads storage in case
    // another tab signed in or out.
    set({ ...restoreSession(), isLoading: false });
  },
}));

