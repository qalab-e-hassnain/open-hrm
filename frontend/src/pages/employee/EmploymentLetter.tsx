import { useEffect, useRef, useState } from 'react';
import { FiDownload, FiFileText, FiPrinter } from 'react-icons/fi';
import qrcode from 'qrcode-generator';
import { authService } from '../../services/authService';
import { employeeService } from '../../services/employeeService';
import { useAuthStore } from '../../store/authStore';
import { COMPANY, ROUTES } from '../../config/constants';
import { type Employee, type User } from '../../types';
import qgtLogo from '../../assets/logo.webp';
import './EmploymentLetter.css';

/** "12 August 2025" — the form used on letterhead, not a numeric date. */
const longDate = (value?: string | null) => {
  if (!value) return null;
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return null;
  return parsed.toLocaleDateString('en-GB', { day: 'numeric', month: 'long', year: 'numeric' });
};

/** Synchronous, so Save PDF's html2canvas capture never races an image load. */
const letterQr = (url: string) => {
  const qr = qrcode(0, 'M');
  qr.addData(url);
  qr.make();
  return qr.createDataURL(4, 0);
};

/**
 * Employment verification letter.
 *
 * Addressed "To Whom It May Concern" because the reader is a bank, embassy or
 * landlord rather than anyone inside the company. It deliberately states only
 * employment facts — name, CNIC, designation, joining date — and never salary:
 * a letter that carries pay is a different document, requested for different
 * reasons, and handing it out by default leaks more than the reader asked for.
 */
