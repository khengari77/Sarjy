"""Shared style and timing helpers for the Sarjy presentation scenes."""

import json
from pathlib import Path

from manim import *  # noqa: F401,F403

# Palette from apps/web/src/lib/ui/tokens.css (Living Cloud v2.1, dark "Cosmic Cloud").
BG = "#080F29"  # --bg-app
PANEL = "#19284B"  # --surface-elevated
TEXT = "#F3F6FF"  # --text-primary
MUTED = "#A6B6DC"  # --text-muted
ACCENT = "#7762F6"  # --accent
TEAL = "#4CC9B6"  # --orb-idle
BLUE = "#4DA8FF"  # --orb-listening
VIOLET = "#A684F7"  # --orb-thinking
GREEN = "#4ADD91"  # --orb-speaking
AMBER = "#FFA65A"  # --orb-confirmation
GOLD = "#E9B454"  # --orb-memory
RED = "#FF7D9D"  # --status-error
CLOUD = ["#548FFF", "#3B6FFF", "#9A74F7", "#BAA7FF", "#57D7D2", "#F67C9F"]  # --orb-palette-1..6
FONT = "Noto Sans"  # Inter (the app font) is not installed here; Noto Sans is its fallback in the app stack

_TIMING = json.loads((Path(__file__).resolve().parent.parent / "timing.json").read_text())


def duration_for(name: str) -> float:
    return float(_TIMING[name])


def txt(s, size=34, color=TEXT, weight=NORMAL, **kw):
    return Text(s, font=FONT, font_size=size, color=color, weight=weight, **kw)


def box(label, color=TEAL, w=3.4, h=1.1, size=34, fill=PANEL):
    rect = RoundedRectangle(
        corner_radius=0.18, width=w, height=h, stroke_color=color, stroke_width=3, fill_color=fill, fill_opacity=1
    )
    t = txt(label, size=size)
    if t.width > w - 0.3:
        t.scale_to_fit_width(w - 0.3)
    t.move_to(rect)
    return VGroup(rect, t)


def arrow(a, b, color=MUTED, **kw):
    return Arrow(a, b, buff=0.08, color=color, stroke_width=4, max_tip_length_to_length_ratio=0.15, **kw)


def cloud(radius=1.0, seed=3):
    """Living Cloud orb: overlapping soft blobs in the app's orb palette."""
    import random

    rnd = random.Random(seed)
    g = VGroup()
    for i, c in enumerate(CLOUD):
        r = radius * rnd.uniform(0.55, 0.8)
        ang = i * TAU / len(CLOUD)
        blob = Circle(radius=r, stroke_width=0, fill_color=c, fill_opacity=0.5)
        blob.move_to(radius * 0.32 * np.array([np.cos(ang), np.sin(ang), 0]))
        g.add(blob)
    return g


def logo():
    return cloud(0.22).to_corner(UR, buff=0.4)


def tag(text, color=MUTED):
    """Small part label, top left."""
    return txt(text, size=24, color=color).to_corner(UL, buff=0.3)


def title(text, color=TEXT, size=44):
    return txt(text, size=size, color=color, weight=BOLD).to_edge(UP, buff=1.0)


class TimedScene(Scene):
    """Scene whose beats are placed at fractions of its duration from timing.json."""

    SCENE_KEY = ""
    SHOW_LOGO = True

    def setup(self):
        self.camera.background_color = BG
        if self.SHOW_LOGO:
            self.add(logo())
        self.total = duration_for(self.SCENE_KEY)

    def now(self):
        return self.renderer.time

    def sync(self, frac):
        """Wait until `frac` of the scene duration has elapsed (no wait if already past)."""
        gap = frac * self.total - self.now()
        if gap > 0.02:
            self.wait(gap)

    def finish(self):
        gap = self.total - self.now()
        if gap > 0.02:
            self.wait(gap)
        else:
            print(f"WARNING {self.SCENE_KEY}: animations overran by {-gap:.2f}s")
