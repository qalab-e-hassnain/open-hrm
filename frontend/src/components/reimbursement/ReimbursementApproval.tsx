import { useState } from 'react';
import Button from '../common/Button';
import TextArea from '../common/TextArea';
import './ReimbursementApproval.css';

interface ReimbursementApprovalProps {
  reimbursement: any;
  onApprove: (id: string) => void;
  onReject: (id: string, reason: string) => void;
  isManagerOrAdmin: boolean;
}

const ReimbursementApproval = ({ reimbursement, onApprove, onReject, isManagerOrAdmin }: ReimbursementApprovalProps) => {
  const [showReject, setShowReject] = useState(false);
  const [rejectionReason, setRejectionReason] = useState('');
  const isRejected = reimbursement.status === 'rejected';
  const isApproved = reimbursement.status === 'approved';

  return (
    <div className="reimbursement-approval-section">
      <div className="reimbursement-details">
        <div className="detail-row">
          <span className="detail-label">Type:</span>
          <span className="detail-value">{reimbursement.reimbursementType || 'Other'}</span>
        </div>
        <div className="detail-row">
          <span className="detail-label">Amount:</span>
          <span className="detail-value">PKR {reimbursement.amount?.toLocaleString() || '0'}</span>
        </div>
        <div className="detail-row">
          <span className="detail-label">Description:</span>
          <span className="detail-value">{reimbursement.reason || '-'}</span>
        </div>
        <div className="detail-row">
          <span className="detail-label">Status:</span>
          <span className={`status-badge status-${reimbursement.status}`}>{reimbursement.status}</span>
        </div>
        {reimbursement.rejection_reason && (
          <div className="detail-row rejection-row">
            <span className="detail-label">Rejection Reason:</span>
            <span className="rejection-text">{reimbursement.rejection_reason}</span>
          </div>
        )}
      </div>
      {isManagerOrAdmin && !isApproved && !isRejected && (
        <div className="approval-actions">
          <Button variant="success" onClick={() => onApprove(reimbursement.id)}>
            Approve
          </Button>
          <Button variant="secondary" onClick={() => setShowReject(!showReject)}>
            Reject
          </Button>
        </div>
      )}
      {showReject && !isApproved && !isRejected && (
        <div className="rejection-reason-section">
          <TextArea
            label="Rejection Reason"
            value={rejectionReason}
            onChange={e => setRejectionReason(e.target.value)}
            placeholder="Enter reason for rejection"
            required
            className="rejection-reason-textarea"
            rows={3}
          />
          <div className="rejection-actions">
            <Button
              variant="secondary"
              onClick={() => setShowReject(false)}
            >
              Cancel
            </Button>
            <Button
              variant="primary"
              onClick={() => onReject(reimbursement.id, rejectionReason)}
              disabled={!rejectionReason}
            >
              Submit
            </Button>
          </div>
        </div>
      )}
    </div>
  );
};

export default ReimbursementApproval;