const EmploymentLetter = () => {
  const { user } = useAuthStore();
  const isAdmin = user?.role === 'admin';

  const [profile, setProfile] = useState<User | null>(null);
  const [employees, setEmployees] = useState<Employee[]>([]);
  const [employeeId, setEmployeeId] = useState('');
  const [subject, setSubject] = useState<{
    name: string; cnic?: string | null; designation?: string | null;
    department?: string | null; employeeId?: string | null; joinDate?: string | null;
    verificationCode?: string | null;
  } | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState('');
  const [isExporting, setIsExporting] = useState(false);
  const sheetRef = useRef<HTMLDivElement>(null);

  // Own record first: an employee only ever needs their own letter, so it is
  // ready without them choosing anything.
  useEffect(() => {
    authService
      .getProfile()
      .then((data) => {
        setProfile(data);
        setSubject({
          name: data.name,
          cnic: data.cnic,
          designation: data.designation,
          department: data.department,
          employeeId: data.employmentDetails?.employeeId,
          joinDate: data.employmentDetails?.joinDate,
          verificationCode: data.employmentVerificationCode,
        });
      })
      .catch(() => setError('Could not load your employment record. Please try again.'))
      .finally(() => setIsLoading(false));
  }, []);

  // Admins can issue a letter for anyone.
  useEffect(() => {
    if (!isAdmin) return;
    employeeService
      .getAllUsers()
      .then((list) => setEmployees(list.filter((e) => e.status === 'active')))
      .catch(() => setEmployees([]));
  }, [isAdmin]);

  const pickEmployee = (id: string) => {
    setEmployeeId(id);
    if (!id) {
      if (profile) {
        setSubject({
          name: profile.name,
          cnic: profile.cnic,
          designation: profile.designation,
          department: profile.department,
          employeeId: profile.employmentDetails?.employeeId,
          joinDate: profile.employmentDetails?.joinDate,
          verificationCode: profile.employmentVerificationCode,
        });
      }
      return;
    }
    const match = employees.find((e) => String(e.id) === String(id));
    if (!match) return;
    setSubject({
      name: match.name,
      cnic: match.cnic,
      designation: match.designation,
      department: match.department,
      employeeId: match.employeeId,
      joinDate: match.joinDate,
      verificationCode: match.employmentVerificationCode,
    });
  };

  const handleSavePdf = async () => {
    if (!sheetRef.current || !subject) return;
    setIsExporting(true);
    setError('');
    try {
      // The shared exporter rather than a bespoke html2canvas call: it already
      // handles the scroll-clipping that silently truncated other documents,
      // and one code path means one place to fix the next surprise.
      const { exportElementToPdf } = await import('../../utils/pdfExport');
      // Drop the on-screen card border and radius for the capture.
      sheetRef.current.classList.add('is-exporting');
      await exportElementToPdf(
        sheetRef.current,
        `Employment-Letter-${subject.name.replace(/[^a-z0-9]+/gi, '-')}.pdf`,
      );
    } catch (err) {
      // Logged, not just swallowed: "try Print instead" told the user nothing
      // and left nothing to diagnose from.
      console.error('Employment letter PDF export failed:', err);
      setError('Could not generate the PDF. Please try Print instead.');
    } finally {
      sheetRef.current?.classList.remove('is-exporting');
      setIsExporting(false);
    }
  };

  const joined = longDate(subject?.joinDate);
  const issuedOn = longDate(new Date().toISOString());
  const reference = subject?.employeeId ? `QGT/HR/${subject.employeeId}` : 'QGT/HR';
  // The QR and the printed address both carry the verification code, not the
  // reference: the reference is the employee ID reformatted, so anyone could
  // forge one, while the code is a server-issued secret the endpoint checks.
  const verifyUrl = subject?.verificationCode
    ? `${window.location.origin}${ROUTES.VERIFY_EMPLOYMENT}?code=${encodeURIComponent(subject.verificationCode)}`
    : `${window.location.origin}${ROUTES.VERIFY_EMPLOYMENT}`;

  return (
    <div className="el-page">
      <div className="el-toolbar no-print">
        <div>
          <h1>Employment Letter</h1>
          <p>An employment verification letter for banks, embassies and landlords.</p>
        </div>
        <div className="el-actions">
          {isAdmin && (
            <label className="el-control">
              <span>Employee</span>
              <select value={employeeId} onChange={(e) => pickEmployee(e.target.value)}>
                <option value="">Myself</option>
                {employees.map((e) => (
                  <option key={e.id} value={e.id}>{e.name}</option>
                ))}
              </select>
            </label>
          )}
          <button className="el-btn el-btn-ghost" onClick={() => window.print()} disabled={!subject}>
            <FiPrinter size={16} /> Print
          </button>
          <button className="el-btn el-btn-primary" onClick={handleSavePdf} disabled={!subject || isExporting}>
            <FiDownload size={16} /> {isExporting ? 'Saving…' : 'Save PDF'}
          </button>
        </div>
      </div>

      {isLoading && <div className="el-status no-print">Loading your employment record…</div>}
      {error && <div className="el-status el-error no-print">{error}</div>}

      {subject && !isLoading && (
        <div className="el-sheet" ref={sheetRef}>
          <header className="el-letterhead">
            <img src={qgtLogo} alt="" className="el-logo" />
            <div>
              <h2>{COMPANY.NAME}</h2>
              <p>{COMPANY.ADDRESS}</p>
            </div>
            <div className="el-ntn">
              <span>NTN</span>
              <strong>{COMPANY.NTN}</strong>
            </div>
          </header>
          <div className="el-accent" />

          <div className="el-meta">
            <span>Ref: {reference}</span>
            <span>Date: {issuedOn}</span>
          </div>

          <h3 className="el-title">To Whom It May Concern</h3>

          <div className="el-body">
            <p>
              This is to certify that <strong>{subject.name}</strong>
              {subject.cnic ? <> , holder of CNIC <strong>{subject.cnic}</strong>,</> : ','}{' '}
              is employed with <strong>{COMPANY.NAME}</strong> as{' '}
              <strong>{subject.designation || 'an employee'}</strong>
              {subject.department ? <> in the {subject.department} department</> : null}
              {joined ? <> with effect from <strong>{joined}</strong></> : null}
              {subject.employeeId ? <>, under employee ID <strong>{subject.employeeId}</strong></> : null}
              .
            </p>

            <p>
              {subject.name.split(' ')[0]} remains in the active employment of the company as at the
              date of this letter, and is a member of our staff in good standing.
            </p>

            <p>
              This letter has been issued at the request of the employee for the purpose of
              verification of employment, and carries no financial obligation or guarantee on the
              part of the company. It states employment particulars only; details of remuneration
              are not disclosed in this letter.
            </p>

            <p>
              Should you require any further confirmation, please contact us at{' '}
              <strong>{COMPANY.EMAIL}</strong>.
            </p>
          </div>

          <div className="el-signoff">
            <p>Issued by,</p>
            <div className="el-signatory">
              <span className="el-signatory-name">{COMPANY.SIGNATORY_NAME}</span>
              <span className="el-signatory-title">{COMPANY.SIGNATORY_TITLE}</span>
              <span className="el-signatory-company">{COMPANY.NAME}</span>
            </div>
          </div>

          <footer className="el-footer">
            <img
              src={letterQr(verifyUrl)}
              alt="QR code linking to the issuing portal"
              className="el-qr"
            />
            <div>
              <strong>System-generated letter</strong>
              <span>
                Issued electronically by {COMPANY.NAME}. It requires no signature or stamp.
                Scan the code, or visit the address below, to confirm these particulars.
              </span>
              <span>{verifyUrl}</span>
              <span>{COMPANY.ADDRESS} · {COMPANY.EMAIL}</span>
            </div>
          </footer>
        </div>
      )}

      {!subject && !isLoading && !error && (
        <div className="el-status el-placeholder no-print">
          <FiFileText size={18} /> No employment record was found for this account.
        </div>
      )}
    </div>
  );
};

export default EmploymentLetter;
