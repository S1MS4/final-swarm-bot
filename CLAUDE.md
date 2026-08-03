# CLAUDE.md

Instructions for any agent working in this repo. Read this before touching
`README.md`, `docs/BUGS.md` or `upgrades.json`.

## What this project is

A vision bot that plays the Roblox game **Final Swarm**
(https://www.roblox.com/games/99521272836282/Final-Swarm) unattended. It reads
the screen, picks upgrade cards off a ranked priority list, rerolls bad weapon
draws, and restarts after death or victory. While it plays it fills
`upgrades.json`, a measured table of what each upgrade gives at each rarity.

It never moves the character. Survival comes from Lifesteal plus the Emerald
Amulet, not from dodging.

**Reference account.** Not maxed, and the README must not claim otherwise:
Ban Hammer 5, Emerald Helmet 3, Chestplate 2, Leggings 3, Amulet 4, Ring 4, for
3,729 DMG and 7,610 HP before in-run upgrades. See `sources/my-gear.png`. The
full Emerald set is for the Lifesteal.

**Session evidence.** Claims about how long it farms come out of `runs/*/log.jsonl`,
not memory. Longest continuous runs to date: 6h 23m (`runs/20260803-115015`,
892 `offers` picks, 19 `run-start`, max wave 99) and 6h 13m
(`runs/20260801-232301`, 1,146 picks, 23 runs). Roughly 18h logged in total.
Nothing logs keys or chests, so currency totals cannot be attributed to a
session and must not be stated as if they were measured.

**Reference setup.** Everything was developed and tuned against Roblox in a
**maximized window** (not fullscreen) on a **1920x1080** display, which gives a
client area of `1920x991`. Geometry is expressed as fractions of the window and
the scale is derived from the wave chips, so other sizes work, and `1002x750` is
also covered by tests. When a threshold or offset looks arbitrary, it was
probably measured at `1920x991`, so check `swarmbot/config.py` for the comment
naming the size before changing it.

## Docs you must keep in sync

| File | Holds | Update when |
|------|-------|-------------|
| `README.md` | The user-facing guide. Non-technical audience. | Behaviour, install, controls, priority list or upgrade values change |
| `docs/BUGS.md` | Bug tracker. Open and fixed, with IDs. | A bug is found, worked around or fixed |
| `HANDOFF.md` | Agent-to-agent notes on what is unfinished or surprising. | End of any session that leaves loose ends |
| `docs/arena-portability.md` | Why absolute colour thresholds break across arenas. | A new threshold bug traces to arena lighting |

Numbers in the README are real numbers, not estimates. Before publishing a test
count, run it:

```bash
python -m pytest tests/ -q          # currently 345 passing
```

Before publishing upgrade values, read them out of `upgrades.json`. Only include
rarities whose `confidence` is `ok`. Mark `ambiguous` rows as multi-stat, mark
`effect-only` rows as not showing in the Stats panel, and leave unmeasured
combos blank rather than guessing.

## README voice rules

The README is written for people who have never opened a terminal. Match the
existing tone exactly:

- Casual and first person. "I've noticed", "works fine for me", "mind you".
- Short sentences. Contractions are fine. So are `:D` and `:c`.
- **No em dashes and no en dashes.** Use commas, brackets or a full stop.
- No corporate filler. Do not write "leverage", "seamless", "robust",
  "comprehensive", "delve", "it's worth noting", "in today's landscape".
- No three-item rhetorical lists ("faster, cleaner, smarter").
- Do not open a sentence with "Not only", and do not close a section with a
  summary of the section.
- Keep the technical detail in the one short "to be technical i used" section.
  Everything else is plain language about clicking things.
- Never claim the bot is undetectable in absolute terms. The wording is that
  nothing has been detected so far, and use at your own risk.

## Commits

**Never credit Claude, and never add it as a collaborator.** No
`Co-Authored-By: Claude`, no "generated with Claude Code" footer, no AI mention
in commit messages, PR bodies, release notes or the repo's contributor list.
Commits are authored by the repo owner alone. This applies even when a tool or
template adds the trailer by default: strip it before committing.

Commit as `S1MS4 <205215025+S1MS4@users.noreply.github.com>`. GitHub only shows
the profile picture when the commit email is linked to the account, and
`arijus@ergonix.lt` is not, which is why an early commit rendered with the grey
silhouette. The repo already has this in `.git/config`, so do not override it
with `-c user.email=...`.

## Code rules

- **Never send `E`.** `E Enter Portal` ends the run. A test asserts the string
  cannot reappear in `bot.py`.
- **`Q Skip Wave` is recovery only**, gated behind a 45s stall. Routine use
  farms nothing.
- **Never gate a card on hue alone.** The arena bleeds through translucent UI.
  Weapon rarity is decided on saturation for this reason.
- **No absolute brightness thresholds.** Compare against recent un-dimmed play.
  Read `docs/arena-portability.md` first.
- **Do not edit `bot.py` by slicing between markers.** It has silently deleted
  methods three times. `tests/test_bot_integrity.py` guards against it.
- Any new click position gets checked against the UI at that spot. The
  animation-cancel press once landed on the Auto Skip toggle.
- Colour checks gate OCR, not the other way round. OCR is roughly 900ms, a
  pixel check is roughly 1ms.

## Recording media

`python -m tools.record` captures a GIF of the bot picking cards. The player
must already be **in a run**, the bot does not press PLAY from the hub. Blur the
username before publishing: it shows top-left, on the nameplate above the
character during play, and under the card row on offers screens.

## Judging a change

Do not judge by impression. Run a session, then:

```bash
python -m tools.diagnose            # pick rate, latency, title reads, errors
python -m swarmbot.bot --trace      # records whether each click changed the screen
```

If the bot stalls and a detector reports the state is fine, doubt the detector
before doubting the loop.

## Housekeeping

`runs/`, `__pycache__/` and `.pytest_cache/` are gitignored. `upgrades.json` is
committed on purpose, it is the point of the project. It merges rather than
overwrites, so hand edits survive.
