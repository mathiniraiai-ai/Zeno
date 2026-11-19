#!/usr/bin/env node
/* Zeno scrolling-text page: draw the whole dialogue as ONE tall transparent PNG and report where every word sits.
 * Usage: node render_page.js <job.json>
 * job.json: {"width":1080,"fontSize":56,"lineHeight":1.72,"padX":54,"padTop":48,"padBottom":120,"paraGap":0.35,
 *            "color":"#2B2B2B","fontFamily":"Noto Sans","fontWeight":500,"boldWeight":800,"dash":true,"out":"/abs/page.png",
 *            "paragraphs":[{"words":[{"text":"Do","color":"#D0021B","bold":true}, ...]}]}
 * Prints one JSON line: {"width":..,"height":..,"rowHeight":..,"words":[{"p":0,"i":0,"x":..,"y":..,"w":..,"h":..,"row":0}, ...]}
 * Python (scripts/scroll_page.py) scrolls this picture and draws the highlight box behind the word being spoken.
 */
const fs = require('fs');
const path = require('path');
const { createCanvas, registerFont } = require('canvas');

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
  } catch (e) { /* optional font */ }
}

function resolveRegisteredFamily(name) {
  const raw = String(name || '').trim();
  if (!raw) return 'Noto Sans';
  const rec = registeredWeightsByFamily.get(raw.toLowerCase());
  return rec ? rec.family : raw;
}

function snapFontWeight(family, requestedWeight) {
  const req = Number(requestedWeight) || 400;
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

const SCRIPT_FONTS = [
  [/[ಀ-೿]/, '"Noto Sans Kannada"', new Set(['noto sans kannada', 'noto serif kannada', 'tiro kannada', 'anek kannada', 'baloo tamma 2'])],
  [/[ഀ-ൿ]/, '"Noto Sans Malayalam"', new Set(['noto sans malayalam', 'noto serif malayalam', 'manjari', 'anek malayalam', 'baloo chettan 2', 'chilanka'])],
  [/[஀-௿]/, '"Noto Sans Tamil"', new Set(['noto sans tamil', 'hind madurai', 'mukta malar', 'anek tamil', 'baloo thambi 2', 'catamaran', 'coiny', 'kavivanar'])],
  [/[ఀ-౿]/, '"Noto Sans Telugu"', new Set(['noto sans telugu', 'hind guntur', 'noto serif telugu', 'anek telugu', 'ramabhadra', 'gidugu'])],
  [/[ऀ-ॿ]/, '"Noto Sans Devanagari"', new Set(['noto sans devanagari', 'hind', 'mukta', 'teko', 'noto serif devanagari', 'anek devanagari', 'baloo 2', 'kalam', 'modak', 'rozha one'])],
  [/[ঀ-৿]/, '"Noto Sans Bengali"', new Set(['noto sans bengali', 'hind siliguri', 'noto serif bengali', 'anek bangla', 'baloo da 2', 'atma', 'galada'])],
  [/[઀-૿]/, '"Noto Sans Gujarati"', new Set(['noto sans gujarati', 'hind vadodara', 'mukta vaani', 'anek gujarati', 'baloo bhai 2', 'mogra'])],
  [/[਀-੿]/, '"Noto Sans Gurmukhi"', new Set(['noto sans gurmukhi', 'mukta mahee', 'anek gurmukhi', 'baloo paaji 2'])],
  [/[଀-୿]/, '"Noto Sans Oriya"', new Set(['noto sans oriya', 'baloo bhaina 2', 'noto serif oriya', 'anek odia'])],
];

(async () => {
  const job = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
  const W = Number(job.width) || 1080;
  const size = Number(job.fontSize) || 56;
  const rowH = Math.round(size * (Number(job.lineHeight) || 1.72));
  const padX = Number(job.padLeft !== undefined ? job.padLeft : job.padX) || 54;
  const padRight = Number(job.padRight !== undefined ? job.padRight : padX) || 54;
  const padTop = Number(job.padTop) || 48;
  const padBottom = Number(job.padBottom) || 120;
  const paraGap = Math.round(rowH * (job.paraGap !== undefined ? Number(job.paraGap) : 0.35));
  const baseColor = job.color || '#2B2B2B';
  const family = resolveRegisteredFamily(job.fontFamily || 'Noto Sans');
  const familyLower = family.toLowerCase();
  const weight = snapFontWeight(family, job.fontWeight || 500);
  const boldWeight = snapFontWeight(family, job.boldWeight || 800);
  const maxX = W - padRight;

  const fontFor = (text, bold) => {
    let fam = `"${family}"`;
    let isIndic = false;
    let familySupportsScript = false;
    for (const [re, name, nativeSet] of SCRIPT_FONTS) {
      if (re.test(text)) {
        isIndic = true;
        if (nativeSet.has(familyLower)) {
          fam = `"${family}", ${name}`;
          familySupportsScript = true;
        } else {
          fam = `${name}, "${family}"`;
        }
        break;
      }
    }
    const baseW = bold ? boldWeight : weight;
    const w = bold && isIndic && !familySupportsScript && Number(baseW) >= 700 ? 700 : baseW;
    return `${w} ${size}px ${fam}, "Noto Sans", sans-serif`;
  };

  const probe = createCanvas(16, 16).getContext('2d');
  probe.font = fontFor('n', false);
  const spaceW = probe.measureText(' ').width * 1.05;
  const dashW = job.dash === false ? 0 : probe.measureText('— ').width;

  // 1) lay the words out in rows
  const placed = [];
  let y = padTop;
  let row = 0;
  (job.paragraphs || []).forEach((para, p) => {
    let x = padX + dashW;
    (para.words || []).forEach((wd, i) => {
      const text = String(wd.text || '');
      probe.font = fontFor(text, !!wd.bold);
      const w = probe.measureText(text).width;
      if (x + w > maxX && x > padX + 1) {      // next row (continuation rows start at the left edge, like the reference)
        x = padX;
        y += rowH;
        row += 1;
      }
      placed.push({ p, i, text, bold: !!wd.bold, color: wd.color || baseColor, x, y, w, h: rowH, row });
      x += w + spaceW;
    });
    para._y = y;
    y += rowH + paraGap;
    row += 1;
  });
  const H = Math.max(rowH, y - paraGap + padBottom);

  // 2) draw
  const canvas = createCanvas(W, H);
  const ctx = canvas.getContext('2d');
  ctx.textBaseline = 'alphabetic';   // one shared baseline, so Latin and Indic words sit level
  if (dashW > 0) {
    ctx.fillStyle = baseColor;
    ctx.font = fontFor('n', false);
    let lastP = -1;
    for (const wd of placed) {
      if (wd.p !== lastP && wd.i === 0) { ctx.fillText('—', padX, wd.y + rowH * 0.5 + size * 0.34); lastP = wd.p; }
    }
  }
  for (const wd of placed) {
    ctx.font = fontFor(wd.text, wd.bold);
    ctx.fillStyle = wd.color;
    ctx.fillText(wd.text, wd.x, wd.y + rowH * 0.5 + size * 0.34);
  }
  fs.writeFileSync(job.out, canvas.toBuffer('image/png'));
  process.stdout.write(JSON.stringify({
    width: W, height: H, rowHeight: rowH,
    words: placed.map(w => ({ p: w.p, i: w.i, x: Math.round(w.x), y: Math.round(w.y), w: Math.round(w.w), h: w.h, row: w.row })),
  }) + '\n');
})().catch(e => { console.error(e && e.stack || e); process.exit(1); });
