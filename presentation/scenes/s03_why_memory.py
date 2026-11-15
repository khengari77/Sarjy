from common import *


class S03WhyMemory(TimedScene):
    SCENE_KEY = "s03_why_memory"

    def construct(self):
        self.add(tag("Part A  |  Why memory control"))
        head = title("Why memory control?")
        self.play(FadeIn(head), run_time=0.8)

        # 1. Other chatbots: opaque.
        bot = box("Chatbot memory", MUTED, w=4.6, h=1.4, size=36).move_to(LEFT * 3.3 + UP * 0.3)
        q = txt("?", size=80, color=AMBER).next_to(bot, RIGHT, buff=0.5)
        lines = (
            VGroup(
                txt("What is saved?", size=34, color=MUTED),
                txt("What is not?", size=34, color=MUTED),
                txt("No fine-grained control", size=34, color=RED),
            )
            .arrange(DOWN, aligned_edge=LEFT, buff=0.3)
            .next_to(q, RIGHT, buff=0.5)
        )
        self.play(FadeIn(bot, shift=RIGHT * 0.3), run_time=0.9)
        self.play(Write(q), run_time=0.8)
        self.play(LaggedStart(*[FadeIn(l, shift=LEFT * 0.2) for l in lines], lag_ratio=0.5), run_time=2.5)
        self.sync(0.25)
        cap = txt("Every chatbot I tested", size=32, color=MUTED).to_edge(DOWN, buff=0.7)
        self.play(FadeIn(cap), run_time=0.6)
        self.sync(0.3)
        self.play(FadeOut(VGroup(bot, q, lines, cap)), run_time=0.8)

        # 2. Sarjy: consent flow.
        sub = txt("Sarjy: explicit consent", size=38, color=TEAL).next_to(head, DOWN, buff=0.4)
        self.play(FadeIn(sub), run_time=0.6)
        a = box("Sarjy proposes", AMBER, w=3.7, h=1.2, size=34)
        b = box("You say yes", TEAL, w=3.7, h=1.2, size=34)
        c = box("Saved", TEAL, w=3.0, h=1.2, size=34)
        row = VGroup(a, b, c).arrange(RIGHT, buff=1.0).shift(UP * 0.3)
        arrows = [arrow(a.get_right(), b.get_left()), arrow(b.get_right(), c.get_left())]
        self.play(FadeIn(a), run_time=0.7)
        self.play(GrowArrow(arrows[0]), FadeIn(b), run_time=0.9)
        self.play(GrowArrow(arrows[1]), FadeIn(c), run_time=0.9)
        self.sync(0.5)
        ctl = (
            VGroup(
                box("Hide", VIOLET, w=2.6, h=1.0, size=32),
                box("Undo", VIOLET, w=2.6, h=1.0, size=32),
                box("Delete (confirm)", RED, w=4.2, h=1.0, size=32),
            )
            .arrange(RIGHT, buff=0.6)
            .shift(DOWN * 1.5)
        )
        self.play(LaggedStart(*[FadeIn(x, shift=UP * 0.2) for x in ctl], lag_ratio=0.4), run_time=2.4)
        self.sync(0.65)
        poc = txt("Proof that memory CAN be controlled", size=34, color=TEXT).to_edge(DOWN, buff=0.6)
        self.play(FadeIn(poc, shift=UP * 0.2), run_time=0.8)
        self.sync(0.74)
        self.play(FadeOut(VGroup(sub, row, *arrows, ctl, poc)), run_time=0.8)

        # 3. Real product: policy dial (future).
        sub2 = txt("In a real product", size=38, color=AMBER).next_to(head, DOWN, buff=0.4)
        line = txt("The model decides what to remember, under a policy you tune", size=32).shift(UP * 0.7)
        if line.width > 12.5:
            line.scale_to_fit_width(12.5)
        pos = [
            box("Off", MUTED, w=2.6, h=1.1, size=34),
            box("Ask first", MUTED, w=3.2, h=1.1, size=34),
            box("Automatic", MUTED, w=3.4, h=1.1, size=34),
        ]
        dial = VGroup(*pos).arrange(RIGHT, buff=0.5).shift(DOWN * 0.8)
        future = txt("Future work, not built", size=28, color=AMBER).next_to(dial, DOWN, buff=0.6)
        self.play(FadeIn(sub2), FadeIn(line, shift=UP * 0.2), run_time=1.0)
        self.play(LaggedStart(*[FadeIn(p) for p in pos], lag_ratio=0.3), run_time=1.2)
        self.play(FadeIn(future), pos[1][0].animate.set_stroke(AMBER), run_time=1.0)
        self.finish()
