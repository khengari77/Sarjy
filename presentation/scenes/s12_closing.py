from common import *


class S12Closing(TimedScene):
    SCENE_KEY = "s12_closing"
    SHOW_LOGO = False

    def construct(self):
        orb = cloud(1.2).shift(UP * 1.2)
        t = txt("Now, Sarjy live", size=60, weight=BOLD).shift(DOWN * 1.0)
        s = txt("Voice Context Studio", size=32, color=MUTED).next_to(t, DOWN, buff=0.3)
        self.play(FadeIn(orb, scale=0.7), run_time=1.0)
        self.play(Write(t), FadeIn(s), run_time=1.5)
        self.finish()
