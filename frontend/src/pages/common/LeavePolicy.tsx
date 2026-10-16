import { useEffect, useState } from 'react';
import { FiBook, FiCalendar, FiClock, FiFileText } from 'react-icons/fi';
import Card from '../../components/common/Card';
import { useAuthStore } from '../../store/authStore';
import { leaveService, type LeaveBalanceSummary } from '../../services/leaveService';
import { describeDay, hoursPerDay, isPartTime, rateBasis } from '../../utils/workHours';
import './LeavePolicy.css';

const LeavePolicy = ({ embedded = false }: { embedded?: boolean }) => {
  const { user } = useAuthStore();
  // The schedule shown is the reader's own contract, not the company default:
  // a part-time employee works a shorter day and a shorter month, and their
  // unpaid-leave deduction divides by fewer hours to match.
  const day = hoursPerDay(user?.standardHours);
  const partTime = isPartTime(user?.standardHours);

  // Every figure below comes from the balance response. They were written into
  // this page as constants and had drifted: annual is accrued per person and
  // moves monthly, nothing carries any more, and personal leave is a monthly
  // allowance rather than twelve days a year.
  const [balance, setBalance] = useState<LeaveBalanceSummary | null>(null);
  useEffect(() => {
    let live = true;
    leaveService.getMyLeaveBalance()
      .then((b) => { if (live) setBalance(b); })
      .catch(() => undefined);
    return () => { live = false; };
  }, []);

  const b = balance?.balances ?? {};
  const annual = b.annual;
  const sick = b.sick;
  const personal = b.personal ?? b.casual;
  const policy = balance?.policyInfo ?? {};
  /** A figure from the API, or a dash until it arrives — never a guess. */
  const n = (v?: number) => (typeof v === 'number' ? `${v}` : '—');
  const days = (v?: number) => (typeof v === 'number' ? `${v} working day${v === 1 ? '' : 's'}` : '—');

  return (
    <div className={`leave-policy-page${embedded ? ' leave-policy-embedded' : ''}`}>
      {!embedded && (
        <div className="policy-header">
          <FiBook size={40} className="policy-icon" />
          <div className="policy-header-text">
            <h1>Company Leave Policy</h1>
            <p className="effective-date">Effective Date: 1 January 2026</p>
          </div>
        </div>
      )}

      {/* Standard Working Hours */}
      <Card title="Standard Working Hours" icon={<FiClock size={20} />} className="policy-section">
        <div className="policy-content">
          <div className="policy-item">
            <strong>Work Schedule:</strong> Monday to Friday
          </div>
          <div className="policy-item">
            <strong>Daily Working Hours:</strong> {describeDay(user?.standardHours)}
          </div>
          <div className="policy-item">
            <strong>Weekly Working Hours:</strong> {day * 5} hours per week
          </div>
        </div>
      </Card>

      {/* Leave Types */}
      <Card title="Types of Leave" icon={<FiCalendar size={20} />} className="policy-section">
        <div className="leave-types-grid">
          {/* Annual Leave */}
          <div className="leave-type-card annual">
            <div className="leave-type-header">
              <h3>1. Annual Leave (Paid Vacation)</h3>
              <span className="leave-entitlement">
                {annual?.accrual ? `${annual.accrual.rate_per_year} days/year` : '—'}
              </span>
            </div>
            <ul className="leave-details">
              <li><strong>Earned so far:</strong> {days(annual?.total)} — {n(annual?.remaining)} still available</li>
              {annual?.accrual && (
                <li><strong>How it accrues:</strong> {annual.accrual.explanation}</li>
              )}
              <li><strong>Notice:</strong> {policy.plannedLeaveNotice ?? '—'}</li>
              <li>
                <strong>Carryover:</strong>{' '}
                {annual?.maxCarryover === 0
                  ? 'None. Unused days do not carry into the new year.'
                  : `Up to ${n(annual?.maxCarryover)} days.`}
              </li>
            </ul>
          </div>

          {/* Sick Leave */}
          <div className="leave-type-card sick">
            <div className="leave-type-header">
              <h3>2. Sick Leave</h3>
              <span className="leave-entitlement">{n(sick?.total)} days/year</span>
            </div>
            <ul className="leave-details">
              <li><strong>Entitlement:</strong> {days(sick?.total)} per year — {n(sick?.remaining)} still available</li>
              <li><strong>Medical Certificate:</strong> Expected from the first day — hospital admission or a doctor’s prescription. Undocumented sick leave is adjusted against personal or annual leave</li>
              <li><strong>Carryover:</strong> Unused sick leave cannot be carried over to the next year</li>
            </ul>
          </div>

          {/* Personal Leave */}
          <div className="leave-type-card personal">
            <div className="leave-type-header">
              <h3>3. Personal Leave</h3>
              <span className="leave-entitlement">{n(personal?.total)} day/month</span>
            </div>
            <ul className="leave-details">
              {/* A monthly allowance, not a slice of a yearly pool: an unused
                  day is lost at month end rather than banked. */}
              <li>
                <strong>This month{personal?.period ? ` (${personal.period})` : ''}:</strong>{' '}
                {n(personal?.used)} used, {n(personal?.remaining)} left.
                {personal?.accrualRate ? ` ${personal.accrualRate}.` : ''}
              </li>
              <li><strong>Usage:</strong> For personal matters such as urgent appointments or unforeseen events</li>
              <li><strong>Notice:</strong> {policy.personalLeaveNotice ?? '—'}. Personal leave is not automatic — same-day requests are considered only for genuine emergencies</li>
              {typeof personal?.usedThisYear === 'number' && (
                <li className="policy-muted">
                  {personal.usedThisYear} taken across {balance?.year} so far — for your reference only; it is not deducted from this month.
                </li>
              )}
            </ul>
          </div>

          {/* Maternity Leave */}
          <div className="leave-type-card parental">
            <div className="leave-type-header">
              <h3>4. Maternity Leave</h3>
              <span className="leave-entitlement">{b.maternity?.duration ?? '—'}</span>
            </div>
            <ul className="leave-details">
              <li><strong>Entitlement:</strong> {b.maternity?.duration ?? days(b.maternity?.total)} of paid leave</li>
            </ul>
          </div>

          {/* Paternity Leave */}
          <div className="leave-type-card parental">
            <div className="leave-type-header">
              <h3>5. Paternity Leave</h3>
              <span className="leave-entitlement">{b.paternity?.duration ?? '—'}</span>
            </div>
            <ul className="leave-details">
              <li><strong>Entitlement:</strong> {b.paternity?.duration ?? days(b.paternity?.total)} of paid leave</li>
            </ul>
          </div>

          {/* Bereavement Leave */}
          <div className="leave-type-card bereavement">
            <div className="leave-type-header">
              <h3>6. Bereavement Leave</h3>
              <span className="leave-entitlement">Up to {n(b.bereavement?.maxDays ?? b.bereavement?.total)} days</span>
            </div>
            <ul className="leave-details">
              <li><strong>Entitlement:</strong> Up to {days(b.bereavement?.maxDays ?? b.bereavement?.total)}, depending on the relation to the deceased</li>
              <li><strong>Usage:</strong> In the event of the death of immediate family members</li>
            </ul>
          </div>

          {/* Unpaid Leave */}
          <div className="leave-type-card unpaid">
            <div className="leave-type-header">
              <h3>7. Unpaid Leave</h3>
              <span className="leave-entitlement">Case-by-case</span>
            </div>
            <ul className="leave-details">
              <li><strong>Usage:</strong> Granted on a case-by-case basis</li>
              <li><strong>Approval:</strong> Subject to management approval based on business needs</li>
              <li><strong>Salary Deduction:</strong> Each approved unpaid working day deducts {day} hours at the employee's {rateBasis(user?.standardHours)} hourly rate{partTime ? ' — a part-time day costs a part-time day, not a full one' : ''}</li>
            </ul>
          </div>
        </div>
      </Card>

      {/* Application Process */}
      <Card title="Leave Application and Approval Process" icon={<FiFileText size={20} />} className="policy-section">
        <div className="process-section">
          <h4>1. How to Apply:</h4>
          <ul className="process-list">
            <li>Submit your leave requests through the system at least <strong>2 weeks in advance</strong> for planned leaves</li>
            <li>For sick leave or emergencies, notify your manager as soon as possible</li>
          </ul>

          <h4>2. Approval Workflow:</h4>
          <ul className="process-list">
            <li>HR or an administrator will review and approve or decline your leave request</li>
            <li className="warning-text">
              <strong>Important:</strong> Unapproved leave is considered an absence. Admin or HR may manually
              apply a fine of <strong>{policy.unapprovedLeavePenalty ?? '—'}</strong> when generating the employee's payslip.
            </li>
            <li>The HR team will update your leave balance accordingly</li>
          </ul>

          <h4>3. Cancellation or Modification:</h4>
          <ul className="process-list">
            <li>You can cancel or modify leave requests through the system, subject to approval</li>
          </ul>
        </div>
      </Card>

      {/* General Policies */}
      <Card title="General Policies" className="policy-section">
        <div className="general-policies">
          <div className="policy-item-block">
            <h4>1. Leave Encashment:</h4>
            <p>Unused annual leave days may be encashed at the end of the year or upon termination of employment, as per company policy.</p>
          </div>

          <div className="policy-item-block">
            <h4>2. Compliance with Laws:</h4>
            <p>Our leave policy complies with all applicable local labor laws and regulations.</p>
          </div>
        </div>
      </Card>

      {/* Issued by, not signed by: the rule that used to sit here read as a
          place to sign, and this is rendered by the system. */}
      <div className="policy-signature">
        <div className="signature-info">
          <p className="signature-name"><strong>SARDAR USAIREM ALAMGEER</strong></p>
          <p className="signature-title">Chief Executive Officer</p>
          <p className="signature-date">1 January 2026</p>
        </div>
      </div>
    </div>
  );
};

export default LeavePolicy;

