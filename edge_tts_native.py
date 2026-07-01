"""Built-in Edge TTS client -- speaks Microsoft's free Neural voices with zero
external install (no edge-tts CLI, no aiohttp, no pip).

The public `edge-tts` package is *not* pure Python: it pulls in aiohttp, whose
compiled C extensions would have to be shipped as a per-OS, per-Python-version
wheel inside the add-on -- exactly what we're trying to avoid. Instead this
module re-implements just the synthesis path of edge-tts 7.2.8 over the standard
library: a minimal RFC 6455 WebSocket client on top of `socket` + `ssl`, the
Sec-MS-GEC DRM token via `hashlib`, and the same SSML / framing the service
expects. The result drops into Anki's bundled Python on Linux, macOS, and Windows.

Scope is deliberately narrow: one short text -> one MP3 (the add-on only ever
synthesizes a word or a single sentence), so there is no streaming, no text
chunking, and no word-boundary metadata. The protocol constants below are lifted
verbatim from edge-tts and may need refreshing if Microsoft changes them; the
caller (tts.py) keeps the edge-tts CLI as an automatic fallback for that case.
"""

import base64
import hashlib
import os
import socket
import ssl
import struct
import tempfile
import time
import uuid
from xml.sax.saxutils import escape

# --- Protocol constants (from edge_tts.constants / drm, v7.2.8) ------------- #
_TRUSTED_CLIENT_TOKEN = "6A5AA1D4EAFF4E9FB37E23D68491D6F4"
_BASE_HOST = "speech.platform.bing.com"
_WSS_PATH = "/consumer/speech/synthesize/readaloud/edge/v1?TrustedClientToken=" + _TRUSTED_CLIENT_TOKEN
_CHROMIUM_FULL_VERSION = "143.0.3650.75"
_CHROMIUM_MAJOR = _CHROMIUM_FULL_VERSION.split(".", 1)[0]
_SEC_MS_GEC_VERSION = "1-" + _CHROMIUM_FULL_VERSION
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/%s.0.0.0 Safari/537.36 Edg/%s.0.0.0"
    % (_CHROMIUM_MAJOR, _CHROMIUM_MAJOR)
)
_ORIGIN = "chrome-extension://jdiccldimpdaibmpdkjnbmckianbfold"
_WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"  # RFC 6455 magic
_WIN_EPOCH = 11644473600  # seconds between 1601-01-01 and 1970-01-01

# Built once and shared: create_default_context() loads the system CA bundle,
# which costs ~25 ms -- wasteful to repeat per clip when several synthesize
# concurrently. SSLContext is safe to share across threads. Lazy so importing
# this module stays free until TTS is actually used.
_SSL_CTX = None


def _ssl_context():
    global _SSL_CTX
    if _SSL_CTX is None:
        _SSL_CTX = ssl.create_default_context()
    return _SSL_CTX


class EdgeTTSError(RuntimeError):
    """A failure in the built-in TTS engine (network, handshake, or no audio)."""


def _generate_sec_ms_gec():
    """The Sec-MS-GEC DRM token: SHA-256 of (Windows-filetime ticks rounded to
    5 minutes) + the trusted client token. Mirrors edge_tts.drm exactly."""
    ticks = time.time() + _WIN_EPOCH
    ticks -= ticks % 300              # round down to the nearest 5 minutes
    ticks *= 1e9 / 100                # seconds -> 100-nanosecond intervals
    to_hash = "%.0f%s" % (ticks, _TRUSTED_CLIENT_TOKEN)
    return hashlib.sha256(to_hash.encode("ascii")).hexdigest().upper()


def _connect_id():
    return uuid.uuid4().hex


def _clean(text):
    """Drop control characters the service rejects (e.g. the vertical tab found
    in OCR'd text), then XML-escape for SSML."""
    cleaned = "".join(ch if ch in "\t\n\r" or ord(ch) >= 0x20 else " " for ch in text)
    return escape(cleaned)


def _mkssml(voice, rate, pitch, volume, text):
    return (
        "<speak version='1.0' xmlns='http://www.w3.org/2001/10/synthesis' "
        "xml:lang='en-US'>"
        "<voice name='%s'>"
        "<prosody pitch='%s' rate='%s' volume='%s'>%s</prosody>"
        "</voice></speak>" % (voice, pitch, rate, volume, _clean(text))
    )


