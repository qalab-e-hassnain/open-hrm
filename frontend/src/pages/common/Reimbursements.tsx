import { useState, useEffect, useMemo } from 'react';
import { FiFileText, FiCheckCircle, FiClock } from 'react-icons/fi';
import { useAuthStore } from '../../store/authStore';
import Reimbursement from '../employee/Reimbursement';
import ManageReimbursement from '../manager/ManageReimbursement';
import ReimbursementHistory from './ReimbursementHistory';
import './Reimbursements.css';

type TabType = 'apply' | 'manage' | 'history';

const Reimbursements = () => {
  const { user } = useAuthStore();
  const canReview = user?.role === 'manager' || user?.role === 'admin';

  const availableTabs = useMemo(() => {
    const tabs: { id: TabType; label: string; icon: React.ReactNode }[] = [
      { id: 'apply', label: 'Apply Reimbursement', icon: <FiFileText /> },
    ];

    if (canReview) {
      tabs.push({ id: 'manage', label: 'Manage Reimbursements', icon: <FiCheckCircle /> });
    }

    tabs.push({ id: 'history', label: 'Reimbursement History', icon: <FiClock /> });

    return tabs;
  }, [canReview]);

  // Reviewers land on the queue they act on; everyone else lands on the form.
  const defaultTab: TabType = canReview ? 'manage' : 'apply';
  const [activeTab, setActiveTab] = useState<TabType>(defaultTab);

  // A role change can remove the tab that is currently open (e.g. a manager
  // demoted to employee while the Manage tab is showing).
  useEffect(() => {
    if (!availableTabs.some((tab) => tab.id === activeTab)) {
      setActiveTab(defaultTab);
    }
  }, [availableTabs, activeTab, defaultTab]);

  return (
    <div className="reimbursements-page">
      <header className="rb-page-header">
        <div>
          <h2 className="rb-page-title">Reimbursements</h2>
          <p>Submit expense claims and track their status.</p>
        </div>
      </header>

      <div className="rb-tabs" role="tablist" aria-label="Reimbursement sections">
        {availableTabs.map((tab) => (
          <button
            key={tab.id}
            type="button"
            role="tab"
            id={`rb-tab-${tab.id}`}
            aria-selected={activeTab === tab.id}
            aria-controls={`rb-panel-${tab.id}`}
            className={`rb-tab-btn${activeTab === tab.id ? ' rb-tab-active' : ''}`}
            onClick={() => setActiveTab(tab.id)}
          >
            {tab.icon}
            {tab.label}
          </button>
        ))}
      </div>

      <div className="rb-tab-content">
        {availableTabs.map((tab) => (
          activeTab === tab.id && (
            <div
              key={tab.id}
              className="rb-tab-panel"
              role="tabpanel"
              id={`rb-panel-${tab.id}`}
              aria-labelledby={`rb-tab-${tab.id}`}
            >
              {tab.id === 'apply' && <Reimbursement />}
              {tab.id === 'manage' && canReview && <ManageReimbursement />}
              {tab.id === 'history' && <ReimbursementHistory />}
            </div>
          )
        ))}
      </div>
    </div>
  );
};

export default Reimbursements;
