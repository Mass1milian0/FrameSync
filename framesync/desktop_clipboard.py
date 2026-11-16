import asyncio
import os
from pathlib import Path

from .protocol import MAX_TEXT_BYTES


def session_bus_address() -> str | None:
    for process in Path("/proc").iterdir():
        if not process.name.isdecimal():
            continue
        try:
            if (process / "comm").read_text().strip() != "plasmashell":
                continue
            environment = (process / "environ").read_bytes().split(b"\0")
        except (OSError, UnicodeDecodeError):
            continue
        for item in environment:
            if item.startswith(b"DBUS_SESSION_BUS_ADDRESS="):
                return os.fsdecode(item.partition(b"=")[2])
    return None


async def call(method: str, text: str | None = None, address: str | None = None) -> str | None:
    address = address or session_bus_address()
    if not address:
        return None
    try:
        from dbus_next import Message, MessageType
        from dbus_next.aio import MessageBus
    except ImportError as error:
        raise RuntimeError("The dbus-next Python package is required for Desktop Mode") from error

    try:
        bus = await asyncio.wait_for(MessageBus(bus_address=address).connect(), timeout=5)
        try:
            reply = await asyncio.wait_for(
                bus.call(
                    Message(
                        destination="org.kde.klipper",
                        path="/klipper",
                        interface="org.kde.klipper.klipper",
                        member=method,
                        signature="s" if text is not None else "",
                        body=[text] if text is not None else [],
                    )
                ),
                timeout=5,
            )
            if reply.message_type == MessageType.ERROR:
                raise RuntimeError(f"Klipper call failed: {reply.error_name}")
            return reply.body[0] if reply.body else None
        finally:
            bus.disconnect()
    except Exception as error:
        raise RuntimeError(f"Could not communicate with Klipper: {error}") from error


async def read_text() -> str | None:
    text = await call("getClipboardContents")
    if text is not None and len(text.encode("utf-8")) > MAX_TEXT_BYTES:
        raise ValueError(f"clipboard text exceeds {MAX_TEXT_BYTES} bytes")
    return text


async def set_text(text: str) -> bool:
    if len(text.encode("utf-8")) > MAX_TEXT_BYTES:
        raise ValueError(f"clipboard text exceeds {MAX_TEXT_BYTES} bytes")
    address = session_bus_address()
    if not address:
        return False
    await call("setClipboardContents", text, address)
    return True