def _date_to_string():
    return time.strftime(
        "%a %b %d %Y %H:%M:%S GMT+0000 (Coordinated Universal Time)", time.gmtime()
    )


# --------------------------------------------------------------------------- #
# Minimal RFC 6455 WebSocket client (text out, text+binary in; client-masked). #
# --------------------------------------------------------------------------- #
class _WebSocket:
    def __init__(self, host, path, timeout):
        self._deadline = time.monotonic() + timeout
        self._buf = b""
        raw = socket.create_connection((host, 443), timeout=self._remaining())
        self._sock = _ssl_context().wrap_socket(raw, server_hostname=host)
        self._handshake(host, path)

    def _remaining(self):
        left = self._deadline - time.monotonic()
        if left <= 0:
            raise EdgeTTSError("Timed out talking to the TTS service.")
        return left

    def _handshake(self, host, path):
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        # No Sec-WebSocket-Extensions: we deliberately skip permessage-deflate so
        # the server replies with uncompressed frames (the MP3 payload is already
        # compressed), which keeps this client dependency-free.
        req = (
            "GET %s HTTP/1.1\r\n"
            "Host: %s\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            "Sec-WebSocket-Key: %s\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            "Origin: %s\r\n"
            "User-Agent: %s\r\n"
            "Pragma: no-cache\r\n"
            "Cache-Control: no-cache\r\n"
            "Accept-Language: en-US,en;q=0.9\r\n"
            "\r\n" % (path, host, key, _ORIGIN, _USER_AGENT)
        )
        self._sock.sendall(req.encode("ascii"))
        header = self._read_until(b"\r\n\r\n")
        status = header.split(b"\r\n", 1)[0]
        if b"101" not in status:
            raise EdgeTTSError(
                "TTS handshake rejected: %s" % status.decode("latin-1", "replace")
            )
        expected = base64.b64encode(
            hashlib.sha1((key + _WS_GUID).encode("ascii")).digest()
        ).decode("ascii")
        if expected.encode("ascii") not in header:
            raise EdgeTTSError("TTS handshake failed (bad Sec-WebSocket-Accept).")

    def _read_until(self, marker):
        while marker not in self._buf:
            self._sock.settimeout(self._remaining())
            chunk = self._sock.recv(4096)
            if not chunk:
                raise EdgeTTSError("TTS service closed the connection early.")
            self._buf += chunk
        head, self._buf = self._buf.split(marker, 1)
        return head + marker

    def _recv_exact(self, n):
        while len(self._buf) < n:
            self._sock.settimeout(self._remaining())
            chunk = self._sock.recv(65536)
            if not chunk:
                raise EdgeTTSError("TTS service closed the connection early.")
            self._buf += chunk
        out, self._buf = self._buf[:n], self._buf[n:]
        return out

    def send_text(self, text):
        self._send_frame(0x1, text.encode("utf-8"))

    def _send_frame(self, opcode, payload):
        header = bytearray([0x80 | opcode])  # FIN + opcode
        length = len(payload)
        if length < 126:
            header.append(0x80 | length)             # MASK bit + length
        elif length < 65536:
            header.append(0x80 | 126)
            header += struct.pack(">H", length)
        else:
            header.append(0x80 | 127)
            header += struct.pack(">Q", length)
        mask = os.urandom(4)
        header += mask
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        self._sock.sendall(bytes(header) + masked)

    def recv_message(self):
        """Read one (possibly fragmented) WebSocket message.

        Returns (opcode, payload). Control pings are answered transparently and
        skipped; a close frame surfaces as opcode 0x8.
        """
        first_opcode = None
        data = b""
        while True:
            b0, b1 = self._recv_exact(2)
            fin = b0 & 0x80
            opcode = b0 & 0x0F
            masked = b1 & 0x80
            length = b1 & 0x7F
            if length == 126:
                length = struct.unpack(">H", self._recv_exact(2))[0]
            elif length == 127:
                length = struct.unpack(">Q", self._recv_exact(8))[0]
            mask = self._recv_exact(4) if masked else b""
            payload = self._recv_exact(length)
            if masked:
                payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
            if opcode == 0x9:        # ping -> pong, keep reading
                self._send_frame(0xA, payload)
                continue
            if opcode == 0x8:        # close
                return 0x8, payload
            if opcode != 0x0:        # not a continuation: remember the type
                first_opcode = opcode
            data += payload
            if fin:
                return first_opcode, data

    def close(self):
        try:
            self._send_frame(0x8, b"")
        except Exception:
            pass
        try:
            self._sock.close()
        except Exception:
            pass


