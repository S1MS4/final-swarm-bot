"""The autonomous run loop.

Flow of one run:

    in-wave  ->  upgrade offers  ->  pick  ->  gear  ->  read stats  ->  resume
             ->  ...repeat...    ->  wave 20  ->  Auto Skip  ->  boss
             ->  YOU DIED -> GIVE UP  ->  VICTORY  ->  AGAIN  ->  next run

Two invariants hold everywhere:

  * **Never click blind.**  Every click target comes from the frame captured
    immediately before it.  If a target is missing, the bot waits and looks
    again rather than clicking a remembered coordinate.
  * **Never stall silently.**  Every wait has a deadline; on expiry the frame is
    dumped to runs/ and the loop re-orients instead of hanging.

Run with --dry-run to exercise all detection and decision-making against a live
game while every click is only logged.
"""

from __future__ import annotations

import argparse
import sys
import time
from dataclasses import dataclass, field

import cv2
import keyboard
import numpy as np

from . import config, priorities, strategy as strategy_mod, templates
from .capture import Capturer, Occluded, save_image
from .logbook import Logbook
from .mouse import Aborted, AbortSwitch, FocusLost, Mouse
from .state import GameState, Observation, classify
from .table import UpgradeTable
from .layout import LayoutCache
from .vision import cards as cards_vision, fastpath, hud, stats as stats_vision
from .vision.stats import StatValue


class Stalled(RuntimeError):
    """A state did not resolve before its deadline."""


def _card_band(frame: np.ndarray, boxes) -> np.ndarray:
    """The strip spanning all three cards - what to watch for settling."""
    x1 = max(0, min(b.x for b in boxes))
    x2 = min(frame.shape[1], max(b.x2 for b in boxes))
    y1 = max(0, min(b.y for b in boxes))
    y2 = min(frame.shape[0], max(b.y2 for b in boxes))
    return frame[y1:y2, x1:x2]


# Screens that are certainly *not* covered by an overlay, so their brightness
# is a fair sample of what this arena looks like lit.  The staging screens are
# included deliberately: a run's first offers screen arrives within seconds of
# launch, well before enough combat has been seen to measure anything.
UNDIMMED_STATES = frozenset(
    {GameState.IN_WAVE, GameState.PORTAL, GameState.AUTO_SKIP}
)


