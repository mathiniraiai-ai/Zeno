# Format 5: Translate / Flashcard Drill (`translate_drill`)

Declare on line 1 of `script_analysis.md`: `Format: translate_drill (Split screen with flashcards)` or `Format: translate_drill (How would you translate)`

## Script Structure & Modes
- **5A. Split-Screen Flashcard Drill**: Teacher (`Robot`) + learner run `Flashcard Text: En | Native` beats (`layout=split_h z=1`, teacher `slot=left`, learner `slot=right` with advancing silent `peer=`; `split_v` only if `Top:`/`Bottom:`), then teacher takes over full screen (`layout=single`) for pitch & CTA.
- **5B. Solo Translate Drill**: Single AI teacher in one set (`layout=single` or `chat_bar`) through `Flashcard Text:` prompts + CTA.
- Cards normalize **English top / Native bottom** regardless of script order and clear on full-screen takeover/pitch.

## Flashcard Overlays & Captions
- **Use `card:` (never `banner:`)**: `card:"English | Native"` (`@at[/dur]`, `@pos=auto|top|empty|body|bottom`, `@style=clean|classic`; opt out `card:none`/`#cards off`). Scripted `Flashcard Text:` auto-attaches face-safe top flashcards across teacher & learner cuts.
- On `split_h`, default `pos=auto`/`top` spans top-center headroom clear of faces; keep the same card pinned across Robot+Woman turns without blink, clearing on takeover.
- `#subs auto y=65` (single-line; `y=50` on `split_v`) auto-suppresses during active flashcards (`subs=on`/`subs=off`) and runs on takeover/CTA.

## Card-Beat Timing, Motion & Sound
- **Split drill (`split_h`/`split_v`)**: Lock `z=1` (`14%` headroom; `offset_y=+0.08` / `@offset_y=+0.08` shifts speaker downward), advance silent `peer=`, keep sentences complete (`start - 0.05s` to `end + 0.06-0.10s` via `pauses:`, `0.06-0.15s` joins), never cut `<0.45s` breaths, trim dead-air `>=0.55s`.
- **Solo drill (`single`)**: Alternate still `z=1.0` and slow push `z=1.0->1.06` across consecutive cards with fixed `focus=x,y`.
- **Takeover & CTA (`single`)**: Clear cards; keep sentences complete (trim dead-air `>=0.50s` only), slow `1.0->1.06` pushes (`>=2.5s`), `z=sudden_in:1.08@t` or `>=0.45s` `pauses:` punch-in (`>=25%` still `z=1`).
- **SFX**: Flashcards stay silent by default; `mark:wrong`/`mark:right` only if scripted (live non-frozen post-word visuals, no dialogue overlap).

## Transitions (Max 3)
- **Zero transitions between flashcards or inside split turns** — crisp hard cuts on every card beat.
- Allow **<=1 transition** (`whip_left@0.34`, `whip_right@0.34`, `split_reveal@0.40`, `slide_up@0.36`, `slide_down@0.36`, `zoom_in`, `zoom_out`) entering takeover or CTA (`<=3` total).

## Example EDL Cuts
```text
L1 "Robot.mp4" 0.04-4.10 layout=split_h z=1 sp="Robot" slot=left peer="Woman:Woman_Idle.mp4"@0.00-4.06@right card:"Nothing | ಏನಿಲ್ಲ"@pos=top
L2 "Woman.mp4" 0.12-1.85 layout=split_h z=1 sp="Woman" slot=right peer="Robot:Robot_Idle.mp4"@0.00-1.73@left card:"Nothing | ಏನಿಲ್ಲ"@pos=top
L17 "Robot_CTA.mp4" 0.05-8.40 layout=single z=1.0->1.06 focus=0.48,0.28 sp="Robot" tr=whip_left@0.34
```
