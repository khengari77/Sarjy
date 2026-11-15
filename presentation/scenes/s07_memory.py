from common import *


class S07Memory(TimedScene):
    SCENE_KEY = "s07_memory"

    def construct(self):
        self.add(tag("Part B  |  Memory"))
        head = title("Memory: you stay in control")
        self.play(FadeIn(head), run_time=0.8)

        # 1. Consent flow.
        steps = [
            ("Model proposes", AMBER),
            ("App asks\nfor consent", AMBER),
            ("You say yes", TEAL),
            ("App saves", GREEN),
        ]
        row = VGroup(*[box(n, c, w=2.85, h=1.5, size=30) for n, c in steps]).arrange(RIGHT, buff=0.55).shift(UP * 0.8)
        arrs = [arrow(row[i].get_right(), row[i + 1].get_left()) for i in range(3)]
        note = txt("Nothing is saved until you consent", size=32, color=AMBER).next_to(row, DOWN, buff=0.7)
        for i in range(4):
            self.play(FadeIn(row[i], shift=RIGHT * 0.2), *([GrowArrow(arrs[i - 1])] if i else []), run_time=0.9)
            self.sync(0.04 + 0.05 * i)
        self.play(FadeIn(note, shift=UP * 0.15), run_time=0.8)
        self.sync(0.34)
        self.play(FadeOut(VGroup(row, *arrs, note)), run_time=0.7)

        # 2. Control: hide, undo, delete.
        card = box("A saved memory", TEAL, w=4.4, h=1.3, size=34).shift(UP * 1.6)
        hidden = box("Hidden\n(used nowhere)", VIOLET, w=4.0, h=1.4, size=30).shift(LEFT * 3.7 + DOWN * 0.7)
        gone = box("Deleted\n(permanent)", RED, w=4.0, h=1.4, size=30).shift(RIGHT * 3.7 + DOWN * 0.7)
        a_h = arrow(card.get_bottom() + LEFT * 1.2, hidden.get_top() + RIGHT * 0.5, VIOLET)
        a_u = arrow(hidden.get_top() + RIGHT * 1.2, card.get_bottom() + LEFT * 0.4, TEAL)
        a_d = arrow(card.get_bottom() + RIGHT * 1.0, gone.get_top(), RED)
        l_h = txt("Hide, then Undo:\nby voice or the eye icon", size=26, color=VIOLET, line_spacing=0.9).next_to(
            hidden, DOWN, buff=0.3
        )
        l_d = txt("Trash icon\nplus confirmation", size=26, color=RED, line_spacing=0.9).next_to(gone, DOWN, buff=0.3)
        only = txt("Delete is possible only in the panel, never by voice", size=28, color=MUTED).to_edge(DOWN, buff=0.5)
        self.play(FadeIn(card), run_time=0.8)
        self.play(GrowArrow(a_h), FadeIn(hidden), FadeIn(l_h), run_time=1.0)
        self.sync(0.46)
        self.play(GrowArrow(a_u), run_time=0.9)
        self.sync(0.54)
        self.play(GrowArrow(a_d), FadeIn(gone), FadeIn(l_d), run_time=1.0)
        self.play(FadeIn(only), run_time=0.8)
        self.sync(0.70)
        self.play(FadeOut(VGroup(card, hidden, gone, a_h, a_u, a_d, l_h, l_d, only)), run_time=0.7)

        # 3. Recall in a new conversation.
        store = box("Saved\nmemories", GOLD, w=3.4, h=1.5, size=30).shift(UP * 1.0)
        c1 = box("Conversation 1", MUTED, w=3.3, h=1.1, size=28).shift(LEFT * 5.0 + UP * 1.0)
        c2 = box("New conversation", BLUE, w=3.3, h=1.1, size=28).shift(RIGHT * 5.0 + UP * 1.0)
        w1 = arrow(c1.get_right(), store.get_left(), MUTED)
        w2 = arrow(store.get_right(), c2.get_left(), GOLD)
        t1 = txt("saved with\nconsent", size=22, color=MUTED, line_spacing=0.9).next_to(w1, DOWN, buff=0.15)
        t2 = txt("added to\ncontext", size=22, color=GOLD, line_spacing=0.9).next_to(w2, DOWN, buff=0.15)
        q = txt('"What is my name?"  ->  it knows', size=34, color=TEXT).shift(DOWN * 1.3)
        blocked = txt("Hidden and deleted memories are never included", size=30, color=VIOLET).shift(DOWN * 2.4)
        self.play(FadeIn(c1), FadeIn(store), GrowArrow(w1), FadeIn(t1), run_time=1.2)
        self.play(FadeIn(c2), GrowArrow(w2), FadeIn(t2), run_time=1.1)
        self.sync(0.86)
        self.play(FadeIn(q, shift=UP * 0.15), run_time=0.8)
        self.play(FadeIn(blocked, shift=UP * 0.15), run_time=0.8)
        self.finish()
