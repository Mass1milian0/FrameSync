import argparse
import asyncio
import base64
import logging
import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path

from .protocol import (
    MAX_TEXT_BYTES,
    client_authenticate,
    read_message,
    server_authenticate,
)
from .sync import Peer, SyncState
from .wayland_clipboard import set_text as set_wayland_text
from .wayland_clipboard import watch as serve_wayland_events
from .wayland_clipboard import watch_wl_paste

logger = logging.getLogger("framesync")


def load_secret(path: Path) -> bytes:
    try:
        secret = base64.b64decode(path.read_text(encoding="ascii").strip(), validate=True)
    except (OSError, ValueError) as error:
        raise RuntimeError(f"Could not read the authentication secret at {path}") from error
    if len(secret) < 32:
        raise RuntimeError(f"Authentication secret at {path} must contain at least 32 bytes")
    return secret


async def read_wayland_text() -> str | None:
    try:
        process = await asyncio.create_subprocess_exec(
            "wl-paste",
            "--no-newline",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError as error:
        raise RuntimeError("wl-paste was not found; install wl-clipboard") from error
    stdout, stderr = await process.communicate()
    if process.returncode:
        message = stderr.decode("utf-8", errors="replace").strip()
        if "empty" in message.lower() or "no selection" in message.lower():
            return None
        raise RuntimeError(f"wl-paste failed ({process.returncode}): {message}")
    if len(stdout) > MAX_TEXT_BYTES:
        raise ValueError(f"clipboard text exceeds {MAX_TEXT_BYTES} bytes")
    return stdout.decode("utf-8")


async def frame_peer_loop(state: SyncState, secret: bytes, port: int) -> None:
    backoff = 1
    while True:
        writer: asyncio.StreamWriter | None = None
        peer: Peer | None = None
        try:
            reader, writer = await asyncio.open_connection(
                "127.0.0.1", port, limit=2 * MAX_TEXT_BYTES + 16
            )
            await asyncio.wait_for(client_authenticate(reader, writer, secret), timeout=10)
            peer = Peer(reader, writer, state)
            state.peer = peer
            logger.info("Connected to the Windows clipboard agent")
            backoff = 1
            await peer.run()
        except asyncio.CancelledError:
            raise
        except (OSError, asyncio.IncompleteReadError, ValueError, asyncio.TimeoutError) as error:
            logger.warning("Clipboard connection unavailable: %s", error)
        finally:
            if state.peer is peer:
                state.peer = None
            if peer is not None:
                await peer.close()
            elif writer is not None:
                writer.close()
                try:
                    await writer.wait_closed()
                except OSError:
                    pass
        await asyncio.sleep(backoff)
        backoff = min(backoff * 2, 30)


async def run_frame(args: argparse.Namespace) -> None:
    secret = load_secret(args.secret_file)
    if args.backend == "x11":
        from .frame_clipboard import FrameClipboard

        clipboard = FrameClipboard()
        state = SyncState(clipboard.read_text, clipboard.set_text)
        tasks = [
            asyncio.create_task(clipboard.watch(state)),
            asyncio.create_task(frame_peer_loop(state, secret, args.frame_port)),
        ]
    else:
        await run_wayland_frame(args, secret)
        return

    try:
        await asyncio.gather(*tasks)
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def run_wayland_frame(args: argparse.Namespace, secret: bytes) -> None:
    runtime_dir = os.environ.get("XDG_RUNTIME_DIR")
    socket_dir = (
        Path(runtime_dir) / "framesync"
        if runtime_dir
        else Path.home() / ".cache" / "framesync" / "run"
    )
    socket_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(socket_dir, 0o700)
    socket_path = socket_dir / "clipboard.sock"
    state = SyncState(read_wayland_text, set_wayland_text)
    tasks = [
        asyncio.create_task(serve_wayland_events(socket_path, state.local_change)),
        asyncio.create_task(watch_wl_paste(socket_path)),
        asyncio.create_task(frame_peer_loop(state, secret, args.frame_port)),
    ]
    try:
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_EXCEPTION)
        for task in done:
            error = task.exception()
            if error is not None:
                raise error
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def _host_connection(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    state: SyncState,
    secret: bytes,
) -> None:
    peer: Peer | None = None
    try:
        await asyncio.wait_for(server_authenticate(reader, writer, secret), timeout=10)
        peer = Peer(reader, writer, state)
        previous = state.peer
        state.peer = peer
        if previous is not None:
            await previous.close()
        logger.info("Authenticated Steam Frame clipboard connection")
        await peer.run()
    except asyncio.CancelledError:
        raise
    except (OSError, asyncio.IncompleteReadError, ValueError, asyncio.TimeoutError) as error:
        logger.warning("Clipboard peer disconnected: %s", error)
    finally:
        if state.peer is peer:
            state.peer = None
        if peer is not None:
            await peer.close()
        else:
            writer.close()
            try:
                await writer.wait_closed()
            except OSError:
                pass


