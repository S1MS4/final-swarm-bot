"""Hotkey screenshot dumper for the game window.

Used to collect reference frames for screens we have no image of yet - the
pause/Resume panel, "Auto Skip", "YOU DIED"/"GIVE UP", "VICTORY", "AGAIN".  Play
normally, tap F9 at each screen, then point tools/inspect.py at the results.

    python -m tools.grab
    python -m tools.grab --interval 2      # also grab automatically every 2s

Keys:
    F9   grab now
    F10  grab and immediately print what the detectors make of it
    ESC  quit
"""

from __future__ import annotations

import argparse
import time
from datetime import datetime
from pathlib import Path

import keyboard

from swarmbot import config
from swarmbot.capture import Capturer, save_image
from swarmbot.state import classify
from swarmbot.window import WindowNotFound, enable_dpi_awareness, find_game_window, list_windows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tools.grab", description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=config.SOURCES / "captures")
    parser.add_argument("--interval", type=float, default=None,
                        help="also grab automatically every N seconds")
    parser.add_argument("--window", default=None, help="window title substring override")
    args = parser.parse_args(argv)

    enable_dpi_awareness()
    try:
        window = find_game_window(args.window)
    except WindowNotFound as exc:
        print(exc)
        print("\nVisible windows:")
        for _, title, (w, h) in list_windows()[:15]:
            print(f"  {w:5d}x{h:<5d} {title}")
        return 2

    args.out.mkdir(parents=True, exist_ok=True)
    rect = window.client_rect
    print(f"Window : {window.title} ({rect.width}x{rect.height})")
    print(f"Saving : {args.out}")
    print("F9 grab | F10 grab+inspect | ESC quit\n")

    state = {"n": 0, "quit": False, "pending": None}

    def request(mode: str) -> None:
        state["pending"] = mode

    keyboard.add_hotkey("f9", request, args=("grab",))
    keyboard.add_hotkey("f10", request, args=("inspect",))
    keyboard.add_hotkey("esc", lambda: state.__setitem__("quit", True))

    next_auto = time.monotonic() + args.interval if args.interval else None

    try:
        with Capturer(window) as capturer:
            while not state["quit"]:
                mode = state["pending"]
                auto_due = next_auto is not None and time.monotonic() >= next_auto
                if mode is None and not auto_due:
                    time.sleep(0.05)
                    continue
                state["pending"] = None
                if auto_due:
                    next_auto = time.monotonic() + args.interval

                frame = capturer.grab()
                state["n"] += 1
                stamp = datetime.now().strftime("%H%M%S")
                path = args.out / f"{stamp}-{state['n']:03d}.png"
                save_image(path, frame)
                print(f"  saved {path.name}  ({frame.shape[1]}x{frame.shape[0]})")

                if mode == "inspect":
                    obs = classify(frame)
                    print(f"     state={obs.state.value} wave={obs.wave} "
                          f"buttons={sorted(obs.buttons) or '-'}")
                    if obs.offers:
                        for card in obs.offers.cards:
                            print(f"     card{card.index}: {card.title} / {card.rarity} "
                                  f"new={card.is_new} footer={card.footer_stat}")
    except KeyboardInterrupt:
        pass
    finally:
        keyboard.unhook_all_hotkeys()

    print(f"\n{state['n']} frames saved to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
