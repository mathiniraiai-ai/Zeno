#!/usr/bin/env node
/* Zeno subtitles: render caption PNGs (with 3-subframe active-word karaoke pop & cropped horizontal band) on node-canvas.
 * Usage: node render_cues.js <job.json>
 * job.json: {"width":1080,"height":1920,"fps":30,"styleId":4,"outDir":"/abs/dir","positionY":65,"cues":[...]}
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

const INDIC_FAMILY_NAMES = new Set([
  'noto sans kannada', 'anek kannada', 'noto serif kannada', 'tiro kannada', 'baloo tamma 2',
  'noto sans tamil', 'hind madurai', 'mukta malar', 'anek tamil', 'baloo thambi 2', 'catamaran', 'coiny', 'kavivanar',
  'noto sans telugu', 'hind guntur', 'anek telugu', 'noto serif telugu', 'ramabhadra', 'gidugu',
  'noto sans malayalam', 'manjari', 'anek malayalam', 'noto serif malayalam', 'baloo chettan 2', 'chilanka',
  'noto sans devanagari', 'hind', 'mukta', 'teko', 'anek devanagari', 'baloo 2', 'noto serif devanagari', 'kalam', 'modak', 'rozha one',
  'noto sans bengali', 'hind siliguri', 'anek bangla', 'baloo da 2', 'noto serif bengali', 'atma', 'galada',
  'noto sans gujarati', 'hind vadodara', 'mukta vaani', 'anek gujarati', 'baloo bhai 2', 'mogra',
  'noto sans gurmukhi', 'mukta mahee', 'anek gurmukhi', 'baloo paaji 2',
  'noto sans oriya', 'baloo bhaina 2', 'anek odia', 'noto serif oriya',
  'nirmala ui', 'nirmala text',
]);

(async () => {
  const job = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
  const styles = JSON.parse(fs.readFileSync(path.join(here, 'styles.json'), 'utf8'));
  const base = styles.find(s => s.id === job.styleId) || styles[0];
  const fullWidth = Number(job.width) || 1080;
  const fullHeight = Number(job.height) || 1920;
  const fps = Math.max(24, Number(job.fps) || 25);
  const defaultPosY = job.positionY !== undefined ? Number(job.positionY) : (base.positionY !== undefined ? Number(base.positionY) : 65);
  const style = { ...base, positionY: defaultPosY };
  const hasExplicitJobFont = Boolean(job.fontFamily && String(job.fontFamily).trim());
  if (hasExplicitJobFont) {
    const resolvedFam = resolveRegisteredFamily(job.fontFamily);
    style.fontFamily = resolvedFam;
    style.fontWeight = snapFontWeight(resolvedFam, job.fontWeight || style.fontWeight || 700);
  }
  const hasIndicCueText = (c) => /[\u0900-\u0D7F]/.test(String((c && c.text) || '')) ||
    (Array.isArray(c && c.words) && c.words.some(w => /[\u0900-\u0D7F]/.test(typeof w === 'string' ? w : String((w && (w.text || w.word)) || ''))));
  // One fixed caption size for every style (set by Zeno), so captions stay neat and never oversized.
  if (Number(job.fontSize) > 0) style.fontSize = Number(job.fontSize);
  style.maxLines = job.maxLines !== undefined ? Number(job.maxLines) : (base.maxLines !== undefined ? Number(base.maxLines) : 1);
  style.singleLine = job.singleLine !== false && style.maxLines !== 2;   // one row only (owner's rule)

  const cues = Array.isArray(job.cues) ? job.cues : [];
  const jobHasIndic = cues.some(hasIndicCueText);
  if (jobHasIndic) {
    // When auto-selected style on an Indic bilingual reel uses a pure-Latin geometric font,
    // harmonize Latin glyphs to Noto Sans (matches Indic Noto Sans stroke geometry & x-height 1:1).
    if (!hasExplicitJobFont && !INDIC_FAMILY_NAMES.has(String(style.fontFamily || '').trim().toLowerCase())) {
      const notoLat = resolveRegisteredFamily('Noto Sans');
      if (notoLat) style.fontFamily = notoLat;
    }
    // Lock bold weight to 700 at the JOB level so English-only, mixed, and Indic-only cues share exact weight.
    if (Number(style.fontWeight || 700) >= 700) {
      style.fontWeight = snapFontWeight(style.fontFamily, 700);
    }
  }
  style._jobHasIndic = jobHasIndic;
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

  // Job-level uniform font-size pre-pass across normal cues so cues never jump sizes frame-to-frame.
  if (cues.length > 0 && typeof CaptionRenderer.computeUniformFontSize === 'function') {
    style.fontSize = CaptionRenderer.computeUniformFontSize({
      cues,
      width: fullWidth,
      height: bandHeight,
      fullHeight,
      style,
    });
  }

  // Render cue-by-cue so every caption can carry scene-adapted colours & per-cue safe Y while sharing the job-constant band.
  const frames = [];
  for (let i = 0; i < cues.length; i++) {
    const cue = cues[i];
    const cueStyle = { ...style, ...(cue.style || {}), _jobHasIndic: jobHasIndic };
    if (cue.style && cue.style.fontFamily && String(cue.style.fontFamily).trim()) {
      const resolvedCueFam = resolveRegisteredFamily(cue.style.fontFamily);
      cueStyle.fontFamily = resolvedCueFam;
      cueStyle.fontWeight = snapFontWeight(resolvedCueFam, cueStyle.fontWeight || style.fontWeight || 700);
    }
    if (jobHasIndic && Number(cueStyle.fontWeight || 700) >= 700) {
      cueStyle.fontWeight = snapFontWeight(cueStyle.fontFamily, 700);
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
      f.fontFamily = cueStyle.fontFamily;
      f.fontWeight = cueStyle.fontWeight;
      f.fontSize = cueStyle.fontSize;
      frames.push(f);
    }
  }
  frames.sort((a, b) => a.startMs - b.startMs || a.captionIndex - b.captionIndex || a.stepIndex - b.stepIndex || (a.subIndex || 0) - (b.subIndex || 0));
  process.stdout.write(JSON.stringify({
    style: style.name,
    styleId: style.id,
    fontFamily: style.fontFamily,
    fontWeight: style.fontWeight,
    fontSize: style.fontSize,
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
