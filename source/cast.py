# Description: This file contains the Chromecast class that handles connecting to the Chromecast and casting to it
# When a message is sent to the ESP32 via MQTT, a Chromecast object is created

# Import necessary modules
# usocket: MicroPython's socket implementation for network connections
# ssl: For secure socket layer connections
# ustruct: For packing/unpacking binary data
# ujson: MicroPython's JSON implementation
# utime: MicroPython's time implementation
import usocket as socket
import ssl
from ustruct import pack, unpack
import ujson as json
import utime as time

# TODO: update this to use S3 instead of Google Cloud Storage and new logo
# thumbnail that shows up on Google Home with screen
THUMB = "https://storage.googleapis.com/athans/athan_logo.png"


def calc_variant(value):
    """
    Encodes an integer value into a variable-length format using protocol buffers varint encoding.
    This is used in the Google Cast protocol for message length encoding.
    
    Args:
        value: Integer to encode
        
    Returns:
        bytes: Encoded value in protocol buffer varint format
    """
    byte_list = []
    while value > 0x7F:  # While value is larger than 7 bits
        byte_list += [value & 0x7F | 0x80]  # Take 7 bits and set the MSB
        value >>= 7  # Shift right by 7 bits
    return bytes(byte_list + [value])


class Chromecast(object):
    """
    A class to handle Chromecast communication and media control.
    Uses the Google Cast protocol to connect to and control Chromecast devices.
    """

    def __init__(self, cast_ip, cast_port):
        """
        Initialize connection to Chromecast device.
        
        Args:
            cast_ip: IP address of the Chromecast
            cast_port: Port number for the Chromecast (typically 8009)
        """
        self.ip = cast_ip
        # Create TCP socket and establish SSL connection
        self.s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.s.connect((self.ip, cast_port))
        self.s = ssl.wrap_socket(self.s)
        
        # Send initial CONNECT message to establish connection
        self.s.write(
            b'\x00\x00\x00Y\x08\x00\x12\x08sender-0\x1a\nreceiver-0"(urn:x-cast:com.google.cast.tp.connection(\x002\x13{"type": "CONNECT"}'
        )
        # Request initial device status
        self.s.write(
            b'\x00\x00\x00g\x08\x00\x12\x08sender-0\x1a\nreceiver-0"#urn:x-cast:com.google.cast.receiver(\x002&{"type": "GET_STATUS", "requestId": 1}'
        )

    def set_volume(self, volume):
        """
        Set the volume level of the Chromecast.
        
        Args:
            volume: String representing volume level (e.g., '0.50' for 50%)
        """
        r_volmsg = b'\x00\x00\x00\x81\x08\x00\x12\x08sender-0\x1a\nreceiver-0"#urn:x-cast:com.google.cast.receiver(\x002@{"type": "SET_VOLUME", "volume": {"level": ###}, "requestId": 2}'
        r_volmsg = r_volmsg.replace(b"###", bytes(volume, "utf-8"))
        self.s.write(r_volmsg)

    def play_url(self, url):
        """
        Play audio from specified URL on the Chromecast.
        
        Args:
            url: String URL of the audio to play
            
        Returns:
            bool: True if playback was successfully started, False otherwise
        """
        # Launch the Default Media Receiver app (CC1AD845)
        self.s.write(
            (
                b'\x00\x00\x00x\x08\x00\x12\x08sender-0\x1a\nreceiver-0"#urn:x-cast:com.google.cast.receiver(\x0027{"type": "LAUNCH", "appId": "CC1AD845", "requestId": 3}'
            )
        )

        # Need to establish media channel twice to ensure reliable connection
        for i in range(2):
            transport_id = None
            # Keep trying to get transport ID from device responses
            while not transport_id:
                try:
                    response = self.read_message()
                    transport_id = response.split(b'"transportId"')[1].split(b'"')[1]
                except:
                    pass
                    
            # Send CONNECT message with device and client information
            self.s.write(
                b'\x00\x00\x01Q\x08\x00\x12\x08sender-0\x1a$%s"(urn:x-cast:com.google.cast.tp.connection(\x002\xf0\x01{"type": "CONNECT", "origin": {}, "userAgent": "PyChromecast", "senderInfo": {"sdkType": 2, "version": "15.605.1.3", "browserVersion": "44.0.2403.30", "platform": 4, "systemVersion": "Macintosh; Intel Mac OS X10_10_3", "connectionType": 1}}'
                % transport_id
            )
            # Request media status
            self.s.write(
                b'\x00\x00\x00~\x08\x00\x12\x08sender-0\x1a$%s" urn:x-cast:com.google.cast.media(\x002&{"type": "GET_STATUS", "requestId": 4}'
                % transport_id
            )

        # Prepare media playback message with metadata
        payload = json.dumps(
            {
                "media": {
                    "contentId": url,
                    "streamType": "BUFFERED",
                    "contentType": "audio/mp3",
                    "metadata": {
                        "title": "Bilal Cast",
                        "metadataType": 0,
                        "thumb": THUMB,
                        "images": [{"url": THUMB}],
                    },
                },
                "type": "LOAD",
                "autoplay": True,
                "customData": {},
                "requestId": 5,
                "sessionId": transport_id,
            }
        )
        
        # Construct final message with proper protocol formatting
        msg = (
            (
                b'\x08\x00\x12\x08sender-0\x1a$%s" urn:x-cast:com.google.cast.media(\x002'
                % transport_id
            )
            + calc_variant(len(payload))
            + payload
        )
        
        # Attempt to send play message multiple times if needed
        # Sometimes first attempt fails if connection was idle
        for i in range(2):
            self.s.write(pack(">I", len(msg)) + msg)
            # Check responses for confirmation of media load
            for _ in range(4):
                status = self.read_message()
                if status.find(b'"title":"Bilal Cast"') != -1:
                    return True
            
        return False  # Return False if media failed to load after all attempts

    def read_message(self):
        """
        Read a message from the Chromecast device.
        
        Returns:
            bytes: The message content received from the device
        """
        siz = unpack(">I", self.s.read(4))[0]  # Read message size (4 bytes, big endian)
        status = self.s.read(siz)  # Read the actual message content
        return status

    def disconnect(self):
        """
        Close the connection to the Chromecast device.
        """
        self.s.close()