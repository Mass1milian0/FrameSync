import asyncio
import logging
from collections.abc import Awaitable, Callable

from .protocol import MAX_TEXT_BYTES

logger = logging.getLogger("framesync")


async def read_text() -> str | None:
    process = await asyncio.create_subprocess_exec(
        "xclip", "-selection", "clipboard", "-out", "-target", "UTF8_STRING",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        stdout, _ = await asyncio.wait_for(process.communicate(), timeout=5)
    except asyncio.TimeoutError:
        process.kill()
        await process.wait()
        raise RuntimeError("Timed out reading the X11 clipboard")
    if process.returncode:
        return None
    if len(stdout) > MAX_TEXT_BYTES:
        raise ValueError(f"clipboard text exceeds {MAX_TEXT_BYTES} bytes")
    return stdout.decode("utf-8")


async def set_text(text: str) -> None:
    payload = text.encode("utf-8")
    if len(payload) > MAX_TEXT_BYTES:
        raise ValueError(f"clipboard text exceeds {MAX_TEXT_BYTES} bytes")
    process = await asyncio.create_subprocess_exec(
        "xclip", "-selection", "clipboard", "-in", "-target", "UTF8_STRING",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    await process.communicate(payload)
    if process.returncode:
        raise RuntimeError(f"xclip failed with status {process.returncode}")


async def watch(on_change: Callable[[str], Awaitable[None]]) -> None:
    previous: str | None = None
    while True:
        try:
            text = await read_text()
            if text is not None and text != previous:
                previous = text
                await on_change(text)
        except (OSError, RuntimeError, UnicodeDecodeError, ValueError) as error:
            logger.warning("Could not read X11 clipboard: %s", error)
        await asyncio.sleep(0.5)