import { useEffect } from 'react';
import qrcode from 'qrcode-generator';
import { FiPrinter, FiX } from 'react-icons/fi';
import type { InventoryItem } from '../../services/inventoryService';
import './inventory.css';

/** Synchronous, so printing never races an image load. */
const qrDataUrl = (value: string) => {
  const qr = qrcode(0, 'M');
  qr.addData(value);
  qr.make();
  return qr.createDataURL(4, 0);
};

/**
 * The sticker that goes on the thing: a QR of the tag and nothing else, the
 * tag in human-readable text beneath it, and the short name. That is all that
 * fits legibly on 25 × 15 mm.
 *
 * Printed at the receiving table, from the create response, before the item
 * leaves the table — a label printed later is a label never printed.
 */
const ItemLabel = ({ item, onClose }: { item: InventoryItem; onClose?: () => void }) => {
  // Enter fires the printer: at the receiving table this is a scanner-gun and
  // keyboard job, not a mouse one.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose?.();
      if (e.key === 'Enter') { e.preventDefault(); window.print(); }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  const shortName = item.name.length > 34 ? `${item.name.slice(0, 33)}…` : item.name;

  return (
    <div className="inv-overlay inv-label-overlay">
      <div className="inv-label-panel">
        <header className="inv-label-head">
          <h3>Label for {item.tag}</h3>
          {onClose && <button type="button" className="inv-icon-btn" onClick={onClose} aria-label="Close"><FiX /></button>}
        </header>

        {/* The only part that reaches the printer — see @media print. */}
        <div className="inv-label" id="inv-label-print">
          <img src={qrDataUrl(item.tag)} alt="" />
          <div className="inv-label-text">
            <strong>{item.tag}</strong>
            <span>{shortName}</span>
          </div>
        </div>

        <p className="inv-label-hint">
          25 × 15 mm. Print it now, on the receiving table, and stick it on before the item moves.
        </p>

        <footer className="inv-label-foot">
          {onClose && <button type="button" className="inv-btn" onClick={onClose}>Done</button>}
          <button type="button" className="inv-btn inv-btn-primary" onClick={() => window.print()}>
            <FiPrinter /> Print label
          </button>
        </footer>
      </div>
    </div>
  );
};

export default ItemLabel;
