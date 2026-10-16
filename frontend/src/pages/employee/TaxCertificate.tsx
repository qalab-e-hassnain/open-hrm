import { useRef, useState, useEffect } from 'react';
import { FiPrinter, FiDownload, FiFileText } from 'react-icons/fi';
import { taxService, type TaxCertificate as TaxCert, type MonthRange } from '../../services/taxService';
import { employeeService } from '../../services/employeeService';
import { useAuthStore } from '../../store/authStore';
import { type Employee } from '../../types';
import qgtLogo from '../../assets/logo.webp';
import './TaxCertificate.css';

const today = new Date();

// Pakistan's tax year runs 01 July to 30 June and is named after the calendar
// year it ends in: Tax Year 2026 is 01 July 2025 - 30 June 2026. A certificate
// is always issued for one whole tax year, so the period is chosen as a year
// rather than as two arbitrary dates.
const TAX_YEAR_START_MONTH = 7;
const TAX_YEAR_END_MONTH = 6;

/** The tax year a date falls in: July onwards belongs to the next one. */
const taxYearOf = (date: Date) =>
  date.getMonth() + 1 >= TAX_YEAR_START_MONTH ? date.getFullYear() + 1 : date.getFullYear();

const CURRENT_TAX_YEAR = taxYearOf(today);
const EARLIEST_TAX_YEAR = 2023;

/** Newest first — the one people want is nearly always the latest. */
const TAX_YEARS = Array.from(
  { length: CURRENT_TAX_YEAR - EARLIEST_TAX_YEAR + 1 },
  (_, i) => CURRENT_TAX_YEAR - i,
);

const taxYearLabel = (taxYear: number) =>
  `Tax Year ${taxYear} (01 July ${taxYear - 1} - 30 June ${taxYear})`;

const ONES = ['', 'One', 'Two', 'Three', 'Four', 'Five', 'Six', 'Seven', 'Eight', 'Nine', 'Ten',
  'Eleven', 'Twelve', 'Thirteen', 'Fourteen', 'Fifteen', 'Sixteen', 'Seventeen', 'Eighteen', 'Nineteen'];
const TENS = ['', '', 'Twenty', 'Thirty', 'Forty', 'Fifty', 'Sixty', 'Seventy', 'Eighty', 'Ninety'];

/** Under 100. */
const twoDigits = (n: number): string =>
  n < 20 ? ONES[n] : `${TENS[Math.floor(n / 10)]}${n % 10 ? `-${ONES[n % 10]}` : ''}`;

/** Under 1000. */
const threeDigits = (n: number): string => {
  const hundreds = Math.floor(n / 100);
  const rest = n % 100;
  return [hundreds ? `${ONES[hundreds]} Hundred` : '', rest ? twoDigits(rest) : ''].filter(Boolean).join(' ');
};

/**
 * Amount in words on the South Asian scale (crore / lakh), which is what a
 * Pakistani tax certificate is expected to read. Paisa is dropped: tax is
 * certified in whole rupees.
 */
const amountInWords = (amount: number): string => {
  const rupees = Math.round(amount);
  if (rupees <= 0) return 'Rupees Zero Only';
  const crore = Math.floor(rupees / 10_000_000);
  const lakh = Math.floor((rupees % 10_000_000) / 100_000);
  const thousand = Math.floor((rupees % 100_000) / 1_000);
  const rest = rupees % 1_000;
  const parts = [
    crore ? `${threeDigits(crore)} Crore` : '',
    lakh ? `${twoDigits(lakh)} Lakh` : '',
    thousand ? `${threeDigits(thousand)} Thousand` : '',
    rest ? threeDigits(rest) : '',
  ].filter(Boolean);
  return `Rupees ${parts.join(' ')} Only`;
};

