// Backend timestamps are naive UTC (e.g. "2026-07-20T14:39:22" — no timezone
// suffix). The browser would otherwise parse them as LOCAL time, showing the
// wrong hour. These helpers parse them as UTC and render in Pakistan Standard
// Time (PKT, UTC+5) so times read correctly regardless of the viewer's device.

const TZ = 'Asia/Karachi';

// Parse an ISO string, treating a naive date-time (has "T", no timezone) as UTC.
const asUTC = (iso: string): Date => {
  const hasTime = iso.includes('T');
  const hasTz = /[zZ]$|[+-]\d{2}:?\d{2}$/.test(iso);
  return new Date(hasTime && !hasTz ? `${iso}Z` : iso);
};

// "03:39 PM"
export const formatPktTime = (iso?: string): string => {
  if (!iso) return '--:--';
  return asUTC(iso).toLocaleTimeString('en-PK', {
    timeZone: TZ, hour: '2-digit', minute: '2-digit', hour12: true,
  });
};

// "20 Jul 2026"
export const formatPktDate = (iso?: string): string => {
  if (!iso) return '--';
  return asUTC(iso).toLocaleDateString('en-GB', {
    timeZone: TZ, day: '2-digit', month: 'short', year: 'numeric',
  });
};

// "20 Jul 2026, 03:39 PM"
export const formatPktDateTime = (iso?: string): string => {
  if (!iso) return '--';
  return asUTC(iso).toLocaleString('en-GB', {
    timeZone: TZ, day: '2-digit', month: 'short', year: 'numeric',
    hour: '2-digit', minute: '2-digit', hour12: true,
  });
};
