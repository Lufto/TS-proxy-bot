import asyncio

from tsproxy.services.locks import KeyedLocks


async def test_same_key_waits_for_the_first() -> None:
    locks = KeyedLocks()
    order: list[str] = []

    async def work(name: str) -> None:
        async with locks.hold(1):
            order.append(f"{name}:start")
            await asyncio.sleep(0)
            order.append(f"{name}:end")

    await asyncio.gather(work("a"), work("b"))

    assert order == ["a:start", "a:end", "b:start", "b:end"]


async def test_other_keys_do_not_wait() -> None:
    locks = KeyedLocks()
    async with locks.hold(1):
        async with asyncio.timeout(1):
            async with locks.hold(2):
                pass


async def test_unused_locks_are_forgotten() -> None:
    locks = KeyedLocks()
    async with locks.hold(1):
        assert locks.held_keys() == {1}
    assert locks.held_keys() == set()
