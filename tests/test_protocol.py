import asyncio
import base64
import unittest

from framesync.protocol import (
    MAX_TEXT_BYTES,
    ClipboardUpdate,
    client_authenticate,
    read_message,
    send_message,
    server_authenticate,
)
from framesync.sync import Peer, SyncState


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


class WireTests(unittest.IsolatedAsyncioTestCase):
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
