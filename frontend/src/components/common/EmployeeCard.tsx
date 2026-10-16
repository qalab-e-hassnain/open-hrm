import { useRef, useState } from 'react';
import qrcode from 'qrcode-generator';
import { FiCreditCard, FiDownload, FiGlobe, FiMail, FiPhone, FiRotateCw } from 'react-icons/fi';
import { COMPANY, ROUTES } from '../../config/constants';
import logo from '../../assets/logo.webp';
import './EmployeeCard.css';

/** Synchronous, so a capture never races an image load. */
const qrDataUrl = (value: string) => {
  const qr = qrcode(0, 'M');
  qr.addData(value);
  qr.make();
  // 6 module-pixels rather than 3: this is the only scannable thing left on
  // the badge, so it is drawn large enough to read across a desk.
  return qr.createDataURL(6, 0);
};

const initials = (name?: string) =>
  (name || '').split(/\s+/).filter(Boolean).slice(0, 2)
    .map((part) => part[0]?.toUpperCase() ?? '').join('') || '—';

const longDate = (value?: string | null) => {
  if (!value) return '—';
  const d = new Date(value);
  return Number.isNaN(d.getTime())
    ? '—'
    : d.toLocaleDateString('en-GB', { day: '2-digit', month: 'short', year: 'numeric' });
};

export interface EmployeeCardProps {
  name?: string;
  designation?: string | null;
  department?: string | null;
  employeeId?: string | number;
  photoUrl?: string | null;
  joiningDate?: string | null;
  email?: string | null;
  phone?: string | null;
  /** Encoded in the QR on the back. Without it the QR still points at the
   *  verification page, just unfilled. */
  verificationCode?: string | null;
}

/**
 * The employee's identity badge, on a lanyard.
 *
 * Deliberately not a security credential: it carries no signature or stamp,
 * because anyone can photograph a badge. The only thing on it that proves
 * anything is the QR, which resolves against the public verification page.
 */
