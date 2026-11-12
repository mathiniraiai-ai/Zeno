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
const registeredPairs = new Set();

for (const f of fontDefs) {
  const fontPath = path.join(here, 'fonts', f.file);
  const wStr = String(f.weight);
  try {
    registerFont(fontPath, { family: f.family, weight: wStr });
    registeredPairs.add(`${f.family}::${wStr}`);
  } catch (e) { /* missing optional font file falls back cleanly */ }
}

// Register 700, 800, and 900 weight aliases for bundled Indic bold fonts (and 800->900 for Plus Jakarta Sans)
// so Pango/HarfBuzz never drops back to unstyled system fonts when a style requests weight 800 or 900.
const INDIC_BOLD_FAMILIES = /^(Anek\b|Noto Sans (?:Kannada|Tamil|Telugu|Malayalam|Devanagari|Bengali|Gujarati|Gurmukhi|Oriya)|Baloo Tamma 2|Catamaran|Ramabhadra|Plus Jakarta Sans)/;
for (const f of fontDefs) {
  const wNum = Number(f.weight);
  if (INDIC_BOLD_FAMILIES.test(f.family) && (wNum === 700 || wNum === 800)) {
    const fontPath = path.join(here, 'fonts', f.file);
    for (const targetW of ['700', '800', '900']) {
      const key = `${f.family}::${targetW}`;
      if (!registeredPairs.has(key)) {
        try {
          registerFont(fontPath, { family: f.family, weight: targetW });
          registeredPairs.add(key);
        } catch (e) {}
      }
    }
  }
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
