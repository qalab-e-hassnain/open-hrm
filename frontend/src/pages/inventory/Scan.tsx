import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react';
import { Link } from 'react-router-dom';
import {
  FiAlertTriangle, FiCamera, FiCheckCircle, FiCornerDownLeft, FiExternalLink,
  FiMapPin, FiPlus, FiRotateCcw, FiUser,
} from 'react-icons/fi';
import { ROUTES } from '../../config/constants';
import { useAuthStore } from '../../store/authStore';
import { isLabCustodian } from '../../utils/permissions';
import {
  inventoryService, whereIs, lifecycleLabel, isOffice, type ScanResult,
} from '../../services/inventoryService';
import { StatusChip, ConditionChip, OverdueChip } from '../../components/inventory/StatusChip';
import ItemActions from '../../components/inventory/ItemActions';
import './Scan.css';

/**
 * The screen the whole register lives or dies on.
 *
 * Budget: one GET, one POST, no navigation. Scanning a label has to take
 * seconds — if logging something takes thirty seconds of typing, people walk
 * off with the board instead and the register is dead inside a month.
 *
 * Everything rendered below comes out of the single `by-tag` response: the
 * item, who has it, where it is, and the actions this caller may take on it
 * in its current state. No second call resolves the holder or the location;
 * removing that round trip is the entire reason the endpoint exists.
 */
