// Several source elements (the attendance table, the payslip preview) are
// `overflow: auto` with a capped `max-height` for on-screen scrolling.
// html2canvas snapshots the DOM as currently laid out, not what a real
// browser print would reflow to — so without this, it captured only
// whatever fit inside that clipped viewport (confirmed: an attendance PDF
// with "20 records" in its own title showed just the first 8 rows, the rest
// silently gone) and stopped there instead of continuing onto more pages.
// Every clipped ancestor is opened up before capture and restored after.
const relaxScrollClipping = (root: HTMLElement): (() => void) => {
  const restored: { el: HTMLElement; overflow: string; overflowY: string; maxHeight: string }[] = [];
  for (const el of [root, ...Array.from(root.querySelectorAll<HTMLElement>('*'))]) {
    const style = window.getComputedStyle(el);
    const clips = style.overflow === 'auto' || style.overflow === 'scroll'
      || style.overflowY === 'auto' || style.overflowY === 'scroll';
    const capped = style.maxHeight !== 'none' && style.maxHeight !== '';
    if (!clips || !capped) continue;
    restored.push({ el, overflow: el.style.overflow, overflowY: el.style.overflowY, maxHeight: el.style.maxHeight });
    el.style.setProperty('overflow', 'visible', 'important');
    el.style.setProperty('overflow-y', 'visible', 'important');
    el.style.setProperty('max-height', 'none', 'important');
  }
  // Style writes alone don't reflow synchronously — html2canvas, called right
  // after this returns, was reading layout before the browser had actually
  // resized these elements to their new unclipped height, so it still
  // captured the old (clipped) size. Reading a layout property forces the
  // browser to flush the pending reflow immediately.
  void root.offsetHeight;
  return () => {
    restored.forEach(({ el, overflow, overflowY, maxHeight }) => {
      el.style.overflow = overflow;
      el.style.overflowY = overflowY;
      el.style.maxHeight = maxHeight;
    });
  };
};

// Shared "Save PDF" implementation for pages that also offer a separate
// Print button (window.print()). The two must stay genuinely different:
// Print opens the OS print dialog: it is what the user drives themselves
// when they actually want a printout. Save PDF is a one-click download of an
// actual .pdf file, with no dialog in between — see TaxCertificate.tsx's
// "the save pdf should download it as a pdf but in this it just print and
// act same as a print button" fix, which this generalizes.
//
// jsPDF/html2canvas-pro are dynamically imported (not top-level) so pages
// that never call this don't pay for it in their main bundle.
export async function exportElementToPdf(
  element: HTMLElement,
  filename: string,
  options: { ignoreElements?: (el: Element) => boolean } = {},
): Promise<void> {
  const [{ default: jsPDF }, { default: html2canvas }] = await Promise.all([
    import('jspdf'),
    import('html2canvas-pro'),
  ]);

  const restoreScrollClipping = relaxScrollClipping(element);
  let canvas: HTMLCanvasElement;
  try {
    canvas = await html2canvas(element, {
      scale: 2,
      backgroundColor: '#ffffff',
      useCORS: true,
      ignoreElements: options.ignoreElements,
    });
  } finally {
    restoreScrollClipping();
  }

  const pdf = new jsPDF('p', 'mm', 'a4');
  const pageWidthMm = pdf.internal.pageSize.getWidth();
  const pageHeightMm = pdf.internal.pageSize.getHeight();
  const marginMm = 8;
  const contentWidthMm = pageWidthMm - marginMm * 2;
  const contentHeightMm = pageHeightMm - marginMm * 2;

  // Content taller than one page (a long attendance table, a multi-month
  // payslip history) is sliced into page-height chunks and stacked as
  // separate PDF pages, rather than squeezed to fit one page — squeezing a
  // 30-row table onto one sheet would shrink the text past readable.
  const pxPerMm = canvas.width / contentWidthMm;
  const pageHeightPx = Math.max(1, Math.floor(contentHeightMm * pxPerMm));

  let renderedHeightPx = 0;
  let pageIndex = 0;
  while (renderedHeightPx < canvas.height) {
    const sliceHeightPx = Math.min(pageHeightPx, canvas.height - renderedHeightPx);
    const sliceCanvas = document.createElement('canvas');
    sliceCanvas.width = canvas.width;
    sliceCanvas.height = sliceHeightPx;
    const ctx = sliceCanvas.getContext('2d');
    if (!ctx) break;
    ctx.drawImage(canvas, 0, renderedHeightPx, canvas.width, sliceHeightPx, 0, 0, canvas.width, sliceHeightPx);

    // JPEG, not PNG: cut earlier TaxCertificate PDFs from ~9.7MB to ~240KB
    // at scale 2 with no visible quality loss on a document like this.
    const imageData = sliceCanvas.toDataURL('image/jpeg', 0.92);
    const sliceHeightMm = sliceHeightPx / pxPerMm;

    if (pageIndex > 0) pdf.addPage();
    pdf.addImage(imageData, 'JPEG', marginMm, marginMm, contentWidthMm, sliceHeightMm);

    renderedHeightPx += sliceHeightPx;
    pageIndex += 1;
  }

  pdf.save(filename);
}
