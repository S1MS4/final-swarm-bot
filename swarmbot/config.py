"""Tunables for the whole bot.

Every threshold that might need adjusting lives here rather than being buried in
the detector that uses it.  Values marked "measured" were derived from the
reference screenshots in sources/ (see tests/), not guessed.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCES = ROOT / "sources"
RUNS = ROOT / "runs"
UPGRADES_JSON = ROOT / "upgrades.json"
# Plain-text pick order, editable without touching code.  See priorities.py.
PRIORITY_FILE = ROOT / "priority.txt"

# --------------------------------------------------------------------------
# Window
# --------------------------------------------------------------------------

# Substrings that identify the Roblox game window title.  Roblox uses
# "Roblox" for the player client; the launcher/browser must not match.
WINDOW_TITLE_CANDIDATES = ("Roblox",)
WINDOW_TITLE_EXCLUDE = ("Roblox Studio", "Roblox Player Beta", "Microsoft Edge", "Chrome")

# --------------------------------------------------------------------------
# Rarity classification (measured from sources/*.png)
# --------------------------------------------------------------------------

# Rarity is decided from the *median HSV of the card body*, never from OCR.
# Measured medians:
#   weapon    H39 S15  V73     <- desaturated grey
#   common    H61 S136 V49
#   rare      H106 S148 V59
#   epic      H147 S106 V67
#   legendary H16 S148 V74
#
# OpenCV hue is 0-179.
WEAPON_MAX_SATURATION = 60

# Grey alone is not enough to call something a weapon card: any desaturated
# surface qualifies, and a white browser page behind the game read as three
# perfect weapon cards.  Weapon bodies are *dark* grey (measured V=73).
# The bound is on the *median*, which is what rejects a white page (V=255);
# the range itself stays wide enough to include the card's own white text and
# borders, so coverage stays high.
WEAPON_VALUE_RANGE = (30, 205)

RARITY_HUE_RANGES: dict[str, tuple[int, int]] = {
    "legendary": (8, 28),
    "common": (42, 78),
    "rare": (88, 122),
    "epic": (128, 168),
}

# In-game chip label -> canonical rarity key.  The green tier is labelled
# "Common" in game even though it is the second tier.
RARITY_LABELS: dict[str, str] = {
    "weapon": "weapon",
    "common": "common",
    "rare": "rare",
    "epic": "epic",
    "legendary": "legendary",
}

RARITY_ORDER = ("weapon", "common", "rare", "epic", "legendary")

# --------------------------------------------------------------------------
# Card segmentation
# --------------------------------------------------------------------------

# The offers screen usually deals three cards, but late in a run - once most
# upgrades are already owned - it can deal only two or one.  Everything must
# cope with the whole range; assuming three made the bot ignore those screens
# entirely and sit there until the player intervened.
MAX_CARDS = 3
MIN_CARDS = 1

# Used only when a single card is on offer, where there is no spacing between
# chips to derive the layout scale from.  Measured 314px at 1920 wide.
UNIT_PER_FRAME_WIDTH = 0.1635

# The offers screen never uses the outer margins, so OCR only reads this slice
# of the frame: (x, y, w, h) as fractions.  Generous enough for both observed
# card-row positions.
OFFERS_BAND = (0.08, 0.00, 0.84, 0.94)

# Fraction of a card body that must agree with the classified rarity for the
# fast path to accept it as a real card rather than scenery.  Cards are a flat
# colour wash; gameplay is not.
CARD_MIN_COVERAGE = 0.55
# Much lower bar, used only to fire the animation-skip click as early as
# possible - the first hint of rarity colour in any slot is enough.
CARD_EMERGE_COVERAGE = 0.15
# The offers overlay dims the world behind it; grass does not.  Measured:
# gameplay 0.008-0.019 dark, real offers screens 0.084-0.368.  Without this
# gate a green field read as three perfect common cards.
# Fallback only - used until the session has seen enough un-dimmed play to know
# how bright *this* arena is.  It is absolute, so it is arena-dependent; see
# OFFERS_DIM_RATIO.
OFFERS_MIN_DARK = 0.05
# How dim the world must go, as a fraction of this arena's own un-dimmed
# brightness.  The overlay scales brightness rather than subtracting from it, so
# this ratio holds on any arena while an absolute threshold does not - the green
# arena's dimmed world sits at V 61-89, and the blue arena's stays above V=60
# entirely, which made the bot fail to see real offers screens at all.
# Measured on the green frames: gameplay median V 118-130, offers 61-89, i.e.
# ratios of 0.47-0.75.  Everything un-dimmed (hub, staging, AGAIN) measures
# ~1.0, so this sits with clear margin on both sides.
OFFERS_DIM_RATIO = 0.85
# In-wave frames kept to derive that baseline, of which the *brightest* is used.
# A window rather than a single sample so a dimmed frame that slipped through
# misclassification cannot set it, and so it still re-converges within seconds
# of the arena changing.
DIM_BASELINE_SAMPLES = 9
# White coverage in the heading band above the cards.  Measured: real offers
# 0.033-0.041, gameplay and every recorded phantom 0.000-0.0095.  This is the
# check a bright or dark area-of-effect cannot fake.
#
# Used only until a layout has been solved; after that the heading's real box is
# known and OFFERS_MIN_TITLE_WHITE applies instead.
OFFERS_MIN_HEADING = 0.020
# How far above the card row the band reaches, in card heights.  The gap between
# heading and cards does *not* scale with the cards - the game re-lays out its UI
# at different window sizes rather than scaling it.  Measured, the heading spans
# 0.09-0.17 card-heights above the row at 1920x991 but 0.28-0.39 at 1002x750, so
# the 0.30 ceiling this used to have clipped all but a sliver.  That measured
# 0.0098 on a screen whose three cards parsed perfectly, and the bot logged
# `no-cards` 74 times in a row without taking a single upgrade.
OFFERS_HEADING_BAND_TOP = 0.45
# White coverage *inside the heading's own box*, once OCR has located it.  Far
# better separated than any derived band, because it is measuring the text
# rather than a guess at where the text might be: measured 0.144-0.202 on real
# offers screens against 0.000-0.024 on gameplay.
OFFERS_MIN_TITLE_WHITE = 0.08
# White-text coverage in a card's title zone.  A card is only takeable once its
# title is drawn; measured minimum across the three cards is 0.000 while the
# deal animation runs and 0.044-0.093 once settled.
CARD_MIN_TITLE_INK = 0.025
# Minimum height of a card title, as a fraction of the chip spacing.  The
# player's nametag ("Final Swarm Master") and floating damage numbers bleed
# through the translucent cards and land in the title zone; they were recorded
# as upgrades called "Final Master", "Fina aster" and "astei".  Measured
# heights: real titles 0.115-0.137 of unit, the nametag 0.060.
CARD_TITLE_MIN_HEIGHT = 0.090

# How much a 32x32 digest must move to count as a changed screen.  Used to tell
# a fresh deal from the cards still on screen from the previous pick.
CARD_STABLE_DELTA = 2.5
# Grace period after the animation-cancel press before looking for settled
# cards.  Kept short because the title-ink gate does the real waiting.
CARD_SETTLE_DELAY = 0.10
# Polling interval while waiting for that snap - tighter than the idle poll
# because the whole point is to click inside a narrow window.
FAST_POLL_INTERVAL = 0.015

# Sanity bounds on the offers row, as fractions of the chip spacing.  Both are
# loose on purpose: a settled, perfectly readable screen measured gaps of 317
# and 300 px (diff 16.5) and chip y of 324/320/346, and tight bounds rejected
# those outright.  Mid-deal frames are excluded by CARD_MIN_TITLE_INK instead,
# which is a direct test of the property that actually matters.
CHIP_SPACING_TOLERANCE = 0.12
CHIP_ROW_TOLERANCE = 0.12

# "NEW!" badge: bright saturated yellow in the top-right corner of the card.
NEW_BADGE_HSV_LOW = (20, 120, 170)
NEW_BADGE_HSV_HIGH = (35, 255, 255)
NEW_BADGE_MIN_PIXEL_FRAC = 0.02  # of the badge search box
# Badges are located as blobs in the card row and assigned to the nearest
# card, which tolerates the row shifting between offers screens.
BADGE_MIN_AREA_FRAC = 0.0012   # of card width squared
BADGE_ANCHOR_OFFSET = 0.42     # badge sits right of the card centre
BADGE_MAX_OFFSET = 0.45        # max distance from that anchor, in card widths

# Green "magnitude" arrows in the card footer.
ARROW_HSV_LOW = (40, 120, 90)
ARROW_HSV_HIGH = (85, 255, 255)
ARROW_MIN_AREA_FRAC = 0.0008  # of the footer box

# --------------------------------------------------------------------------
# Templates
# --------------------------------------------------------------------------

TEMPLATE_SCALES = tuple(round(0.40 + 0.05 * i, 2) for i in range(33))  # 0.40 .. 2.00
TEMPLATE_MATCH_THRESHOLD = 0.62

# --------------------------------------------------------------------------
# Stats panel
# --------------------------------------------------------------------------

# Rows that are running counters, not attributes.  Never diffed.
STAT_DENYLIST = frozenset(
    {
        "kills",
        "damage delt",
        "damage dealt",
        "damage taken",
        "revives",
        "xp collected",
        "level",
    }
)

# Canonical attribute rows, in panel order.  Used to fuzzy-correct OCR output.
STAT_VOCAB = (
    "Max HP",
    "HP Regen",
    "Lifesteal",
    "Armor",
    "Evasion",
    "Thorns",
    "Damage",
    "Crit Chance",
    "Crit Damage",
    "Attack Speed",
    "Projectile Count",
    "Size",
    "Luck",
    "Drops Luck",
    "Movement Speed",
    "Jump Height",
    "Kills",
    "Damage Delt",
    "Damage Taken",
    "Revives",
    "Xp Collected",
    "Level",
)

# --------------------------------------------------------------------------
# OCR
# --------------------------------------------------------------------------

OCR_MIN_CONFIDENCE = 0.45
FUZZY_MATCH_MIN_SCORE = 72  # rapidfuzz 0-100

# White-glyph isolation for text drawn on semi-transparent panels.  Measured:
# V>140 / S<80 removes the world and the yellow quest HUD bleeding through the
# Stats panel while keeping every panel row (including "x7.08").
LIGHT_TEXT_MIN_VALUE = 140
LIGHT_TEXT_MAX_SATURATION = 80

# Card titles seen so far.  This is a spell-checker, not a whitelist: an
# unrecognised title falls through to the raw OCR text so a new upgrade still
# gets recorded under its own name (see vision/cards.py).  Names below the
# stat block were read off the live pause screen's Upgrades grid.
CARD_TITLE_VOCAB = (
    "Damage",
    "Health",
    "Attack Speed",
    "Movement Speed",
    "Thorns",
    "Regen",
    "Luck",
    "Size",
    "Armor",
    "Crit Chance",
    "Crit Damage",
    "Lifesteal",
    "Evasion",
    "Projectile Count",
    "Drops Luck",
    "Jump Height",
    "Multishot",
    "Bolt",
    "Ricochet",
    "Demon Slayer",
    "Stand Strong",
    "Giant's Strength",
    "Spike Ball",
    "Frost Walker",
    "Bow",
)

# Footer stat names printed on stat cards.  These are the card's own wording,
# which does not always match the Stats panel's - see FOOTER_TO_STAT.
CARD_FOOTER_VOCAB = (
    "Damage",
    "Max Health",
    "Attack Speed",
    "Movement Speed",
    "Thorns",
    "Health Regen",
    "Luck",
    "Size",
    "Armor",
    "Critical Chance",
    "Critical Damage",
    "Lifesteal",
    "Evasion",
    "Projectile Count",
    "Drops Luck",
    "Jump Height",
)

# Card footer stat name -> Stats-panel row name.
FOOTER_TO_STAT = {
    "Max Health": "Max HP",
    "Health Regen": "HP Regen",
    "Critical Chance": "Crit Chance",
    "Critical Damage": "Crit Damage",
}

# --------------------------------------------------------------------------
# Screen text anchors
# --------------------------------------------------------------------------

TEXT_UPGRADE_OFFERS = "UPGRADE OFFERS"
TEXT_STATS_HEADER = "Stats"
TEXT_RESUME = "Resume"
TEXT_AUTO_SKIP = "Auto Skip"
TEXT_YOU_DIED = "YOU DIED"
TEXT_GIVE_UP = "GIVE UP"
TEXT_VICTORY = "VICTORY"
TEXT_AGAIN = "AGAIN"
# The hub shows an "E  Enter Portal" prompt to start a run.
TEXT_ENTER_PORTAL = "Enter Portal"
TEXT_REROLL = "REROLL"

# Documentation only: the end of a run is driven by the "Auto Skip" text
# appearing, not by counting to a wave number, because the wave counter is
# unreliable during the boss transition (it has been observed reading 1).
# Auto Skip is a toggle drawn with a green border when it is enabled, so the
# bot measures the border rather than clicking blindly - a blind click is as
# likely to switch it off as on.  Threshold is deliberately low; the exact
# coverage is logged on every boss transition so it can be tightened from real
# data rather than guessed at.
# Narrow on purpose.  A broad green range picked up the grass behind the button
# and read a disabled toggle as enabled - the false reading that led to the
# portal being clicked.  Measured: the border is hue 55 / sat 98 / val 158,
# the grass is hue 45 / sat 145 / val 130.
AUTO_SKIP_GREEN_LOW = (48, 60, 120)
AUTO_SKIP_GREEN_HIGH = (68, 130, 215)
# Measured live at the first boss transition: 0.000 with the toggle off,
# 0.032 with it on.  The initial guess of 0.06 sat *above* the real "on"
# value, which is exactly why the bot converges on the delta instead of
# trusting this number.
# Measured with the narrow range above: 0.000 disabled, 0.026 enabled.
AUTO_SKIP_ON_GREEN = 0.012
# How much the green coverage must move to count as the toggle flipping.  The
# bot converges on "on" by watching this delta, so it never depends on the
# absolute threshold above being right.
AUTO_SKIP_GREEN_DELTA = 0.01
AUTO_SKIP_ATTEMPTS = 4
# A single "off" reading is not evidence that the toggle is off.  The border is
# drawn in a narrow green, and the wave-transition banner tints the whole screen
# - measured over 40 consecutive frames with the toggle physically untouched,
# coverage swung between 0.000 and 0.030 and fell below AUTO_SKIP_ON_GREEN on 16
# of them.  Acting on one sample therefore switched a *working* toggle off, 23
# times in a single 25-minute session.
# The suppression is transient, so each check takes the best of several frames
# spread over longer than a banner lasts, and the click needs consecutive checks
# to agree before it goes out.
AUTO_SKIP_SAMPLES = 5
AUTO_SKIP_SAMPLE_GAP = 0.45
AUTO_SKIP_OFF_STREAK = 2
# Whether the border can be seen at all.  The high-wave overlay is not the brief
# banner the streak above was sized for - it covers the button for minutes at a
# time, long enough for consecutive checks to agree on a reading that is simply
# unavailable.  Unreadable is its own answer: the bot waits rather than guessing.
#
# This is asked as *positive evidence that the button is visible*: either border
# will do, green for enabled or pale chrome for disabled.  Measuring the absence
# of tint instead (median ring saturation under 90) was a property of the arena,
# not of the button - the ring is mostly the translucent world behind it, which
# measured 24-40 on the green arena but 109 on the blue one.  Every blue-arena
# run therefore read the toggle as permanently obscured and never checked it.
AUTO_SKIP_CHROME_LOW = (0, 0, 150)
AUTO_SKIP_CHROME_HIGH = (179, 70, 255)
# Measured in the ring: 0.092 blue arena toggle-off, 0.105 green arena
# toggle-off, 0.059 hub toggle-on.  Half the smallest of those.
AUTO_SKIP_MIN_CHROME = 0.030

# The staging area between waves.  "Q" is Skip Wave, which always advances the
# run; "E" is Enter Portal, which *ends* it.  Only Q is ever sent.
KEY_SKIP_WAVE = "q"
# How long the staging screen must persist before Q is sent.  Waves advance on
# their own well inside this; Q is a recovery action, and sending it routinely
# skipped the run from wave 1 to 30 without fighting anything.
# Waves advance on their own after a short countdown, so the bot waits rather
# than pressing anything.  Q is a last resort for a genuine freeze only, and
# the window is deliberately long: pressing it during normal play skips the
# wave without fighting it, which earns no experience and no upgrade offers.
STAGING_PATIENCE = 45.0
# Stalls tolerated before concluding Auto Skip is off and switching it on.  Two
# is enough to rule out a one-off slow transition, and cheap to be wrong about
# because Q still works either way.
SKIPS_BEFORE_ENABLING_AUTO = 2
STAGING_MAX_TOGGLES = 2
# Attempts to switch Auto Skip on per run.  Verified by the border going green,
# so a failed click is retried rather than assumed to have worked.
# The toggle resets when a new run begins, and has also been seen switching off
# mid-run for reasons not yet identified - so this is an allowance per check,
# not a budget for the whole session.
AUTO_SKIP_ENABLE_ATTEMPTS = 40
# The toggle is re-checked on this interval rather than confirmed once: a stray
# click near it switches it off, and a run lasts many minutes.
AUTO_SKIP_RECHECK = 15.0

FINAL_WAVE = 20
# The wave counter is read off a stylised HUD label and misreads during the
# boss transition - values of 48 and 63 were logged on a 20-wave run.  Anything
# outside this range is discarded rather than recorded.
# Waves run well past 20 - wave 44 has been observed - so this is only a guard
# against garbage OCR, not a real ceiling.
MAX_SANE_WAVE = 99
MAX_WEAPONS = 5

# --------------------------------------------------------------------------
# Pick priorities (used by strategy.PriorityStrategy)
# --------------------------------------------------------------------------

# Defaults, used only when priority.txt is missing or a section is empty.
# Weapons worth taking, best first.  Anything not on this list is a candidate
# for a reroll rather than a pick.
WEAPON_PRIORITY = (
    "Ban Hammer",
    "Bananarang",
    "Ninja Star",
    "Lightning Staff",
    "Firestaff",
    "Revolver",
    "Missile",
    "Axe",
)

# Stat upgrades worth taking, best first.  Anything absent ranks neutral -
# taken when nothing better is on offer.
UPGRADE_PRIORITY = (
    "Multishot",
    "Luck",
    "Stand Strong",
    "Demon Slayer",
    "Projectile Count",
    "Armor",
    "Health",
    "Attack Speed",
    "Damage",
)

# Upgrades that only earn their priority at a particular rarity; elsewhere they
# rank neutral.
PRIORITY_ONLY_AT_RARITY = {"Multishot": "legendary"}

# Ranked below anything unlisted, but still above the avoid tier.
UPGRADE_LOW_PRIORITY = ("Piercing", "Freeze", "Ricochet", "Thorns")

# Taken only when there is nothing else at all.
UPGRADE_AVOID = ("Movement Speed",)

# Rerolls are a per-run resource and are spent exclusively on weapon offers.
MAX_REROLLS = 3
STARTING_WEAPONS = 1

# --------------------------------------------------------------------------
# Timing (seconds)
# --------------------------------------------------------------------------

# The fast path is colour-only (~3ms), so polling can be genuinely fast.
POLL_INTERVAL = 0.04
# A full OCR classify costs ~800ms, so it runs only when the screen has visibly
# changed or this long has passed - it is the safety net, not the main loop.
FULL_SCAN_INTERVAL = 2.5
CLICK_SETTLE = 0.10  # after a click, before trusting the next frame
PANEL_OPEN_TIMEOUT = 1.2  # per attempt; see PANEL_OPEN_ATTEMPTS
# Short: a real deal is colour-visible almost immediately after the skip
# click, so a longer wait only burns time on cards that are fading out.
CARD_DEAL_TIMEOUT = 1.5
STATE_WATCHDOG = 90.0
# States that can legitimately persist far longer than the watchdog window, so
# sitting in them is not evidence of a problem.
LONG_LIVED_STATES = frozenset({"in-wave", "unknown"})
BOSS_POLL_TIMEOUT = 600.0

# The pause panel is waited for, not guessed at: measured 87% of pixels below
# V=60 with it open versus 0.9% mid-wave.
DIM_VALUE_THRESHOLD = 60
DIM_MIN_FRACTION = 0.45

# Let the panel finish animating in after it is first detected.
PANEL_SNAP_DELAY = 0.12
# A complete panel has 22 rows.  Anything well short of that is a partial
# render, and diffing a partial snapshot invents stat changes that never
# happened - so it is rejected rather than recorded.
MIN_STAT_ROWS = 18

# Input timing.  Small jitter and short delays: fast, but not a single
# instantaneous teleport-and-click.
CLICK_JITTER_PX = 3
CLICK_DELAY_RANGE = (0.015, 0.030)

# Cursor motion.  Roblox decides what is hovered from the stream of mouse-move
# events, so a teleport-then-click can press before anything registers the
# cursor arriving - the cause of gear clicks silently vanishing.  The glide is
# interpolated and eased, but takes a fixed ~35ms no matter how far it travels:
# step count scales with distance instead of duration.
MOVE_DURATION = 0.035
MOVE_MIN_STEPS = 8
MOVE_MAX_STEPS = 22
MOVE_PX_PER_STEP = 45
MOVE_MAX_BOW_PX = 18  # perpendicular arc, so the path is not ruler-straight

# Absolute SendInput coordinates are quantised across the virtual desktop, so
# the cursor can land a pixel or two off; re-issue until it is exact.
MOVE_CORRECTIONS = 3

# The glide now supplies the hover time, so the pre-press pause is short.  The
# hold still spans several frames, which is what the game samples.
MOVE_DWELL = 0.025
CLICK_HOLD_RANGE = (0.045, 0.070)
# Dismissal clicks (skipping the deal animation) still glide, but skip the
# dwell and the post-click settle.
FAST_CLICK_HOLD = 0.025

# Card picks are burst-clicked: the card is only takeable during a short
# settling window, and one press timed a frame early is simply dropped.  Five
# presses over ~250ms cover the window instead of betting on one instant.
CARD_CLICK_BURST = 5
BURST_HOLD = 0.030
BURST_GAP = 0.020

# Clicking the gear right after picking a card is often ignored while the game
# settles, so retry quickly rather than waiting out one long timeout.
PANEL_OPEN_ATTEMPTS = 4
# The game ignores input briefly while a level-up resolves; reaching for the
# gear immediately had all four attempts swallowed and cost the measurement.
POST_PICK_SETTLE = 0.35

# Any of these stops the bot instantly.  Shift+\ is the quick one to reach
# without looking; the chord is a fallback in case an app grabs the first.
ABORT_HOTKEYS = ("shift+\\", "ctrl+shift+q")
ABORT_HOTKEY = ABORT_HOTKEYS[0]

# --------------------------------------------------------------------------
# Click tracing (--trace)
# --------------------------------------------------------------------------

# How long to let the screen react before deciding a click did nothing.
TRACE_SETTLE = 0.25
# Mean per-pixel change in the 32x32 digest that counts as "something happened".
# Gameplay animates constantly, so this is deliberately low - it is meant to
# catch a screen that is *identical*, not to measure how big the reaction was.
TRACE_CHANGE_THRESHOLD = 2.0

# Taking a card always visibly changes the screen.  Measured: a real pick moves
# the digest by tens of points, while clicking the post-death results screen
# (which passes the colour checks) moved it by 0.3-1.4.
PICK_EFFECT_THRESHOLD = 3.0