const TaxCertificate = () => {
  const { user } = useAuthStore();
  const isAdmin = user?.role === 'admin';

  const [taxYear, setTaxYear] = useState<number>(CURRENT_TAX_YEAR);
  const [employees, setEmployees] = useState<Employee[]>([]);
  const [employeeId, setEmployeeId] = useState<string>(user?.id || '');
  const [cert, setCert] = useState<TaxCert | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [fieldErrors, setFieldErrors] = useState<{ employee?: string }>({});
  const [isExporting, setIsExporting] = useState(false);
  const sheetRef = useRef<HTMLDivElement>(null);

  // Admin picks any employee; everyone else only sees their own.
  useEffect(() => {
    if (!isAdmin) return;
    employeeService
      .getAllUsers()
      .then((list) => setEmployees(list.filter((e) => e.status === 'active')))
      .catch(() => setEmployees([]));
  }, [isAdmin]);

  // Every way this form can be filled out wrong, checked field by field
  // rather than one bail-on-first-problem generic message — so "Employee"
  // required and "End date can't be before start" don't get stepped on by
  // whichever check happened to run first.
  // The period can no longer be entered wrongly — it is picked from a list of
  // whole tax years — so the employee is the only field left to check.
  const validateGenerate = (): boolean => {
    const next: typeof fieldErrors = {};
    if (isAdmin && !employeeId) next.employee = 'Select an employee.';
    setFieldErrors(next);
    return Object.keys(next).length === 0;
  };

  const handleGenerate = () => {
    if (!validateGenerate()) {
      setCert(null);
      return;
    }
    const range: MonthRange = {
      startYear: taxYear - 1,
      startMonth: TAX_YEAR_START_MONTH,
      endYear: taxYear,
      endMonth: TAX_YEAR_END_MONTH,
    };
    setIsLoading(true);
    setError(null);

    const request =
      isAdmin && employeeId
        ? taxService.getEmployeeTaxCertificate(employeeId, range)
        : taxService.getMyTaxCertificate(range);

    request
      .then((data) => setCert(data))
      .catch((err) => {
        console.error('Failed to load tax certificate:', err);
        setError('Could not load the tax certificate. Please try again.');
        setCert(null);
      })
      .finally(() => setIsLoading(false));
  };

  // Renders the certificate sheet to an image and embeds it in a PDF, then
  // triggers a real file download — distinct from Print, which opens the
  // browser's print dialog and lets the user pick "Save as PDF" themselves.
  const handleSavePdf = async () => {
    if (!sheetRef.current || !cert) return;
    setIsExporting(true);
    setError(null);
    try {
      // Dynamically imported, not a top-level import: jsPDF + html2canvas
      // together add ~250KB to the bundle, and only this one button on this
      // one page ever needs them — every other page loading them upfront for
      // a feature they'll never use isn't worth it.
      const [{ jsPDF }, { default: html2canvas }] = await Promise.all([
        import('jspdf'),
        import('html2canvas-pro'),
      ]);
      const canvas = await html2canvas(sheetRef.current, {
        scale: 2,
        backgroundColor: '#ffffff',
        useCORS: true,
      });
      // JPEG, not PNG: this is a flat, mostly-white document, and PNG's
      // lossless encoding of a full-resolution (scale: 2) capture produced a
      // multi-megabyte file for one page. Quality 0.92 keeps text crisp at a
      // fraction of the size.
      const imgData = canvas.toDataURL('image/jpeg', 0.92);
      const pdf = new jsPDF({ orientation: 'portrait', unit: 'mm', format: 'a4' });
      const pageWidth = pdf.internal.pageSize.getWidth();
      const pageHeight = pdf.internal.pageSize.getHeight();
      const imgWidth = pageWidth;
      const imgHeight = (canvas.height * imgWidth) / canvas.width;
      // The certificate is designed to fit one A4 page (see the print CSS's
      // compact spacing for a full tax year). If an unusually long date
      // range still makes it taller than one page, scale the whole image
      // down to fit rather than silently cropping the bottom off.
      const finalHeight = Math.min(imgHeight, pageHeight);
      const finalWidth = finalHeight === imgHeight ? imgWidth : (canvas.width * finalHeight) / canvas.height;
      pdf.addImage(imgData, 'JPEG', (pageWidth - finalWidth) / 2, 0, finalWidth, finalHeight);
      const employeeName = cert.employee.name.replace(/[^a-z0-9]+/gi, '-');
      const period = cert.period_label.replace(/\s+/g, '');
      pdf.save(`Tax-Certificate-${employeeName}-${period}.pdf`);
    } catch (err) {
      console.error('Failed to generate PDF:', err);
      setError('Could not generate the PDF. Please try Print instead.');
    } finally {
      setIsExporting(false);
    }
  };

  const fmt = (n: number) =>
    new Intl.NumberFormat('en-PK', { maximumFractionDigits: 0 }).format(Math.round(n || 0));

  const hasRows = !!cert && cert.rows.length > 0;

  return (
    <div className="taxc-page">
      {/* Controls — hidden on print */}
      <div className="taxc-toolbar no-print">
        <div className="taxc-toolbar-title">
          <h1>Tax Certificate</h1>
          <p>Choose an employee and a date range, then generate the statement.</p>
        </div>

        <div className="taxc-controls">
          {isAdmin && (
            <label className={`taxc-control${fieldErrors.employee ? ' taxc-control-invalid' : ''}`}>
              <span>Employee</span>
              <select
                value={employeeId}
                onChange={(e) => { setEmployeeId(e.target.value); setFieldErrors((cur) => ({ ...cur, employee: undefined })); }}
              >
                {employees.length === 0 && <option value={user?.id || ''}>Me</option>}
                {employees.map((emp) => (
                  <option key={emp.id} value={emp.id}>
                    {emp.name}
                    {emp.employeeId ? ` (${emp.employeeId})` : ''}
                  </option>
                ))}
              </select>
              {fieldErrors.employee && <small className="taxc-field-error">{fieldErrors.employee}</small>}
            </label>
          )}

          <label className="taxc-control">
            <span>Tax year</span>
            <select value={taxYear} onChange={(e) => setTaxYear(Number(e.target.value))}>
              {TAX_YEARS.map((year) => (
                <option key={year} value={year}>{taxYearLabel(year)}</option>
              ))}
            </select>
          </label>

          <button className="taxc-btn taxc-btn-primary" onClick={handleGenerate} disabled={isLoading}>
            <FiFileText size={16} /> {isLoading ? 'Generating…' : 'Generate'}
          </button>
          <button className="taxc-btn taxc-btn-ghost" onClick={() => window.print()} disabled={!hasRows}>
            <FiPrinter size={16} /> Print
          </button>
          <button
            className="taxc-btn taxc-btn-ghost"
            onClick={handleSavePdf}
            disabled={!hasRows || isExporting}
          >
            <FiDownload size={16} /> {isExporting ? 'Saving…' : 'Save PDF'}
          </button>
        </div>
      </div>

      {isLoading && <div className="taxc-status no-print">Generating certificate…</div>}
      {error && !isLoading && <div className="taxc-status taxc-error no-print">{error}</div>}
      {!cert && !isLoading && !error && (
        <div className="taxc-status taxc-placeholder no-print">
          Select an employee and date range, then click <strong>Generate</strong>.
        </div>
      )}

      {cert && !isLoading && !error && (
        <div className="taxc-sheet" ref={sheetRef}>
          {/* Letterhead */}
          <header className="taxc-letterhead">
            <div className="taxc-brand">
              <img src={qgtLogo} alt={cert.company.name} className="taxc-logo" />
              <div className="taxc-brand-text">
                <h2 className="taxc-company">{cert.company.name}</h2>
                <p className="taxc-company-sub">{cert.company.address}</p>
              </div>
            </div>
            <div className="taxc-regno">
              <span className="taxc-regno-label">Company Reg. No.</span>
              <span className="taxc-regno-value">{cert.company.reg_no}</span>
            </div>
          </header>

          <div className="taxc-accent" />

          {/* Title */}
          <div className="taxc-title-block">
            <h3 className="taxc-title">Certificate of Deduction of Income Tax — Tax Year {taxYear}</h3>
            <p className="taxc-subtitle">
              (Under Section 164 of the Income Tax Ordinance, 2001, read with Rule 42 of the
              Income Tax Rules, 2002)
            </p>
          </div>

          <p className="taxc-certify">
            This is to certify that income tax was deducted at source under Section 149 of the
            Income Tax Ordinance, 2001 from the salary paid to the above-named employee and
            deposited into the Federal Government treasury, as per the month-wise detail and
            annual computation below:
          </p>

          {/* Employee / period details */}
          <section className="taxc-details">
            <div className="taxc-detail-row">
              <span className="taxc-detail-label">Employee Name</span>
              <span className="taxc-detail-value">{cert.employee.name}</span>
              <span className="taxc-detail-label">Period</span>
              <span className="taxc-detail-value taxc-period-value">{cert.period_label}</span>
            </div>
            <div className="taxc-detail-row">
              <span className="taxc-detail-label">CNIC No.</span>
              <span className="taxc-detail-value">{cert.employee.cnic || '—'}</span>
              <span className="taxc-detail-label">Designation</span>
              <span className="taxc-detail-value">{cert.employee.designation || '—'}</span>
            </div>
            <div className="taxc-detail-row">
              <span className="taxc-detail-label">Employee ID</span>
              <span className="taxc-detail-value">{cert.employee.employee_id || '—'}</span>
              <span className="taxc-detail-label">Months Covered</span>
              <span className="taxc-detail-value">{cert.rows.length}</span>
            </div>
          </section>

          {/* Month-wise detail: gross paid and tax deducted, as the
              certificate's own wording promises. The per-month taxable and
              cumulative columns belonged to an internal view, not to a
              document handed to a tax authority. */}
          <table className="taxc-table">
            <thead>
              <tr>
                <th>Month</th>
                <th className="num">Gross Salary Paid ({cert.currency})</th>
                <th className="num">Income Tax Deducted ({cert.currency})</th>
              </tr>
            </thead>
            <tbody>
              {hasRows ? (
                cert.rows.map((r) => (
                  <tr key={`${r.year}-${r.month}`}>
                    <td>{r.month_label}</td>
                    <td className="num">{fmt(r.total_income)}</td>
                    <td className="num">{fmt(r.tax_paid)}</td>
                  </tr>
                ))
              ) : (
                <tr>
                  <td colSpan={3} className="taxc-empty">
                    No processed payroll records for this employee in Tax Year {taxYear}.
                  </td>
                </tr>
              )}
            </tbody>
            {hasRows && (
              <tfoot>
                <tr>
                  <td>Total</td>
                  <td className="num">{fmt(cert.totals.total_income)}</td>
                  <td className="num">{fmt(cert.totals.tax_paid)}</td>
                </tr>
              </tfoot>
            )}
          </table>

          {hasRows && (
            <>
              <h4 className="taxc-section-title">
                Computation of Taxable Income &amp; Tax — Tax Year {taxYear}{' '}
                (01 July {taxYear - 1} — 30 June {taxYear})
              </h4>
              <table className="taxc-table taxc-computation">
                <thead>
                  <tr>
                    <th>Description</th>
                    <th className="num">Amount ({cert.currency})</th>
                  </tr>
                </thead>
                <tbody>
                  <tr>
                    <td>Total Income — Salary paid during the year</td>
                    <td className="num">{fmt(cert.totals.total_income)}</td>
                  </tr>
                  <tr>
                    <td>Less: Income Exempt from Tax (allowances / exempt benefits)</td>
                    <td className="num">
                      {cert.totals.exempt_income ? `(${fmt(cert.totals.exempt_income)})` : '—'}
                    </td>
                  </tr>
                  <tr className="taxc-row-emphasis">
                    <td>Taxable Income</td>
                    <td className="num">{fmt(cert.totals.taxable_income)}</td>
                  </tr>
                  <tr className="taxc-row-emphasis">
                    <td>Tax Deducted at Source u/s 149</td>
                    <td className="num">{fmt(cert.totals.tax_paid)}</td>
                  </tr>
                </tbody>
              </table>

              <p className="taxc-total-words">
                <strong>Total Income Tax Deducted:</strong> {cert.currency}{' '}
                {fmt(cert.totals.tax_paid)}/- ({amountInWords(cert.totals.tax_paid)})
              </p>

              <p className="taxc-legal">
                It is further certified that the above tax has been computed in accordance with
                the rates for salaried individuals specified in Division I, Part I of the First
                Schedule to the Income Tax Ordinance, 2001, as amended by the applicable Finance
                Act for Tax Year {taxYear}.
              </p>
            </>
          )}

          {/* No signature or stamp — issued by the system, and verifiable from
              the portal, consistent with the payslip. */}
          <footer className="taxc-footer">
            <span>This is a system-generated certificate. All amounts are in {cert.currency}.</span>
            <span>Generated on {new Date().toLocaleDateString('en-GB')}</span>
          </footer>
        </div>
      )}
    </div>
  );
};

export default TaxCertificate;
