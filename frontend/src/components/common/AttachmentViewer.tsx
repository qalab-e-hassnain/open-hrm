import { useEffect, useState } from 'react';
import { FiFile, FiX } from 'react-icons/fi';
import { documentFilename, fetchDocumentBlob } from '../../services/documentService';

/**
 * Modal preview for an uploaded file (a reimbursement receipt, a leave
 * document).
 *
 * Uploads sit behind an authenticated endpoint, so the stored URL cannot be
 * handed straight to <object> or a link: that request arrives without a token,
 * the file never loads, and the viewer shows its "cannot be previewed"
 * fallback for a file that is perfectly fine. The bytes are fetched once with
 * the caller's credentials and everything then points at that local copy.
 *
 * `prefix` selects the host page's own style namespace (`ml-doc-*`,
 * `rh-doc-*`), since page CSS is bundled globally and each page prefixes its
 * classes to stay out of the others' way.
 */
const AttachmentViewer = ({
  url,
  label,
  title = 'Attachment',
  prefix,
  onClose,
}: {
  url: string;
  label: string;
  title?: string;
  prefix: string;
  onClose: () => void;
}) => {
  const [objectUrl, setObjectUrl] = useState('');
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let cancelled = false;
    let created = '';
    setObjectUrl('');
    setFailed(false);

    void (async () => {
      try {
        const blob = await fetchDocumentBlob(url);
        if (cancelled) return;
        created = URL.createObjectURL(blob);
        setObjectUrl(created);
      } catch {
        if (!cancelled) setFailed(true);
      }
    })();

    return () => {
      cancelled = true;
      if (created) URL.revokeObjectURL(created);
    };
  }, [url]);

  return (
    <div
      className={`${prefix}-doc-backdrop`}
      onMouseDown={(event) => event.target === event.currentTarget && onClose()}
    >
      <div className={`${prefix}-doc-viewer`} role="dialog" aria-modal="true" aria-label={title}>
        <header>
          <span><FiFile /> {label}</span>
          <span className={`${prefix}-doc-tools`}>
            {objectUrl && <a href={objectUrl} target="_blank" rel="noopener noreferrer">Open in new tab</a>}
            <button type="button" onClick={onClose} aria-label={`Close ${title.toLowerCase()}`}><FiX /></button>
          </span>
        </header>
        {objectUrl ? (
          /* An <object> renders images and PDFs inline and falls back to the
             link below for anything the browser cannot display itself. */
          <object data={objectUrl} aria-label={title}>
            <div className={`${prefix}-doc-fallback`}>
              <FiFile />
              <p>This file cannot be previewed here.</p>
              <a href={objectUrl} download={documentFilename(url, label, title)}>Download the file</a>
            </div>
          </object>
        ) : (
          <div className={`${prefix}-doc-fallback`}>
            <FiFile />
            <p>{failed ? 'This file could not be loaded. Ask an administrator to check it was uploaded correctly.' : 'Loading the file…'}</p>
          </div>
        )}
      </div>
    </div>
  );
};

export default AttachmentViewer;
