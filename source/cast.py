import usocket as socket
import ssl
import time
from struct import pack, unpack
import gc

# Chromecast Configuration
# Note: Using byte strings (b"...") is common and efficient in MicroPython
THUMB = b"https://storage.googleapis.com/athans/athan_logo.png"

# Default Protobuf Message Fields
_SRC = b"sender-0"
_RECV = b"receiver-0"

# Chromecast Namespaces
_NS_CONN = b"urn:x-cast:com.google.cast.tp.connection"
_NS_RECV = b"urn:x-cast:com.google.cast.receiver"
_NS_MEDIA = b"urn:x-cast:com.google.cast.media"
# Receivers PING senders on this namespace and expect a PONG. A sender that
# never answers is treated as dead and its session is torn down — which from
# our side is indistinguishable from a slow speaker, because the LOAD we sent
# simply never gets acknowledged.
_NS_HEARTBEAT = b"urn:x-cast:com.google.cast.tp.heartbeat"

# Payload markers that mean the receiver rejected us outright, as opposed to
# just being slow. Worth keeping verbatim: they are the difference between
# "speaker was sleepy" and "the media never had a chance".
_REJECTION_MARKERS = (
    b"LOAD_FAILED",
    b"LOAD_CANCELLED",
    b"INVALID_REQUEST",
    b"detailedErrorCode",
    b'"idleReason":"ERROR"',
)
# Signals that audio actually started. The receiver only echoes our title once
# it has populated media metadata, which lags the first MEDIA_STATUS by seconds
# — playerState is in there immediately. Matching on the title alone is what
# made every Kirkland play need a retry for three days.
_PLAYING_STATES = (b'"playerState":"PLAYING"', b'"playerState":"BUFFERING"')
_EMPTY_STATUS = b'"status":[]'
_IDLE_ERROR = b'"idleReason":"ERROR"'
# Bound on what we retain per play. This runs on a device where internal DRAM
# fragmentation is the known enemy, so the diagnostic must not become a leak.
_MAX_SEEN_TYPES = 10
_MAX_ERROR_DETAIL = 160
_MAX_STATUS_SAMPLE = 200

# play_url failure reasons, reported via Chromecast.last_error. These mean very
# different things: NO_SESSION is guaranteed silence (the audio was never even
# requested), while NO_MEDIA_ACK usually means it IS playing and we just didn't
# hear back in time. The caller must not treat them alike.
ERR_NO_SESSION = "no_session"
ERR_NO_MEDIA_ACK = "no_media_ack"

# App ID for the Default Media Receiver (used for audio/video streaming)
_DEFAULT_MEDIA_APP_ID = b"CC1AD845"


def _varint(n):
    """Minimal protobuf varint encoder (bytes)."""
    out = bytearray()
    while n > 0x7F:
        out.append((n & 0x7F) | 0x80)
        n >>= 7
    out.append(n)
    return bytes(out)


def _frame(namespace, payload_utf8, dest=_RECV, src=_SRC):
    """
    Build a CastMessage protobuf frame, preceded by the 4-byte length.
    """
    if isinstance(namespace, str):
        namespace = namespace.encode()
    if isinstance(dest, str):
        dest = dest.encode()
    if isinstance(payload_utf8, str):
        payload_utf8 = payload_utf8.encode()

    # Protobuf body fields
    body = (
        b"\x08\x00"  # protocol_version = 0
        + b"\x12"
        + _varint(len(src))
        + src  # source_id
        + b"\x1a"
        + _varint(len(dest))
        + dest  # destination_id
        + b"\x22"
        + _varint(len(namespace))
        + namespace  # namespace
        + b"\x28\x00"  # payload_type = STRING (0)
        + b"\x32"
        + _varint(len(payload_utf8))
        + payload_utf8  # payload_utf8
    )
    # Prepend 4-byte big-endian length of the body
    return pack(">I", len(body)) + body


