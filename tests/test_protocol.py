import asyncio
import base64
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from framesync.agent import serve_host
from framesync.frame_clipboard import FrameClipboard
from framesync.protocol import (
    MAX_TEXT_BYTES,
    ClipboardUpdate,
    client_authenticate,
    read_message,
    send_message,
    server_authenticate,
)
from framesync.sync import Peer, SyncState
from framesync import x11_clipboard


class ProtocolTests(unittest.TestCase):
    def test_round_trip_unicode_and_trailing_newlines(self):
        text = "héllo, 世界 🎧\nsecond line\n\n"
        message = ClipboardUpdate(123, "frame-1", text).to_message()
        self.assertEqual(ClipboardUpdate.from_message(message).text, text)

    def test_round_trip_maximum_text_payload(self):
        text = "é" * (MAX_TEXT_BYTES // 2)
        self.assertEqual(
            ClipboardUpdate.from_message(
                ClipboardUpdate(123, "frame-1", text).to_message()
            ).text,
            text,
        )

    def test_rejects_oversized_text(self):
        with self.assertRaisesRegex(ValueError, "exceeds"):
            ClipboardUpdate(1, "origin", "x" * (MAX_TEXT_BYTES + 1)).to_message()

    def test_rejects_invalid_utf8(self):
        message = {
            "type": "update",
            "timestamp_ns": 1,
            "origin": "origin",
            "text": base64.b64encode(b"\xff").decode("ascii"),
        }
        with self.assertRaisesRegex(ValueError, "UTF-8"):
            ClipboardUpdate.from_message(message)

    def test_rejects_unknown_message_type(self):
        with self.assertRaisesRegex(ValueError, "type"):
            ClipboardUpdate.from_message({"type": "unknown"})


class SyncStateTests(unittest.IsolatedAsyncioTestCase):
    async def test_snapshot_preserves_timestamp_of_unchanged_clipboard(self):
        async def read():
            return "unchanged"

        async def write(_text):
            return None

        state = SyncState(read, write)
        await state.local_change("unchanged")
        original = state.current
        state.peer = SimpleNamespace(send=AsyncMock())
        await state.publish_snapshot()
        self.assertIs(state.current, original)
        state.peer.send.assert_awaited_once_with(original)

    async def test_remote_update_is_written_once_and_echo_is_ignored(self):
        clipboard = {"text": "old"}
        writes = []

        async def read():
            return clipboard["text"]

        async def write(text):
            writes.append(text)
            clipboard["text"] = text

        state = SyncState(read, write)
        event = ClipboardUpdate(10, "remote", "new\n🙂")
        await state.receive(event)
        await state.local_change(clipboard["text"])
        self.assertEqual(writes, ["new\n🙂"])
        self.assertEqual(state.current.text, "new\n🙂")

    async def test_conflict_order_uses_timestamp_then_origin(self):
        async def read():
            return None

        async def write(_text):
            return None

        state = SyncState(read, write)
        await state.receive(ClipboardUpdate(20, "peer-b", "b"))
        await state.receive(ClipboardUpdate(20, "peer-a", "a"))
        self.assertEqual(state.current.text, "b")
        await state.receive(ClipboardUpdate(21, "peer-a", "newer"))
        self.assertEqual(state.current.text, "newer")


class X11ClipboardTests(unittest.IsolatedAsyncioTestCase):
    async def test_read_text_decodes_utf8_and_preserves_newline(self):
        process = AsyncMock()
        process.communicate.return_value = ("café 🎧\n".encode("utf-8"), None)
        process.returncode = 0
        with patch("framesync.x11_clipboard.asyncio.create_subprocess_exec", return_value=process):
            self.assertEqual(await x11_clipboard.read_text(), "café 🎧\n")

    async def test_set_text_passes_utf8_to_xclip(self):
        process = AsyncMock()
        process.returncode = 0
        with patch("framesync.x11_clipboard.asyncio.create_subprocess_exec", return_value=process):
            await x11_clipboard.set_text("世界\n")
        process.communicate.assert_awaited_once_with("世界\n".encode("utf-8"))

    async def test_read_rejects_oversized_text(self):
        process = AsyncMock()
        process.communicate.return_value = (b"x" * (MAX_TEXT_BYTES + 1), None)
        process.returncode = 0
        with patch("framesync.x11_clipboard.asyncio.create_subprocess_exec", return_value=process):
            with self.assertRaisesRegex(ValueError, "exceeds"):
                await x11_clipboard.read_text()


class FrameClipboardTests(unittest.IsolatedAsyncioTestCase):
    async def test_desktop_and_game_mode_changes_are_mirrored(self):
        values = {"desktop": None, "x11": "game"}

        async def read_desktop():
            return values["desktop"]

        async def write_desktop(text):
            if values["desktop"] is None:
                return False
            values["desktop"] = text
            return True

        async def read_x11():
            return values["x11"]

        async def write_x11(text):
            values["x11"] = text

        async def wait_for(predicate):
            for _ in range(150):
                if predicate():
                    return
                await asyncio.sleep(0.02)
            self.fail("Clipboard change was not propagated")

        clipboard = FrameClipboard()
        state = SyncState(clipboard.read_text, clipboard.set_text)
        with patch("framesync.desktop_clipboard.read_text", read_desktop), patch(
            "framesync.desktop_clipboard.set_text", write_desktop
        ), patch("framesync.x11_clipboard.read_text", read_x11), patch(
            "framesync.x11_clipboard.set_text", write_x11
        ):
            watcher = asyncio.create_task(clipboard.watch(state))
            try:
                await wait_for(lambda: state.current is not None)
                values["desktop"] = "desktop text"
                await wait_for(lambda: values["x11"] == "desktop text")
                self.assertEqual(state.current.text, "desktop text")
                values["x11"] = "game text"
                await wait_for(lambda: values["desktop"] == "game text")
                self.assertEqual(state.current.text, "game text")
                await state.receive(ClipboardUpdate(state.clock + 1, "remote", "windows text"))
                self.assertEqual(values, {"desktop": "windows text", "x11": "windows text"})
            finally:
                watcher.cancel()
                await asyncio.gather(watcher, return_exceptions=True)


class WireTests(unittest.IsolatedAsyncioTestCase):
    async def test_host_shutdown_closes_active_connection(self):
        ready = asyncio.get_running_loop().create_future()
        start_server = asyncio.start_server

        async def capture_server(*args, **kwargs):
            server = await start_server(*args, **kwargs)
            ready.set_result(server)
            return server

        async def tunnel(_args):
            await asyncio.Future()

        async def read():
            return None

        async def write(_text):
            return None

        with patch("framesync.agent.asyncio.start_server", capture_server), patch(
            "framesync.agent.run_ssh_tunnel", tunnel
        ):
            host = asyncio.create_task(
                serve_host(SyncState(read, write), b"s" * 32, SimpleNamespace(local_port=0))
            )
            server = await asyncio.wait_for(ready, 3)
            reader, writer = await asyncio.open_connection(
                "127.0.0.1", server.sockets[0].getsockname()[1]
            )
            try:
                await client_authenticate(reader, writer, b"s" * 32)
                host.cancel()
                await asyncio.wait_for(asyncio.gather(host, return_exceptions=True), 3)
            finally:
                host.cancel()
                writer.close()
                await writer.wait_closed()

    async def test_authentication_and_messages_over_stream(self):
        secret = b"s" * 32
        received = asyncio.get_running_loop().create_future()

        async def handle(reader, writer):
            await server_authenticate(reader, writer, secret)
            received.set_result(await read_message(reader))
            writer.close()
            await writer.wait_closed()

        server = await asyncio.start_server(handle, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            await client_authenticate(reader, writer, secret)
            await send_message(writer, ClipboardUpdate(99, "test", "text").to_message())
            self.assertEqual((await asyncio.wait_for(received, 2))["text"], "dGV4dA==")
            writer.close()
            await writer.wait_closed()
        finally:
            server.close()
            await server.wait_closed()

    async def test_bidirectional_peers_apply_changes_without_echoes(self):
        secret = b"s" * 32
        left_clipboard = {"text": None}
        right_clipboard = {"text": None}
        right_writes = []

        async def left_read():
            return left_clipboard["text"]

        async def left_write(text):
            left_clipboard["text"] = text

        async def right_read():
            return right_clipboard["text"]

        async def right_write(text):
            right_writes.append(text)
            right_clipboard["text"] = text

        left = SyncState(left_read, left_write)
        right = SyncState(right_read, right_write)
        server_peer = None

        async def handle(reader, writer):
            nonlocal server_peer
            await server_authenticate(reader, writer, secret)
            server_peer = Peer(reader, writer, left)
            left.peer = server_peer
            try:
                await server_peer.run()
            except (asyncio.IncompleteReadError, OSError):
                pass
            finally:
                await server_peer.close()

        server = await asyncio.start_server(handle, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        writer = None
        client_task = None
        client_peer = None
        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            await client_authenticate(reader, writer, secret)
            client_peer = Peer(reader, writer, right)
            right.peer = client_peer
            client_task = asyncio.create_task(client_peer.run())
            for _ in range(20):
                if left.peer is not None:
                    break
                await asyncio.sleep(0.01)
            self.assertIsNotNone(left.peer)
            left_clipboard["text"] = "Frame → Windows\n🙂"
            await left.local_change(left_clipboard["text"])
            for _ in range(20):
                if right_clipboard["text"] is not None:
                    break
                await asyncio.sleep(0.01)
            self.assertEqual(right_clipboard["text"], "Frame → Windows\n🙂")
            await right.local_change(right_clipboard["text"])
            self.assertEqual(right_writes, ["Frame → Windows\n🙂"])
        finally:
            server.close()
            if writer is not None:
                writer.transport.abort()
            if server_peer is not None:
                await server_peer.close()
            if client_task is not None:
                client_task.cancel()
            if client_task is not None:
                await asyncio.gather(client_task, return_exceptions=True)
            await server.wait_closed()
