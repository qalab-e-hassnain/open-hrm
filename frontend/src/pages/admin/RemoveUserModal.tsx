import { useState, useEffect } from 'react';
import { useAuthStore } from '../../store/authStore';
import Button from '../../components/common/Button';
import Input from '../../components/common/Input';



interface RemoveUserModalProps {
  isOpen: boolean;
  onClose: () => void;
  onSuccess: () => void;
  userId?: string;
  email?: string;
}

const RemoveUserModal = ({ isOpen, onClose, onSuccess, userId, email: propEmail = '' }: RemoveUserModalProps) => {
  const { user: currentUser } = useAuthStore();
  const [email, setEmail] = useState(propEmail);
  const [reason, setReason] = useState('');
  useEffect(() => {
    if (isOpen) setEmail(propEmail);
  }, [isOpen, propEmail]);
  const [isPermanent, setIsPermanent] = useState(false);
  const [isLoading, setIsLoading] = useState(false);
  const [errorMsg, setErrorMsg] = useState('');
  // Role guard: Only admin can remove users
  const isAdmin = currentUser?.role === 'admin';

  const handleRemove = async () => {
    if (!isAdmin) {
      setErrorMsg('Unauthorized action');
      return;
    }
    if (!userId) {
      setErrorMsg('User ID is required');
      return;
    }
    setIsLoading(true);
    setErrorMsg('');
    try {
      // Call service method from employeeService
      const { removeUserById } = await import('../../services/employeeService');
      const response = await removeUserById(userId);
      if (response.success !== false && !response.detail) {
        onSuccess();
        onClose();
      } else {
        // Extract error from backend schema
        const detail = response.detail;
        if (Array.isArray(detail) && detail[0]?.msg) {
          setErrorMsg(detail[0].msg);
        } else if (typeof detail === 'string') {
          setErrorMsg(detail);
        } else {
          setErrorMsg('Failed to remove user');
        }
      }
    } catch (error: any) {
      // Enhanced error handling for REST API
      if (error?.response) {
        const status = error.response.status;
        const detail = error.response.data?.detail;
        if (status === 404) {
          setErrorMsg('User not found (404)');
        } else if (status === 422) {
          setErrorMsg('Invalid user ID (422)');
        } else if (status === 401) {
          setErrorMsg('Unauthorized (401)');
        } else if (Array.isArray(detail) && detail[0]?.msg) {
          setErrorMsg(detail[0].msg);
        } else if (typeof detail === 'string') {
          setErrorMsg(detail);
        } else {
          setErrorMsg('Failed to remove user');
        }
      } else {
        setErrorMsg(error.message || 'Failed to remove user');
      }
    } finally {
      setIsLoading(false);
    }
  };

  if (!isOpen) return null;

  return (
    <div className="modal-overlay">
      <div className="modal-content">
        <div className="modal-header">
          <h2>Remove User (Unregister)</h2>
          <button
            type="button"
            aria-label="Close"
            onClick={onClose}
            className="modal-close"
            tabIndex={0}
          >
            &times;
          </button>
        </div>
        <Input
          label="User Email"
          type="email"
          value={email || ''}
          disabled
        />
        <textarea
          className="removal-reason-textarea"
          placeholder="Reason for removal (required)"
          value={reason}
          onChange={e => setReason(e.target.value)}
          required
          disabled={!isAdmin || isLoading}
        />
        <div className="checkbox-group">
          <input
            type="checkbox"
            checked={isPermanent}
            onChange={e => setIsPermanent(e.target.checked)}
            disabled={!isAdmin || isLoading}
          />
          <label>User is leaving permanently</label>
        </div>
        {errorMsg && <div className="error-message">{errorMsg}</div>}
        <div className="modal-actions">
          <Button
            variant="secondary"
            onClick={onClose}
            disabled={isLoading}
          >
            Cancel
          </Button>
          <Button
            variant="primary"
            onClick={handleRemove}
            disabled={!userId || !isAdmin || isLoading}
            isLoading={isLoading}
          >
            Confirm Remove
          </Button>
        </div>
      </div>
    </div>
  );
};

export default RemoveUserModal;
