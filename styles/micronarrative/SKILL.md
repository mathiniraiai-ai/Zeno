# micronarrative

A short story skit (about 60–95 s): a problem scene, a friend's tip, then an AI-teacher demo. All visuals are AI-generated. Learned from one reference: W42, Malayalam, 93 s ("Friends ordering food abroad"). Numbers are in `spec.json`; script lines and times are in `map.json`; screenshots are in `refs/`.

## Use when
- The script tab has `SCENE n` headings, named speakers (`Name: line`), and a last scene marked `SPLIT SCREEN` with Miss Nova.
- The user calls the video a micronarrative.

## Structure
1. **Scene 1, the problem** (about 20 s): `single`, speaker-follow cuts. A wide establishing shot, then one close-up per speaker, then a reaction close-up.
2. **Scene 2, the tip** (about 16 s): `single`, ONE continuous two-shot. No cuts, no zoom.
3. **Scene 3, the AI teacher** (about 56 s): `split_h`. Miss Nova on the left, the character on the right. The full frame never cuts; each panel swaps clips at turn boundaries.
4. **Between scenes**: a 0.42 s `warm_flash` transition, with the content swap hidden under it.

## Rules
- Cut to the next speaker 0.1–0.2 s before they speak. Cut away 0.1–0.2 s after the previous line ends.
- Keep the laughter and reaction beat after the last line of scene 1 (about 6 s in total).
- Turn gaps in the dialogue are about 0.2 s. Keep longer gaps (1.0–1.2 s) at dramatic beats.
- No digital zoom. Variety comes from shot size.
- Faces sit about 35% from the top in every close-up and split panel.
- No music and no SFX. Normalise loudness to -14 LUFS (the reference is -19.3).
- No on-screen text beyond what the script asks for. Burned-in subtitles are ignored for now.
- Split divider: thin light line (about 3 px of 720) on the left panel's right edge, no active-speaker border, no dimming of the listener. Both panels play live video.

## Engine status
- Built and checked against the reference: `single` and `split_h` (live panels, exact framing, measured divider), the `warm_flash` transition, plan building from this spec (`scripts/style_plan.py`), loudness. Rebuilding the reference from stand-in clips matches it frame by frame (clean frames differ by 2-3 of 255; flash frames by 10-18).
- Not built or not tested: take selection and clip-to-line matching on real raw footage, per-panel clip swaps, reaction-shot selection, a style router.

## Open questions (see `spec.json` "unknowns")
Format of the future raw clips (one per line, or one per scene?), and what the listening panel shows in scene 3.
