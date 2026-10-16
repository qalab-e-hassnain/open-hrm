import { useCallback, useEffect, useRef, useState } from 'react';

/**
 * A camera that keeps reading QR labels until it is stopped.
 *
 * The scan screen reads one label and stops; a stock count walks a rack and
 * reads dozens in a row, so this one keeps going and only ignores the label it
 * just read for a moment — long enough not to post the same tag ten times
 * while it is still in frame, short enough to rescan on purpose.
 */
export const useQrCamera = (onDecode: (text: string) => void, repeatMs = 2500) => {
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const frameRef = useRef<number | null>(null);
  const lastRef = useRef<{ text: string; at: number } | null>(null);
  const handlerRef = useRef(onDecode);
  const [live, setLive] = useState(false);
  const [cameraError, setCameraError] = useState('');

  useEffect(() => { handlerRef.current = onDecode; }, [onDecode]);

  const stop = useCallback(() => {
    if (frameRef.current !== null) { cancelAnimationFrame(frameRef.current); frameRef.current = null; }
    streamRef.current?.getTracks().forEach((track) => track.stop());
    streamRef.current = null;
    setLive(false);
  }, []);

  const start = useCallback(async () => {
    setCameraError('');
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        video: { facingMode: { ideal: 'environment' } },
      });
      streamRef.current = stream;
      setLive(true);
      const video = videoRef.current;
      if (!video) return;
      video.srcObject = stream;
      await video.play();

      // Fetched only by screens that decode, not by every page load.
      const mod = await import('jsqr');
      const jsQR = (mod.default ?? (mod as unknown as typeof mod.default));

      const tick = () => {
        if (!streamRef.current) return;
        const canvas = canvasRef.current;
        if (!canvas || video.readyState !== video.HAVE_ENOUGH_DATA) {
          frameRef.current = requestAnimationFrame(tick);
          return;
        }
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
        const text = found?.data?.trim();
        if (text) {
          const now = Date.now();
          const last = lastRef.current;
          if (!last || last.text !== text || now - last.at > repeatMs) {
            lastRef.current = { text, at: now };
            handlerRef.current(text);
          }
        }
        frameRef.current = requestAnimationFrame(tick);
      };
      frameRef.current = requestAnimationFrame(tick);
    } catch (err) {
      setCameraError(
        (err as Error).name === 'NotAllowedError'
          ? 'The camera was blocked. Allow it in the address bar, or type the tag below.'
          : 'No camera available here. Type the tag below instead.',
      );
      stop();
    }
  }, [repeatMs, stop]);

  useEffect(() => stop, [stop]);

  return { videoRef, canvasRef, live, cameraError, start, stop };
};

export default useQrCamera;
