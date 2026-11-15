from common import *


class S01Title(TimedScene):
    SCENE_KEY = "s01_title"
    SHOW_LOGO = False

    def construct(self):
        orb = cloud(1.5)
        ring = Circle(radius=1.5, stroke_color=CLOUD[4], stroke_width=2).set_opacity(0.5)
        name = txt("Sarjy", size=96, weight=BOLD)
        sub = txt("Voice Context Studio", size=40, color=MUTED)
        VGroup(name, sub).arrange(DOWN, buff=0.3).shift(DOWN * 0.9)
        orb.shift(UP * 1.6)
        ring.move_to(orb)
        self.play(FadeIn(orb, scale=0.6), run_time=1.2)
        self.play(ring.animate.scale(1.35).set_opacity(0), Rotate(orb, angle=PI / 3), run_time=1.6)
        self.play(Write(name), run_time=1.2)
        self.play(FadeIn(sub, shift=UP * 0.2), run_time=1.0)
        self.finish()
