import { useEffect, useMemo, useRef, useState } from 'react';
import { FiVolume2 } from 'react-icons/fi';
import { announcementService } from '../../services/announcementService';
import { type Announcement } from '../../types';
import './AnnouncementTicker.css';

/** Pixels per second. Slow enough to read a passing sentence. */
const SPEED = 55;

/**
 * Company announcements as a moving strip across the top of the dashboard.
 *
 * The card below it stays: this is the thing you cannot miss on the way past,
 * that one is where you read the whole notice. It fetches the same endpoint as
 * the card and they mount together, so the in-flight dedupe in `api.ts`
 * collapses the two into one request.
 */
const AnnouncementTicker = () => {
  const [items, setItems] = useState<Announcement[]>([]);
  const trackRef = useRef<HTMLDivElement>(null);
  const [duration, setDuration] = useState(30);

  useEffect(() => {
    let live = true;
    announcementService.getAnnouncements()
      .then((list) => { if (live) setItems(list); })
      .catch(() => { if (live) setItems([]); });
    return () => { live = false; };
  }, []);

  const messages = useMemo(
    () => items.filter((a) => a.title || a.content),
    [items],
  );

  // The strip holds two copies of the list and slides exactly one copy's
  // width, so the seam lands where the sequence repeats and the loop is
  // invisible. Timing it from the measured width keeps the speed constant
  // whether there is one notice or ten.
  useEffect(() => {
    if (!trackRef.current || messages.length === 0) return;
    const half = trackRef.current.scrollWidth / 2;
    if (half > 0) setDuration(Math.max(12, half / SPEED));
  }, [messages]);

  // Nothing to say is not worth a bar saying so.
  if (messages.length === 0) return null;

  const run = (keyPrefix: string) => messages.map((a, i) => (
    <span className="tk-item" key={`${keyPrefix}-${a.id || i}`}>
      <b>{a.title}</b>
      {a.content && <span className="tk-text">{a.content}</span>}
    </span>
  ));

  return (
    <aside className="tk-bar" aria-label="Company announcements">
      <span className="tk-badge"><FiVolume2 aria-hidden="true" /> Announcements</span>

      <div className="tk-window">
        <div
          className="tk-track"
          ref={trackRef}
          style={{ animationDuration: `${duration}s` }}
        >
          {run('a')}
          {/* The second copy is decoration; a screen reader should hear the
              notices once, not twice. */}
          <span aria-hidden="true" className="tk-dup">{run('b')}</span>
        </div>
      </div>
    </aside>
  );
};

export default AnnouncementTicker;