@dataclass
class Bot:
    mouse: Mouse
    capturer: Capturer
    table: UpgradeTable
    log: Logbook
    strategy: strategy_mod.PickStrategy
    abort: AbortSwitch
    run: strategy_mod.RunState
    measure: bool = True
    last_stats: dict[str, StatValue] | None = None
    runs_completed: int = 0
    layout: LayoutCache = field(default_factory=LayoutCache)
    trace: bool = False
    traced: int = 0
    # Set when the colour fast path proved wrong, forcing one full classify.
    force_classify: bool = False
    # Digest of the card strip at the moment of the last pick, so the same
    # offers screen is not picked from twice.
    picked_band: np.ndarray | None = None
    # When the staging screen was first seen, so Q is only sent if it persists.
    staging_since: float | None = None
    # When the Auto Skip border was last checked.
    auto_skip_checked: float | None = None
    # Consecutive checks that read the border as off.  One is never acted on:
    # the wave banner suppresses the green and makes an enabled toggle look
    # disabled, and the click that follows switches off the very thing it
    # was meant to guarantee.
    auto_skip_off_streak: int = 0
    # A measurement whose "after" read failed.  The upgrade is already applied
    # and the panel still shows it, so this is finished on a later attempt
    # rather than thrown away.
    pending: dict | None = None
    # Brightness of recent un-dimmed play, which is what "the world is dimmed"
    # is judged against.  Arenas are lit differently - the blue one is brighter
    # than the green one - and an absolute threshold tuned on one of them made
    # the bot blind to real offers screens on the other.
    lit_frames: list[float] = field(default_factory=list)
    # Client size of the last captured frame, so a resize can be noticed.
    last_size: tuple[int, int] | None = None

    # ------------------------------------------------------------ plumbing

    def note_lit_frame(self, frame: np.ndarray) -> None:
        """Remember how bright the world is when nothing is covering it.

        Called only for states known to be un-dimmed, so the baseline can never
        be poisoned by the very overlays it is used to detect.
        """
        self.lit_frames.append(fastpath.world_brightness(frame))
        del self.lit_frames[: -config.DIM_BASELINE_SAMPLES]

    @property
    def dim_baseline(self) -> float | None:
        """Brightest recent un-dimmed play, or None until some is seen.

        None means "abstain": at session start the arena is unknown, and
        guessing from a single frame that might itself be an overlay is how
        this went wrong before.

        **The brightest, not the median.**  Every overlay in this game
        subtracts light, so lit play is the brightest thing in a run and a
        maximum cannot be dragged down by something that is not lit play.  That
        matters because the samples are not trustworthy one by one: `classify`
        returns IN_WAVE for any frame with a readable wave number that nothing
        else claimed, so a mid-deal offers screen - dimmed, but whose cards have
        not finished drawing and so do not parse - is labelled in-wave and
        sampled.  With a median that poisoned the baseline down to roughly the
        brightness of an offers screen, after which *every* offers screen read
        as "not dimmed" and the bot went back to spending level-ups on nothing.
        Measured on the blue arena: lit play 231, an offers screen 100, so a
        baseline anywhere under 118 breaks the gate.

        Erring high is safe in a way erring low is not: too high only makes this
        gate permissive, and the heading, title-ink and card-colour checks still
        have to agree before anything is clicked.  Too low blinds the bot
        completely, which is the bug this whole mechanism exists to fix.
        """
        if not self.lit_frames:
            return None
        return float(max(self.lit_frames))

    def grab(self) -> np.ndarray:
        """Capture one frame, recovering focus if something stole it.

        An overnight run must survive a notification or an accidental alt-tab,
        so occlusion actively tries to bring the game back rather than waiting
        forever for a human to do it.
        """
        self.abort.check()
        complained = False
        while True:
            try:
                frame = self.capturer.grab(strict=True)
            except Occluded as exc:
                if not complained:
                    self.log.event("occluded", detail=str(exc))
                    complained = True
                self.window.focus()
                self.abort.sleep(0.5)
                continue
            if complained:
                self.log.event("refocused")

            # A resize invalidates every remembered coordinate.  `for_frame`
            # drops the card, gear and Resume boxes, but `picked_band` is a
            # digest taken through the *old* boxes and would otherwise be
            # compared against one taken through the new ones.  Logged because
            # "it stops working when I resize the window" is a report that
            # cannot be diagnosed without knowing when the size actually moved.
            size = (frame.shape[1], frame.shape[0])
            if self.last_size is not None and self.last_size != size:
                self.log.event("window-resized",
                               was=f"{self.last_size[0]}x{self.last_size[1]}",
                               now=f"{size[0]}x{size[1]}")
                self.picked_band = None
                self.force_classify = True
            self.last_size = size

            self.layout.for_frame(frame)
            return frame

    @property
    def window(self):
        return self.capturer.window

    def look(self) -> tuple[np.ndarray, Observation]:
        """Capture and classify one frame.

        This is the slow path (~800ms of OCR); prefer `grab` plus a fast-path
        check when all that is needed is "are the cards up yet".
        """
        frame = self.grab()
        return frame, classify(frame)

    def wait_for(
        self,
        predicate,
        timeout: float,
        tag: str,
        poll: float = 1.0,
    ) -> tuple[np.ndarray, Observation]:
        """Poll until `predicate(observation)` holds, or raise Stalled.

        Each iteration is a full OCR classify, so `poll` defaults to a full
        second - this waits on slow events (a boss fight), never on a click.
        """
        deadline = time.monotonic() + timeout
        last: tuple[np.ndarray, Observation] | None = None
        while time.monotonic() < deadline:
            frame, obs = self.look()
            last = (frame, obs)
            if predicate(obs):
                return frame, obs
            self.abort.sleep(poll)
        if last is not None:
            path = self.log.dump_frame(last[0], f"stall-{tag}")
            self.log.error(f"timed out waiting for {tag}", state=last[1].state.value, frame=str(path))
        raise Stalled(tag)

    def click(self, point, label: str, fast: bool = False, burst: bool = False) -> None:
        x, y = point
        self.log.event("click", target=label, x=int(x), y=int(y), dry=self.mouse.dry_run)

        before = self.capturer.grab() if self.trace else None

        # Losing focus mid-click used to end the whole session.  For an
        # unattended run that is the wrong response to something as ordinary as
        # a notification stealing focus for a moment: take it back and retry.
        for attempt in range(2):
            try:
                if burst:
                    self.mouse.click_burst(int(x), int(y), label=label)
                else:
                    self.mouse.click(int(x), int(y), label, fast=fast)
                break
            except FocusLost:
                if attempt:
                    raise
                self.log.event("focus-lost", target=label)
                self.window.focus()
                self.abort.sleep(0.4)

        if not fast:
            self.abort.sleep(config.CLICK_SETTLE)

        if before is not None:
            self._trace_click(before, (int(x), int(y)), label)

    def _trace_click(self, before: np.ndarray, point, label: str) -> None:
        """Record whether a click actually did anything.

        "The click did not register" is otherwise unfalsifiable from a log: the
        bot dutifully reports sending it either way.  Comparing the screen
        before and after turns that into evidence - an unchanged screen means
        the press was swallowed, and the paired frames show what was under the
        cursor at the time.
        """
        self.abort.sleep(config.TRACE_SETTLE)
        after = self.capturer.grab()
        delta = float(np.abs(fastpath.digest(before) - fastpath.digest(after)).mean())
        registered = delta >= config.TRACE_CHANGE_THRESHOLD

        self.traced += 1
        tag = "OK" if registered else "NOCHANGE"
        self.log.event("trace", n=self.traced, target=label, delta=round(delta, 2), landed=registered)
        if not registered:
            stamp = f"{self.traced:03d}-{tag}-{label.replace(':', '_').replace('/', '_')}"
            for name, shot in (("before", before), ("after", after)):
                marked = shot.copy()
                cv2.drawMarker(marked, point, (0, 0, 255), cv2.MARKER_CROSS, 40, 3)
                cv2.circle(marked, point, 26, (0, 0, 255), 2)
                save_image(self.log.directory / "trace" / f"{stamp}-{name}.png", marked)

    # -------------------------------------------------------------- stats

    def read_stats_panel(self) -> dict[str, StatValue] | None:
        """Open the gear, *snapshot* the Stats panel, resume, then parse.

        The parse is deliberately after the Resume click.  Parsing costs ~900ms
        of OCR, and doing it while the panel is open held the game paused for
        five seconds per upgrade.  Pixels do not go stale, so the snapshot is
        just as good read a second later - and the game is already running.
        """
        snapshot = self.open_panel()
        if snapshot is None:
            return None

        resume = self.layout.resume
        if resume is None:
            # First time only: locate Resume by OCR and remember where it is.
            resume = hud.find_resume(snapshot, hud.read_all(snapshot, light_text=True))
            if resume is None:
                path = self.log.dump_frame(snapshot, "no-resume")
                self.log.error("Resume button not found", frame=str(path))
                return None
            self.layout.resume = resume

        self.click((resume.cx, resume.cy), "resume")

        # Panel is closed and the game is live again; now do the slow part.
        parsed = stats_vision.parse_panel(snapshot, stats_vision.panel_box(snapshot))
        if len(parsed) < config.MIN_STAT_ROWS:
            path = self.log.dump_frame(snapshot, "partial-stats")
            self.log.error(f"Stats panel only gave {len(parsed)} rows", frame=str(path))
            return None
        self.log.event("stats", rows=len(parsed))
        return parsed

    def open_panel(self) -> np.ndarray | None:
        """Click the gear and wait until the panel is genuinely up.

        The gear click is retried: mid-combat the game sometimes swallows it,
        and a fixed delay after a swallowed click produced snapshots of live
        gameplay that were then recorded as "no stat changed".
        """
        # If a modal is already covering the screen, the panel is either open
        # or something else is - either way, clicking the gear now would either
        # be redundant or land on whatever modal is up.
        frame = self.grab()
        if fastpath.is_dimmed(frame):
            self.abort.sleep(config.PANEL_SNAP_DELAY)
            return self.grab()

        gear_box = self.layout.gear
        if gear_box is None:
            gear = templates.find_gear(frame)
            if gear is None:
                self.log.error("gear button not found")
                return None
            gear_box = self.layout.gear = gear.box

        # The first click is routinely swallowed - straight after picking a
        # card the game is still settling and ignores it - so the wait per
        # attempt is short and we simply click again, rather than burning one
        # long timeout on a click that was never going to land.
        for attempt in range(config.PANEL_OPEN_ATTEMPTS):
            self.click((gear_box.cx, gear_box.cy), "gear")
            deadline = time.monotonic() + config.PANEL_OPEN_TIMEOUT
            while time.monotonic() < deadline:
                frame = self.grab()
                if fastpath.is_dimmed(frame):
                    self.abort.sleep(config.PANEL_SNAP_DELAY)
                    return self.grab()
                self.abort.sleep(config.POLL_INTERVAL)
            self.log.event("gear-retry", attempt=attempt + 1)
        self.log.error("panel never opened")
        return None

    def _close_panel(self, obs: Observation) -> None:
        resume = obs.button("resume") or self.layout.resume
        if resume is None:
            _, obs = self.look()
            resume = obs.button("resume")
        if resume is None:
            self.log.error("Resume button not found; leaving panel open")
            return
        self.click((resume.cx, resume.cy), "resume")

    # ------------------------------------------------------------- offers

    def wait_until_still(self, timeout: float = 2.0) -> np.ndarray:
        """Return a frame only once the screen has stopped moving."""
        deadline = time.monotonic() + timeout
        previous = None
        frame = self.grab()
        while time.monotonic() < deadline:
            frame = self.grab()
            current = fastpath.digest(frame)
            if previous is not None and not fastpath.changed(previous, current, config.CARD_STABLE_DELTA):
                return frame
            previous = current
            self.abort.sleep(config.POLL_INTERVAL)
        return frame

    def solve_offers_layout(self, frame: np.ndarray | None = None) -> cards_vision.Offers | None:
        """Locate the card boxes by OCR and cache them.

        Solved from a settled frame only.  `cards.detect` rejects a frame whose
        chips are uneven or not level, so a still frame is what it takes to get
        an answer at all - and a layout solved mid-deal would stay wrong for the
        rest of the session.
        """
        frame = self.wait_until_still() if frame is None else frame
        offers = cards_vision.detect(frame)
        if offers is None:
            return None
        self.layout.remember_offers(
            frame, [c.box for c in offers.cards], offers.unit, offers.title_box
        )
        self.log.event("layout", unit=round(offers.unit, 1),
                       cards=[f"{c.box.x},{c.box.y}" for c in offers.cards])
        return offers

    def offers_closed_early(self, frame: np.ndarray) -> bool:
        """Did the offers screen go away before a pick could be made?

        Almost always benign: the tail of the previous screen is still fading
        when the loop re-enters, and it finishes disappearing mid-poll.  The
        animation-cancel press lands on the side margin, which cannot select
        anything, so nothing has been spent - this is a note, not an error.

        The baseline is still dropped, because if a queued level-up *did*
        resolve in the meantime the stored "before" no longer describes the
        character.
        """
        if fastpath.has_offers_backdrop(frame, self.dim_baseline):
            return False
        # The numbers, not just the verdict: this gate has now been wrong twice
        # for reasons only visible in what it measured, and it is the one that
        # decides whether a level-up gets spent.
        self.log.event(
            "offers-closed",
            note="screen went away before a pick",
            brightness=round(fastpath.world_brightness(frame), 1),
            baseline=round(self.dim_baseline, 1) if self.dim_baseline else None,
            samples=len(self.lit_frames),
        )
        self.force_classify = True
        self.last_stats = None
        return True

    def park_cursor(self) -> None:
        """Rest the cursor where the animation-cancel press is harmless.

        Parking *on* a card removes the last of the travel, but level-ups
        queue: the next screen deals while the cursor is still sitting on a
        card, the cards are interactive by the time the press lands, and it
        takes that card instead of merely cancelling the animation.  Measured
        at roughly one offer in seventeen - a real cost, since each one spends
        a level-up on a card nobody chose.

        The side margin cancels the animation just as well and cannot select
        anything.  The press is still sent with no movement at all; only the
        pick that follows pays a ~47ms glide, which is worth it.
        """
        if self.mouse.dry_run:
            return
        try:
            frame = self.capturer.grab()
        except Occluded:
            return
        x, y = cards_vision.safe_side_point(frame)
        try:
            self.mouse.park(x, y)
        except FocusLost:
            pass

    def handle_offers(self, frame: np.ndarray) -> None:
        """Handle one offers screen, then re-park the cursor.

        Parking is in a finally so it happens on every exit path - a pick that
        stalls or fails must still leave the cursor over a card, or the next
        animation-cancel press lands on empty ground and does nothing.
        """
        try:
            self._handle_offers(frame)
        finally:
            self.park_cursor()

    def _handle_offers(self, frame: np.ndarray) -> None:
        """Skip the deal animation immediately, then pick deliberately.

        Only the *skip* click is time-critical - it has to land as the cards
        start flying in - and its target is a fixed screen margin, so it needs
        no layout knowledge and goes out the instant any rarity colour appears.

        The pick is made from **colour only** - rarity from the card body, the
        NEW! badge from yellow blobs assigned to the nearest card - which takes
        about 3ms, so the click goes out as soon as the cards settle.  Titles
        are needed only to name rows in upgrades.json, so they are OCR'd
        afterwards from the same frame, while the game is already running.
        """
        boxes = self.layout.card_boxes
        if not boxes:
            if self.solve_offers_layout() is None:
                return
            boxes = self.layout.card_boxes

        # Cancel the deal animation without moving: the cursor is parked on the
        # middle card between offers screens, so this costs no travel at the
        # one moment where latency matters, and often none afterwards either
        # because the card we want is already under the pointer.
        self.log.event("click", target="skip-animation", inplace=True, dry=self.mouse.dry_run)
        self.mouse.press_here("skip-animation")
        self.abort.sleep(config.CARD_SETTLE_DELAY)
        frame = self.grab()

        offers = None
        settled_band = None
        deadline = time.monotonic() + config.CARD_DEAL_TIMEOUT
        while time.monotonic() < deadline:
            # Rebound every iteration, but seeded above so the post-loop
            # handling still has a frame if the deadline had already passed.
            frame = self.grab()

            # Cheap gates first, so the expensive read is only attempted on a
            # frame that stands a chance: dimmed backdrop, heading up, and all
            # three slots reading as drawn cards.
            if fastpath.offers_visible(
                frame, boxes, self.dim_baseline, self.layout.title_box
            ) is None:
                self.abort.sleep(config.FAST_POLL_INTERVAL)
                continue

            if self.picked_band is not None and not fastpath.changed(
                self.picked_band,
                fastpath.digest(_card_band(frame, boxes)),
                config.CARD_STABLE_DELTA,
            ):
                # Still the cards we already clicked; levels queue up, so wait
                # for the next deal rather than spending one without measuring.
                self.abort.sleep(config.POLL_INTERVAL)
                continue

            # Wait for the card strip to stop moving before spending OCR on it.
            # This is only a cost filter, not a correctness check - the zoom has
            # plateaus that look stable, which is why the parse below is still
            # the thing that decides.  Without it the loop burnt a ~0.6s read on
            # every frame of the animation and pick latency reached 4.3s.
            band = fastpath.digest(_card_band(frame, boxes))
            if settled_band is None or fastpath.changed(settled_band, band, config.CARD_STABLE_DELTA):
                settled_band = band
                self.abort.sleep(config.FAST_POLL_INTERVAL)
                continue

            # Then read for real.  The cards zoom in one at a time from the
            # right, and while one is still oversized no cheap test can tell:
            # title ink and edge coverage both overlap the settled case.  A
            # successful parse is the only reliable proof that every card has
            # reached its final size *and* is named, and it costs ~0.6s on a
            # screen that is waiting for the player anyway.  It also removes
            # any reliance on the cached boxes, which drift between screens.
            candidate = cards_vision.detect(frame)
            if candidate is not None and all(c.title for c in candidate.cards):
                offers = candidate
                break
            self.abort.sleep(config.FAST_POLL_INTERVAL)

        if offers is None and self.offers_closed_early(frame):
            return

        if offers is None:
            # Force the slow path next iteration.  Without this the loop spins:
            # the fast check fires on stale card boxes over some *other* dimmed
            # screen - the AGAIN screen is dimmed too - finds no cards, returns,
            # and immediately re-enters the fast check.  The full classify that
            # would recognise the screen is never reached, and the bot presses
            # skip at it forever.
            self.force_classify = True
            self.log.event("no-cards", note="offers gone or never settled")
            return

        boxes = [c.box for c in offers.cards]
        self.layout.remember_offers(frame, boxes, offers.unit, offers.title_box)

        for warning in offers.warnings:
            self.log.error(warning)
        for card in offers.cards:
            self.table.note_seen(card.name, card.rarity, card.is_weapon)

        decision = self.strategy.pick(offers.cards, self.table, self.run)

        if decision.reroll:
            self.log.event(
                "reroll",
                wave=self.run.wave,
                left=self.run.rerolls_left,
                reason=decision.reason,
                options=" / ".join(f"{c.name}({c.rarity})" for c in offers.cards),
            )
            if self.do_reroll(frame):
                self.run.rerolls_left -= 1
            # Deliberately no picked_band here: the reroll deals a fresh set,
            # and the loop should treat it as a new offers screen.
            return

        chosen = offers.cards[decision.index]

        # The table is the authority on what still needs measuring, and it only
        # ever grows: once an upgrade+rarity has a usable measurement, that
        # combination never needs the gear/read/resume cycle again, no matter
        # how many runs later it is offered.
        #
        # The NEW! badge is deliberately *not* used here.  It says the game has
        # not seen the combination before, which is a different question from
        # whether *we* have already measured it - after a restart the badges
        # stay while the table persists, and re-opening the panel for a row we
        # already hold is the single most expensive thing the bot can waste
        # time on.
        worth_measuring = self.measure and not self.table.has_measurement(
            chosen.title, chosen.rarity
        )

        self.log.event(
            "offers",
            wave=self.run.wave,
            options=" / ".join(
                f"{c.name}({c.rarity}){'*' if c.is_new else ''}" for c in offers.cards
            ),
            chose=chosen.key,
            reason=decision.reason,
            measure=worth_measuring,
        )

        # A diff needs a "before".  If the baseline was dropped by an earlier
        # skipped pick, re-establish it now, while the offers screen still
        # holds the game - after clicking, this upgrade's effect is already
        # baked in and the sample would be lost.
        if worth_measuring and self.last_stats is None:
            self.last_stats = self.read_stats_panel()
        before = self.last_stats

        before_click = fastpath.digest(frame)
        self.click(chosen.click_point, f"card{decision.index}({chosen.rarity})", burst=True)
        self.run.picks += 1
        self.picked_band = fastpath.digest(_card_band(frame, boxes))

        # Taking a card always changes the screen.  If nothing moved, this was
        # never an offers screen - the results screen after GIVE UP passes the
        # colour checks and had the bot clicking into it repeatedly (measured
        # deltas of 0.3-1.4 where a real pick moves tens of points).
        if not fastpath.changed(before_click, fastpath.digest(self.grab()),
                                config.PICK_EFFECT_THRESHOLD):
            path = self.log.dump_frame(frame, "pick-ignored")
            self.log.error("pick changed nothing; not an offers screen", frame=str(path))
            self.force_classify = True
            return

        if chosen.is_weapon:
            self.run.note_weapon(chosen.title or chosen.raw_title)
            self.table.record_weapon(chosen.name, wave=self.run.wave)
            self.table.save()
            self.log.event("weapon", name=chosen.name, owned=self.run.weapon_count)
            # Weapons move no listed stat, so there is nothing to measure - but
            # the baseline is now stale, and saying so is what makes the next
            # real measurement re-read it instead of diffing against fiction.
            self.last_stats = None
            return

        if not worth_measuring:
            # Skipped the panel, so the recorded baseline no longer matches the
            # character.  Dropping it is what keeps this optimisation honest.
            self.last_stats = None
            return

        title = chosen.title or chosen.raw_title
        if title is None:
            self.log.error("picked a card with an unreadable title; not recording")
            self.last_stats = None
            return

        # The game swallows input briefly while the level-up resolves; going
        # for the gear immediately had all four attempts ignored.
        self.abort.sleep(config.POST_PICK_SETTLE)

        after = self.read_stats_panel()
        if after is None:
            # Not lost: the upgrade is applied and the panel will still show it.
            # Finish this measurement on a later, calmer attempt.
            self.pending = {
                "title": title,
                "rarity": chosen.rarity,
                "arrows": chosen.arrows,
                "footer_stat": chosen.footer_stat,
                "wave": self.run.wave,
                "before": before,
            }
            self.log.event("measurement-deferred", card=chosen.key)
            return

        self.record_measurement(
            {
                "title": title,
                "rarity": chosen.rarity,
                "arrows": chosen.arrows,
                "footer_stat": chosen.footer_stat,
                "wave": self.run.wave,
                "before": before,
            },
            after,
        )

    # ---------------------------------------------------------- end of run

    def finish_pending_measurement(self) -> None:
        """Complete a measurement whose "after" read failed earlier.

        Nothing about the diff is time-critical once the upgrade is applied -
        the Stats panel keeps showing the new values - so a swallowed gear
        click costs a retry rather than the sample.
        """
        job, self.pending = self.pending, None
        if job is None:
            return
        after = self.read_stats_panel()
        if after is None:
            self.log.error("deferred measurement failed again", card=job["title"])
            return
        self.record_measurement(job, after)

    def record_measurement(self, job: dict, after: dict[str, StatValue]) -> None:
        """Diff a before/after pair into the table."""
        before = job["before"]
        if before is None:
            self.log.event("baseline", rows=len(after))
            self.last_stats = after
            return

        deltas = stats_vision.diff(before, after)
        verdict = self.table.record_stat(
            title=job["title"],
            rarity=job["rarity"],
            deltas=deltas,
            arrows=job.get("arrows", 0),
            footer_stat=job.get("footer_stat"),
            wave=job.get("wave"),
        )
        self.table.save()
        self.log.event(
            "measured",
            card=f"{job['title']}|{job['rarity']}",
            confidence=verdict,
            changed=", ".join(f"{d.stat} {d.before.raw}->{d.after.raw}" for d in deltas) or "none",
        )
        self.last_stats = after

    def do_reroll(self, frame: np.ndarray) -> bool:
        """Spend a reroll to redraw the three offers.

        Returns whether it actually happened, so a missing or dead button does
        not silently burn one of the three from the counter.
        """
        button = hud.find_reroll(frame, hud.read_all(frame))
        if button is None:
            self.log.error("reroll button not found")
            return False

        before = fastpath.digest(frame)
        self.click((button.cx, button.cy), "reroll")
        if not fastpath.changed(before, fastpath.digest(self.grab()),
                                config.PICK_EFFECT_THRESHOLD):
            self.log.error("reroll click changed nothing")
            return False
        return True

    def auto_skip_best_green(self, button) -> float | None:
        """The strongest green reading across several frames.

        The border is only ever *hidden* by something drawn over it - the
        wave-transition banner tints the whole screen and the coverage collapses
        to nearly zero - so the reading is suppressed, never invented.  The best
        of a spread of samples is therefore the honest one: an enabled toggle
        shows its border in at least one frame, while a disabled one reads flat
        in all of them.

        Sampled live over 40 consecutive frames with the toggle untouched:
        0.000 to 0.030 on the same physical state, dipping below the threshold
        on 16 of them.  A single grab decides nothing.

        Returns None when every sample was covered by an overlay.  That is not
        the same as "off": a reading that could not be taken must not be turned
        into a click.
        """
        best = None
        for i in range(config.AUTO_SKIP_SAMPLES):
            if i:
                self.abort.sleep(config.AUTO_SKIP_SAMPLE_GAP)
            try:
                frame = self.grab()
            except Occluded:
                continue
            if not hud.auto_skip_readable(frame, button):
                continue       # washed out - carries no information either way
            best = max(best or 0.0, hud.auto_skip_green(frame, button))
            if best >= config.AUTO_SKIP_ON_GREEN:
                break          # already proved on; no reason to keep looking
        return best

    def ensure_auto_skip(self, obs: Observation) -> None:
        """Switch Auto Skip on if its border says it is off.

        With it enabled the wave countdown is skipped automatically, so the run
        moves faster and the bot never needs the Q fallback.

        The border is read with a deliberately narrow colour range.  A broad
        one matched the grass behind the button and reported a disabled toggle
        as enabled - that false reading is what convinced me to add a portal
        click, which ended runs.  Measured with the narrow range: 0.000
        disabled, 0.026 enabled.

        The click is verified by the border going green, so a swallowed press
        is retried instead of being assumed to have worked.
        """
        if self.run.auto_skip_toggles >= config.AUTO_SKIP_ENABLE_ATTEMPTS:
            return
        button = obs.button("auto_skip")
        if button is None:
            return

        # Re-checked periodically rather than latched.  Anything that clicks
        # near the toggle switches it back off, so "confirmed on once" is not
        # a safe assumption for a run lasting many minutes.
        now = time.monotonic()
        if self.auto_skip_checked is not None and now - self.auto_skip_checked < config.AUTO_SKIP_RECHECK:
            return
        self.auto_skip_checked = now

        green = self.auto_skip_best_green(button)
        if green is None:
            # Covered by the high-wave overlay.  Not evidence of anything, so
            # the streak is left where it is and nothing is clicked.
            return
        if green >= config.AUTO_SKIP_ON_GREEN:
            self.auto_skip_off_streak = 0
            if not self.run.auto_skip_ok:
                self.run.auto_skip_ok = True
                self.log.event("auto-skip", state="on", green=round(green, 4))
            return

        # Below the threshold on every sample.  Whether that is worth waiting on
        # depends entirely on what the toggle was doing before.
        #
        # Once it has been confirmed on this run, a check that says "off" is more
        # likely to be a suppressed reading than a real change - the wave banner
        # tints the border - and acting on it switches off the very thing it was
        # meant to guarantee.  That cost 23 needless toggles in one session, so
        # consecutive checks have to agree, and they are AUTO_SKIP_RECHECK apart.
        #
        # Before then there is nothing to protect.  The toggle resets with every
        # run, so "never yet seen on" means off, and waiting only burns the start
        # of phase 2 - where waves pass every ~5s, so the two-check streak cost
        # 16s and Auto Skip did not come on until wave 4.  A wrong click here is
        # cheap and self-correcting: the verification read below catches it and
        # the next check clicks again.
        if self.run.auto_skip_ok:
            self.auto_skip_off_streak += 1
            if self.auto_skip_off_streak < config.AUTO_SKIP_OFF_STREAK:
                self.log.event("auto-skip", state="maybe-off", green=round(green, 4),
                               streak=self.auto_skip_off_streak)
                return
        self.auto_skip_off_streak = 0

        self.run.auto_skip_toggles += 1
        self.click((button.cx, button.cy), "auto-skip")
        # The click leaves the cursor sitting on the toggle, and the
        # animation-cancel press fires wherever the cursor happens to be - so
        # without this the next offers screen presses the toggle straight back
        # off.  That is the same fault safe_side_point was introduced to fix,
        # reached by a different route.
        self.park_cursor()
        green = self.auto_skip_best_green(button)
        if green is None:
            # The overlay arrived before the border could be re-read.  Say so
            # rather than recording a state that was never observed.
            self.log.event("auto-skip", state="unverified",
                           attempt=self.run.auto_skip_toggles)
        elif green >= config.AUTO_SKIP_ON_GREEN:
            was_on = self.run.auto_skip_ok
            self.run.auto_skip_ok = True
            if was_on:
                self.log.error("Auto Skip had switched off again; re-enabled")
            self.log.event("auto-skip", state="enabled", green=round(green, 4),
                           attempt=self.run.auto_skip_toggles)
        else:
            self.log.event("auto-skip", state="still-off", green=round(green, 4),
                           attempt=self.run.auto_skip_toggles)

    def advance_from_staging(self, obs: Observation) -> None:
        """Nudge the run along only if it has genuinely stalled.

        Waves advance by themselves - by the wave timer, or instantly when the
        Auto Skip toggle is on - so the normal action here is to *wait*.

        Q (Skip Wave) is a recovery action, not a routine one.  Treating it as
        routine was destructive: the portal is visible from the arena during
        ordinary play, so this state fires constantly, and pressing Q on every
        sighting drove the run from wave 1 to wave 30 in about a hundred
        seconds without fighting anything.  Nothing was farmed and no upgrades
        were offered.

        So Q is only sent once the screen has sat unchanged for longer than a
        wave transition could plausibly take.  E (Enter Portal) is never sent
        at all - it ends the run.
        """
        now = time.monotonic()
        if self.staging_since is None:
            self.staging_since = now
            return

        if now - self.staging_since < config.STAGING_PATIENCE:
            return  # A wave transition in progress looks exactly like this.

        self.log.event("staging", note="stalled; sending Skip Wave",
                       waited=round(now - self.staging_since, 1), wave=self.run.wave)
        self.press_skip_wave()
        self.run.skips_used += 1
        self.staging_since = now  # give it another full window before retrying

        # Needing Q at all means Auto Skip is off - when it is on, waves
        # advance by themselves and this code never runs.  That makes the stall
        # pattern a far better signal than the toggle's green border, which is
        # unreadable against the grassy staging area.  Enable it after a couple
        # of stalls so the run stops paying ~12s per wave.
        if self.run.skips_used == config.SKIPS_BEFORE_ENABLING_AUTO and (
            self.run.auto_skip_toggles < config.STAGING_MAX_TOGGLES
        ):
            button = obs.button("auto_skip")
            if button is not None:
                self.run.auto_skip_toggles += 1
                self.log.event("auto-skip", note="repeated stalls; enabling",
                               skips=self.run.skips_used)
                self.click((button.cx, button.cy), "auto-skip")

    def press_skip_wave(self) -> None:
        """Send the Skip Wave key."""
        self.log.event("key", target="skip-wave", key=config.KEY_SKIP_WAVE)
        if self.mouse.dry_run:
            return
        try:
            keyboard.send(config.KEY_SKIP_WAVE)
        except Exception as exc:  # noqa: BLE001 - keyboard raises bare errors
            self.log.error(f"could not send the skip-wave key: {exc}")

    def handle_end_of_run(self, obs: Observation) -> bool:
        """Advance the post-wave-20 sequence.  Returns True when a run ended."""
        if obs.state is GameState.AUTO_SKIP:
            # Same situation as the staging area, just without the portal
            # prompt in view: waiting is the action, and the toggle is only
            # flipped if the wait proves it is off.
            self.advance_from_staging(obs)
            return False

        if obs.state is GameState.PORTAL:
            self.advance_from_staging(obs)
            return False

        if obs.state is GameState.DIED:
            button = obs.button("give_up")
            if button is None:
                return False
            self.click((button.cx, button.cy), "give-up")
            self.log.event("died", wave=self.run.wave)
            return False

        if obs.state is GameState.VICTORY:
            button = obs.button("again")
            if button is not None:
                self.click((button.cx, button.cy), "again")
                self.log.event("again")
                return True
            # The victory screen animates before the AGAIN button appears.
            self.click(cards_vision.safe_side_point(self.grab()), "victory-ack")
            self.log.event("victory")
            return False

        if obs.state is GameState.AGAIN:
            button = obs.button("again")
            if button is None:
                return False
            self.click((button.cx, button.cy), "again")
            self.log.event("again")
            return True

        return False

    def start_new_run(self) -> None:
        self.runs_completed += 1
        self.run = strategy_mod.RunState()
        self.auto_skip_checked = None   # the toggle resets with the run
        self.auto_skip_off_streak = 0
        self.last_stats = None
        self.log.event("run-start", number=self.runs_completed + 1)

    # ---------------------------------------------------------- main loop

    def loop(self, max_runs: int | None = None) -> None:
        """Poll cheaply; think expensively only when something changed.

        The fast check (~3ms) answers the only time-critical question - "are the
        cards up?" - so offers get clicked within a frame or two of appearing.
        The full OCR classify, which handles waves and the end-of-run sequence,
        runs on a change in the screen or on a timer, not on every iteration.
        """
        last_state: GameState | None = None
        last_change = time.monotonic()
        last_digest = None
        last_full_scan = 0.0

        while True:
            self.abort.check()

            # --- fast path -------------------------------------------------
            frame = self.grab()
            boxes = self.layout.card_boxes
            if (
                boxes
                and not self.force_classify
                and fastpath.offers_emerging(
                    frame, boxes, self.dim_baseline, self.layout.title_box
                )
            ):
                # The cards linger on screen for a moment after being clicked.
                # Without this check the loop re-entered handle_offers on the
                # screen it had just picked from and then sat out the whole
                # deal timeout waiting for cards that would never change.
                same_as_picked = self.picked_band is not None and not fastpath.changed(
                    self.picked_band,
                    fastpath.digest(_card_band(frame, boxes)),
                    config.CARD_STABLE_DELTA,
                )
                if not same_as_picked:
                    if last_state is not GameState.UPGRADE_OFFERS:
                        self.log.event("state", now="upgrade-offers", was=
                                       last_state.value if last_state else None)
                    last_state, last_change = GameState.UPGRADE_OFFERS, time.monotonic()
                    try:
                        self.handle_offers(frame)
                    except Stalled as exc:
                        self.log.error(f"recovering from stall: {exc}")
                    except FocusLost:
                        self.log.event("focus-lost", where="offers")
                        self.window.focus()
                        self.abort.sleep(0.5)
                    last_digest = None
                    continue

            # --- slow path, only when warranted ----------------------------
            now = time.monotonic()
            current = fastpath.digest(frame)
            screen_changed = fastpath.changed(last_digest, current)
            if not screen_changed and now - last_full_scan < config.FULL_SCAN_INTERVAL:
                self.abort.sleep(config.POLL_INTERVAL)
                continue
            last_digest = current
            last_full_scan = now
            self.force_classify = False

            obs = classify(frame)

            # Learn this arena's brightness from screens that are certainly not
            # dimmed.  Deriving it from the slow classify rather than the fast
            # path keeps the two from feeding each other: the fast path asks
            # "is this dimmer than usual", so "usual" must be established by
            # something that does not consult it.
            if obs.state in UNDIMMED_STATES:
                self.note_lit_frame(frame)

            if (
                obs.wave is not None
                and obs.wave != self.run.wave
                and 1 <= obs.wave <= config.MAX_SANE_WAVE
            ):
                self.run.wave = obs.wave
                self.log.event("wave", n=obs.wave)

            if obs.state is not GameState.UPGRADE_OFFERS:
                # Back in the game, so the next offers screen is genuinely new
                # even if it happens to deal the same three cards.
                self.picked_band = None

            if obs.state not in (GameState.PORTAL, GameState.AUTO_SKIP):
                self.staging_since = None

            # The toggle is on screen during ordinary waves, so this is the
            # natural place to make sure it is enabled.
            if obs.button("auto_skip") is not None:
                self.ensure_auto_skip(obs)

            if obs.state is not last_state:
                self.log.event("state", now=obs.state.value, was=last_state.value if last_state else None)
                last_state, last_change = obs.state, time.monotonic()
            elif (
                obs.state not in config.LONG_LIVED_STATES
                and time.monotonic() - last_change > config.STATE_WATCHDOG
            ):
                # IN_WAVE is excluded: with Auto Skip on, a strong character
                # clears waves for minutes without a single level-up, and
                # flagging that as "stuck" buried the real failures in noise.
                path = self.log.dump_frame(frame, f"stuck-{obs.state.value}")
                self.log.error(f"stuck in {obs.state.value}", frame=str(path))
                last_change = time.monotonic()

            # A stall is a local failure, not a session-ending one: log it, let
            # the loop re-orient from the next frame, and keep farming.
            try:
                if obs.state is GameState.UPGRADE_OFFERS:
                    # Only solve the layout when there isn't one.  Solving is
                    # slow - it waits for the screen to settle, then runs a
                    # full OCR pass - and doing it unconditionally here ate the
                    # entire pick window: by the time it finished the offers
                    # screen had closed, which set force_classify, which sent
                    # the next screen down this same slow path.  Nothing was
                    # ever picked.
                    if self.layout.card_boxes or self.solve_offers_layout() is not None:
                        self.handle_offers(self.grab())
                    last_digest = None
                elif obs.state is GameState.STATS_PANEL:
                    # The panel is open outside a measurement cycle - close it.
                    self._close_panel(obs)
                elif obs.state in (
                    GameState.AUTO_SKIP,
                    GameState.PORTAL,
                    GameState.DIED,
                    GameState.VICTORY,
                    GameState.AGAIN,
                ):
                    if self.handle_end_of_run(obs):
                        if max_runs is not None and self.runs_completed + 1 >= max_runs:
                            self.log.event("done", runs=self.runs_completed + 1)
                            return
                        self.start_new_run()
                # No baseline is taken here on purpose.  Skipping the panel for
                # an already-known upgrade clears last_stats, and a branch that
                # rebuilt it on sight of IN_WAVE fired the gear again the
                # instant every skipped pick ended - which is what made the
                # settings button appear to open at random.  handle_offers
                # already takes a baseline lazily, and only when a pick that
                # actually needs measuring is about to happen.
                else:
                    self.abort.sleep(config.POLL_INTERVAL)
            except Stalled as exc:
                self.log.error(f"recovering from stall: {exc}")
                self.abort.sleep(config.POLL_INTERVAL)
            except FocusLost:
                # Something stole the foreground.  Take it back rather than
                # ending a session that may have hours of farming left.
                self.log.event("focus-lost", where="loop")
                self.window.focus()
                self.abort.sleep(0.5)


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="swarmbot", description=__doc__.splitlines()[0])
    p.add_argument("--dry-run", action="store_true", help="detect and decide, but never click")
    p.add_argument("--trace", action="store_true",
                   help="record whether each click actually changed the screen")
    p.add_argument("--max-runs", type=int, default=None, help="stop after N completed runs")
    p.add_argument("--strategy", default=strategy_mod.PriorityStrategy.name,
                   choices=sorted(strategy_mod.STRATEGIES))
    p.add_argument("--no-measure", action="store_true",
                   help="skip the gear/Stats cycle; play only, do not build the table")
    p.add_argument("--window", default=None, help="window title substring override")
    p.add_argument("--table", default=None, help="path to upgrades.json")
    return p