const Scan = () => {
  const user = useAuthStore((s) => s.user);
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const frameRef = useRef<number | null>(null);

  const [scanning, setScanning] = useState(false);
  const [cameraError, setCameraError] = useState('');
  const [manualTag, setManualTag] = useState('');
  const [result, setResult] = useState<ScanResult | null>(null);
  const [missTag, setMissTag] = useState('');
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [busy, setBusy] = useState(false);

  const stopCamera = useCallback(() => {
    if (frameRef.current !== null) { cancelAnimationFrame(frameRef.current); frameRef.current = null; }
    streamRef.current?.getTracks().forEach((track) => track.stop());
    streamRef.current = null;
    setScanning(false);
  }, []);

  /** One GET. Whatever comes back is the whole screen. */
  const lookup = useCallback(async (tag: string) => {
    const trimmed = tag.trim();
    if (!trimmed || busy) return;
    stopCamera();
    setBusy(true);
    setError('');
    setNotice('');
    setMissTag('');
    try {
      setResult(await inventoryService.byTag(trimmed));
    } catch (err) {
      setResult(null);
      // A tag with nothing behind it is the one error worth handling specially:
      // either the label is scuffed and the tag was misread, or the thing in
      // your hand has never been registered.
      const message = (err as Error).message || 'That tag could not be looked up.';
      setMissTag(trimmed);
      setError(message);
    } finally {
      setBusy(false);
    }
  }, [busy, stopCamera]);

  const startCamera = useCallback(async () => {
    setCameraError('');
    setError('');
    setResult(null);
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        video: { facingMode: { ideal: 'environment' } },
      });
      streamRef.current = stream;
      setScanning(true);
      const video = videoRef.current;
      if (!video) return;
      video.srcObject = stream;
      await video.play();

      // Loaded here rather than at the top of the file so the decoder is only
      // fetched by the one screen that decodes, not by every page load.
      const mod = await import('jsqr');
      const jsQR = (mod.default ?? (mod as unknown as typeof mod.default));

      const tick = () => {
        const canvas = canvasRef.current;
        if (!video || !canvas || video.readyState !== video.HAVE_ENOUGH_DATA) {
          frameRef.current = requestAnimationFrame(tick);
          return;
        }
        // Decode at a fixed small width: a QR at arm's length reads fine at
        // 480px and the loop stays cheap enough to run every frame on a phone.
        const width = 480;
        const height = Math.round((video.videoHeight / video.videoWidth) * width) || 360;
        canvas.width = width;
        canvas.height = height;
        const ctx = canvas.getContext('2d', { willReadFrequently: true });
        if (!ctx) return;
        ctx.drawImage(video, 0, 0, width, height);
        const found = jsQR(ctx.getImageData(0, 0, width, height).data, width, height, {
          inversionAttempts: 'dontInvert',
        });
        if (found?.data) {
          // The QR encodes the tag string and nothing else.
          void lookup(found.data);
          return;
        }
        frameRef.current = requestAnimationFrame(tick);
      };
      frameRef.current = requestAnimationFrame(tick);
    } catch (err) {
      // No camera, no permission, or an insecure origin. Not fatal — some
      // labels are scuffed anyway, which is why manual entry always shows.
      setCameraError(
        (err as Error).name === 'NotAllowedError'
          ? 'The camera was blocked. Allow it in the address bar, or type the tag below.'
          : 'No camera available here. Type the tag below instead.',
      );
      setScanning(false);
    }
  }, [lookup]);

  useEffect(() => stopCamera, [stopCamera]);

  const onManualSubmit = (event: FormEvent) => {
    event.preventDefault();
    void lookup(manualTag);
    setManualTag('');
  };

  /** One POST, then straight back to a fresh read so the buttons stay honest. */
  const afterAction = (message: string) => {
    setNotice(message);
    const tag = result?.item.tag;
    if (tag) void inventoryService.byTag(tag).then(setResult).catch(() => { /* keep what is on screen */ });
  };

  const item = result?.item;

  return (
    <div className="scan-page">
      <header className="scan-head">
        <h1>Scan</h1>
        <p>Point the camera at the label. One scan, one tap, done.</p>
      </header>

      {notice && <div className="scan-alert scan-alert-ok"><FiCheckCircle /> {notice}</div>}

      {!result && (
        <div className="scan-stage">
          <div className={`scan-viewport${scanning ? ' is-live' : ''}`}>
            <video ref={videoRef} muted playsInline />
            <canvas ref={canvasRef} hidden />
            {!scanning && (
              <button type="button" className="scan-start" onClick={() => void startCamera()}>
                <FiCamera size={26} /> Start the camera
              </button>
            )}
            {scanning && <span className="scan-reticle" aria-hidden="true" />}
          </div>

          {cameraError && <p className="scan-camera-note"><FiAlertTriangle /> {cameraError}</p>}

          {/* Always on screen, not hidden behind a "having trouble?" link:
              scuffed labels are normal and this is the fallback that works. */}
          <form className="scan-manual" onSubmit={onManualSubmit}>
            <input
              value={manualTag}
              onChange={(e) => setManualTag(e.target.value)}
              placeholder="Or type the tag — QGT-EQ-0042"
              autoCapitalize="characters"
              spellCheck={false}
              aria-label="Tag"
            />
            <button type="submit" className="inv-btn inv-btn-primary" disabled={busy || !manualTag.trim()}>
              <FiCornerDownLeft /> Look up
            </button>
          </form>

          {error && (
            <div className="scan-alert scan-alert-error">
              <FiAlertTriangle />
              <div>
                <p>{error}</p>
                {missTag && isLabCustodian(user) && (
                  <Link className="scan-register" to={`${ROUTES.INVENTORY_RECEIVE}?tag=${encodeURIComponent(missTag)}`}>
                    <FiPlus /> Register this item
                  </Link>
                )}
              </div>
            </div>
          )}
        </div>
      )}

      {result && item && (
        <article className="scan-result">
          <header>
            <div>
              <code className="scan-tag">{item.tag}</code>
              <h2>{item.name}</h2>
              <p className="scan-sub">
                {item.category_name}
                {item.serial_number ? ` · ${item.serial_number}` : ''}
              </p>
            </div>
            <div className="scan-chips">
              <StatusChip status={item.status} />
              <ConditionChip condition={item.condition} />
              <OverdueChip item={item} />
              {item.lifecycle_state && (
                <span className="inv-chip inv-chip-info">{lifecycleLabel(item.lifecycle_state)}</span>
              )}
            </div>
          </header>

          <p className="scan-where">
            {item.holder_name ? <FiUser /> : <FiMapPin />}
            {result.held_by_me ? 'You have this one' : whereIs(item)}
            {item.hold_due_date && !result.held_by_me && !isOffice(item) ? ` · due back ${item.hold_due_date}` : ''}
          </p>

          {item.tracking_type === 'quantity' && (
            <p className="scan-qty">
              <strong>{item.qty ?? 0}</strong> in the bin
              {typeof item.min_qty === 'number' ? ` · reorder at ${item.min_qty}` : ''}
            </p>
          )}

          <ItemActions
            item={item}
            actions={result.actions}
            heldByMe={result.held_by_me}
            layout="scan"
            onDone={(_updated, message) => afterAction(message)}
            onError={setError}
          />

          {error && <p className="scan-alert scan-alert-error"><FiAlertTriangle /> {error}</p>}

          <footer className="scan-foot">
            <button type="button" className="inv-btn" onClick={() => { setResult(null); setNotice(''); void startCamera(); }}>
              <FiRotateCcw /> Scan another
            </button>
            <Link className="inv-btn" to={ROUTES.INVENTORY_ITEM(item.id)}>
              <FiExternalLink /> Full history
            </Link>
          </footer>
        </article>
      )}
    </div>
  );
};

export default Scan;
