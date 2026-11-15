from common import *


class S05Architecture(TimedScene):
    SCENE_KEY = "s05_architecture"

    def construct(self):
        self.add(tag("Part B  |  How it works"))
        head = title("Three parts")
        self.play(FadeIn(head), run_time=0.8)

        web = box("Web app", TEAL, w=3.0, h=1.3, size=36).move_to(LEFT * 4.8 + UP * 1.2)
        api = box("API", AMBER, w=3.0, h=1.3, size=36).move_to(UP * 1.2)
        wrk = box("Voice worker", VIOLET, w=3.0, h=1.3, size=36).move_to(RIGHT * 4.8 + UP * 1.2)
        subs = [
            "SvelteKit\nmic, captions,\nmemory panel",
            "FastAPI\nstate, memory,\ncommands, limits",
            "LiveKit agent\nspeech to text,\nmodel, text to speech",
        ]
        notes = VGroup(
            *[
                txt(s, size=24, color=MUTED, line_spacing=0.9).next_to(b, DOWN, buff=0.25)
                for s, b in zip(subs, (web, api, wrk), strict=True)
            ]
        )
        self.play(FadeIn(web, shift=UP * 0.2), FadeIn(notes[0]), run_time=1.0)
        self.sync(0.2)
        self.play(FadeIn(api, shift=UP * 0.2), FadeIn(notes[1]), run_time=1.0)
        self.sync(0.35)
        self.play(FadeIn(wrk, shift=UP * 0.2), FadeIn(notes[2]), run_time=1.0)
        self.sync(0.5)

        a1 = arrow(web.get_right(), api.get_left(), TEAL)
        a2 = arrow(wrk.get_left(), api.get_right(), VIOLET)
        l1 = txt("HTTP", size=22, color=TEAL).next_to(a1, UP, buff=0.1)
        l2 = txt("internal", size=22, color=VIOLET).next_to(a2, UP, buff=0.1)
        self.play(GrowArrow(a1), FadeIn(l1), run_time=0.9)
        self.play(GrowArrow(a2), FadeIn(l2), run_time=0.9)
        self.sync(0.68)

        bar = RoundedRectangle(
            corner_radius=0.2, width=11.0, height=0.9, stroke_color=TEAL, fill_color=PANEL, fill_opacity=1
        ).shift(DOWN * 2.6)
        bl = txt("LiveKit room: live audio and data messages", size=28).move_to(bar)
        up_w = DoubleArrow(
            bar.get_top() + LEFT * 4.8,
            notes[0].get_bottom() + DOWN * 0.1,
            color=TEAL,
            stroke_width=4,
            buff=0.05,
            tip_length=0.2,
        )
        up_k = DoubleArrow(
            bar.get_top() + RIGHT * 4.8,
            notes[2].get_bottom() + DOWN * 0.1,
            color=VIOLET,
            stroke_width=4,
            buff=0.05,
            tip_length=0.2,
        )
        self.play(FadeIn(bar), FadeIn(bl), run_time=0.9)
        self.play(GrowFromCenter(up_w), GrowFromCenter(up_k), run_time=1.0)
        self.sync(0.88)
        db = txt("State lives in a database (PostgreSQL, SQLite locally)", size=26, color=MUTED).next_to(
            bar, DOWN, buff=0.3
        )
        self.play(FadeIn(db), run_time=0.8)
        self.finish()
