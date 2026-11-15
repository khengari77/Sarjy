from common import *


class S02Assignment(TimedScene):
    SCENE_KEY = "s02_assignment"

    def construct(self):
        self.add(tag("Part A  |  Why"))
        head = title("The assignment: pick one deep dive")
        self.play(FadeIn(head, shift=DOWN * 0.2), run_time=1)
        names = [
            "Latency",
            "UI/UX and multimodal",
            "Guardrails",
            "Multistep workflows",
            "Multiplayer",
            "Something else",
        ]
        chips = VGroup(*[box(n, MUTED, w=3.9, h=1.0, size=30) for n in names]).arrange_in_grid(2, 3, buff=0.35)
        chips.shift(UP * 0.3)
        self.play(LaggedStart(*[FadeIn(c, scale=0.9) for c in chips], lag_ratio=0.12), run_time=2.5)
        self.sync(0.35)
        chosen = chips[5]
        others = VGroup(*chips[:5])
        self.play(others.animate.set_opacity(0.18), chosen[0].animate.set_stroke(TEAL), run_time=1.2)
        self.sync(0.5)
        pick = txt("My deep dive: two differentiators", size=38, color=TEAL)
        pick.next_to(head, DOWN, buff=0.5)
        self.play(FadeOut(chips), FadeIn(pick, shift=UP * 0.2), run_time=1)
        a = box("1  Memory control", TEAL, w=6.0, h=1.4, size=38).move_to(LEFT * 3.4 + DOWN * 0.6)
        b = box("2  Voice UI commands", VIOLET, w=6.0, h=1.4, size=38).move_to(RIGHT * 3.4 + DOWN * 0.6)
        self.play(FadeIn(a, shift=UP * 0.3), run_time=1)
        self.sync(0.75)
        self.play(FadeIn(b, shift=UP * 0.3), run_time=1)
        self.finish()
