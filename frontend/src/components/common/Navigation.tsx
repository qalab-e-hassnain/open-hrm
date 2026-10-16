import { useState, useEffect, useRef } from 'react';
import { Link, useLocation, useNavigate } from 'react-router-dom';
import {
  FiActivity,
  FiAward,
  FiBell,
  FiBriefcase,
  FiCalendar,
  FiClock,
  FiCreditCard,
  FiDollarSign,
  FiFileText,
  FiShare2,
  FiBarChart2,
  FiGrid,
  FiTarget,
  FiTrendingUp,
  FiHardDrive,
  FiMaximize,
  FiPackage,
  FiBox,
  FiInbox,
  FiClipboard,
  FiPieChart,
  FiMonitor,
  FiPlusSquare,
  FiLogOut,
  FiMenu,
  FiMoon,
  FiSun,
  FiUser,
  FiUserCheck,
  FiUsers,
  FiX,
  FiWatch,
  FiBook,
  FiBookOpen,
  FiCheckSquare,
  FiEdit3,
  FiCpu,
} from 'react-icons/fi';
import { TbLayoutSidebarLeftCollapse } from 'react-icons/tb';
import { useAuthStore } from '../../store/authStore';
import { ROUTES, BRAND_NAME } from '../../config/constants';
import { notificationService } from '../../services/notificationService';
import { leaveService } from '../../services/leaveService';
import { reimbursementService } from '../../services/reimbursementService';
import qgtLogo from '../../assets/logo.webp';
import NotificationDropdown from './NotificationDropdown';
import { useTheme } from '../../hooks/useTheme';
import { useDocumentImage } from '../../hooks/useDocumentImage';
import { isPrivileged, isLabCustodian } from '../../utils/permissions';
import './Navigation.css';

// Per-user "seen" tracking for the pending-approval dots, persisted in
// localStorage so the dot stays cleared across reloads until a NEW (unseen)
// request arrives.
const readSeen = (key: string): string[] => {
  try {
    const raw = localStorage.getItem(key);
    return raw ? JSON.parse(raw) : [];
  } catch {
    return [];
  }
};
const writeSeen = (key: string, ids: string[]) => {
  try {
    localStorage.setItem(key, JSON.stringify(ids));
  } catch {
    /* ignore quota/serialization errors */
  }
};

// Notification/pending-approval counts change far less often than attendance
// status, so they poll on a slower cadence to keep load on the database down.
const BADGE_REFRESH_MS = 10 * 60 * 1000;

