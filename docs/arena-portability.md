# Five bugs: geometry and colour that were only ever true at one size

Found and fixed on 2026-08-03, after the game added a **blue** arena alongside
the green one the detectors were calibrated on, and after the window was run at
a size other than the one every constant had been measured at.

Every threshold in this bot was measured, not guessed — which is why they were
trusted. The trouble is *what* they were measured against.

- **Two** checks measured **the arena** while claiming to measure the UI,
  because the game's overlays and buttons are translucent: the world behind
  them lands in the same pixels (Bugs 1 and 2).
- **One** measured a *layout offset* that only held at 1920x991, because the
  game re-lays out its UI at other window sizes rather than scaling it (Bug 4).
- **One** was created by the fix for the first, and is the most interesting of
  them (Bug 3).

The rule this leaves behind:

> **Never gate on an absolute value — colour or geometry — that something else
> can move.** Measure the UI's own chrome, or the thing's real location, or
> measure relative to a baseline taken from this arena and this window. A
> threshold with a wide margin is not evidence the signal is sound; it is
> evidence you have only seen one arena, at one size.

Two measurements that make the point:

| | reference | elsewhere |
|---|---|---|
| grey weapon card hue | `H39` green arena | `H110` blue arena |
| heading above card row | 0.09–0.17 card-heights at 1920x991 | 0.28–0.39 at 1002x750 |

---

## Bug 1 — the bot could not see offers screens at all

**Symptom.** On the blue arena the bot pressed skip at a real, fully readable
offers screen over and over, logging `offers-closed — screen went away before a
pick` each time. In `runs/20260803-104812/log.jsonl` it did this ten times in a
row across ~30s before the slow OCR classify finally caught the screen. Picks
that should take ~1.7s took 30s, or were missed entirely.

**Root cause.** `fastpath.has_offers_backdrop` asked "is at least 5% of the
frame darker than `V=60`". The offers overlay does not paint the world dark, it
*scales* its brightness by roughly 0.6. On the green arena that puts the dimmed
world at `V 61-89` — straddling the threshold rather than sitting below it. The
blue arena is lit brighter, so the same overlay never crosses it.

**Evidence.**

| | green arena | blue arena (measured live) |
|---|---|---|
| dark fraction on an offers screen | 0.08 – 0.45 | **0.014** (gate needs ≥ 0.05) |
| world median V, dimmed | 61 – 89 | 100 |
| world hue | H51 | H110 |

`offers-closed` is logged on exactly one condition — this gate returning False —
which is what identified it from the logs before the arena was ever captured.
Reproduced from the green frames alone: a 1.15× brightness gain already drops
three of five reference screens below the gate; 1.3× drops all five.

**Fix.** The test is now relative to that arena's own un-dimmed brightness
(`world_brightness`, ratio 0.85). The baseline comes from frames the *slow*
classifier labelled in-wave / portal / staging — never the fast path, so the
gate cannot drift onto the overlays it is meant to detect. (It is the maximum of
a rolling window rather than the median; the first attempt used the median and
that is Bug 3 below.)

**The follow-on bug, found by running it.** The first fix kept the old absolute
test as a fallback for "arena not yet known". Running it live showed that was
worthless: the bot is routinely started *onto* an offers screen, so it never
sees the un-dimmed play it needs to learn the arena — the fallback was not a
first-frame case, it was the whole session, and the bot sat in the same loop.
The gate now **abstains** when the arena is unknown and lets the white heading,
drawn titles and flat card colour decide; those reject every non-offers screen
on record. `offers_emerging` fires before any title is drawn, so it checks the
heading itself — without that, abstaining put the grass-phantom-card bug
straight back.

**Tests.** `tests/test_bright_arena.py`, including the real captured frame
`sources/offers-blue-arena.png`.

---

## Bug 2 — Auto Skip was never checked, on any blue-arena run

**Symptom.** Auto Skip sat off for entire runs. Across three full blue-arena
runs the bot logged **zero** auto-skip events of any kind; the same code logged
38 in one green-arena session. The failure was completely silent.

