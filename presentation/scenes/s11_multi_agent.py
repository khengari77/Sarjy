from common import *


class S11MultiAgent(TimedScene):
    SCENE_KEY = "s11_multi_agent"

    def construct(self):
        self.add(tag("Part D  |  If I had more days", AMBER))
        head = title("2  Several agents in one call")
        fut = txt("Future work, not built", size=28, color=AMBER).next_to(head, DOWN, buff=0.3)
        self.play(FadeIn(head), FadeIn(fut), run_time=0.9)

        you = box("You ask", TEAL, w=2.6, h=1.1, size=32).move_to(LEFT * 5.6 + DOWN * 0.7)
        router = box("Router", AMBER, w=2.6, h=1.1, size=32).move_to(LEFT * 2.1 + DOWN * 0.7)
        cols = [(BLUE, "Agent A"), (GREEN, "Agent B"), (VIOLET, "Agent C")]
        ys = [1.0, -0.7, -2.4]
        agents = VGroup(
            *[
                VGroup(cloud(0.38, seed=i + 5).set_color(c), txt(n, size=28)).arrange(RIGHT, buff=0.3)
                for i, (c, n) in enumerate(cols)
            ]
        )
        for a, y in zip(agents, ys, strict=True):
            a.move_to(RIGHT * 3.0 + UP * y)
        self.play(FadeIn(you, shift=RIGHT * 0.2), run_time=0.8)
        self.play(GrowArrow(arrow(you.get_right(), router.get_left())), FadeIn(router), run_time=0.9)
        self.sync(0.25)
        self.play(LaggedStart(*[FadeIn(a, shift=LEFT * 0.2) for a in agents], lag_ratio=0.3), run_time=1.8)
        self.sync(0.42)
        to_b = arrow(router.get_right(), agents[1].get_left() + LEFT * 0.2, AMBER)
        pick = (
            txt("Most fitting agent answers", size=28, color=AMBER).next_to(router, DOWN, buff=0.9).shift(RIGHT * 0.9)
        )
        self.play(GrowArrow(to_b), FadeIn(pick), run_time=1.0)
        self.sync(0.6)
        side1 = DashedLine(agents[1].get_top(), agents[0].get_bottom(), color=MUTED, stroke_width=4)
        side2 = DashedLine(agents[1].get_bottom(), agents[2].get_top(), color=MUTED, stroke_width=4)
        add = txt("The others add their thoughts", size=28, color=MUTED).to_edge(DOWN, buff=0.45)
        self.play(Create(side1), Create(side2), FadeIn(add), run_time=1.2)
        self.sync(0.82)
        res = (
            txt("A three- or four-way conversation", size=34, color=TEXT).next_to(head, DOWN, buff=1.0).shift(RIGHT * 0)
        )
        self.play(FadeOut(fut), run_time=0.3)
        self.play(FadeIn(res), run_time=0.8)
        self.finish()
