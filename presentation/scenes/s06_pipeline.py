from common import *


class S06Pipeline(TimedScene):
    SCENE_KEY = "s06_pipeline"

    def construct(self):
        self.add(tag("Part B  |  How it works"))
        head = title("One voice turn")
        self.play(FadeIn(head), run_time=0.8)

        names = [
            ("You speak", TEAL),
            ("Speech\nto text", VIOLET),
            ("Turn check", AMBER),
            ("Model", VIOLET),
            ("Text\nto speech", VIOLET),
        ]
        stage = VGroup(*[box(n, c, w=2.4, h=1.4, size=30) for n, c in names]).arrange(RIGHT, buff=0.45).shift(UP * 1.4)
        stage.scale_to_fit_width(12.6) if stage.width > 12.6 else None
        stage.move_to(UP * 1.4)
        arrows = [arrow(stage[i].get_right(), stage[i + 1].get_left()) for i in range(4)]
        sub = [
            "",
            "Deepgram\nNova-3",
            "API:\ncontext, commands",
            "OpenAI or\nDeepSeek",
            "Deepgram Aura-2\nor ElevenLabs",
        ]
        subs = VGroup(
            *[
                txt(s, size=24, color=MUTED, line_spacing=0.9).next_to(stage[i], DOWN, buff=0.2)
                for i, s in enumerate(sub)
            ]
        )

        for i in range(5):
            anims = [FadeIn(stage[i], shift=RIGHT * 0.2)]
            if i:
                anims.append(GrowArrow(arrows[i - 1]))
                anims.append(FadeIn(subs[i]))
            self.play(*anims, run_time=0.9)
            self.sync(0.06 + 0.09 * i)
        self.sync(0.5)

        # Pulse a token along the pipeline.
        for i in range(5):
            self.play(Indicate(stage[i], color=TEAL, scale_factor=1.08), run_time=0.8)
        self.sync(0.66)

        # Notes about the turn check.
        notes = (
            VGroup(
                txt("Turn check: one API call per finished user turn", size=30),
                txt("It may answer a command itself, or hand context to the model", size=30, color=MUTED),
                txt("Speech is budgeted per day before it is synthesized", size=30, color=MUTED),
            )
            .arrange(DOWN, buff=0.3)
            .shift(DOWN * 1.9)
        )
        for n in notes:
            if n.width > 12.4:
                n.scale_to_fit_width(12.4)
        box_hi = SurroundingRectangle(stage[2], color=AMBER, buff=0.08)
        self.play(Create(box_hi), run_time=0.7)
        self.play(LaggedStart(*[FadeIn(n, shift=UP * 0.15) for n in notes], lag_ratio=0.7), run_time=3.0)
        self.finish()
