from common import *


class S10Personas(TimedScene):
    SCENE_KEY = "s10_personas"

    def construct(self):
        self.add(tag("Part D  |  If I had more days", AMBER))
        head = title("1  Controllable personas")
        fut = txt("Future work, not built", size=28, color=AMBER).next_to(head, DOWN, buff=0.3)
        self.play(FadeIn(head), FadeIn(fut), run_time=0.9)

        sarjy = cloud(0.9).shift(RIGHT * 4.9 + DOWN * 0.3)
        sname = txt("Sarjy", size=34).next_to(sarjy, DOWN, buff=0.3)
        ui = box("Instruction box", TEAL, w=3.6, h=1.1, size=30).move_to(LEFT * 4.9 + UP * 0.5)
        vo = box('Voice: "become a ..."', BLUE, w=4.2, h=1.1, size=26).move_to(LEFT * 4.9 + DOWN * 1.2)
        inst = box("Added to\nSarjy's instructions", VIOLET, w=3.4, h=1.5, size=26).move_to(RIGHT * 0.2 + DOWN * 0.3)
        a1 = arrow(ui.get_right(), inst.get_left() + UP * 0.3, TEAL)
        a2 = arrow(vo.get_right(), inst.get_left() + DOWN * 0.3, BLUE)
        a3 = arrow(inst.get_right(), sarjy.get_left() + LEFT * 0.1, VIOLET)
        self.play(FadeIn(sarjy), FadeIn(sname), run_time=1.0)
        self.play(FadeIn(ui, shift=RIGHT * 0.2), run_time=0.9)
        self.sync(0.28)
        self.play(FadeIn(vo, shift=RIGHT * 0.2), run_time=0.9)
        self.sync(0.46)
        self.play(GrowArrow(a1), GrowArrow(a2), FadeIn(inst), run_time=1.1)
        self.play(GrowArrow(a3), run_time=0.7)
        self.sync(0.66)
        chips = (
            VGroup(
                *[
                    txt(t, size=30, color=c)
                    for t, c in [("Playful peer", GREEN), ("Calm coach", BLUE), ("Direct editor", AMBER)]
                ]
            )
            .arrange(RIGHT, buff=0.8)
            .to_edge(DOWN, buff=0.7)
        )
        self.play(LaggedStart(*[FadeIn(c, shift=UP * 0.15) for c in chips], lag_ratio=0.4), run_time=2.0)
        self.play(
            *[
                sarjy[i].animate.set_fill(c, opacity=0.55)
                for i, c in enumerate([GREEN, TEAL, GREEN, BLUE, TEAL, GREEN])
            ],
            run_time=1.5,
        )
        self.finish()
