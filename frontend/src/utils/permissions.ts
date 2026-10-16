import type { User } from '../types';

/**
 * Admin-level reach: a true admin, a manager flagged as HR, or — while an
 * admin's approved leave is in effect today — whoever is covering for them.
 *
 * Mirrors `is_privileged` in the backend's api/deps.py. HR managers (and
 * covering acting admins) see the whole admin area; what they cannot
 * normally do — act on an admin account or grant the admin role — opens up
 * for an acting admin too, enforced server-side, where it cannot be bypassed.
 */
export const isPrivileged = (
  user?: { role?: string; isHr?: boolean; isActingAdmin?: boolean; hasHrAccess?: boolean } | User | null,
): boolean => {
  if (!user) return false;
  // Prefer the server's own answer. It folds role and is_hr together for
  // exactly this reason: is_hr is false for admins, so a client that gates on
  // it alone hides the HR area from the two people who hold every permission
  // behind it. Recombining those fields here is how the two drift apart.
  if (typeof user.hasHrAccess === 'boolean' && user.hasHrAccess) return true;
  // An acting admin is covering an absent admin, which the field above does
  // not describe — and the fallback keeps a payload issued before the field
  // existed working rather than silently locking someone out.
  return Boolean(user.role === 'admin' || user.isHr || user.isActingAdmin);
};

/**
 * Keeper of the lab register: the flagged custodian, plus anyone with
 * admin/HR reach, who has custodian authority without carrying the flag.
 *
 * Deliberately narrow in scope. This decides which inventory *screens* and
 * which *optional fields* are worth offering — the receiving table, checking
 * something out to somebody else, entering a recovery amount. It never decides
 * whether an action is permitted: that answer comes from the item's own
 * `actions[]` (see the inventory guide, §1), which also accounts for the
 * item's state and would be wrong within a release if recomputed here.
 */
export const isLabCustodian = (
  user?: { role?: string; isHr?: boolean; isActingAdmin?: boolean; hasHrAccess?: boolean; isLabCustodian?: boolean } | User | null,
): boolean => {
  if (!user) return false;
  return Boolean(user.isLabCustodian) || isPrivileged(user);
};
