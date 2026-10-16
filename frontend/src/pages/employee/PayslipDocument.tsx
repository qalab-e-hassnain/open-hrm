import qrcode from 'qrcode-generator';
import qgtLogo from '../../assets/logo.webp';
import { type PayslipDocument as PayslipDoc, type PayslipLine } from '../../services/payslipService';

const STATUS_LABEL: Record<string, string> = {
  draft: 'Draft',
  processing: 'Processing',
  processed: 'Finalized',
  paid: 'Paid',
  corrected: 'Superseded',
  cancelled: 'Cancelled',
};

const formatDate = (value?: string | null) =>
  value ? new Date(value).toLocaleDateString('en-GB', { day: '2-digit', month: 'short', year: 'numeric' }) : '—';

/**
 * The payslip as a document — this is what the print stylesheet lays out on
 * A4, so what the employee sees on screen is what comes out of the printer.
 * It holds no application controls of its own by design.
 */
/**
 * Verification QR as a data URL, generated synchronously.
 *
 * Deliberately not an async QR library: Save PDF captures this DOM with
 * html2canvas, and an image that is still loading when the capture runs is
 * simply absent from the file. A data URL produced during render is always
 * there. Error-correction level M survives the ink spread of a printed page.
 */
const verificationQr = (url: string): string => {
  const qr = qrcode(0, 'M');
  qr.addData(url);
  qr.make();
  return qr.createDataURL(4, 0);
};