async def run_ssh_tunnel(args: argparse.Namespace) -> None:
    target = args.target
    if not target or target.startswith("-") or any(c.isspace() for c in target):
        raise ValueError("SSH target must be a host alias or user@host without whitespace")
    ssh = shutil.which("ssh.exe") or shutil.which("ssh")
    if not ssh:
        raise RuntimeError("OpenSSH client was not found on PATH")
    command = [
        ssh,
        "-N",
        "-T",
        "-o",
        "ExitOnForwardFailure=yes",
        "-o",
        "ServerAliveInterval=20",
        "-o",
        "ServerAliveCountMax=3",
        "-R",
        f"127.0.0.1:{args.frame_port}:127.0.0.1:{args.local_port}",
        target,
    ]
    backoff = 2
    while True:
        logger.info("Opening SSH reverse tunnel to %s", target)
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
                creationflags=creationflags,
            )
        except OSError:
            logger.exception("Could not start OpenSSH")
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30)
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
        detail = (await stderr_task).decode("utf-8", errors="replace").strip()
        logger.warning(
            "SSH tunnel exited with status %d%s",
            code,
            f": {detail[-1000:]}" if detail else "",
        )
        await asyncio.sleep(backoff)
        backoff = min(backoff * 2, 30)


async def run_host(args: argparse.Namespace) -> None:
    if sys.platform != "win32":
        raise RuntimeError("The host agent must run on Windows")
    if not args.target or args.target.startswith("-") or any(c.isspace() for c in args.target):
        raise ValueError("SSH target must be a host alias or user@host without whitespace")
    from . import windows_clipboard

    secret = load_secret(args.secret_file)
    loop = asyncio.get_running_loop()
    state = SyncState(
        lambda: asyncio.to_thread(windows_clipboard.get_text),
        lambda text: asyncio.to_thread(windows_clipboard.set_text, text),
    )

    def clipboard_changed(text: str) -> None:
        def schedule() -> None:
            task = asyncio.create_task(state.local_change(text))

            def report_error(completed: asyncio.Task[None]) -> None:
                try:
                    completed.result()
                except Exception:
                    logger.exception("Could not forward a Windows clipboard change")

            task.add_done_callback(report_error)

        loop.call_soon_threadsafe(schedule)

    def watch_clipboard() -> None:
        while True:
            try:
                windows_clipboard.watch(clipboard_changed)
                logger.error("Windows clipboard message loop exited; restarting it")
            except Exception:
                logger.exception("Windows clipboard listener failed; restarting it")
            threading.Event().wait(2)

    listener = threading.Thread(target=watch_clipboard, name="windows-clipboard-listener", daemon=True)
    listener.start()

    await serve_host(state, secret, args)


async def serve_host(state: SyncState, secret: bytes, args: argparse.Namespace) -> None:
    connections: set[asyncio.Task[None]] = set()

    def accept(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        task = asyncio.create_task(_host_connection(reader, writer, state, secret))
        connections.add(task)
        task.add_done_callback(connections.discard)

    server = await asyncio.start_server(
        accept,
        "127.0.0.1",
        args.local_port,
        limit=2 * MAX_TEXT_BYTES + 16,
    )
    logger.info("Windows clipboard listener is active on 127.0.0.1:%d", args.local_port)
    tunnel = asyncio.create_task(run_ssh_tunnel(args))
    try:
        await asyncio.Future()
    finally:
        server.close()
        tunnel.cancel()
        for connection in connections:
            connection.cancel()
        await asyncio.gather(tunnel, *connections, return_exceptions=True)
        await server.wait_closed()


def configure_logging() -> None:
    if sys.platform == "win32":
        root = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "FrameSync"
    else:
        root = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / "framesync"
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.FileHandler(root / "framesync.log", encoding="utf-8")],
    )


def main() -> None:
    parser = argparse.ArgumentParser(prog="framesync")
    subparsers = parser.add_subparsers(dest="mode", required=True)
    host = subparsers.add_parser("host", help="run the native Windows clipboard agent")
    host.add_argument("--target", required=True, help="SSH alias or user@Steam-Frame-host")
    host.add_argument("--secret-file", type=Path, required=True)
    host.add_argument("--local-port", type=int, default=39101)
    host.add_argument("--frame-port", type=int, default=39102)
    frame = subparsers.add_parser("frame", help="run in the Steam Frame KDE session")
    frame.add_argument("--secret-file", type=Path, required=True)
    frame.add_argument("--frame-port", type=int, default=39102)
    frame.add_argument("--backend", choices=("wayland", "x11"), default="wayland")
    emit_parser = subparsers.add_parser("emit", help=argparse.SUPPRESS)
    emit_parser.add_argument("--socket", required=True)
    args = parser.parse_args()
    if args.mode == "emit":
        from .wayland_clipboard import emit

        emit(args.socket)
        return

    configure_logging()
    try:
        if args.mode == "host":
            asyncio.run(run_host(args))
        else:
            asyncio.run(run_frame(args))
    except KeyboardInterrupt:
        logger.info("FrameSync stopped")
    except Exception:
        logger.exception("FrameSync stopped because of an unrecoverable error")
        raise