def _parse_path(header_blob):
    """Pull the `Path` header value out of a textual header block."""
    for line in header_blob.split(b"\r\n"):
        if line[:5].lower() == b"path:":
            return line[5:].strip()
    return None


def synthesize(voice, rate, pitch, text, timeout=60, volume="+0%"):
    """Synthesize `text` in `voice` and return the path to a temp .mp3.

    `rate`/`pitch`/`volume` are SSML prosody strings ("+25%", "+0Hz", "+0%").
    The caller owns the returned file and must delete it. Raises EdgeTTSError on
    any network/protocol failure or if the service returns no audio.
    """
    host = _BASE_HOST
    path = (
        _WSS_PATH
        + "&Sec-MS-GEC=" + _generate_sec_ms_gec()
        + "&Sec-MS-GEC-Version=" + _SEC_MS_GEC_VERSION
        + "&ConnectionId=" + _connect_id()
    )
    ws = _WebSocket(host, path, timeout)
    audio = bytearray()
    try:
        # 1) Audio output format (and disable boundary metadata we don't use).
        ws.send_text(
            "X-Timestamp:%s\r\n"
            "Content-Type:application/json; charset=utf-8\r\n"
            "Path:speech.config\r\n\r\n"
            '{"context":{"synthesis":{"audio":{"metadataoptions":{'
            '"sentenceBoundaryEnabled":"false","wordBoundaryEnabled":"false"},'
            '"outputFormat":"audio-24khz-48kbitrate-mono-mp3"}}}}\r\n'
            % _date_to_string()
        )
        # 2) The SSML request itself.
        ws.send_text(
            "X-RequestId:%s\r\n"
            "Content-Type:application/ssml+xml\r\n"
            "X-Timestamp:%sZ\r\n"   # trailing Z mirrors Microsoft Edge's own bug
            "Path:ssml\r\n\r\n%s"
            % (_connect_id(), _date_to_string(),
               _mkssml(voice, rate, pitch, volume, text))
        )
        # 3) Collect audio frames until the service signals turn.end.
        while True:
            opcode, msg = ws.recv_message()
            if opcode == 0x8:
                break
            if opcode == 0x1:        # text: control/metadata frames
                blob = msg.split(b"\r\n\r\n", 1)[0]
                if _parse_path(blob) == b"turn.end":
                    break
            elif opcode == 0x2:      # binary: 2-byte header length, then headers, then audio
                if len(msg) < 2:
                    continue
                header_length = int.from_bytes(msg[:2], "big")
                # The 2-byte prefix holds the length of the header *text* only, so
                # the headers span msg[2 : header_length + 2] and the audio bytes
                # begin at header_length + 2 -- exactly edge-tts'
                # get_headers_and_data. (Using header_length as the audio offset
                # instead prepends a stray "\r\n" to every chunk, corrupting an
                # MP3 frame boundary ~every 140 ms -> choppy playback.)
                if header_length + 2 > len(msg):
                    raise EdgeTTSError("Malformed audio frame from TTS service.")
                if _parse_path(msg[2:header_length + 2]) == b"audio":
                    audio += msg[header_length + 2:]
    finally:
        ws.close()

    if not audio:
        raise EdgeTTSError("The TTS service returned no audio.")
    fd, out_path = tempfile.mkstemp(suffix=".mp3")
    os.close(fd)
    try:
        with open(out_path, "wb") as f:
            f.write(audio)
    except Exception:
        if os.path.exists(out_path):
            os.remove(out_path)
        raise
    return out_path