**Root cause.** Not detection, and not the green reading — both were correct.
OCR found the button on every frame probed, at (18,828), and read its green
coverage correctly as 0.000 with the toggle genuinely off. What failed was
`hud.auto_skip_readable`, the guard that asks whether the border is visible at
all. It measured *median saturation in the button ring* and called anything
above 90 "covered by the high-wave overlay". But the ring is mostly the
translucent world behind the button, so it measured the arena.

**Evidence.** Ring median saturation with nothing whatsoever covering the
button: **26–40 on the green arena, 109 on the blue one.** So every blue-arena
frame was classified as obscured. `ensure_auto_skip` treats unreadable as "no
evidence" and returns *without logging* — which is why nothing appeared in any
log to point at it.

**Fix.** The guard now asks for **positive evidence that the button is
visible**: either border will do — green for enabled, or the pale chrome of the
button's own border and lettering for disabled. Both are drawn on top of the
arena rather than tinted by it. Measured chrome coverage: 0.092 blue arena
toggle-off, 0.105 green arena toggle-off, 0.059 hub toggle-on.

The overlay protection this guard exists for is preserved: a saturated wash
hides *both* borders, so it still reads as unreadable and the bot still waits
rather than clicking a working toggle off.

**Verified live.** `auto-skip state=maybe-off green=0.0` → click →
`state=enabled green=0.0318`, against 0.0343 measured on the green arena.

**What Auto Skip actually is, and a wrong turn taken while fixing it.** The
button exists *only in phase 2* — the bonus round that starts after the boss and
counts from wave 1 again. Enabling it there is exactly right.

While verifying, the pick rate looked like it collapsed to zero after every
enable: measured across all 19 enables in the 2026-08-01 session, ~18 offers in
the three minutes before and **0** in the three minutes after. That looks
damning and it is a coincidence of ordering. The wave counter tells the real
story:

```
waves before an enable :  15, 16, 17, 18, 19, 20,  1,  2      <- boss, then phase 2 restarts
waves after an enable  :   3,  4,  5,  6,  7,  8,  9, 10      <- ~5s each
```

Identical at every enable. Picks stop because the run has entered the bonus
round, which deals no upgrade offers — not because Auto Skip suppressed them.
So `if obs.button("auto_skip") is not None` is already the correct guard: the
button's mere existence *means* phase 2. Do not "fix" this by gating Auto Skip
on a stall count; there is nothing to fix.

A run that reports a high wave number with zero offers handled — today's blue
run reached wave 60 with 0 picks and 0 errors — is a run that spent its time in
phase 2, and is not a fault.

**Tests.** `tests/test_auto_skip_arena.py`, including the real captured frame
`sources/auto-skip-blue-arena.png`.

---

## Bug 3 — the new baseline poisoned itself

**Symptom.** The `offers-closed` storm from Bug 1 came back, in a run that had
been working minutes earlier: 12 in a row, no picks, while the offers screen sat
there.

**Root cause.** The fix for Bug 1 learns "how bright is lit play" from frames the
slow classifier labelled in-wave. But `classify` returns `IN_WAVE` for *any*
frame with a readable wave number that nothing else claimed — and a mid-deal
offers screen, dimmed but with cards not yet drawn, fails `cards.detect` and so
falls through to exactly that. It was sampled as lit play.

Averaged in, that dragged the baseline down to roughly the brightness of an
offers screen, after which every offers screen read as "not dimmed" — and the
lower it went the more offers screens were misclassified and sampled. Self
reinforcing. Measured on the blue arena: lit play 231, offers screen 100, so any
baseline under 118 breaks the gate.

Excluding `UPGRADE_OFFERS` by classification was not enough; the danger is
precisely the frames the classifier gets *wrong*.

**Fix.** The baseline is the **maximum** of the recent window, not the median.
Every overlay in this game subtracts light, so lit play is the brightest thing
in a run and a maximum cannot be dragged down by something that is not lit play.

Erring high is safe in a way erring low is not: too high only makes the dimming
gate permissive, and the heading, title-ink and card-colour checks still have to
agree before anything is clicked. Too low blinds the bot completely.

**Instrumentation.** `offers-closed` now logs `brightness`, `baseline` and
`samples`. This gate has been wrong twice for reasons visible only in what it
measured, and it decides whether a level-up gets spent. A healthy log looks like
`brightness: 233, baseline: 233` — lit play, so the screen really had gone.