class Chromecast(object):
    """A class to handle Chromecast communication and media control."""

    def __init__(self, cast_ip, cast_port, timeout_s=5):
        self.ip = cast_ip
        # Which step play_url last failed at (ERR_* above), or None. Kept as an
        # attribute rather than folded into the return value so play_url still
        # returns a plain True/False — cast.py can be OTA'd on its own, and an
        # older mqtt.py must keep behaving identically against a newer cast.py.
        self.last_error = None
        # Diagnostics for a failed play: what the speaker actually sent back.
        # Without these a rejection and a sleepy speaker look identical, since
        # wait_for_media_ack only ever matched the success pattern.
        self.ping_count = 0
        self.seen_types = []
        self.last_error_detail = None
        self.first_media_status = None
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.settimeout(timeout_s)
        self.s = None

        try:
            # Connect and wrap socket with SSL
            self._sock.connect((self.ip, cast_port))
            self.s = ssl.wrap_socket(self._sock)

            # Send initial CONNECT and GET_STATUS messages
            self._send(_frame(_NS_CONN, b'{"type":"CONNECT"}'))
            self._send(_frame(_NS_RECV, b'{"type":"GET_STATUS","requestId":1}'))

            # After handshake, use shorter timeout for message polling
            self._sock.settimeout(3)
        except Exception:
            self.disconnect()
            raise

    # --- Low-Level Socket Operations (for MicroPython reliability) ---

    def _send(self, data):
        """Send all data on SSL sockets (handles partial writes)."""
        mv = memoryview(data)
        total = 0
        while total < len(data):
            n = self.s.write(mv[total:])
            if n is None:
                # Some MicroPython ports return None; treat as all written
                break
            total += n

    def _read_exact(self, n):
        """Read exactly n bytes or raise (handles partial reads)."""
        chunks = bytearray()
        got = 0
        while got < n:
            chunk = self.s.read(n - got)
            if not chunk:
                raise OSError("socket closed while reading")
            chunks.extend(chunk)
            got += len(chunk)
        return bytes(chunks)

    def read_message(self, max_size=65536):
        """Read one Cast message (4-byte size + protobuf body)."""
        size_bytes = self._read_exact(4)
        siz = unpack(">I", size_bytes)[0]
        if siz <= 0 or siz > max_size:
            raise OSError("invalid cast frame size: %d" % siz)
        return self._read_exact(siz)

    @staticmethod
    def _msg_type(msg):
        """Extract the JSON "type" field from a raw frame, or None.

        Same find-to-next-quote trick _wait_for_transport_id uses; the payload
        is plain JSON embedded in the protobuf body, so no real parse needed."""
        key = b'"type":"'
        i = msg.find(key)
        if i == -1:
            return None
        j = msg.find(b'"', i + len(key))
        if j == -1:
            return None
        return msg[i + len(key) : j]

    def _maybe_pong(self, msg):
        """Answer a Cast heartbeat PING. Returns True if this was a PING.

        Nothing in this file used to handle the heartbeat namespace, so every
        PING was read and dropped. Receivers tear down senders that go quiet,
        which is why listening for a longer window never rescued a stalled
        play — by then the session was already gone."""
        if b'"type":"PING"' not in msg:
            return False
        self.ping_count += 1
        try:
            self._send(_frame(_NS_HEARTBEAT, b'{"type":"PONG"}'))
        except Exception:
            pass  # best-effort; the caller's timeout still governs
        return True

    @staticmethod
    def _payload(msg, limit):
        """The JSON payload of a frame, truncated. Slices from the first brace
        because everything before it is protobuf framing that would only
        garble the alert."""
        start = msg.find(b"{")
        return (msg[start:] if start != -1 else msg)[:limit]

    def _note_discarded(self, msg):
        """Record a message we read but did not match, for the failure alert."""
        if any(marker in msg for marker in _REJECTION_MARKERS):
            if self.last_error_detail is None:
                self.last_error_detail = self._payload(msg, _MAX_ERROR_DETAIL)
        # Keep the first MEDIA_STATUS we rejected, verbatim. If the playerState
        # theory is also wrong, this is the payload that says why, instead of
        # another round of guessing from message types alone.
        if self.first_media_status is None and b'"type":"MEDIA_STATUS"' in msg:
            self.first_media_status = self._payload(msg, _MAX_STATUS_SAMPLE)
        mtype = self._msg_type(msg)
        if mtype and mtype not in self.seen_types:
            if len(self.seen_types) < _MAX_SEEN_TYPES:
                self.seen_types.append(mtype)

    @staticmethod
    def _text(raw):
        """bytes -> str without assuming MicroPython supports decode(errors=)."""
        try:
            return raw.decode()
        except Exception:
            return str(raw)

    @staticmethod
    def _is_playback_started(msg):
        """True when a MEDIA_STATUS says our audio is actually going.

        Deliberately broader than the old test and never narrower: title OR
        playerState. The old test required our own title, which the receiver
        only echoes after populating media metadata — so a perfectly healthy
        play looked like a failure until the metadata caught up. An empty
        status array is excluded: that is the receiver saying it has nothing
        loaded, even though it is technically a MEDIA_STATUS."""
        if b'"type":"MEDIA_STATUS"' not in msg:
            return False
        if _EMPTY_STATUS in msg:
            return False
        if b'"Bilal Cast"' in msg:
            return True
        return any(s in msg for s in _PLAYING_STATES)

    def diagnostics(self):
        """One-line summary of what the speaker sent back, for ntfy."""
        parts = ["pings=%d" % self.ping_count]
        if self.seen_types:
            parts.append("saw=%s" % ",".join(self._text(t) for t in self.seen_types))
        else:
            parts.append("saw=nothing")
        if self.last_error_detail:
            parts.append("reject=%s" % self._text(self.last_error_detail))
        if self.first_media_status:
            parts.append("status=%s" % self._text(self.first_media_status))
        return " ".join(parts)

    # --- Utility Methods for Time (MicroPython compatibility) ---

    @staticmethod
    def _ticks_ms():
        try:
            return time.ticks_ms()
        except AttributeError:
            return int(time.time() * 1000)

    @staticmethod
    def _ticks_diff(a, b):
        try:
            return time.ticks_diff(a, b)
        except AttributeError:
            return a - b

    # --- Core Chromecast Methods ---

    def set_volume(self, volume):
        """Set the volume level of the Chromecast (0.0 to 1.0)."""
        if isinstance(volume, float):
            # Format to two decimal places, removing trailing zeros/dot
            v = ("%.2f" % volume).rstrip("0").rstrip(".")
        else:
            v = str(volume)

        payload = (
            b'{"type":"SET_VOLUME","volume":{"level":'
            + v.encode()
            + b'},"requestId":2}'
        )
        self._send(_frame(_NS_RECV, payload, dest=_RECV))

    def play_url(self, url, volume=None):
        """Play audio from specified URL on the Chromecast.

        Args:
            url: The media URL to play
            volume: Optional volume level (0.0 to 1.0). If None, volume is not changed.
        """
        self.last_error = None
        # Reset diagnostics here rather than in wait_for_media_ack — the caller
        # calls that a second time for an extra listen window, and the counts
        # must accumulate across both windows of the same play.
        self.ping_count = 0
        self.seen_types = []
        self.last_error_detail = None
        self.first_media_status = None

        if isinstance(url, str):
            url_b = url.encode()
        else:
            url_b = url

        # 1. Launch the Default Media Receiver app
        self._send(
            _frame(
                _NS_RECV,
                b'{"type":"LAUNCH","appId":"'
                + _DEFAULT_MEDIA_APP_ID
                + b'","requestId":3}',
            )
        )

        # 2. Wait for the new session's transport ID
        transport_id = self._wait_for_transport_id(timeout_ms=8000)
        if not transport_id:
            # Speaker never handed us a session, so LOAD below was never sent.
            # Nothing is playing and nothing can be interrupted by a retry.
            print("Error: Failed to get transport ID for new session.")
            self.last_error = ERR_NO_SESSION
            return False

        # 3. Connect to the media session transport
        self._send(_frame(_NS_CONN, b'{"type":"CONNECT"}', dest=transport_id))

        # 4. Set volume AFTER app is running (before loading media)
        if volume is not None:
            self.set_volume(volume)
            time.sleep(0.3)  # Let volume settle before loading media
            print(f"Volume set to {volume} after app launch")

        self._send(
            _frame(_NS_MEDIA, b'{"type":"GET_STATUS","requestId":4}', dest=transport_id)
        )

        # 5. Construct and send the LOAD command
        load_payload = (
            b'{"media":{"contentId":"'
            + url_b
            + b'","streamType":"BUFFERED","contentType":"audio/mp3","metadata":'
            b'{"metadataType":0,"title":"Bilal Cast","thumb":"'
            + THUMB
            + b'","images":[{"url":"'
            + THUMB
            + b'"}]}},'
            b'"type":"LOAD","autoplay":true,"customData":{},"requestId":5,"sessionId":"'
            + transport_id
            + b'"}'
        )
        self._send(_frame(_NS_MEDIA, load_payload, dest=transport_id))

        # 6. Wait for MEDIA_STATUS confirmation with timeout
        return self.wait_for_media_ack(8000)

    def wait_for_media_ack(self, timeout_ms=8000):
        """Listen for MEDIA_STATUS confirming our media started.

        Split out of play_url so the caller can listen for another window on
        the SAME connection when the first one expires. An idle speaker can
        take longer than one window to get going, and re-sending LOAD would
        restart audio that is already playing.
        """
        start = self._ticks_ms()
        while self._ticks_diff(self._ticks_ms(), start) < timeout_ms:
            try:
                status = self.read_message()
            except OSError:
                time.sleep(0.3)
                continue  # Retry on socket timeout, don't give up
            if self._is_playback_started(status):
                self.last_error = None
                return True
            # Answer heartbeats before discarding anything, then record what
            # this was so a failure can say what the speaker actually replied.
            if not self._maybe_pong(status):
                self._note_discarded(status)
                if _IDLE_ERROR in status:
                    # The receiver has given up on the media. Waiting out the
                    # rest of the window (and then a whole second one) only
                    # delays an alert whose answer is already known.
                    self.last_error = ERR_NO_MEDIA_ACK
                    return False
            time.sleep(0.2)  # Brief delay to avoid busy-looping

        # LOAD went out on the socket and nothing came back. This used to claim
        # the audio was "most likely playing" — a confirmed-silent Asr on
        # 2026-08-04 disproved that. Nothing here verifies the receiver ever
        # accepted the LOAD; _send only proves bytes reached the socket. Treat
        # this as a possible silent failure and read diagnostics() for what the
        # speaker actually sent instead.
        self.last_error = ERR_NO_MEDIA_ACK
        return False

    def _wait_for_transport_id(self, timeout_ms=4000):
        """
        Wait for a message containing a transportId associated with the launched App ID.
        This is the fix for session ID confusion.
        """
        start = self._ticks_ms()
        key = b'"transportId":"'

        while self._ticks_diff(self._ticks_ms(), start) < timeout_ms:
            try:
                msg = self.read_message()
            except OSError:
                time.sleep(0.3)
                continue  # Don't abort on single socket timeout

            # PINGs arrive during app launch too, not just during media load —
            # ignoring them here would let the session die before LOAD is sent.
            if self._maybe_pong(msg):
                continue

            # CRITICAL FIX: Ensure the message is for the Default Media Receiver app
            if _DEFAULT_MEDIA_APP_ID in msg:
                i = msg.find(key)
                if i != -1:
                    j = msg.find(b'"', i + len(key))
                    if j != -1:
                        # transportId found and confirmed to be for the newly launched app
                        return msg[i + len(key) : j]

        return None

    def disconnect(self):
        """Close the connection to the Chromecast device."""
        try:
            if self.s:
                self.s.close()
        finally:
            try:
                self._sock.close()
            except Exception:
                pass
        # Perform garbage collection if running on MicroPython
        gc.collect()
