"""adapters.job_logs.tail_log: bounded reads, UTF-8-safe chunk boundaries,
and a slow finished() cadence (R1-79, R2-18)."""

import asyncio

from graphrag_ui.adapters.job_logs import tail_log


def _done_after(n: int):
    calls = {"n": 0}

    async def finished() -> bool:
        calls["n"] += 1
        return calls["n"] > n

    return finished, calls


async def _until(predicate, timeout_s: float = 5.0) -> None:
    """Wait for the reader task to reach a state instead of sleeping a
    fixed time and hoping it got there."""
    async with asyncio.timeout(timeout_s):
        while not predicate():
            await asyncio.sleep(0.005)


async def _collect(path, offset=0, **kw) -> list[tuple[int, bytes]]:
    finished, _ = _done_after(0)
    return [item async for item in tail_log(path, offset, finished=finished, **kw)]


async def test_a_large_log_is_read_in_bounded_chunks(tmp_path):
    log = tmp_path / "job.log"
    log.write_bytes(b"x" * 10_000)
    out = await _collect(log, chunk_bytes=4096, poll_s=0.01)
    assert [len(c) for _, c in out] == [4096, 4096, 1808]
    assert [pos for pos, _ in out] == [4096, 8192, 10_000]


async def test_a_chunk_never_ends_inside_a_utf8_sequence(tmp_path):
    """A multi-byte character cut by the read size moves whole into the
    next chunk, so each chunk decodes on its own and every offset is a
    character boundary a client can resume from."""
    text = "ab━━cd中文\n" * 3
    log = tmp_path / "job.log"
    log.write_bytes(text.encode())
    out = await _collect(log, chunk_bytes=4, poll_s=0.01)
    assert "".join(c.decode() for _, c in out) == text  # strict decode
    assert out[-1][0] == len(text.encode())


async def test_a_character_half_written_by_the_job_waits_for_its_tail(tmp_path):
    log = tmp_path / "job.log"
    rule = "━".encode()
    log.write_bytes(b"ab" + rule[:1])
    stop = asyncio.Event()

    async def finished() -> bool:
        return stop.is_set()

    got: list[tuple[int, bytes]] = []

    async def reader():
        async for item in tail_log(log, 0, finished=finished, poll_s=0.01, finished_every_s=0):
            got.append(item)

    task = asyncio.create_task(reader())
    await _until(lambda: got)
    await asyncio.sleep(0.05)  # a few more polls: the half character stays held back
    assert got == [(2, b"ab")]
    with log.open("ab") as fh:
        fh.write(rule[1:] + b"\n")
    await _until(lambda: len(got) == 2)
    stop.set()
    await asyncio.wait_for(task, timeout=2)
    assert got == [(2, b"ab"), (6, rule + b"\n")]


async def test_a_finished_log_flushes_a_truncated_tail(tmp_path):
    """A job that died mid-character still drains to the file size."""
    log = tmp_path / "job.log"
    log.write_bytes(b"ok" + "━".encode()[:2])
    out = await _collect(log, poll_s=0.01, finished_every_s=0)
    assert out[-1][0] == 4
    assert b"".join(c for _, c in out) == log.read_bytes()


async def test_finished_is_polled_only_when_idle_and_at_a_slow_cadence(tmp_path):
    log = tmp_path / "job.log"
    log.write_bytes(b"x" * 1000)
    calls = {"n": 0}
    stop = asyncio.Event()

    async def finished() -> bool:
        calls["n"] += 1
        return stop.is_set()

    async def reader():
        async for _ in tail_log(
            log, 0, finished=finished, chunk_bytes=10, poll_s=0.01, finished_every_s=0.2
        ):
            pass

    task = asyncio.create_task(reader())
    await asyncio.sleep(0.5)  # ~50 idle polls after the 100 data chunks
    stop.set()
    await asyncio.wait_for(task, timeout=2)
    # no check while 100 chunks drained; ~3 checks across 0.5 s of idling
    assert calls["n"] <= 5


async def test_bytes_written_just_before_the_job_ends_are_drained(tmp_path):
    """finished() can turn true between the last read and its own check:
    the tail drains once more before it stops."""
    log = tmp_path / "job.log"
    log.write_bytes(b"first\n")

    async def finished() -> bool:
        with log.open("ab") as fh:
            fh.write(b"last\n")
        return True

    out = [item async for item in tail_log(log, 0, finished=finished, poll_s=0.01)]
    assert b"".join(c for _, c in out) == b"first\nlast\n"


async def test_a_missing_log_ends_when_the_job_is_finished(tmp_path):
    assert await _collect(tmp_path / "absent.log", poll_s=0.01) == []
