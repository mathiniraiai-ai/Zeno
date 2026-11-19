#!/usr/bin/env node
/* Zeno subtitles: render caption PNGs (with 3-subframe active-word karaoke pop & cropped horizontal band) on node-canvas.
 * Usage: node render_cues.js <job.json>
 * job.json: {"width":1080,"height":1920,"fps":30,"styleId":4,"outDir":"/abs/dir","positionY":72,"cues":[...]}
 * Prints one JSON line: {"style":...,"bandY0":...,"bandHeight":...,"width":...,"height":...,"fps":...,"frames":[...]}
 */
const fs = require('fs');
const path = require('path');
const { registerFont } = require('canvas');

const here = __dirname;
const fontDefs = JSON.parse(fs.readFileSync(path.join(here, 'fonts.json'), 'utf8'));

/**
 * Return true if the SFNT table directory contains a variable-font `fvar` table.
 * Variable TTFs (e.g., Anek*-Bold, Montserrat-400, NotoSansGurmukhi-Bold, Baloo*-Bold)
 * misreport static weights in Cairo/Pango on macOS and must be skipped.
 */
function isVariableFont(buf) {
  if (!buf || buf.length < 12) return false;
  const numTables = buf.readUInt16BE(4);
  for (let i = 0; i < numTables; i++) {
    const off = 12 + i * 16;
    if (off + 4 > buf.length) break;
    if (buf.toString('ascii', off, off + 4) === 'fvar') return true;
  }
  return false;
}

// Register each static TTF path at most ONCE (no variable `fvar` fonts and no
// duplicate-path weight aliasing, which clobbers macOS CoreText weight matching).
// Register canonical weights (400/700/800/900) BEFORE 600/500 so SemiBold never
// shadows Bold (700) in macOS CoreText/Pango (e.g. Hind, Mukta, Barlow).
const WEIGHT_REG_ORDER = { 400: 0, 700: 1, 800: 2, 900: 3, 600: 4, 500: 5, 300: 6 };
const orderedFontDefs = [...fontDefs].sort(
  (a, b) => (WEIGHT_REG_ORDER[Number(a.weight)] ?? 9) - (WEIGHT_REG_ORDER[Number(b.weight)] ?? 9)
);
const registeredPaths = new Set();
const registeredWeightsByFamily = new Map();
const hdrBuf = Buffer.alloc(4096);
for (const f of orderedFontDefs) {
  const fontPath = path.resolve(here, 'fonts', f.file);
  if (registeredPaths.has(fontPath) || !fs.existsSync(fontPath)) continue;
  let fd;
  let isVar = false;
  try {
    fd = fs.openSync(fontPath, 'r');
    const n = fs.readSync(fd, hdrBuf, 0, hdrBuf.length, 0);
    isVar = isVariableFont(hdrBuf.subarray(0, n));
  } catch (e) {
    continue;
  } finally {
    if (fd !== undefined) { try { fs.closeSync(fd); } catch (e) {} }
  }
  if (isVar) continue;
  registeredPaths.add(fontPath);
  try {
    registerFont(fontPath, { family: f.family, weight: String(f.weight) });
    const key = String(f.family || '').trim().toLowerCase();
    if (key) {
      let rec = registeredWeightsByFamily.get(key);
      if (!rec) {
        rec = { family: f.family, weights: new Set() };
        registeredWeightsByFamily.set(key, rec);
      }
      rec.weights.add(Number(f.weight) || 400);
    }
  } catch (e) { /* missing optional font file falls back cleanly */ }
}

function resolveRegisteredFamily(name) {
  const raw = String(name || '').trim();
  if (!raw) return raw;
  const rec = registeredWeightsByFamily.get(raw.toLowerCase());
  return rec ? rec.family : raw;
}

function snapFontWeight(family, requestedWeight) {
  const req = Number(requestedWeight) || 700;
  const rec = registeredWeightsByFamily.get(String(family || '').trim().toLowerCase());
  if (!rec || rec.weights.size === 0) return req;
  if (rec.weights.has(req)) return req;
  const sorted = Array.from(rec.weights).sort((a, b) => a - b);
  let best = sorted[0];
  let bestDiff = Math.abs(best - req);
  for (const w of sorted) {
    const diff = Math.abs(w - req);
    if (diff < bestDiff || (diff === bestDiff && req >= 600 && w > best)) {
      best = w;
      bestDiff = diff;
    }
  }
  return best;
}

const { CaptionRenderer } = require('./caption-renderer.js');

