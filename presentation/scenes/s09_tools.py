from common import *


class S09Tools(TimedScene):
    SCENE_KEY = "s09_tools"

    def construct(self):
        self.add(tag("Part B  |  Tools"))
        head = title("The model calls tools")
        self.play(FadeIn(head), run_time=0.8)

        model = box("Model", VIOLET, w=3.0, h=1.3, size=36).move_to(LEFT * 5.0 + DOWN * 0.5)
        tools = [
            ("Weather", "live city data\nfrom Open-Meteo", BLUE),
            ("Memory", "propose, forget,\nrecall", GOLD),
            ("Wordle", "play, guess,\nstatus, close", GREEN),
        ]
        tb = (
            VGroup(*[box(n, c, w=3.3, h=1.1, size=34) for n, _, c in tools])
            .arrange(DOWN, buff=0.7)
            .move_to(RIGHT * 0.2 + DOWN * 0.5)
        )
        notes = VGroup(
            *[
                txt(s, size=26, color=MUTED, line_spacing=0.9).next_to(tb[i], RIGHT, buff=0.4)
                for i, (_, s, _) in enumerate(tools)
            ]
        )
        self.play(FadeIn(model), run_time=0.8)
        for i in range(3):
            self.play(
                GrowArrow(arrow(model.get_right(), tb[i].get_left(), tools[i][2])),
                FadeIn(tb[i]),
                FadeIn(notes[i]),
                run_time=1.0,
            )
            self.sync(0.14 + 0.1 * i)
        self.sync(0.42)
        side = txt("The server runs each tool; the model only asks", size=30, color=TEXT).to_edge(DOWN, buff=0.4)
        self.play(FadeIn(side, shift=UP * 0.15), run_time=0.9)
        self.sync(0.6)
        self.play(
            FadeOut(VGroup(model, tb, notes, side, *[m for m in self.mobjects if isinstance(m, Arrow)])), run_time=0.8
        )

        # Wordle detail.
        sub = txt("Wordle: live popup", size=38, color=GREEN).next_to(head, DOWN, buff=0.4)
        wrk = box("Worker", VIOLET, w=3.0, h=1.2, size=32).move_to(LEFT * 4.8 + UP * 0.4)
        api = box("API\nowns the game", AMBER, w=3.6, h=1.5, size=28).move_to(RIGHT * 4.6 + UP * 0.4)
        web = box("Web popup", TEAL, w=3.4, h=1.2, size=32).move_to(DOWN * 1.8)
        a1 = arrow(wrk.get_right(), api.get_left(), AMBER)
        t1 = txt("guess", size=24, color=AMBER).next_to(a1, UP, buff=0.1)
        a2 = arrow(wrk.get_bottom(), web.get_left(), TEAL)
        t2 = txt("open / refresh", size=24, color=TEAL).next_to(a2, LEFT, buff=0.1).shift(DOWN * 0.3 + RIGHT * 0.6)
        a3 = arrow(web.get_right(), api.get_bottom(), AMBER)
        t3 = txt("fetch the board", size=24, color=AMBER).next_to(a3, RIGHT, buff=0.1).shift(DOWN * 0.3 + LEFT * 0.4)
        self.play(FadeIn(sub), FadeIn(wrk), FadeIn(api), run_time=1.0)
        self.play(GrowArrow(a1), FadeIn(t1), run_time=0.8)
        self.play(GrowArrow(a2), FadeIn(web), FadeIn(t2), run_time=1.0)
        self.sync(0.80)
        self.play(GrowArrow(a3), FadeIn(t3), run_time=0.8)
        nb = txt("The message carries no board: the popup fetches it", size=28, color=MUTED).to_edge(DOWN, buff=0.5)
        self.sync(0.9)
        self.play(FadeIn(nb), run_time=0.8)
        self.finish()
