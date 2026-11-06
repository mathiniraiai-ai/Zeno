# Subtitles

Burn-in captions for a Zeno video, only when `plan.edl` has `#subs auto|"<style>" [y=68] [adapt=off]`. `auto` rotates the style away from the last two used on this computer and adapts each caption's colours to the rendered picture behind it.
`caption-renderer.js` is Edit Flow AI's renderer (unchanged); `render_cues.js` runs it on node-canvas; `styles.json` holds the eight tested styles;
`scripts/subtitles.py` builds the cues from the aligned words and `scripts/render.py` overlays the pictures. Needs Node.js; the first use runs `npm install` here.
