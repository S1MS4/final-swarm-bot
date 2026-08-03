# Bug tracker

Everything found so far, open and closed. If you hit something new, add a row
and a short note under it. Open bugs are at the top so they are hard to miss.

## Open

| ID | What happens | How bad | Workaround | Status |
|----|--------------|---------|------------|--------|
| #1 | Auto Skip switches itself off about 45s into a run, with no bot click anywhere near the toggle | Low | Bot re-checks the border every 15s and turns it back on, logs `Auto Skip had switched off again` | Cause unknown |
| #2 | Wave counter never logs above 99, but real runs go past 120 | Low | Ignore the logged wave, it does not gate any decision | Confirmed, three-digit waves unread |
| #3 | Movement Speed avoidance is name based, so `Soul of Swiftness` slips through and gives ×1.11 move speed | Low | Add it to `[avoid]` in `priority.txt` yourself | Open, needs a judgement call |
| #4 | Odd window sizes may break it | Unknown | Maximized window on 1920x1080, which is what it was built on | Untested |

**#4 detail.** This sh.. might break if you change the window to some weird
format, but i tried to make the bot dynamically work on any idk haven't tested
yet lul. What is known: geometry is stored as fractions of the window and the
scale comes off the wave chips, so it is not hardcoded to one size. Two sizes are
covered by tests, `1920x991` (maximized on 1080p) and `1002x750`. Everything
between and beyond is a guess.

**#1 detail.** The toggle sits near `(79, 845)`. Do not widen the green border
range to chase it, grass matches a wide range. The narrow one separates them:
border `H55 S98 V158`, grass `H45 S145 V130`.

**#2 detail.** Across all 80 sessions the highest wave ever logged is 100, and a
single reading at that. In `runs/20260803-115015` the counter reports 99 roughly
every 20 minutes, once per run, which is the reader pinning rather than 43 runs
all ending on the same wave. The player sees 120+ on screen. Nothing depends on
the wave number except log output and the 45s stall timer, so this is cosmetic,
but it does make session logs misleading.

**Dropped.** "Few picks at high waves" was tracked here and is not real, the
picks slow down because the account out-levels the offers, not because anything
failed. The two unmeasured combos (`Freeze rare`, `Piercing rare`) are also no
longer tracked: both cards are weak enough that measuring them changes nothing.

## Fixed

| ID | What happened | Cause | Fix |
|----|---------------|-------|-----|
| #5 | Grass read as three `common` cards, bot picked at empty screens | Grass is flat, uniform and the right hue | Requires the overlay dimming **and** its white heading |
| #6 | Runs ended early for no reason | Bot pressed `E Enter Portal`, which looks like progress | `E` is never sent, and a test asserts the string cannot reappear in `bot.py` |
| #7 | Waves shot 1 to 30 with nothing farmed | `Q Skip Wave` sent routinely, it skips the fight so no XP and no offers | Recovery only, gated behind a 45s stall |
| #8 | Auto Skip toggled itself off every single pick | The animation-cancel click was moved to the bottom left to dodge the cards and landed on the toggle | Click position moved and checked against the UI |
| #9 | Every blue arena run never checked Auto Skip and never logged a thing | The "is the border visible" guard read the translucent world behind the button, 26 to 40 on green, 109 on blue | Guard now wants positive evidence, a green border or the button's own pale chrome |
| #10 | Bot pressed skip at real offers screens while logging `offers-closed` | "Is the world dimmed" used a fixed `V=60`. The overlay scales brightness about 0.6x, so green arena landed at `V 61-89`, right on the line, and blue never crossed it | Compares against the median brightness of recent un-dimmed play, and abstains until it has seen some |
| #11 | Rarity misread when the arena changed | The arena bleeds through the cards, the same grey weapon card reads `H39` on green and `H110` on blue | Weapon tier decided on saturation, never on hue alone |
| #12 | Three methods vanished from `bot.py` | Edited by slicing between markers, which deletes whatever sits between them | `tests/test_bot_integrity.py` parses every `self.x()` call and asserts it exists |
| #13 | Damage read as `807X` instead of `x7.08` | OCR angle classification rotated the text | Angle classification disabled |
| #14 | Clicks read the wrong window | Capture reads a screen region, not a window, so anything on top gets read instead | Capture is strict and the bot re-focuses |
| #15 | Bot could not focus the game | `SetForegroundWindow` fails silently from a background process | `AttachThreadInput` plus an ALT tap |

## Not bugs

Things that look broken and are not:

- **A run reporting a high wave with zero picks.** Auto Skip only exists in
  phase 2, the bonus round after the boss that counts from wave 1 again. It
  deals no upgrade offers.
- **The offers screen showing one or two cards.** It deals one, two or three,
  centred when there are fewer.
- **`no-change` rows in `upgrades.json`.** Some upgrades do not touch a Stats
  panel number at all. Repeated ones get marked `effect-only`.

## Pattern to watch for

Four bugs in a single day (#9, #10, #11 and one more) traced to the same root
cause: thresholds measured against one arena's lighting, applied to UI that is
translucent. See [arena-portability.md](arena-portability.md) before adding any
threshold written as an absolute hue, value or saturation.
