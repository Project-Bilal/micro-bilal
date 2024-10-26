# Description: This file contains the Chromecast class that handles connecting to the Chromecast and casting to it
# When a message is sent to the ESP32 via MQTT, a Chromecast object is created
import usocket as socket
import ssl
from ustruct import pack, unpack
import ujson as json
import utime as time

# TODO: update this to use S3 instead of Google Cloud Storage and new logo
# thumbnail that shows up on Google Home with screen
THUMB = "https://storage.googleapis.com/athans/athan_logo.png"


# encoding an integer value into a variable-length format
# commonly used in situations where the size of the number to be stored is not constant
def calc_variant(value):
    byte_list = []
    while value > 0x7F:
        byte_list += [value & 0x7F | 0x80]
        value >>= 7
    return bytes(byte_list + [value])


# Chromecast class that handles connecting to the Chromecast and casting to it
class Chromecast(object):
    # needs two arguments: the IP address of the Chromecast and the port to connect to
    def __init__(self, cast_ip, cast_port):
        self.ip = cast_ip
        self.s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.s.connect((self.ip, cast_port))
        self.s = ssl.wrap_socket(self.s)
        self.s.write(
            b'\x00\x00\x00Y\x08\x00\x12\x08sender-0\x1a\nreceiver-0"(urn:x-cast:com.google.cast.tp.connection(\x002\x13{"type": "CONNECT"}'
        )
        self.s.write(
            b'\x00\x00\x00g\x08\x00\x12\x08sender-0\x1a\nreceiver-0"#urn:x-cast:com.google.cast.receiver(\x002&{"type": "GET_STATUS", "requestId": 1}'
        )

    # sets the volume of the Chromecast, volume must be in this format 'X.XX'
    def set_volume(self, volume):
        r_volmsg = b'\x00\x00\x00\x81\x08\x00\x12\x08sender-0\x1a\nreceiver-0"#urn:x-cast:com.google.cast.receiver(\x002@{"type": "SET_VOLUME", "volume": {"level": ###}, "requestId": 2}'
        r_volmsg = r_volmsg.replace(b"###", bytes(volume, "utf-8"))
        self.s.write(r_volmsg)

    # plays the audio from the url passed in on the connected Chromecast
    def play_url(self, url):
        # appId is a constant for media player based on Google Cast SDK
        self.s.write(
            (
                b'\x00\x00\x00x\x08\x00\x12\x08sender-0\x1a\nreceiver-0"#urn:x-cast:com.google.cast.receiver(\x0027{"type": "LAUNCH", "appId": "CC1AD845", "requestId": 3}'
            )
        )

        # need to loop through this twice before sending message to play audio
        for i in range(2):
            transport_id = None
            while not transport_id:
                try:
                    response = self.read_message()
                    transport_id = response.split(b'"transportId"')[1].split(b'"')[1]
                except:
                    pass
            self.s.write(
                b'\x00\x00\x01Q\x08\x00\x12\x08sender-0\x1a$%s"(urn:x-cast:com.google.cast.tp.connection(\x002\xf0\x01{"type": "CONNECT", "origin": {}, "userAgent": "PyChromecast", "senderInfo": {"sdkType": 2, "version": "15.605.1.3", "browserVersion": "44.0.2403.30", "platform": 4, "systemVersion": "Macintosh; Intel Mac OS X10_10_3", "connectionType": 1}}'
                % transport_id
            )
            self.s.write(
                b'\x00\x00\x00~\x08\x00\x12\x08sender-0\x1a$%s" urn:x-cast:com.google.cast.media(\x002&{"type": "GET_STATUS", "requestId": 4}'
                % transport_id
            )

        payload = json.dumps(
            {
                "media": {
                    "contentId": url,
                    "streamType": "BUFFERED",
                    "contentType": "audio/mp3",
                    # Metadata here is configurable if we wanted to change the title of the audio and the thumbnail, but only applies to Chromecasts with screens
                    "metadata": {
                        "title": "bilal_cast",
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
        msg = (
            (
                b'\x08\x00\x12\x08sender-0\x1a$%s" urn:x-cast:com.google.cast.media(\x002'
                % transport_id
            )
            + calc_variant(len(payload))
            + payload
        )
        
        # need to try sending the message a few times
        # if not connected for a while the first attempt will fail
        # check the 3rd status for each attempt until successful
        for i in range(2):
            self.s.write(pack(">I", len(msg)) + msg)
            for _ in range(4):
                status = self.read_message()
                if status.find(b'"title":"bilal_cast"') != -1:
                    return True
            
        return False #if we arrive here the message didn't work

    def read_message(self):
        siz = unpack(">I", self.s.read(4))[0]
        status = self.s.read(siz)
        return status

    def disconnect(self):
        self.s.close()