const Navigation = () => {
  const location = useLocation();
  const navigate = useNavigate();
  const { user, logout } = useAuthStore();
  const { theme, toggleTheme } = useTheme();
  const [isNotificationOpen, setIsNotificationOpen] = useState(false);
  const [isMobileMenuOpen, setIsMobileMenuOpen] = useState(false);
  const [isSidebarCollapsed, setIsSidebarCollapsed] = useState(
    () => localStorage.getItem('qgt-sidebar-collapsed') === 'true',
  );
  const [isProfileOpen, setIsProfileOpen] = useState(false);
  const [unreadCount, setUnreadCount] = useState(0);
  const [pendingLeaves, setPendingLeaves] = useState(0);
  const [pendingReimbursements, setPendingReimbursements] = useState(0);
  const notificationBtnRef = useRef<HTMLButtonElement>(null);
  const mobileMenuRef = useRef<HTMLDivElement>(null);
  const mobileMenuBtnRef = useRef<HTMLButtonElement>(null);
  const profileMenuRef = useRef<HTMLDivElement>(null);
  const storedAvatarUrl = user?.profilePictureUrl || user?.avatar;
  const { imageUrl: avatarImageUrl } = useDocumentImage(storedAvatarUrl);

  const getNavLinks = () => {
    // The lab register is not an admin area. Scanning, the catalogue and your
    // own holdings are everybody's; the receiving table is the custodian's,
    // which is a flag on the user rather than a role — a plain employee can
    // hold it, and an admin has the same reach without it.
    const labLinks = [
      { path: ROUTES.INVENTORY_SCAN, label: 'Scan', icon: FiMaximize, group: 'Lab' },
      { path: ROUTES.INVENTORY_ITEMS, label: 'Lab Inventory', icon: FiHardDrive, group: 'Lab' },
      { path: ROUTES.MY_EQUIPMENT, label: 'My Equipment', icon: FiBox, group: 'Lab' },
      { path: ROUTES.INVENTORY_REQUESTS, label: 'Requests', icon: FiInbox, group: 'Lab' },
      ...(isLabCustodian(user)
        ? [
            { path: ROUTES.INVENTORY_RECEIVE, label: 'Receiving', icon: FiPackage, group: 'Lab' },
            { path: ROUTES.INVENTORY_AUDIT, label: 'Stock Count', icon: FiClipboard, group: 'Lab' },
            { path: ROUTES.INVENTORY_REPORTS, label: 'Lab Reports', icon: FiPieChart, group: 'Lab' },
          ]
        : []),
    ];

    // The other section of the same register — laptops, phones, SIMs, access
    // cards. Admin and HR issue and take it back; the lab custodian flag
    // stops at the lab door, so this is gated on admin/HR reach alone.
    const officeLinks = [
      { path: ROUTES.OFFICE_EQUIPMENT, label: 'Office Equipment', icon: FiMonitor, group: 'Office' },
      { path: ROUTES.OFFICE_EQUIPMENT_NEW, label: 'Register Equipment', icon: FiPlusSquare, group: 'Office' },
      { path: ROUTES.OFFICE_EQUIPMENT_REPORTS, label: 'Office Reports', icon: FiPieChart, group: 'Office' },
    ];

    if (isPrivileged(user)) {
      return [
        { path: ROUTES.DASHBOARD, label: 'Dashboard', icon: FiGrid, group: 'Overview' },
        { path: ROUTES.EMPLOYEES, label: 'User Management', icon: FiUsers, group: 'People' },
        { path: ROUTES.PROBATION, label: 'Probation', icon: FiUserCheck, group: 'People' },
        ...labLinks,
        ...officeLinks,
        { path: ROUTES.ORG_CHART, label: 'Org Chart', icon: FiShare2, group: 'People' },
        { path: ROUTES.ANALYTICS, label: 'Analytics', icon: FiBarChart2, group: 'Overview' },
        { path: ROUTES.HR_DOCUMENTS, label: 'Contract Content', icon: FiEdit3, group: 'Documents' },
        { path: ROUTES.SHIFTS, label: 'Shifts', icon: FiClock, group: 'Workforce' },
        { path: ROUTES.ADMIN_JOBS, label: 'Scheduled Jobs', icon: FiActivity, group: 'System' },
        { path: ROUTES.BIOMETRIC, label: 'Fingerprint Device', icon: FiCpu, group: 'System' },
        { path: ROUTES.ATTENDANCE_HISTORY, label: 'Attendance', icon: FiClock, group: 'Workforce' },
        { path: ROUTES.TIME_LOGS, label: 'Time Logs', icon: FiWatch, group: 'Workforce' },
        { path: ROUTES.LEAVES, label: 'Leaves', icon: FiCalendar, group: 'Workforce' },
        { path: ROUTES.PERFORMANCE, label: 'Performance', icon: FiTarget, group: 'Workforce' },
        { path: ROUTES.AWARDS, label: 'Employee of the Month', icon: FiAward, group: 'Workforce' },
        { path: ROUTES.REIMBURSEMENTS, label: 'Reimbursements', icon: FiCreditCard, group: 'Finance' },
        { path: ROUTES.LOANS, label: 'Advances', icon: FiTrendingUp, group: 'Finance' },
        { path: ROUTES.PAYROLL, label: 'Payroll', icon: FiDollarSign, group: 'Finance' },
        { path: ROUTES.OFFBOARDING, label: 'Resignation', icon: FiLogOut, group: 'Account' },
        { path: ROUTES.ONBOARDING, label: 'Onboarding', icon: FiCheckSquare, group: 'Account' },
        { path: ROUTES.HANDBOOK, label: 'Handbook', icon: FiBook, group: 'Documents' },
        { path: ROUTES.DOCUMENTS, label: 'My Documents', icon: FiBookOpen, group: 'Documents' },
        { path: ROUTES.PAYSLIPS, label: 'Payslips', icon: FiBriefcase, group: 'Documents' },
        { path: ROUTES.TAX_CERTIFICATE, label: 'Tax Certificate', icon: FiFileText, group: 'Documents' },
        { path: ROUTES.EMPLOYMENT_LETTER, label: 'Employment Letter', icon: FiAward, group: 'Documents' },
        { path: ROUTES.PROFILE, label: 'Profile', icon: FiUser, group: 'Account' },
      ];
    }
    if (user?.role === 'manager') {
      return [
        { path: ROUTES.DASHBOARD, label: 'Dashboard', icon: FiGrid, group: 'Overview' },
        { path: ROUTES.ORG_CHART, label: 'Org Chart', icon: FiShare2, group: 'Overview' },
        ...labLinks,
        { path: ROUTES.PROBATION, label: 'Probation', icon: FiUserCheck, group: 'People' },
        { path: ROUTES.ATTENDANCE_HISTORY, label: 'Attendance', icon: FiClock, group: 'Workforce' },
        { path: ROUTES.TIME_LOGS, label: 'Time Logs', icon: FiWatch, group: 'Workforce' },
        { path: ROUTES.LEAVES, label: 'Leaves', icon: FiCalendar, group: 'Workforce' },
        { path: ROUTES.PERFORMANCE, label: 'Performance', icon: FiTarget, group: 'Workforce' },
        { path: ROUTES.AWARDS, label: 'Employee of the Month', icon: FiAward, group: 'Workforce' },
        { path: ROUTES.REIMBURSEMENTS, label: 'Reimbursements', icon: FiCreditCard, group: 'Finance' },
        { path: ROUTES.LOANS, label: 'Advances', icon: FiTrendingUp, group: 'Finance' },
        { path: ROUTES.OFFBOARDING, label: 'Resignation', icon: FiLogOut, group: 'Account' },
        { path: ROUTES.ONBOARDING, label: 'Onboarding', icon: FiCheckSquare, group: 'Account' },
        { path: ROUTES.HANDBOOK, label: 'Handbook', icon: FiBook, group: 'Documents' },
        { path: ROUTES.DOCUMENTS, label: 'My Documents', icon: FiBookOpen, group: 'Documents' },
        { path: ROUTES.PAYSLIPS, label: 'Payslips', icon: FiBriefcase, group: 'Documents' },
        { path: ROUTES.TAX_CERTIFICATE, label: 'Tax Certificate', icon: FiFileText, group: 'Documents' },
        { path: ROUTES.EMPLOYMENT_LETTER, label: 'Employment Letter', icon: FiAward, group: 'Documents' },
        { path: ROUTES.PROFILE, label: 'Profile', icon: FiUser, group: 'Account' },
      ];
    }
    return [
      { path: ROUTES.DASHBOARD, label: 'Dashboard', icon: FiGrid, group: 'Overview' },
      { path: ROUTES.ORG_CHART, label: 'Org Chart', icon: FiShare2, group: 'Overview' },
      ...labLinks,
      { path: ROUTES.ATTENDANCE_HISTORY, label: 'Attendance', icon: FiClock, group: 'Workforce' },
      { path: ROUTES.TIME_LOGS, label: 'Time Logs', icon: FiWatch, group: 'Workforce' },
      { path: ROUTES.LEAVES, label: 'Leaves', icon: FiCalendar, group: 'Workforce' },
        { path: ROUTES.PERFORMANCE, label: 'Performance', icon: FiTarget, group: 'Workforce' },
        { path: ROUTES.AWARDS, label: 'Employee of the Month', icon: FiAward, group: 'Workforce' },
      { path: ROUTES.REIMBURSEMENTS, label: 'Reimbursements', icon: FiCreditCard, group: 'Finance' },
        { path: ROUTES.LOANS, label: 'Advances', icon: FiTrendingUp, group: 'Finance' },
      { path: ROUTES.OFFBOARDING, label: 'Resignation', icon: FiLogOut, group: 'Account' },
      { path: ROUTES.ONBOARDING, label: 'Onboarding', icon: FiCheckSquare, group: 'Account' },
      { path: ROUTES.HANDBOOK, label: 'Handbook', icon: FiBook, group: 'Documents' },
        { path: ROUTES.DOCUMENTS, label: 'My Documents', icon: FiBookOpen, group: 'Documents' },
      { path: ROUTES.PAYSLIPS, label: 'Payslips', icon: FiBriefcase, group: 'Documents' },
      { path: ROUTES.TAX_CERTIFICATE, label: 'Tax Certificate', icon: FiFileText, group: 'Documents' },
      { path: ROUTES.EMPLOYMENT_LETTER, label: 'Employment Letter', icon: FiAward, group: 'Documents' },
      { path: ROUTES.PROFILE, label: 'Profile', icon: FiUser, group: 'Account' },
    ];
  };

  // A failed check returns null ("unknown"), not 0 — keep the last known count
  // rather than clearing the badge and implying there is nothing waiting.
  const applyUnreadCount = (count: number | null) => {
    if (count !== null) setUnreadCount(count);
  };

  useEffect(() => {
    const load = async () => {
      applyUnreadCount(await notificationService.getUnreadCount());
    };
    load();
    const interval = setInterval(load, BADGE_REFRESH_MS);
    // Also re-check when the tab regains focus, so switching back to an admin
    // session shows what arrived while it was in the background.
    const onFocus = () => { void load(); };
    window.addEventListener('focus', onFocus);
    document.addEventListener('visibilitychange', onFocus);
    return () => {
      clearInterval(interval);
      window.removeEventListener('focus', onFocus);
      document.removeEventListener('visibilitychange', onFocus);
    };
  }, []);

  // Re-check when the dropdown CLOSES, so reading a notification updates the
  // badge. Skipping the first run matters: the effect above already fetches
  // on mount, and without this guard every page load asked twice.
  const hasOpenedNotifications = useRef(false);
  useEffect(() => {
    if (isNotificationOpen) {
      hasOpenedNotifications.current = true;
      return;
    }
    if (!hasOpenedNotifications.current) return;
    notificationService.getUnreadCount().then(applyUnreadCount).catch(() => {});
  }, [isNotificationOpen]);

  // Pending-approval badge dots on the Leaves/Reimbursements nav links. Only
  // approvers (admin/manager) see them. A dot shows only for pending requests
  // the user hasn't seen yet; visiting the relevant page marks its current
  // pending requests as seen, so the dot clears and only reappears when a NEW
  // request comes in. Re-checked on each navigation and on the badge interval.
  const canApprove = isPrivileged(user) || user?.role === 'manager';
  useEffect(() => {
    if (!canApprove) {
      setPendingLeaves(0);
      setPendingReimbursements(0);
      return;
    }
    const leaveKey = `qg_seen_pending_leaves_${user?.id}`;
    const reimbKey = `qg_seen_pending_reimbursements_${user?.id}`;

    const loadPending = async () => {
      const [leaveIds, reimbIds] = await Promise.all([
        leaveService.getPendingIds().catch(() => [] as string[]),
        reimbursementService.getPendingIds().catch(() => [] as string[]),
      ]);

      // Being on a page counts as seeing everything currently pending on it.
      if (location.pathname === ROUTES.LEAVES) writeSeen(leaveKey, leaveIds);
      if (location.pathname === ROUTES.REIMBURSEMENTS) writeSeen(reimbKey, reimbIds);

      const seenLeaves = readSeen(leaveKey);
      const seenReimb = readSeen(reimbKey);
      setPendingLeaves(leaveIds.filter((id) => !seenLeaves.includes(id)).length);
      setPendingReimbursements(reimbIds.filter((id) => !seenReimb.includes(id)).length);
    };

    loadPending();
    const interval = setInterval(loadPending, BADGE_REFRESH_MS);
    return () => clearInterval(interval);
  }, [canApprove, user?.id, location.pathname]);

  // Close the mobile menu when navigating to another page
  useEffect(() => {
    setIsMobileMenuOpen(false);
  }, [location.pathname]);

  // Close the mobile menu when clicking/tapping outside it
  useEffect(() => {
    if (!isMobileMenuOpen) return;
    const handleOutside = (e: MouseEvent | TouchEvent) => {
      const target = e.target as Node;
      // The toggle button itself lives outside .nav-mobile-overlay (it's a
      // header sibling, not a descendant), so without this check a tap on it
      // while open fired BOTH this handler (closing it) and the button's own
      // onClick (immediately reopening it, via a stale isMobileMenuOpen
      // closure re-evaluated after the close) — net effect: the X button
      // appeared to do nothing.
      if (mobileMenuBtnRef.current?.contains(target)) return;
      if (mobileMenuRef.current && !mobileMenuRef.current.contains(target)) {
        setIsMobileMenuOpen(false);
      }
    };
    document.addEventListener('mousedown', handleOutside);
    document.addEventListener('touchstart', handleOutside);
    return () => {
      document.removeEventListener('mousedown', handleOutside);
      document.removeEventListener('touchstart', handleOutside);
    };
  }, [isMobileMenuOpen]);

  // Close the profile/logout dropdown when clicking/tapping outside it —
  // previously had no such handler at all, so it only closed via one of its
  // own menu items (Profile/Log out), never by clicking elsewhere.
  useEffect(() => {
    if (!isProfileOpen) return;
    const handleOutside = (e: MouseEvent | TouchEvent) => {
      if (profileMenuRef.current && !profileMenuRef.current.contains(e.target as Node)) {
        setIsProfileOpen(false);
      }
    };
    document.addEventListener('mousedown', handleOutside);
    document.addEventListener('touchstart', handleOutside);
    return () => {
      document.removeEventListener('mousedown', handleOutside);
      document.removeEventListener('touchstart', handleOutside);
    };
  }, [isProfileOpen]);

  const handleLogout = () => {
    logout();
    navigate(ROUTES.LOGIN);
  };

  useEffect(() => {
    document.documentElement.setAttribute(
      'data-sidebar',
      isSidebarCollapsed ? 'collapsed' : 'expanded',
    );
    localStorage.setItem('qgt-sidebar-collapsed', String(isSidebarCollapsed));
    return () => document.documentElement.removeAttribute('data-sidebar');
  }, [isSidebarCollapsed]);

  const navLinks = getNavLinks();
  const groupedLinks = navLinks.reduce<Record<string, typeof navLinks>>((groups, link) => {
    (groups[link.group] ||= []).push(link);
    return groups;
  }, {});

  const renderLinks = (mobile = false) => Object.entries(groupedLinks).map(([group, links]) => (
    <div className="nav-section" key={group}>
      <span className="nav-section-label">{group}</span>
      {links.map((link) => {
        const Icon = link.icon;
        const showDot =
          (link.path === ROUTES.LEAVES && pendingLeaves > 0) ||
          (link.path === ROUTES.REIMBURSEMENTS && pendingReimbursements > 0);
        return (
          <Link
            key={link.path}
            to={link.path}
            className={`${mobile ? 'nav-mobile-link' : 'nav-link'} ${location.pathname === link.path ? 'active' : ''}`}
            aria-current={location.pathname === link.path ? 'page' : undefined}
            title={!mobile && isSidebarCollapsed ? link.label : undefined}
          >
            <Icon size={18} aria-hidden="true" />
            <span>{link.label}</span>
            {showDot && <span className="nav-pending-dot" aria-label="Pending requests" />}
          </Link>
        );
      })}
    </div>
  ));

  return (
    <div className="app-navigation">
      <aside className="navigation" aria-label="Primary navigation">
        <div className="nav-brand">
          <Link
            to={ROUTES.DASHBOARD}
            className="nav-brand-home"
            aria-label={isSidebarCollapsed ? 'Expand sidebar' : 'Go to dashboard'}
            title={isSidebarCollapsed ? 'Expand sidebar' : 'Dashboard'}
            onClick={(event) => {
              if (isSidebarCollapsed) {
                event.preventDefault();
                setIsSidebarCollapsed(false);
              }
            }}
          >
            <img src={qgtLogo} alt="QGT" className="nav-logo" />
            <span className="nav-brand-copy">
              <span className="nav-brand-name">{BRAND_NAME}</span>
              <span className="nav-brand-edition">Enterprise Suite</span>
            </span>
          </Link>
          <button
            type="button"
            className="nav-sidebar-toggle"
            onClick={() => setIsSidebarCollapsed((collapsed) => !collapsed)}
            aria-label={isSidebarCollapsed ? 'Expand sidebar' : 'Collapse sidebar'}
            title={isSidebarCollapsed ? 'Expand sidebar' : 'Collapse sidebar'}
          >
            {/* Panel-with-chevron icon: shows both what is being collapsed and
                which way it goes. Only the collapse direction is needed — the
                button is hidden once collapsed, and clicking the logo expands it. */}
            <TbLayoutSidebarLeftCollapse size={18} aria-hidden="true" />
          </button>
        </div>
        <div className="nav-links">{renderLinks()}</div>
        <div className="nav-sidebar-footer">
          <strong>{BRAND_NAME}</strong>
          <span>Enterprise Edition</span>
        </div>
      </aside>

      <header className="nav-topbar">
        <button
          ref={mobileMenuBtnRef}
          type="button"
          className="nav-icon-btn nav-mobile-menu-btn"
          onClick={() => setIsMobileMenuOpen(!isMobileMenuOpen)}
          aria-label={isMobileMenuOpen ? 'Close navigation menu' : 'Open navigation menu'}
          aria-expanded={isMobileMenuOpen}
        >
          {isMobileMenuOpen ? <FiX size={20} /> : <FiMenu size={20} />}
        </button>
        <span className="nav-current-page">
          {navLinks.find((link) => link.path === location.pathname)?.label ?? BRAND_NAME}
        </span>
        <div className="nav-actions">
          <button
            type="button"
            className="nav-icon-btn nav-theme-btn"
            onClick={toggleTheme}
            title={theme === 'dark' ? 'Switch to light mode' : 'Switch to dark mode'}
            aria-label={theme === 'dark' ? 'Switch to light mode' : 'Switch to dark mode'}
          >
            {theme === 'dark' ? <FiSun size={18} /> : <FiMoon size={18} />}
          </button>

          <div className="notification-wrapper">
            <button
              ref={notificationBtnRef}
              className="nav-icon-btn"
              onClick={() => setIsNotificationOpen(!isNotificationOpen)}
              title="Notifications"
              aria-label={`Notifications${unreadCount > 0 ? `, ${unreadCount} unread` : ''}`}
              aria-expanded={isNotificationOpen}
            >
              <FiBell size={18} />
              {unreadCount > 0 && (
                <span className="notification-badge">
                  {unreadCount > 99 ? '99+' : unreadCount}
                </span>
              )}
            </button>
            <NotificationDropdown
              isOpen={isNotificationOpen}
              onClose={() => setIsNotificationOpen(false)}
              onUpdate={async () => {
                applyUnreadCount(await notificationService.getUnreadCount());
              }}
            />
          </div>

          <div className="nav-profile-menu" ref={profileMenuRef}>
            <button
              className="nav-profile"
              onClick={() => setIsProfileOpen((open) => !open)}
              aria-label="Open profile menu"
              aria-expanded={isProfileOpen}
            >
              <span className="nav-profile-avatar">
                {avatarImageUrl
                  ? <img src={avatarImageUrl} alt={`${user?.name || 'User'} profile`} />
                  : (user?.name?.[0] ?? 'U').toUpperCase()}
              </span>
              <span className="nav-profile-copy">
                <strong>{user?.name ?? 'User'}</strong>
                <small>{user?.role ?? 'employee'}</small>
              </span>
            </button>
            {isProfileOpen && (
              <div className="nav-profile-dropdown">
                <button type="button" onClick={() => { setIsProfileOpen(false); navigate(ROUTES.PROFILE); }}>
                  <FiUser size={16} /> Profile
                </button>
                <button type="button" onClick={handleLogout}>
                  <FiLogOut size={16} /> Log out
                </button>
              </div>
            )}
          </div>
        </div>
      </header>

      {isMobileMenuOpen && (
        <div className="nav-mobile-overlay" ref={mobileMenuRef}>
          <div className="nav-mobile-drawer">
            <div className="nav-brand">
              <Link to={ROUTES.DASHBOARD} className="nav-brand-home" aria-label="Go to dashboard">
                <img src={qgtLogo} alt="QGT" className="nav-logo" />
                <span className="nav-brand-copy">
                  <span className="nav-brand-name">{BRAND_NAME}</span>
                  <span className="nav-brand-edition">Enterprise Suite</span>
                </span>
              </Link>
            </div>
            <div className="nav-mobile-links">{renderLinks(true)}</div>
          </div>
        </div>
      )}
    </div>
  );
};

export default Navigation;