const EmployeeCard = ({
  name, designation, department, employeeId, photoUrl,
  joiningDate, email, phone, verificationCode,
}: EmployeeCardProps) => {
  const [flipped, setFlipped] = useState(false);
  const [saving, setSaving] = useState(false);
  const hangRef = useRef<HTMLDivElement>(null);
  const frontRef = useRef<HTMLDivElement>(null);
  const backRef = useRef<HTMLDivElement>(null);

  const verifyUrl = verificationCode
    ? `${window.location.origin}${ROUTES.VERIFY_EMPLOYMENT}?code=${encodeURIComponent(String(verificationCode))}`
    : `${window.location.origin}${ROUTES.VERIFY_EMPLOYMENT}`;

  /**
   * Both faces in one PDF.
   *
   * Neither face can be captured where it sits: the back is rotated 180° with
   * `backface-visibility: hidden`, and whichever face is turned away is
   * invisible to a capture. So both are cloned onto an off-screen stage with
   * the 3D transforms removed, shot flat, and placed side by side.
   */
  /**
   * Both faces on one sheet, at a size somebody can print and cut out.
   *
   * What stops a face being captured is the 3D staging, not its position:
   * the back is rotated 180° and whichever side faces away is hidden. So the
   * staging is flattened in place and the unwanted face hidden, leaving each
   * one in its real layout box. Cloning onto a detached stage — the obvious
   * approach, and the first one tried — let every percentage-sized shape
   * resolve against a different box, which put the artwork outside the card.
   */
  const download = async () => {
    if (!frontRef.current || !backRef.current) return;
    setSaving(true);
    try {
      const [{ default: jsPDF }, { default: html2canvas }] = await Promise.all([
        import('jspdf'),
        import('html2canvas-pro'),
      ]);

      // The sway is on the hanger, above the scene. Left running, the
      // capture catches the badge mid-rotation and every card comes out
      // tilted a degree or two.
      const hang = hangRef.current;
      const rect = frontRef.current.getBoundingClientRect();

      const shoot = async (face: HTMLDivElement, showBack: boolean) => {
        hang?.classList.toggle('show-back-for-export', showBack);
        void face.offsetHeight;
        await new Promise((r) => requestAnimationFrame(() => r(null)));
        return html2canvas(face, { scale: 4, backgroundColor: '#ffffff', logging: false });
      };

      hang?.classList.add('is-exporting');
      const front = await shoot(frontRef.current, false);
      const back = await shoot(backRef.current, true);
      hang?.classList.remove('is-exporting', 'show-back-for-export');

      const pdf = new jsPDF({ unit: 'mm', format: 'a4', orientation: 'portrait' });
      // Real millimetres, at the card's own proportions, so what comes out of
      // the printer is the size it looks.
      // CR80 portrait — the size every badge holder and lanyard wallet takes.
      const CARD_W = 54;
      const CARD_H = CARD_W * (rect.height / rect.width);
      const GAP = 14;
      const totalW = CARD_W * 2 + GAP;
      const x1 = (210 - totalW) / 2;
      const x2 = x1 + CARD_W + GAP;
      const y = 52;

      pdf.setFontSize(14);
      pdf.setTextColor(20);
      pdf.text(name || 'Employee', 105, 24, { align: 'center' });
      pdf.setFontSize(9);
      pdf.setTextColor(115);
      pdf.text(
        [designation, employeeId ? `ID ${employeeId}` : ''].filter(Boolean).join('   ·   '),
        105, 30, { align: 'center' },
      );
      pdf.setFontSize(8);
      pdf.text('Print at 100% (no page scaling), cut on the marks, and place back to back.',
        105, 40, { align: 'center' });

      pdf.addImage(front.toDataURL('image/png'), 'PNG', x1, y, CARD_W, CARD_H);
      pdf.addImage(back.toDataURL('image/png'), 'PNG', x2, y, CARD_W, CARD_H);

      // Crop marks sit outside the card so nothing is drawn over the design.
      pdf.setDrawColor(150);
      pdf.setLineWidth(0.2);
      const TICK = 4;
      const GUTTER = 1.5;
      for (const x of [x1, x2]) {
        for (const [cx, cy] of [[x, y], [x + CARD_W, y], [x, y + CARD_H], [x + CARD_W, y + CARD_H]]) {
          const dx = cx === x ? -1 : 1;
          const dy = cy === y ? -1 : 1;
          pdf.line(cx + dx * GUTTER, cy, cx + dx * (GUTTER + TICK), cy);
          pdf.line(cx, cy + dy * GUTTER, cx, cy + dy * (GUTTER + TICK));
        }
      }

      pdf.setFontSize(7.5);
      pdf.setTextColor(140);
      pdf.text('Front', x1, y + CARD_H + 9);
      pdf.text('Back', x2, y + CARD_H + 9);
      pdf.text(`${CARD_W} × ${CARD_H.toFixed(0)} mm`, x1 + CARD_W, y + CARD_H + 9, { align: 'right' });
      pdf.text('Verify at ' + verifyUrl, 105, 285, { align: 'center' });

      pdf.save(`${(name || 'employee').replace(/\s+/g, '-').toLowerCase()}-badge.pdf`);
    } catch {
      /* A failed capture is not worth an error dialog on a badge. */
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="ec-wrap">
      {/* Hangs and sways from the clip, so the whole assembly pivots where a
          real lanyard would. */}
      <div className="ec-hang" ref={hangRef}>
        <svg className="ec-lanyard" viewBox="0 0 120 80" aria-hidden="true" preserveAspectRatio="none">
          {/* Two straps converging on the clip, not one peak. */}
          <path d="M14 0 L34 0 L64 74 L54 74 Z" fill="#1f2937" />
          <path d="M86 0 L106 0 L66 74 L56 74 Z" fill="#111827" />
        </svg>

        <span className="ec-clip" aria-hidden="true">
          <span className="ec-clip-ring" />
          <span className="ec-clip-hook" />
        </span>

        <div className="ec-holder">
          <span className="ec-slot" aria-hidden="true" />

          <div className={`ec-scene${flipped ? ' is-flipped' : ''}`}>
            <div className="ec-inner">
              {/* ---------- FRONT ---------- */}
              <div className="ec-face ec-front" ref={frontRef}>
                {/* One full-bleed layer in the card's own coordinate space, so a
                    capture has no percentage sizing left to disagree about. */}
                <svg className="ec-art" viewBox="0 0 300 476" preserveAspectRatio="none" aria-hidden="true">
                  <defs>
                    <linearGradient id="ec-field" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="0" stopColor="#A93226" />
                      <stop offset="1" stopColor="#8F2C22" />
                    </linearGradient>
                    <clipPath id="ec-field-clip"><rect x="0" y="0" width="300" height="298" /></clipPath>
                  </defs>
                  <rect x="0" y="0" width="300" height="298" fill="url(#ec-field)" />
                  {/* Clipped to the field so they stop at the white panel rather
                      than bleeding across the join. */}
                  <g clipPath="url(#ec-field-clip)" fill="#D9685D" opacity=".34">
                    <circle cx="44" cy="146" r="80" />
                    <circle cx="272" cy="262" r="64" />
                  </g>
                </svg>

                <div className="ec-mark">
                  <img src={logo} alt="" />
                  <span>{COMPANY.SHORT_NAME}</span>
                </div>

                <div className="ec-photo">
                  {photoUrl
                    ? <img src={photoUrl} alt="" crossOrigin="anonymous" />
                    : <span className="ec-photo-fallback">{initials(name)}</span>}
                </div>

                <div className="ec-panel">
                  <strong className="ec-name">{name || 'Unnamed'}</strong>
                  <span className="ec-role">{designation || 'Designation not set'}</span>
                  <i className="ec-rule" aria-hidden="true" />

                  <ul className="ec-contact">
                    {phone && <li><span className="ec-ico"><FiPhone /></span>{phone}</li>}
                    {email && <li><span className="ec-ico"><FiMail /></span><span className="ec-trunc">{email}</span></li>}
                    <li><span className="ec-ico"><FiCreditCard /></span>ID: {employeeId || '—'}</li>
                    <li><span className="ec-ico"><FiGlobe /></span>{COMPANY.WEBSITE}</li>
                  </ul>
                </div>
              </div>

              {/* ---------- BACK ---------- */}
              {/* Everything the front has no room for, and one scannable mark.
                  The QR is the only thing on this badge that proves anything:
                  the rest can be photographed, but the code resolves against
                  the company record. */}
              <div className="ec-face ec-back" ref={backRef}>
                <svg className="ec-art" viewBox="0 0 300 476" preserveAspectRatio="none" aria-hidden="true">
                  <defs>
                    <linearGradient id="ec-field-back" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="0" stopColor="#A93226" />
                      <stop offset="1" stopColor="#8F2C22" />
                    </linearGradient>
                    <clipPath id="ec-head-clip"><rect x="0" y="0" width="300" height="96" /></clipPath>
                  </defs>
                  <rect x="0" y="0" width="300" height="96" fill="url(#ec-field-back)" />
                  <g clipPath="url(#ec-head-clip)">
                    <circle cx="268" cy="34" r="54" fill="#D9685D" opacity=".3" />
                  </g>
                  <rect x="0" y="428" width="300" height="48" fill="#A93226" />
                </svg>

                <div className="ec-mark ec-mark-back">
                  <img src={logo} alt="" />
                  <span>{COMPANY.SHORT_NAME}</span>
                </div>

                <div className="ec-qr">
                  <img src={qrDataUrl(verifyUrl)} alt="QR code linking to the employment verification page" />
                  <strong>Scan to verify</strong>
                  <span>Checks this badge against the company record, and says plainly if the employment has ended.</span>
                  {verificationCode && <code>{verificationCode}</code>}
                </div>

                <dl className="ec-facts">
                  <div><dt>Employee ID</dt><dd>{employeeId || '—'}</dd></div>
                  {department && <div><dt>Department</dt><dd>{department}</dd></div>}
                  <div><dt>Joined</dt><dd>{longDate(joiningDate)}</dd></div>
                </dl>

                <p className="ec-note">Company property. If found, please return it to the address below.</p>

                <div className="ec-foot">
                  <span>{COMPANY.ADDRESS}</span>
                  <span>{COMPANY.EMAIL}</span>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>

      <div className="ec-actions">
        <button type="button" onClick={() => setFlipped(!flipped)}>
          <FiRotateCw /> {flipped ? 'Show front' : 'Show back'}
        </button>
        <button type="button" onClick={() => void download()} disabled={saving}>
          <FiDownload /> {saving ? 'Saving…' : 'Save as PDF'}
        </button>
      </div>
    </div>
  );
};

export default EmployeeCard;