def main(argv: list[str] | None = None) -> int:
    from .window import WindowNotFound, enable_dpi_awareness, find_game_window, list_windows

    args = build_parser().parse_args(argv)
    enable_dpi_awareness()

    try:
        window = find_game_window(args.window)
    except WindowNotFound as exc:
        print(f"{exc}\n\nVisible windows:", file=sys.stderr)
        for _, title, (w, h) in list_windows()[:15]:
            print(f"  {w:5d}x{h:<5d} {title}", file=sys.stderr)
        return 2

    log = Logbook.create()
    table = UpgradeTable.load(args.table)

    print(f"Window : {window.title} ({window.client_rect.width}x{window.client_rect.height})")
    print(f"Log    : {log.directory}")
    print(f"Table  : {table.path} ({len(table.upgrades)} upgrades known)")
    print(f"Picks  : {priorities.current().describe()}  ({config.PRIORITY_FILE.name})")
    print(f"Mode   : {'DRY RUN - no clicks' if args.dry_run else 'LIVE'}")
    print(f"Abort  : {config.ABORT_HOTKEY}\n")

    # Focus even for a dry run: capture reads the screen region the window
    # occupies, so an unfocused game is simply not what gets analysed.
    window.focus()
    time.sleep(0.8)
    if not window.is_foreground:
        print("Could not bring the game to the foreground - click it, then rerun.", file=sys.stderr)
        return 4

    with AbortSwitch() as abort, Capturer(window) as capturer:
        bot = Bot(
            mouse=Mouse(window=window, abort=abort, dry_run=args.dry_run),
            capturer=capturer,
            table=table,
            log=log,
            strategy=strategy_mod.get(args.strategy),
            abort=abort,
            run=strategy_mod.RunState(),
            measure=not args.no_measure,
            trace=args.trace,
        )
        log.event("start", strategy=args.strategy, dry=args.dry_run, window=window.title)
        try:
            bot.loop(max_runs=args.max_runs)
        except Aborted as exc:
            log.event("aborted", reason=str(exc))
        except FocusLost as exc:
            log.error(f"focus lost: {exc}")
            return 3
        except KeyboardInterrupt:
            log.event("interrupted")
        finally:
            table.save()
            print(f"\nTable saved to {table.path}")
            print(f"Log saved to    {log.directory}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