**Verified live.** 19 offers in 184s (6.2/min), median latency 1.65s, titles
100%, 0 errors, and the two remaining `offers-closed` both benign by their own
numbers.

---

## Bug 4 — a smaller window made the bot blind again

**Symptom.** With the game window at 1002x750 instead of 1920x991, the bot
logged `no-cards — offers gone or never settled` **74 times in a row**, took no
upgrades at all in 227s, and the watchdog fired `stuck in upgrade-offers` twice.

**Root cause.** Not OCR, and not the cards: the frame the watchdog dumped parses
perfectly — three common cards, all three titles read. The failing gate was
`has_offers_heading`, which looked for white text in a band placed *0.02 to 0.30
card-heights above the card row*.

The game does not scale its UI with the window; it re-lays it out. Measured, the
heading sits 0.09–0.17 card-heights above the row at 1920x991, but **0.28–0.39**
at 1002x750 — so the band's 0.30 ceiling clipped all but a sliver of it, giving
0.0098 against a 0.020 minimum.

This is the same mistake as the colour bugs in a different currency: a constant
that describes *one window size* pretending to describe the layout.

**Why it was so quiet.** `cards.detect` succeeded throughout, so `classify` kept
returning `UPGRADE_OFFERS` and the loop kept re-entering the offers handler,
whose inner loop gates on `offers_visible` and so never reached the parse.

**Fix.** `cards.detect` already locates the heading by OCR. That box is now kept
in the layout cache alongside the card boxes, and the fast path measures white
coverage *inside it* — exact, and immune to window size. Measured inside the
box: real offers 0.144–0.202, gameplay 0.000–0.024, against the derived band's
much tighter 0.033–0.041 vs 0.000–0.0095.

The derived band survives only for the first screen of a session, before any
layout exists, and now reaches 0.45 card-heights up so it covers small windows.

**A wrong turn worth recording.** A sliding-window maximum over a wide region
was tried first. It fixed the small window but let the AGAIN screen through
(0.0348, over the 0.020 minimum) — taking a maximum over many positions will
eventually find white text on any screen. Precision beat search.

**Verified live** on the same 1002x750 window that was stuck: 17 offers in 132s
(7.7/min), median latency 1.6s, titles 100%, 0 errors, and zero `no-cards`.

**Tests.** `tests/test_window_and_toggle_timing.py`, against the frame the bot
dumped itself, kept as `sources/offers-small-window.png`.

---

## Bug 5 (latent) — Armor was never actually prioritised

Not arena-related; found while retuning. `Armor` appeared in neither
`priority.txt` nor `config.UPGRADE_PRIORITY`, so it ranked *neutral* — below
every named upgrade, including Health. Now ranked directly above Health in both.

The two must stay in step: `priority.txt` ships as a mirror of the built-in
defaults so that a missing file behaves identically, and
`tests/test_priority_file.py` enforces it.

---

## Still outstanding

**Auto Skip at a small window size.** The wave-4 delay was fixed (the off-streak
now applies only once the toggle has been confirmed on, so the first enable of a
run goes out immediately), but that fix has not been *confirmed live*: in the
1002x750 run the button was never found, and no `auto-skip` event fired at all.
Whether `find_auto_skip` works at that window size is untested — the only small
window frame on hand is an offers screen, where the button is not on show.

`fastpath.is_dimmed` (`DIM_MIN_FRACTION`, used to wait for the Stats panel to
open) is the last absolute-brightness gate of this family. It did not misbehave
on the blue arena — the pause panel is near-opaque, so its margin is genuinely
wide, measured 87% of pixels below `V=60` with the panel open against 0.9%
mid-wave — but it is the same shape of bug and would bite on a bright enough
arena.

## What to do when the next arena appears

1. Capture one frame of each screen on it. `python -m tools.grab`, F9.
2. Run `pytest tests/test_bright_arena.py tests/test_auto_skip_arena.py` — they
   are written against real captured frames, so a new arena means adding one
   frame and one parametrised case, not rewriting the logic.
3. Grep the config for any threshold expressed as an absolute hue, value or
   saturation, and ask what is *behind* the thing it measures.
