#!/usr/bin/env node
/* Zeno subtitles: render caption PNGs (one per spoken word state) with Edit Flow AI's caption-renderer.js on node-canvas.
 * Usage: node render_cues.js <job.json>
 * job.json: {"width":1080,"height":1920,"styleId":9,"outDir":"/abs/dir","cues":[{"startTime":0.0,"endTime":2.4,"text":"...","words":[{"text":"w","startTime":0.0}]}]}
 * Prints one JSON line: {"frames":[{"path","startMs","durationMs","text","captionIndex","stepIndex"}]}
 */
const fs = require('fs');
const path = require('path');
const { registerFont } = require('canvas');

const here = __dirname;
for (const f of JSON.parse(fs.readFileSync(path.join(here, 'fonts.json'), 'utf8'))) {
  try { registerFont(path.join(here, 'fonts', f.file), { family: f.family, weight: String(f.weight) }); } catch (e) { /* a missing weight just falls back */ }
}
const { CaptionRenderer } = require('./caption-renderer.js');

(async () => {
  const job = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
  const styles = JSON.parse(fs.readFileSync(path.join(here, 'styles.json'), 'utf8'));
  const base = styles.find(s => s.id === job.styleId) || styles[0];
  const style = { ...base, positionY: job.positionY !== undefined ? job.positionY : base.positionY };
  const res = await CaptionRenderer.renderAllCuesToPngs({
    cues: job.cues, width: job.width || 1080, height: job.height || 1920, style, outputDir: job.outDir,
  });
  process.stdout.write(JSON.stringify({ style: style.name, frames: res.frames }) + '\n');
})().catch(e => { process.stderr.write(String(e && e.stack || e) + '\n'); process.exit(1); });
