import { FiBookOpen, FiDownload, FiExternalLink } from 'react-icons/fi';
import './Handbook.css';
import { BRAND_NAME } from '../../config/constants';

/** Both served straight from /public, so neither is anything the bundle
 *  has to carry. The web version is the one people read; the PDF is for
 *  printing and for keeping a copy. */
// Deliberately not index.html: a static /handbook/index.html makes Azure
// resolve the /handbook route to the file and the React page never renders
// at all. Naming it anything else leaves the route to the app.
const HANDBOOK_WEB = '/handbook/book.html';
const HANDBOOK_PDF = '/handbook/employee-handbook.pdf';

const CONTENTS = [
  ['1', 'The company', 'What we do, where we are, who leads it'],
  ['2', 'How we are organised', 'Hierarchy, roles, who approves what'],
  ['3', 'Your first months', 'Probation, and how it ends'],
  ['4', 'Your working day', 'Hours, shifts, attendance, overtime'],
  ['5', 'Leave', 'Every type, entitlement and notice period'],
  ['6', 'Pay', 'Payslips, tax, advances and the payroll cycle'],
  ['7', 'How you are reviewed', 'Reviews, scoring, and employee of the month'],
  ['8', 'Documents and your employment', 'Contracts, badge, equipment, joining and leaving'],
  ['9', 'Using the portal', 'Step by step, screen by screen'],
  ['10', 'Quick reference', 'Numbers, contacts and links'],
];

const Handbook = () => (
  <div className="hb-page">
    <header className="hb-head">
      <div>
        <h1><FiBookOpen /> Employee handbook</h1>
        <p>
          Everything about working here in one place — what you are entitled to,
          how each process actually runs, and where to do it in the portal.
        </p>
      </div>
      <div className="hb-actions">
        <a className="hb-btn" href={HANDBOOK_WEB} target="_blank" rel="noreferrer">
          <FiExternalLink /> Open in a new tab
        </a>
        <a className="hb-btn hb-btn-primary" href={HANDBOOK_PDF} download>
          <FiDownload /> Download PDF
        </a>
      </div>
    </header>

    <div className="hb-body">
      {/* The handbook as a web page rather than a PDF in a box. Embedding
          the PDF relied on the browser having a viewer, which is not
          something to depend on — and on a phone it simply refused. This is
          ordinary HTML, so it reads, scrolls and searches everywhere. */}
      <iframe
        className="hb-viewer"
        src={HANDBOOK_WEB}
        title={`${BRAND_NAME} employee handbook`}
        loading="lazy"
      />

      <aside className="hb-toc">
        <h2>What is in it</h2>
        <ol>
          {CONTENTS.map(([n, title, desc]) => (
            <li key={n}><strong>{title}</strong><small>{desc}</small></li>
          ))}
        </ol>
        <p className="hb-note">
          Every figure in it is the one the portal actually enforces. If the
          book and the system ever disagree, the system is what will happen to
          your request — and the book is what needs correcting. Tell HR.
        </p>
      </aside>
    </div>
  </div>
);

export default Handbook;
