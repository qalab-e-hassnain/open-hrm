import Card from '../../components/common/Card';
import qgtLogo from '../../assets/logo.webp';
import './terms.css';
import { COMPANY } from '../../config/constants';

const Terms = () => {
  const items = Array.from({ length: 12 }).map((_, i) =>
    `Lorem ipsum dolor sit amet, consectetur adipiscing elit. Sed do eiusmod tempor incididunt ut labore et dolore magna aliqua. (${i + 1})`
  );

  return (
    <div className="terms-page-container">
      <Card className="terms-card card-compact">
        <div className="terms-header">
          <img src={qgtLogo} alt={`${COMPANY.SHORT_NAME} logo`} className="terms-logo" />
          <h2 className="terms-title">Terms & Policy</h2>
        </div>

        <div className="terms-content" role="region" aria-label="Terms and policies">
          <ul>
            {items.map((text, idx) => (
              <li key={idx}>{text}</li>
            ))}
          </ul>
        </div>
      </Card>
    </div>
  );
};

export default Terms;
