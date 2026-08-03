# Handoff

State of the bot as of the last session. Read `README.md` first, then
`docs/BUGS.md` for the tracked bug list. This covers only what is *unfinished*
or *surprising*.

## Run it

```bash
python -m swarmbot.bot          # Shift+\ stops it
python -m tools.diagnose        # score the last session
pytest tests/ -q                # 345 tests, no game needed
```

Edit `priority.txt` to change what gets picked. Nothing else needs touching.

## Known-unknown

**Auto Skip switches off mid-run, cause unidentified.** It resets on a new run
(expected, handled), but was also seen switching off ~45s into a run with no
bot click anywhere near it — the toggle sits at roughly `(79, 845)` and no
click the bot makes goes near there any more. The bot re-checks the border
every 15s and re-enables it, logging `Auto Skip had switched off again`. If
that line appears often, that is the thread to pull.

Do not trust the border colour on a wide green range — grass matches it. The
narrow range (`H48-68 S60-130 V120-215`) separates them: border `H55 S98 V158`,
grass `H45 S145 V130`.

## Open, in priority order

1. **Table has stopped growing** — 46/48 offered combos measured, gaps at
   `Freeze|rare` and `Piercing|rare`. `--strategy discover-first` chases
   unmeasured combos instead of strong ones; run it for a stretch to close them
   and to find upgrades never yet offered.
2. **Few picks at high waves.** A run resumed at wave 83 produced zero picks.
   Most likely the character out-levels the XP curve, but that has not been
   verified — worth watching an offers screen at wave 70+ before assuming.
3. **Movement Speed avoidance is name-based.** `Soul of Swiftness`
   (`×1.11 Movement Speed`) is picked as "no ranked option". Making the avoid
   rule consult measured effects is a judgement call about strictness.

## Things that will bite

Every one of these cost a bug. They are in the README's Notes section too, but
these are the ones most likely to be re-broken:

- **`E Enter Portal` ends the run.** It looks like progress. It has been added
  twice by reasoning that a prompt saying "Enter Portal" wants to be entered.
  A test asserts the string cannot reappear in `bot.py`.
- **`Q Skip Wave` skips the fight** — no XP, no offers. Recovery only, gated
  behind a 45s stall. Sending it routinely drove waves 1→30 with nothing farmed.
- **A visible wave counter must outrank the portal prompt.** The player fights
  from the platform, so both prompts are on screen during ordinary play.
- **Any click in a margin needs checking against the UI.** The animation-cancel
  press was moved to the bottom-left to avoid the cards and landed on Auto Skip
  instead, toggling it every pick.
- **Editing `bot.py` by slicing between markers deletes whatever sits between
  them.** Three methods vanished that way. `tests/test_bot_integrity.py` parses
  every `self.x()` call and asserts it exists.

## Method

`tools/diagnose.py` scores a session — pick rate, latency, title-read rate,
usable measurements, errors. Judge changes on those numbers. `--trace` records
whether each click actually changed the screen, which turns "the click didn't
register" into a measurement rather than an impression.

When the bot stalls and a detector says the state is fine, doubt the detector.
That mistake — trusting a colour reading that was itself the thing under test —
caused the worst regression of the session.