(async () => {
  const job = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
  const styles = JSON.parse(fs.readFileSync(path.join(here, 'styles.json'), 'utf8'));
  const base = styles.find(s => s.id === job.styleId) || styles[0];
  const fullWidth = Number(job.width) || 1080;
  const fullHeight = Number(job.height) || 1920;
  const fps = Math.max(24, Number(job.fps) || 25);
  const defaultPosY = job.positionY !== undefined ? Number(job.positionY) : (base.positionY !== undefined ? Number(base.positionY) : 72);
  const style = { ...base, positionY: defaultPosY };
  if (job.fontFamily && String(job.fontFamily).trim()) {
    const resolvedFam = resolveRegisteredFamily(job.fontFamily);
    style.fontFamily = resolvedFam;
    style.fontWeight = snapFontWeight(resolvedFam, job.fontWeight || style.fontWeight || 700);
  }
  // One fixed caption size for every style (set by Zeno), so captions stay neat and never oversized.
  if (Number(job.fontSize) > 0) style.fontSize = Number(job.fontSize);
  style.singleLine = job.singleLine !== false;   // one row only (owner's rule)

  const cues = Array.isArray(job.cues) ? job.cues : [];
  const cropBand = job.cropBand !== false;

  let bandY0 = 0;
  let bandHeight = fullHeight;
  if (typeof job.bandY0 === 'number' && typeof job.bandHeight === 'number') {
    bandY0 = Math.max(0, Math.min(fullHeight - 2, Math.floor(job.bandY0 / 2) * 2));
    const rem = fullHeight - bandY0;
    const reqH = Math.max(2, Math.ceil(job.bandHeight / 2) * 2);
    bandHeight = Math.min(rem, reqH);
  } else if (cropBand && cues.length > 0 && fullHeight >= 240) {
    let minPosY = defaultPosY;
    let maxPosY = defaultPosY;
    for (const c of cues) {
      const cy = (c && c.style && c.style.positionY !== undefined) ? Number(c.style.positionY) : defaultPosY;
      if (Number.isFinite(cy)) {
        if (cy < minPosY) minPosY = cy;
        if (cy > maxPosY) maxPosY = cy;
      }
    }
    const padPx = Math.max(96, Math.ceil(fullHeight * 0.19));
    const rawTop = Math.max(0, Math.floor((minPosY / 100) * fullHeight - padPx));
    bandY0 = rawTop - (rawTop % 2);
    const rawBot = Math.min(fullHeight, Math.ceil((maxPosY / 100) * fullHeight + padPx));
    let rawH = Math.max(64, rawBot - bandY0);
    if (rawH % 2 !== 0) rawH += 1;
    if (bandY0 + rawH > fullHeight) {
      rawH = fullHeight - bandY0;
      if (rawH % 2 !== 0 && bandY0 >= 1) {
        bandY0 -= 1;
        rawH += 1;
      }
    }
    bandHeight = Math.max(2, Math.min(fullHeight - bandY0, rawH));
  }

  // Render cue-by-cue so every caption can carry scene-adapted colours & per-cue safe Y while sharing the job-constant band.
  const frames = [];
  for (let i = 0; i < cues.length; i++) {
    const cue = cues[i];
    const cueStyle = { ...style, ...(cue.style || {}) };
    if (cue.style && cue.style.fontFamily && String(cue.style.fontFamily).trim()) {
      const resolvedCueFam = resolveRegisteredFamily(cue.style.fontFamily);
      cueStyle.fontFamily = resolvedCueFam;
      cueStyle.fontWeight = snapFontWeight(resolvedCueFam, cueStyle.fontWeight || style.fontWeight || 700);
    }
    const res = await CaptionRenderer.renderAllCuesToPngs({
      cues: [cue],
      width: fullWidth,
      height: bandHeight,
      fullHeight,
      bandY0,
      fps,
      style: cueStyle,
      outputDir: path.join(job.outDir, 'c' + i),
    });
    for (const f of res.frames) {
      f.captionIndex = i;
      f.bandY0 = bandY0;
      f.bandHeight = bandHeight;
      frames.push(f);
    }
  }
  frames.sort((a, b) => a.startMs - b.startMs || a.captionIndex - b.captionIndex || a.stepIndex - b.stepIndex || (a.subIndex || 0) - (b.subIndex || 0));
  process.stdout.write(JSON.stringify({
    style: style.name,
    styleId: style.id,
    fontFamily: style.fontFamily,
    width: fullWidth,
    height: fullHeight,
    bandY0,
    bandHeight,
    fps,
    frames,
  }) + '\n');
})().catch(e => {
  process.stderr.write(String((e && e.stack) || e) + '\n');
  process.exit(1);
});
