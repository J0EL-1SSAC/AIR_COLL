import asyncio

from backend.airwatch.clock import ReplayClock


def test_replay_clock_pause_step_resume_and_speed():
    async def run():
        clock=ReplayClock(); clock.set(100); clock.pause()
        waiting=asyncio.create_task(clock.wait_until(101,10))
        await asyncio.sleep(0)
        assert not waiting.done()
        clock.step(); await waiting
        assert clock.now()==101 and clock.paused
        clock.set_speed(5); assert clock.speed==5
        clock.resume(); assert not clock.paused
    asyncio.run(run())
