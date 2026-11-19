# Format 5: Translate / Flashcard Drill (`translate_drill`)

Declare on line 1 of `script_analysis.md`: `Format: translate_drill (Split screen with flashcards)` or `Format: translate_drill (How would you translate)`

## Script Structure & Modes
- **5A. Split-Screen Flashcard Drill**: Teacher (`Robot`) + learner run `Flashcard Text: En | Native` beats (`layout=split_h z=1`, teacher `slot=left`, learner `slot=right` with advancing silent `peer=`; use `split_v` only if `Top:`/`Bottom:` is specified), then teacher takes over full screen (`layout=single`) for pitch & CTA.
- **5B. Solo Translate Drill**: Single AI teacher in one consistent set (`layout=single` or `chat_bar`) through `Flashcard Text:` prompts + CTA.
- Cards always normalize **English top / Native bottom** regardless of script order and clear on full-screen takeover/pitch.

## Flashcard Overlays & Captions
- **Use `card:` (never `banner:`)**: `card:"English | Native"` (optional `@at[/dur]`, `@pos=auto|top|empty|body|bottom`, `@style=clean|classic`; opt out via `card:none` or `#cards off`). Scripted `Flashcard Text:` cues auto-attach bilingual top flashcards with face-safe placement across both teacher & learner cuts even if omitted.
- On `split_h`, default `pos=auto`/`top` spans top-center headroom clear of faces; keep the identical card pinned continuously across Robot+Woman turns with zero blink, and clear it on full-screen takeover.
- Bottom `#subs` auto-suppresses during active flashcards (use `subs=on` to force, `subs=off` to suppress on any segment) and runs normally on takeover/CTA.

## Card-Beat Timing, Motion & Sound
- **Split drill (`split_h`/`split_v`)**: Lock `z=1` (`14%` headroom; use `offset_y=+0.08` / `@offset_y=+0.08` to shift a speaker downward across dialogue & silent cuts), advance silent `peer=`, tight turn joins (`0.06–0.15s`), trim pauses `>=0.50s`.
- **Solo drill (`single`)**: Alternate still `z=1.0` and slow push `z=1.0->1.06` across consecutive cards with fixed `focus=x,y`.
- **Takeover & CTA (`single`)**: Clear cards on takeover; slow `1.0->1.06` pushes (`>=2.5s`), `sudden_in:1.08@t` on peaks, `1.08` punch-ins across `>0.25s` pauses (`>=25%` still `z=1`).
- **SFX**: Flashcards stay silent by default (no unsolicited click/pop SFX unless explicitly scripted); `mark:wrong`/`mark:right` only if scripted.

## Transitions (Max 3)
- **Zero transitions between flashcards or inside split turns** — crisp hard cuts on every card beat.
- Allow **<=1 transition** (`tr=whip_left@0.34`, `whip_right@0.34`, `split_reveal@0.40`, `slide_up@0.36`, `slide_down@0.36`, `zoom_in`, `zoom_out`) entering full-screen takeover or a different CTA shot (`<=3` total).

## Example EDL Cuts
```text
L1 "Robot.mp4" 0.04-4.10 layout=split_h z=1 sp="Robot" slot=left peer="Woman:Woman_Idle.mp4"@0.00-4.06@right card:"Nothing | ಏನಿಲ್ಲ"@pos=top
L2 "Woman.mp4" 0.12-1.85 layout=split_h z=1 sp="Woman" slot=right peer="Robot:Robot_Idle.mp4"@0.00-1.73@left card:"Nothing | ಏನಿಲ್ಲ"@pos=top
L17 "Robot_CTA.mp4" 0.05-8.40 layout=single z=1.0->1.06 focus=0.48,0.28 sp="Robot" tr=whip_left@0.34
```
