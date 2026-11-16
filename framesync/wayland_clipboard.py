import asyncio
import logging
import os
import socket
import struct
import sys
from collections.abc import Awaitable, Callable
from pathlib import Path

from .protocol import MAX_TEXT_BYTES

logger = logging.getLogger("framesync")


def emit(socket_path: str) -> None:
    data = sys.stdin.buffer.read(MAX_TEXT_BYTES + 1)
    if len(data) > MAX_TEXT_BYTES:
        raise ValueError(f"clipboard text exceeds {MAX_TEXT_BYTES} bytes")
    text = data.decode("utf-8")
    payload = text.encode("utf-8")
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.connect(socket_path)
        connection.sendall(struct.pack("!I", len(payload)) + payload)


async def watch(
    socket_path: Path,
    on_change: Callable[[str], Awaitable[None]],
) -> None:
    if socket_path.exists():
        if not socket_path.is_socket():
            raise RuntimeError(f"Refusing to replace non-socket path {socket_path}")
        probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            probe.connect(str(socket_path))
        except (ConnectionRefusedError, FileNotFoundError):
            socket_path.unlink()
        else:
            raise RuntimeError(f"Another FrameSync agent is already listening on {socket_path}")
        finally:
            probe.close()

    async def handle(
        reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        try:
            header = await reader.readexactly(4)
            (size,) = struct.unpack("!I", header)
            if size > MAX_TEXT_BYTES:
                raise ValueError("clipboard event is too large")
            text = (await reader.readexactly(size)).decode("utf-8")
            await on_change(text)
        except (asyncio.IncompleteReadError, UnicodeDecodeError, ValueError):
            logger.exception("Rejected malformed local clipboard event")
        except OSError:
            logger.exception("Could not forward a local clipboard event")
        finally:
            writer.close()
            await writer.wait_closed()

    server = await asyncio.start_unix_server(handle, path=str(socket_path))
    os.chmod(socket_path, 0o600)
    try:
        async with server:
            await server.serve_forever()
    finally:
        server.close()
        await server.wait_closed()
        try:
            socket_path.unlink()
        except FileNotFoundError:
            pass


async def watch_wl_paste(socket_path: Path) -> None:
    while True:
        command = [
            "wl-paste",
            "--no-newline",
            "--watch",
            sys.executable,
            str(Path(__file__).resolve().parent.parent / "framesync.py"),
            "emit",
            "--socket",
            str(socket_path),
        ]
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError:
            logger.exception("wl-paste was not found; install wl-clipboard")
            await asyncio.sleep(10)
            continue
        stderr_task = asyncio.create_task(process.stderr.read())
        try:
            code = await process.wait()
        except asyncio.CancelledError:
            process.terminate()
            await process.wait()
            stderr_task.cancel()
            await asyncio.gather(stderr_task, return_exceptions=True)
            raise
        stderr = (await stderr_task).decode("utf-8", errors="replace").strip()
        logger.warning(
            "wl-paste watcher exited with status %d%s",
            code,
            f": {stderr[-1000:]}" if stderr else "",
        )
        await asyncio.sleep(2)


async def set_text(text: str) -> None:
    payload = text.encode("utf-8")
    if len(payload) > MAX_TEXT_BYTES:
        raise ValueError(f"clipboard text exceeds {MAX_TEXT_BYTES} bytes")
    try:
        process = await asyncio.create_subprocess_exec(
            "wl-copy",
            "--type",
            "text/plain;charset=utf-8",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError as error:
        raise RuntimeError("wl-copy was not found; install wl-clipboard") from error
    _, stderr = await process.communicate(payload)
    if process.returncode:
        detail = stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"wl-copy failed ({process.returncode}): {detail}")
