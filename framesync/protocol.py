import asyncio
import base64
import hashlib
import hmac
import json
import secrets
import struct
from dataclasses import dataclass
from typing import Any


MAX_TEXT_BYTES = 1_048_576
MAX_FRAME_BYTES = MAX_TEXT_BYTES * 2
STREAM_LIMIT = MAX_FRAME_BYTES + 4


@dataclass(frozen=True, order=True)
class ClipboardUpdate:
    timestamp_ns: int
    origin: str
    text: str

    def to_message(self) -> dict[str, Any]:
        data = self.text.encode("utf-8")
        if len(data) > MAX_TEXT_BYTES:
            raise ValueError(f"clipboard text exceeds {MAX_TEXT_BYTES} bytes")
        return {
            "type": "update",
            "timestamp_ns": self.timestamp_ns,
            "origin": self.origin,
            "text": base64.b64encode(data).decode("ascii"),
        }

    @classmethod
    def from_message(cls, message: dict[str, Any]) -> "ClipboardUpdate":
        if message.get("type") != "update":
            raise ValueError("unexpected message type")
        timestamp_ns = message.get("timestamp_ns")
        origin = message.get("origin")
        encoded = message.get("text")
        if (
            not isinstance(timestamp_ns, int)
            or timestamp_ns < 0
            or not isinstance(origin, str)
            or not origin
            or len(origin) > 128
            or not isinstance(encoded, str)
            or len(encoded) > MAX_FRAME_BYTES
        ):
            raise ValueError("invalid clipboard update")
        try:
            data = base64.b64decode(encoded, validate=True)
            text = data.decode("utf-8")
        except (ValueError, UnicodeDecodeError) as error:
            raise ValueError("clipboard update is not valid UTF-8 data") from error
        if len(data) > MAX_TEXT_BYTES:
            raise ValueError(f"clipboard text exceeds {MAX_TEXT_BYTES} bytes")
        return cls(timestamp_ns, origin, text)


async def send_message(writer: asyncio.StreamWriter, message: dict[str, Any]) -> None:
    payload = json.dumps(message, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(payload) > MAX_FRAME_BYTES:
        raise ValueError("protocol frame is too large")
    writer.write(struct.pack("!I", len(payload)) + payload)
    await writer.drain()


async def read_message(reader: asyncio.StreamReader) -> dict[str, Any]:
    header = await reader.readexactly(4)
    (size,) = struct.unpack("!I", header)
    if size == 0 or size > MAX_FRAME_BYTES:
        raise ValueError("invalid protocol frame length")
    message = json.loads((await reader.readexactly(size)).decode("utf-8"))
    if not isinstance(message, dict):
        raise ValueError("protocol message must be an object")
    return message


async def server_authenticate(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    secret: bytes,
) -> None:
    nonce = secrets.token_bytes(32)
    await send_message(
        writer,
        {"type": "challenge", "nonce": base64.b64encode(nonce).decode("ascii")},
    )
    response = await read_message(reader)
    if response.get("type") != "auth" or not isinstance(response.get("proof"), str):
        raise ValueError("invalid authentication response")
    expected = hmac.new(secret, nonce, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(response["proof"], expected):
        raise ValueError("clipboard peer authentication failed")


async def client_authenticate(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    secret: bytes,
) -> None:
    challenge = await read_message(reader)
    if challenge.get("type") != "challenge" or not isinstance(challenge.get("nonce"), str):
        raise ValueError("invalid authentication challenge")
    try:
        nonce = base64.b64decode(challenge["nonce"], validate=True)
    except ValueError as error:
        raise ValueError("invalid authentication nonce") from error
    if len(nonce) != 32:
        raise ValueError("invalid authentication nonce length")
    proof = hmac.new(secret, nonce, hashlib.sha256).hexdigest()
    await send_message(writer, {"type": "auth", "proof": proof})
