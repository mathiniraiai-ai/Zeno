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
for (const f of fontDefs) {
  try { registerFont(path.join(here, 'fonts', f.file), { family: f.family, weight: String(f.weight) }); } catch (e) { /* optional font */ }
}
// Indic bold files also answer for the heavier weights, so a bold word never falls back to a system font.
for (const f of fontDefs) {
  if (/^(Anek\b|Noto Sans )/.test(f.family) && Number(f.weight) >= 700) {
    for (const w of ['700', '800', '900']) {
      try { registerFont(path.join(here, 'fonts', f.file), { family: f.family, weight: w }); } catch (e) { /* ignore */ }
    }
  }
}

const SCRIPT_FONTS = [
  [/[ಀ-೿]/, '"Noto Sans Kannada"'], [/[ഀ-ൿ]/, '"Noto Sans Malayalam"'], [/[஀-௿]/, '"Noto Sans Tamil"'],
  [/[ఀ-౿]/, '"Noto Sans Telugu"'], [/[ऀ-ॿ]/, '"Noto Sans Devanagari"'], [/[ঀ-৿]/, '"Noto Sans Bengali"'],
  [/[઀-૿]/, '"Noto Sans Gujarati"'], [/[਀-੿]/, '"Noto Sans Gurmukhi"'], [/[଀-୿]/, '"Anek Odia"'],
];

(async () => {
  const job = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
  const W = Number(job.width) || 1080;
  const size = Number(job.fontSize) || 56;
  const rowH = Math.round(size * (Number(job.lineHeight) || 1.72));
  const padX = Number(job.padX) || 54;
  const padTop = Number(job.padTop) || 48;
  const padBottom = Number(job.padBottom) || 120;
  const paraGap = Math.round(rowH * (job.paraGap !== undefined ? Number(job.paraGap) : 0.35));
  const baseColor = job.color || '#2B2B2B';
  const family = job.fontFamily || 'Noto Sans';
  const weight = job.fontWeight || 500;
  const boldWeight = job.boldWeight || 800;
  const maxX = W - padX;

  const fontFor = (text, bold) => {
    let fam = `"${family}"`;
    for (const [re, name] of SCRIPT_FONTS) { if (re.test(text)) { fam = `${name}, "${family}"`; break; } }
    return `${bold ? boldWeight : weight} ${size}px ${fam}, "Noto Sans", sans-serif`;
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
