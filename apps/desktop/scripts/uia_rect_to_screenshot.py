"""Convert UIA BoundingRectangle (physical px) to computer-use screenshot px.

UIA reports physical pixels; the desktop MCP screenshot and click/drag arguments use
logical window-relative pixels. On a display scaled to `--scale`, the mapping is:

    screenshot = (physical / scale - window_origin) * (screenshot_size / window_size)
"""

import argparse
import re
import sys

RECT = re.compile(r"x=(-?\d+) y=(-?\d+) w=(-?\d+) h=(-?\d+)")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("dump", help="file with 'name|x|y=W h=H|center=...' lines")
    parser.add_argument("--scale", type=float, default=1.25)
    parser.add_argument("--origin", default="108,0", help="window origin in logical px")
    parser.add_argument("--window", default="1320,816")
    parser.add_argument("--shot", default="1240,768")
    args = parser.parse_args()

    ox, oy = (float(v) for v in args.origin.split(","))
    ww, wh = (float(v) for v in args.window.split(","))
    sw, sh = (float(v) for v in args.shot.split(","))

    for line in open(args.dump, encoding="utf-8", errors="replace"):
        m = RECT.search(line)
        if not m:
            continue
        x, y, w, h = (int(v) for v in m.groups())
        cx, cy = x + w / 2, y + h / 2
        out = [
            "%.0f,%.0f" % ((cx / args.scale - ox) * (sw / ww), (cy / args.scale - oy) * (sh / wh)),
            "%.0f,%.0f" % ((x / args.scale - ox) * (sw / ww), (y / args.scale - oy) * (sh / wh)),
            "%.0f,%.0f" % ((w / args.scale) * (sw / ww), (h / args.scale) * (sh / wh)),
        ]
        label = line.split("|")[1].strip().encode("ascii", "replace").decode()
        sys.stdout.write("center=%s topleft=%s size=%s  %s\n" % (out[0], out[1], out[2], label))


if __name__ == "__main__":
    main()