const PayslipDocument = ({
  doc,
  money,
}: {
  doc: PayslipDoc;
  money: (amount: number) => string;
}) => {
  const isDraft = doc.status === 'draft';
  const isVoid = doc.status === 'cancelled' || doc.status === 'corrected';

  // The two ledgers rarely match in length (draft rows, exempt lines, etc.)
  // — this table shows them side by side in one grid, so the shorter side
  // pads with an empty cell rather than the table falling out of alignment.
  const rowCount = Math.max(doc.earnings.length, doc.deductions.length);
  const rows: Array<{ earn?: PayslipLine; deduct?: PayslipLine }> = Array.from(
    { length: rowCount },
    (_, i) => ({ earn: doc.earnings[i], deduct: doc.deductions[i] }),
  );

  return (
    <article className="ps-doc" aria-label={`Payslip for ${doc.period_label}`}>
      {/* A draft or superseded document must never be mistaken for the real
          one, including on paper — hence a watermark rather than only a badge. */}
      {(isDraft || isVoid) && (
        <div className="ps-doc-watermark" aria-hidden="true">
          {isDraft ? 'DRAFT' : STATUS_LABEL[doc.status].toUpperCase()}
        </div>
      )}

      <div className="ps-doc-band">
        <img src={qgtLogo} alt="" className="ps-doc-band-logo" />
        <div className="ps-doc-band-stripe" aria-hidden="true" />
      </div>

      {/* Everything but the band and the address stripe below gets the
          card's padding — those two are meant to bleed edge-to-edge, which
          negative margins on `.ps-doc` itself would technically also
          achieve, but that content would then sit outside `.ps-doc`'s own
          layout box. `.ps-doc` already has to stay a definite, self-contained
          box (see its `aspect-ratio` comment above): Save PDF and this
          preview's own screenshot tooling both capture exactly that box, so
          anything rendered outside it via negative margin is silently
          dropped from both — hence a genuinely full-width inner wrapper
          instead. */}
      <div className="ps-doc-body">

      <div className="ps-doc-companyblock">
        <strong>{doc.company.name}</strong>
        <span>Reg. No: {doc.company.reg_no}</span>
        <span>{doc.company.address}</span>
      </div>

      <div className="ps-doc-statusrow">
        <span>
          {doc.period_label}
          {doc.payslip_no && ` · No. ${doc.payslip_no}`}
          {doc.version > 1 && ` · Version ${doc.version}`}
        </span>
        <em className={`ps-doc-status ps-status-${doc.status}`}>{STATUS_LABEL[doc.status]}</em>
      </div>

      <section className="ps-doc-idblock">
        <dl>
          <div><dt>Employee name</dt><dd>{doc.employee.name}</dd></div>
          <div><dt>Employee ID</dt><dd>{doc.employee.employee_id || '—'}</dd></div>
          <div><dt>CNIC</dt><dd>{doc.employee.cnic || '—'}</dd></div>
          <div><dt>Bank account</dt><dd>{doc.employee.bank_account_masked || '—'}</dd></div>
        </dl>
        {/* Payment date and method are gone: neither is recorded anywhere, so
            both printed as a dash on every payslip ever issued — a field that
            is always empty tells the reader less than no field at all. The
            remaining eight are split evenly so neither column runs long. */}
        <dl>
          <div><dt>Pay period</dt><dd>{doc.period_label}</dd></div>
          <div><dt>Department</dt><dd>{doc.employee.department || '—'}</dd></div>
          <div><dt>Designation</dt><dd>{doc.employee.designation || '—'}</dd></div>
          <div><dt>Date of joining</dt><dd>{formatDate(doc.employee.date_of_joining)}</dd></div>
        </dl>
      </section>

      <table className="ps-doc-table">
        <thead>
          <tr>
            <th colSpan={2} scope="colgroup">Earnings</th>
            <th colSpan={2} scope="colgroup">Deductions</th>
          </tr>
          <tr>
            <th scope="col">Description</th>
            <th scope="col">Amount ({doc.currency})</th>
            <th scope="col">Description</th>
            <th scope="col">Amount ({doc.currency})</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row, index) => (
            // eslint-disable-next-line react/no-array-index-key -- a stable
            // per-row key would need the two ledgers merged pairwise anyway
            <tr key={index}>
              {row.earn ? (
                <>
                  <th scope="row">{row.earn.label}{row.earn.note ? <small>{row.earn.note}</small> : null}</th>
                  <td>{money(row.earn.amount)}</td>
                </>
              ) : <><td /><td /></>}
              {row.deduct ? (
                <>
                  <th scope="row">{row.deduct.label}{row.deduct.note ? <small>{row.deduct.note}</small> : null}</th>
                  <td>{money(row.deduct.amount)}</td>
                </>
              ) : <><td /><td /></>}
            </tr>
          ))}
        </tbody>
        <tfoot>
          <tr>
            <th scope="row">Total Earnings</th>
            <td>{money(doc.gross_earnings)}</td>
            <th scope="row">Total Deductions</th>
            <td>{money(doc.total_deductions)}</td>
          </tr>
          <tr className="ps-doc-netrow">
            <td colSpan={2} />
            <th scope="row">Net Pay</th>
            <td>{doc.currency} {money(doc.net_salary)}</td>
          </tr>
        </tfoot>
      </table>

      {/* The correction reason is not printed. Re-finalizing a reopened run
          writes it automatically ("Regenerated from payroll run 2026-08 after
          reopen"), which describes internal payroll mechanics rather than
          anything the employee can act on. It is still stored, and still
          returned by the payslip and version endpoints for audit. */}
      {doc.cancellation_reason && (
        <p className="ps-doc-note">Reason for cancellation: {doc.cancellation_reason}</p>
      )}

      {/* No signature or stamp: this is issued by the system, and its
          authenticity is established by the verification code below rather
          than by anyone signing it. */}
      <p className="ps-doc-sysnote">
        This is a system-generated payslip. It requires no signature or stamp, and its
        authenticity can be confirmed from the portal using the verification code below.
      </p>

      <footer className="ps-doc-addrbar-items">
        <div className="ps-doc-addrbar-item">
          <strong>Registered office</strong>
          <span>{doc.company.address}</span>
        </div>
        {doc.verification_code && (
          <div className="ps-doc-addrbar-item ps-doc-verify">
            <img
              src={verificationQr(`${window.location.origin}/verify-payslip/${doc.verification_code}`)}
              alt={`QR code linking to the verification page for payslip code ${doc.verification_code}`}
              className="ps-doc-qr"
            />
            <div>
              <strong>Scan to verify</strong>
              <span>{doc.verification_code}</span>
              <span>{window.location.origin}/verify-payslip/{doc.verification_code}</span>
            </div>
          </div>
        )}
        {doc.generated_at && (
          <div className="ps-doc-addrbar-item">
            <strong>Generated</strong>
            <span>{formatDate(doc.generated_at)}</span>
          </div>
        )}
      </footer>
      </div>

      <div className="ps-doc-addrbar-stripe" aria-hidden="true" />
    </article>
  );
};

export default PayslipDocument;
