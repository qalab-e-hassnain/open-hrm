import { useState, useEffect, useCallback } from 'react';
import { useSearchParams } from 'react-router-dom';
import { FiBookOpen, FiX } from 'react-icons/fi';
import { useAuthStore } from '../../store/authStore';
import { isPrivileged } from '../../utils/permissions';
import ApplyLeave from '../employee/ApplyLeave';
import ManageLeaves from '../manager/ManageLeaves';
import LeaveHistory from './LeaveHistory';
import LeavePolicy from './LeavePolicy';
import './Leaves.css';

type TabType = 'apply' | 'manage' | 'history';

const Leaves = () => {
  const { user } = useAuthStore();
  const [searchParams, setSearchParams] = useSearchParams();

  const getAvailableTabs = (): { id: TabType; label: string }[] => {
    const tabs: { id: TabType; label: string }[] = [
      { id: 'apply', label: 'Apply Leave' },
    ];
    if (isPrivileged(user)) {
      tabs.push({ id: 'manage', label: 'Manage Leaves' });
    }
    tabs.push({ id: 'history', label: 'Leave History' });
    return tabs;
  };

  const getDefaultTab = (): TabType => {
    if (isPrivileged(user)) return 'manage';
    return 'apply';
  };

  const availableTabs = getAvailableTabs();
  const requestedTab = searchParams.get('tab') as TabType | null;
  const showPolicy = searchParams.get('policy') === 'open';
  const [activeTab, setActiveTab] = useState<TabType>(
    requestedTab === 'history' || requestedTab === 'manage' || requestedTab === 'apply'
      ? requestedTab
      : getDefaultTab(),
  );

  // The initial useState above only reads the URL once, at mount. A Link
  // elsewhere on this same page (e.g. the "Go to leave history" button after
  // submitting a request) changes only the `tab` query param without
  // remounting Leaves — React Router updates searchParams, but nothing was
  // re-deriving activeTab from it, so the tab strip silently stayed put. This
  // keeps activeTab in sync with the URL for the whole lifetime of the page,
  // not just the first render.
  useEffect(() => {
    const requested = searchParams.get('tab') as TabType | null;
    if (requested === 'history' || requested === 'manage' || requested === 'apply') {
      setActiveTab(requested);
    }
  }, [searchParams]);

  useEffect(() => {
    const canManage = isPrivileged(user);
    if (activeTab === 'manage' && !canManage) {
      setActiveTab('apply');
    }
  }, [activeTab, user?.role, user?.isHr]);

  const setPolicyOpen = useCallback((open: boolean) => {
    const next = new URLSearchParams(searchParams);
    if (open) next.set('policy', 'open');
    else next.delete('policy');
    setSearchParams(next, { replace: true });
  }, [searchParams, setSearchParams]);

  useEffect(() => {
    if (!showPolicy) return;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setPolicyOpen(false);
    };
    window.addEventListener('keydown', closeOnEscape);
    return () => {
      document.body.style.overflow = previousOverflow;
      window.removeEventListener('keydown', closeOnEscape);
    };
  }, [showPolicy, setPolicyOpen]);

  return (
    <div className="lv-page">
      <header className="lv-page-header">
        <div><p>Home / Leaves</p><h1 className="lv-page-title">Leaves</h1><span>Manage your time off, track balances, and submit requests.</span></div>
        {/* No "Leave history" button here — it duplicated the Leave History tab
            directly below, giving two controls for the same destination. Same
            reasoning removed the "Leave policy" button that used to live here:
            it duplicated the Apply Leave tab's "Review Leave Policy" button. */}
      </header>

      <div className="lv-tabs">
        {availableTabs.map((tab) => (
          <button
            key={tab.id}
            className={`lv-tab-btn${activeTab === tab.id ? ' lv-tab-active' : ''}`}
            onClick={() => {
              setActiveTab(tab.id);
              setSearchParams(tab.id === 'apply' ? {} : { tab: tab.id }, { replace: true });
            }}
          >
            {tab.label}
          </button>
        ))}
      </div>

      <div className="lv-tab-content">
        {activeTab === 'apply' && <ApplyLeave onReviewPolicy={() => setPolicyOpen(true)} />}
        {activeTab === 'manage' && isPrivileged(user) && <ManageLeaves />}
        {activeTab === 'history' && <LeaveHistory />}
      </div>

      {showPolicy && (
        <div
          className="lv-policy-backdrop"
          onMouseDown={(event) => event.target === event.currentTarget && setPolicyOpen(false)}
        >
          <section
            className="lv-policy-modal"
            role="dialog"
            aria-modal="true"
            aria-labelledby="lv-policy-title"
          >
            <header className="lv-policy-modal-header">
              <div>
                <span><FiBookOpen /></span>
                <div>
                  <h2 id="lv-policy-title">Company leave policy</h2>
                  <p>Effective 1 January 2026 · Approved by Sardar Usairem Alamgeer, CEO</p>
                </div>
              </div>
              <button type="button" onClick={() => setPolicyOpen(false)} aria-label="Close leave policy" autoFocus>
                <FiX />
              </button>
            </header>
            <div className="lv-policy-modal-body">
              <LeavePolicy embedded />
            </div>
          </section>
        </div>
      )}
    </div>
  );
};

export default Leaves;
