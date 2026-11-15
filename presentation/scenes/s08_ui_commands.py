from common import *


class S08UICommands(TimedScene):
    SCENE_KEY = "s08_ui_commands"

    def construct(self):
        self.add(tag("Part B  |  Voice UI commands"))
        head = title("A spoken request, routed")
        self.play(FadeIn(head), run_time=0.8)

        say = box('"Talk a bit\nquicker"', TEAL, w=3.3, h=1.4, size=28).move_to(LEFT * 5.2 + UP * 0.2)
        s1 = box("1  Exact phrase", AMBER, w=3.6, h=1.1, size=30).move_to(LEFT * 1.0 + UP * 1.5)
        s2 = box("2  Classifier (Jev)", VIOLET, w=3.6, h=1.1, size=30).move_to(LEFT * 1.0 + UP * 0.1)
        s3 = box("3  Main model", BLUE, w=3.6, h=1.1, size=30).move_to(LEFT * 1.0 + DOWN * 1.3)
        out_cmd = box("Command runs", GREEN, w=3.4, h=1.3, size=30).move_to(RIGHT * 4.9 + UP * 0.8)
        out_chat = box("Normal reply", MUTED, w=3.4, h=1.3, size=30).move_to(RIGHT * 4.9 + DOWN * 1.3)

        self.play(FadeIn(say, shift=RIGHT * 0.2), run_time=0.9)
        self.play(GrowArrow(arrow(say.get_right(), s1.get_left())), FadeIn(s1), run_time=0.9)
        d1 = arrow(s1.get_bottom(), s2.get_top())
        n1 = txt("no match", size=22, color=MUTED).next_to(d1, RIGHT, buff=0.15)
        self.sync(0.14)
        self.play(GrowArrow(d1), FadeIn(n1), FadeIn(s2), run_time=0.9)
        d2 = arrow(s2.get_bottom(), s3.get_top())
        n2 = txt("not confident", size=22, color=MUTED).next_to(d2, RIGHT, buff=0.15)
        self.sync(0.26)
        self.play(GrowArrow(d2), FadeIn(n2), FadeIn(s3), run_time=0.9)
        self.sync(0.38)
        o1 = arrow(s1.get_right(), out_cmd.get_left() + UP * 0.3, GREEN)
        o2 = arrow(s2.get_right(), out_cmd.get_left() + DOWN * 0.3, GREEN)
        o3 = arrow(s3.get_right(), out_chat.get_left(), MUTED)
        self.play(GrowArrow(o1), GrowArrow(o2), FadeIn(out_cmd), run_time=1.0)
        self.play(GrowArrow(o3), FadeIn(out_chat), run_time=0.9)
        self.sync(0.52)

        key = (
            VGroup(
                txt("Exact phrases: fast, no model call", size=26, color=AMBER),
                txt("Classifier: paraphrases; one call, seven questions, 85% confidence", size=26, color=VIOLET),
                txt("Anything unsure falls through to the model", size=26, color=MUTED),
            )
            .arrange(DOWN, aligned_edge=LEFT, buff=0.2)
            .shift(DOWN * 3.2)
        )
        key.scale_to_fit_width(min(key.width, 12.8)).move_to(DOWN * 3.25)
        self.play(FadeIn(key, shift=UP * 0.15), run_time=1.0)
        self.sync(0.70)
        self.play(FadeOut(key), run_time=0.6)

        eff = (
            VGroup(
                *[
                    box(n, c, w=3.1, h=0.9, size=26)
                    for n, c in [
                        ("Theme", TEAL),
                        ("Captions", TEAL),
                        ("Speed +/- 0.1", TEAL),
                        ("Voice (next call)", AMBER),
                    ]
                ]
            )
            .arrange(RIGHT, buff=0.25)
            .shift(DOWN * 3.3)
        )
        lab = txt("Result: spoken line + typed action sent to the page", size=28, color=GREEN).next_to(
            eff, UP, buff=0.25
        )
        self.play(FadeIn(lab), LaggedStart(*[FadeIn(e, shift=UP * 0.15) for e in eff], lag_ratio=0.3), run_time=2.4)
        self.finish()
