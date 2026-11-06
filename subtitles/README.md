# Subtitles

Burn-in captions for a Zeno video, only when `plan.edl` has `#subs "<style>" [y=68]`.
`caption-renderer.js` is Edit Flow AI's renderer (unchanged); `render_cues.js` runs it on node-canvas; `styles.json` holds the eight tested styles;
`scripts/subtitles.py` builds the cues from the aligned words and `scripts/render.py` overlays the pictures. Needs Node.js; the first use runs `npm install` here.
