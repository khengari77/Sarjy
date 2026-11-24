"""A held stream makes progress early and reveals nothing until it is released."""

import asyncio

import pytest
from held_stream import HeldStream


class Source:
    def __init__(self, items=(), *, fail=None, gate=None):
        self.items, self.fail, self.gate = items, fail, gate
        self.started = asyncio.Event()
        self.closed = False
        self.task = None

    async def stream(self):
        self.task = asyncio.current_task()
        self.started.set()
        try:
            for item in self.items:
                yield item
            if self.gate is not None:
                await self.gate.wait()
                yield "after gate"
            if self.fail is not None:
                raise self.fail
        finally:
            assert asyncio.current_task() is self.task
            self.closed = True


def run(scenario):
    async def main():
        before = asyncio.all_tasks()
        await scenario()
        assert asyncio.all_tasks() == before

    asyncio.run(main())


def test_runs_before_release_then_yields_in_order():
    async def scenario():
        source = Source(["a", "b"], gate=asyncio.Event())
        held = HeldStream(source.stream())
        await source.started.wait()
        source.gate.set()
        assert [item async for item in held.released()] == ["a", "b", "after gate"]
        assert source.closed

    run(scenario)


def test_discard_closes_the_stream_and_hides_its_failure():
    async def scenario():
        for source in (Source(["a"], gate=asyncio.Event()), Source(["a"], fail=RuntimeError("provider"))):
            held = HeldStream(source.stream())
            await source.started.wait()
            await asyncio.sleep(0)
            await held.discard()
            await held.discard()
            assert source.closed

    run(scenario)


def test_discard_before_the_task_ran_leaves_nothing_open():
    async def scenario():
        source = Source(["a"])
        await HeldStream(source.stream()).discard()
        assert not source.started.is_set()

    run(scenario)


def test_failure_is_raised_after_the_items_that_preceded_it():
    async def scenario():
        held = HeldStream(Source(["a"], fail=RuntimeError("provider")).stream())
        seen = []
        with pytest.raises(RuntimeError, match="provider"):
            async for item in held.released():
                seen.append(item)
        assert seen == ["a"]

    run(scenario)


def test_abandoning_a_released_stream_stops_it():
    async def scenario():
        source = Source(["a", "b"], gate=asyncio.Event())
        released = HeldStream(source.stream()).released()
        assert await anext(released) == "a"
        await released.aclose()
        assert source.closed

    run(scenario)


def test_cancelling_the_consumer_stops_the_stream():
    async def scenario():
        source = Source([], gate=asyncio.Event())
        held = HeldStream(source.stream())

        async def consume():
            return [item async for item in held.released()]

        consumer = asyncio.create_task(consume())
        await source.started.wait()
        consumer.cancel()
        with pytest.raises(asyncio.CancelledError):
            await consumer
        assert source.closed

    run(scenario)


def test_a_stream_that_will_not_close_does_not_hold_up_its_owner(monkeypatch):
    monkeypatch.setattr("held_stream.DISCARD_SECONDS", 0.01)

    async def run():
        release = asyncio.Event()

        async def stubborn():
            try:
                yield 1
                await asyncio.Event().wait()
            finally:
                await release.wait()

        held = HeldStream(stubborn())
        await asyncio.sleep(0)
        await asyncio.wait_for(held.discard(), 1)
        assert not held.task.done()
        release.set()
        await asyncio.wait({held.task})

    asyncio.run(run())
