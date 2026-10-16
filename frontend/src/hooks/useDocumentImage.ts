import { useEffect, useState } from 'react';
import { fetchDocumentBlob } from '../services/documentService';

/**
 * Resolve a protected uploaded image to a local object URL.
 *
 * Stored profile images sit behind an authenticated API route, so assigning
 * their saved URL directly to <img src> fails because the browser does not add
 * the bearer token. Fetch through the API client, then render the local blob.
 */
export const useDocumentImage = (storedUrl?: string | null) => {
  const [imageUrl, setImageUrl] = useState('');
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let cancelled = false;
    let objectUrl = '';

    setImageUrl('');
    setFailed(false);

    if (!storedUrl) return;

    void fetchDocumentBlob(storedUrl)
      .then((blob) => {
        if (cancelled) return;
        objectUrl = URL.createObjectURL(blob);
        setImageUrl(objectUrl);
      })
      .catch(() => {
        if (!cancelled) setFailed(true);
      });

    return () => {
      cancelled = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [storedUrl]);

  return { imageUrl, failed };
};
