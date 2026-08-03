"""Run every detector against a saved PNG and show what it saw.

This is the debugging loop for the whole vision layer: capture a frame with
tools/grab.py, run it through here, read the JSON and look at the overlay.  No
game required, so a misdetection can be reproduced and fixed offline.

    python -m tools.inspect sources/legendary.png
    python -m tools.inspect runs/20260731-1200/003-stuck-unknown.png --no-overlay
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import cv2

from swarmbot import config, templates
from swarmbot.capture import load_image, save_image
from swarmbot.state import classify
from swarmbot.vision import cards as cards_vision, hud, stats as stats_vision

GREEN = (80, 220, 80)
RED = (60, 60, 230)
BLUE = (230, 170, 60)
YELLOW = (60, 220, 230)


def _draw(image, box, colour, caption: str) -> None:
    cv2.rectangle(image, (box.x, box.y), (box.x2, box.y2), colour, 2)
    y = max(14, box.y - 6)
    cv2.putText(image, caption, (box.x, y), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 0, 0), 4)
    cv2.putText(image, caption, (box.x, y), cv2.FONT_HERSHEY_SIMPLEX, 0.55, colour, 1)


def inspect(path: Path, overlay: bool = True) -> dict:
    frame = load_image(path)
    height, width = frame.shape[:2]
    report: dict = {"file": str(path), "size": [width, height]}
    vis = frame.copy()

    observation = classify(frame)
    report["state"] = observation.state.value
    report["wave"] = observation.wave

    for name, box in observation.buttons.items():
        report.setdefault("buttons", {})[name] = box.as_dict()
        if overlay:
            _draw(vis, box, BLUE, name)

    offers = observation.offers or cards_vision.detect(frame)
    if offers is not None:
        report["offers"] = offers.as_dict()
        if overlay:
            _draw(vis, offers.title_box, YELLOW, "UPGRADE OFFERS")
            for card in offers.cards:
                caption = f"{card.index}:{card.title}/{card.rarity}"
                if card.is_new:
                    caption += " NEW"
                if card.arrows:
                    caption += " " + "^" * card.arrows
                _draw(vis, card.box, GREEN, caption)
            sx, sy = cards_vision.safe_side_point(frame)
            cv2.drawMarker(vis, (sx, sy), RED, cv2.MARKER_TILTED_CROSS, 24, 2)

    panel = stats_vision.parse_panel(frame)
    if panel:
        report["stats"] = {k: v.as_dict() for k, v in panel.items()}
        report["stats_attributes"] = len(stats_vision.attributes_only(panel))

    gear = templates.find_gear(frame)
    if gear is not None:
        report["gear"] = gear.as_dict()
        if overlay:
            _draw(vis, gear.box, RED, f"gear {gear.score:.2f}")

    report["text"] = [
        {"text": i.text, "conf": round(i.confidence, 2), "box": i.box.as_dict()}
        for i in sorted(hud.read_all(frame), key=lambda i: (i.box.y, i.box.x))
    ]

    if overlay:
        out = config.RUNS / "inspect" / f"{path.stem}-overlay.png"
        save_image(out, vis)
        report["overlay"] = str(out)

    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tools.inspect", description=__doc__.splitlines()[0])
    parser.add_argument("images", nargs="+", type=Path)
    parser.add_argument("--no-overlay", action="store_true")
    parser.add_argument("--quiet", action="store_true", help="summary only, no OCR dump")
    args = parser.parse_args(argv)

    for path in args.images:
        if not path.exists():
            print(f"missing: {path}", file=sys.stderr)
            continue
        report = inspect(path, overlay=not args.no_overlay)
        if args.quiet:
            report.pop("text", None)
        print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
