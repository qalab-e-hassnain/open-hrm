import { useEffect, useState } from 'react';
import { FiChevronDown, FiInbox, FiPlus, FiRefreshCw, FiTrash2, FiX } from 'react-icons/fi';
import { useAuthStore } from '../../store/authStore';
import { announcementService } from '../../services/announcementService';
import { type Announcement } from '../../types';
import Card from './Card';
import Button from './Button';
import './AnnouncementsCard.css';

const getErrorMessage = (error: unknown, fallback: string) => {
  if (error && typeof error === 'object' && 'response' in error) {
    const response = (error as { response?: { data?: { detail?: string } } }).response;
    if (response?.data?.detail) return response.data.detail;
  }
  return fallback;
};

const AnnouncementsCard = () => {
  const { user } = useAuthStore();
  const canManage = user?.role === 'admin' || user?.role === 'manager';
  const [announcements, setAnnouncements] = useState<Announcement[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [isFormOpen, setIsFormOpen] = useState(false);
  const [isSaving, setIsSaving] = useState(false);
  const [title, setTitle] = useState('');
  const [content, setContent] = useState('');
  const [loadError, setLoadError] = useState('');
  const [feedback, setFeedback] = useState<{ type: 'success' | 'error'; message: string } | null>(null);

  const load = async () => {
    try {
      setIsLoading(true);
      setLoadError('');
      setAnnouncements(await announcementService.getAnnouncements());
    } catch {
      setAnnouncements([]);
      setLoadError('Announcements could not be loaded.');
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    void load();
  }, []);

  const toggle = (id: string) => {
    setAnnouncements((previous) => previous.map(
      (announcement) => announcement.id === id
        ? { ...announcement, isExpanded: !announcement.isExpanded }
        : announcement,
    ));
  };

  const handleCreate = async () => {
    if (!title.trim() || !content.trim()) {
      setFeedback({ type: 'error', message: 'Add a title and announcement before posting.' });
      return;
    }
    try {
      setIsSaving(true);
      await announcementService.createAnnouncement({ title: title.trim(), content: content.trim() });
      setTitle('');
      setContent('');
      setIsFormOpen(false);
      setFeedback({ type: 'success', message: 'Announcement posted.' });
      await load();
    } catch (error: unknown) {
      setFeedback({ type: 'error', message: getErrorMessage(error, 'Announcement could not be posted.') });
    } finally {
      setIsSaving(false);
    }
  };

  const handleDelete = async (id: string) => {
    if (!window.confirm('Delete this announcement?')) return;
    try {
      await announcementService.deleteAnnouncement(id);
      setAnnouncements((previous) => previous.filter((announcement) => announcement.id !== id));
      setFeedback({ type: 'success', message: 'Announcement deleted.' });
    } catch (error: unknown) {
      setFeedback({ type: 'error', message: getErrorMessage(error, 'Announcement could not be deleted.') });
    }
  };

  const formatDate = (iso: string) => {
    if (!iso) return '';
    const date = new Date(iso);
    if (Number.isNaN(date.getTime())) return '';
    return date.toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' });
  };

  return (
    <Card
      title="Announcements"
      className="announcements-card"
      headerAction={canManage ? (
        <button type="button" className="ann-add-btn" onClick={() => setIsFormOpen(!isFormOpen)}>
          {isFormOpen ? <FiX size={15} /> : <FiPlus size={15} />}
          <span>{isFormOpen ? 'Close' : 'New'}</span>
        </button>
      ) : undefined}
    >
      {feedback && (
        <div className={`ann-feedback ann-feedback-${feedback.type}`} role="status">
          <span>{feedback.message}</span>
          <button type="button" onClick={() => setFeedback(null)} aria-label="Dismiss message"><FiX /></button>
        </div>
      )}

      {canManage && isFormOpen && (
        <div className="ann-form">
          <input type="text" className="ann-form-title" placeholder="Announcement title" value={title} maxLength={200} onChange={(event) => setTitle(event.target.value)} />
          <textarea className="ann-form-content" placeholder="Write the announcement..." rows={3} value={content} onChange={(event) => setContent(event.target.value)} />
          <Button variant="primary" onClick={handleCreate} isLoading={isSaving} className="ann-form-submit">Post announcement</Button>
        </div>
      )}

      {isLoading ? (
        <div className="ann-skeleton-list" aria-label="Loading announcements">
          {[1, 2, 3].map((item) => <span className="ann-skeleton-row" key={item} />)}
        </div>
      ) : loadError ? (
        <div className="ann-state">
          <FiInbox />
          <strong>Announcements unavailable</strong>
          <span>{loadError}</span>
          <button type="button" onClick={() => void load()}><FiRefreshCw /> Try again</button>
        </div>
      ) : announcements.length === 0 ? (
        <div className="ann-state">
          <FiInbox />
          <strong>No announcements</strong>
          <span>Company updates will appear here when published.</span>
        </div>
      ) : (
        <div className="ann-list">
          {announcements.slice(0, 5).map((announcement) => (
            <div key={announcement.id} className={`ann-item${announcement.isExpanded ? ' expanded' : ''}`}>
              <button type="button" className="ann-item-header" onClick={() => toggle(announcement.id)}>
                <span className="ann-item-title">{announcement.title}</span>
                <span className="ann-item-meta">{formatDate(announcement.date)}</span>
                <FiChevronDown className={`ann-chevron${announcement.isExpanded ? ' open' : ''}`} size={16} />
              </button>
              {announcement.isExpanded && (
                <div className="ann-item-body">
                  <p className="ann-item-content">{announcement.content}</p>
                  <div className="ann-item-footer">
                    {announcement.creatorName && <span className="ann-item-author">Posted by {announcement.creatorName}</span>}
                    {canManage && (
                      <button type="button" className="ann-delete-btn" onClick={() => void handleDelete(announcement.id)}>
                        <FiTrash2 size={14} /><span>Delete</span>
                      </button>
                    )}
                  </div>
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </Card>
  );
};

export default AnnouncementsCard;
