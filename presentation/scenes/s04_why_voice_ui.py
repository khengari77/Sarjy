from common import *


class S04WhyVoiceUI(TimedScene):
    SCENE_KEY = "s04_why_voice_ui"

    def construct(self):
        self.add(tag("Part A  |  Why voice UI commands"))
        head = title("Why voice commands for the UI?")
        self.play(FadeIn(head), run_time=0.8)

        # 1. Today: STT cheap, models small.
        l1 = box("Speech-to-text: easy and cheap", TEAL, w=8.2, h=1.2, size=34).shift(UP * 1.2)
        l2 = box(
            "Small models (under 100M parameters)\nrun smoothly on phones and laptops", TEAL, w=9.6, h=1.7, size=32
        ).shift(DOWN * 0.7)
        self.play(FadeIn(l1, shift=UP * 0.2), run_time=1.0)
        self.sync(0.14)
        self.play(FadeIn(l2, shift=UP * 0.2), run_time=1.0)
        self.sync(0.28)
        why = txt("So why has the UI not caught up?", size=42, color=AMBER).shift(DOWN * 2.0)
        self.play(Write(why), run_time=1.4)
        self.sync(0.38)
        self.play(FadeOut(VGroup(l1, l2, why)), run_time=0.8)

        # 2. History.
        years = txt("2000 - 2010", size=40, color=MUTED).shift(UP * 1.4)
        ms = box("Microsoft adds speech recognition to Windows and Word", VIOLET, w=11.2, h=1.2, size=30).shift(
            UP * 0.2
        )
        fail = txt("It failed: recognition was immature", size=38, color=RED).shift(DOWN * 1.2)
        self.play(FadeIn(years), run_time=0.7)
        self.play(FadeIn(ms, shift=UP * 0.2), run_time=1.0)
        self.sync(0.50)
        self.play(FadeIn(fail, shift=UP * 0.2), run_time=1.0)
        self.sync(0.58)
        self.play(FadeOut(VGroup(years, ms, fail)), run_time=0.8)

        # 3. Commanding is natural.
        nat = txt("Commanding is the most natural interaction", size=40, color=TEAL).shift(UP * 1.5)
        phone = RoundedRectangle(
            corner_radius=0.3, width=2.2, height=3.6, stroke_color=MUTED, fill_color=PANEL, fill_opacity=1
        ).shift(LEFT * 3.6 + DOWN * 0.7)
        quote1 = txt('"Open the music app"', size=34).next_to(phone, RIGHT, buff=0.6).shift(UP * 0.5)
        quote2 = txt('"Close it"', size=34).next_to(quote1, DOWN, aligned_edge=LEFT, buff=0.4)
        self.play(FadeIn(nat, shift=DOWN * 0.2), run_time=1.0)
        self.play(FadeIn(phone), run_time=0.7)
        self.play(FadeIn(quote1, shift=RIGHT * 0.2), run_time=0.9)
        self.play(FadeIn(quote2, shift=RIGHT * 0.2), run_time=0.9)
        self.sync(0.74)
        self.play(FadeOut(VGroup(phone, quote1, quote2, nat)), run_time=0.8)

        # 4. Sarjy proof of concept.
        poc = txt("Sarjy: a mature proof of concept, built in four days", size=36, color=TEAL).shift(UP * 1.5)
        if poc.width > 12.8:
            poc.scale_to_fit_width(12.8)
        names = ["Dark / light mode", "Speaking speed", "Voice: man or woman", "Captions on / off", "Play Wordle"]
        chips = (
            VGroup(*[box(n, VIOLET, w=4.3, h=1.0, size=30) for n in names])
            .arrange_in_grid(2, 3, buff=0.4)
            .shift(DOWN * 0.3)
        )
        self.play(FadeIn(poc, shift=DOWN * 0.2), run_time=1.0)
        self.play(LaggedStart(*[FadeIn(c, scale=0.9) for c in chips], lag_ratio=0.3), run_time=3.0)
        by = txt("All by voice", size=34, color=MUTED).to_edge(DOWN, buff=0.8)
        self.sync(0.92)
        self.play(FadeIn(by), run_time=0.6)
        self.finish()
