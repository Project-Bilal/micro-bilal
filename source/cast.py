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
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.settimeout(timeout_s)

        # Connect and wrap socket with SSL
        self._sock.connect((self.ip, cast_port))
        self.s = ssl.wrap_socket(self._sock)

        # Send initial CONNECT and GET_STATUS messages
        self._send(_frame(_NS_CONN, b'{"type":"CONNECT"}'))
        self._send(_frame(_NS_RECV, b'{"type":"GET_STATUS","requestId":1}'))

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
        transport_id = self._wait_for_transport_id(timeout_ms=5000)
        if not transport_id:
            print("Error: Failed to get transport ID for new session.")
            return False

        # 3. Connect to the specific media session transport
        self._send(_frame(_NS_CONN, b'{"type":"CONNECT"}', dest=transport_id))
        self._send(
            _frame(_NS_MEDIA, b'{"type":"GET_STATUS","requestId":4}', dest=transport_id)
        )

        # 4. Construct and send the LOAD command
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

        # 5. Check for successful MEDIA_STATUS response
        for _ in range(6):
            try:
                status = self.read_message()
            except OSError:
                break
            # Look for the MEDIA_STATUS type and the custom title to confirm load
            if b'"type":"MEDIA_STATUS"' in status and b'"Bilal Cast"' in status:
                # 6. Set volume after media is loaded (if specified)
                if volume is not None:
                    self.set_volume(volume)
                    print(f"Volume set to {volume} after media loaded")
                return True

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
                break

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
            self.s.close()
        finally:
            try:
                self._sock.close()
            except Exception:
                pass
        # Perform garbage collection if running on MicroPython
        gc.collect()
