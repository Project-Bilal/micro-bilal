import usocket as socket
import ussl as ssl
import ure as re
from ustruct import pack, unpack
import ujson

# thumbnail that shows up on Google Home with screen
THUMB = "https://storage.googleapis.com/athans/athan_logo.png"

def calc_variant(value):
        byte_list = []
        while value > 0x7F:
            byte_list += [value & 0x7F | 0x80]
            value >>= 7
        return bytes(byte_list + [value])

class Chromecast(object):        
    
    def __init__(self, cast_ip, cast_port):
        self.ip = cast_ip
        self.s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.s.connect((self.ip, cast_port))
        self.s = ssl.wrap_socket(self.s)
        self.s.write(b'\x00\x00\x00Y\x08\x00\x12\x08sender-0\x1a\nreceiver-0"(urn:x-cast:com.google.cast.tp.connection(\x002\x13{"type": "CONNECT"}')
        self.s.write(b'\x00\x00\x00g\x08\x00\x12\x08sender-0\x1a\nreceiver-0"#urn:x-cast:com.google.cast.receiver(\x002&{"type": "GET_STATUS", "requestId": 1}')
    
    def set_volume(self, volume):
        r_volmsg = b'\x00\x00\x00\x81\x08\x00\x12\x08sender-0\x1a\nreceiver-0"#urn:x-cast:com.google.cast.receiver(\x002@{"type": "SET_VOLUME", "volume": {"level": ###}, "requestId": 2}'
        r_volmsg = r_volmsg.replace(b'###', bytes(volume, 'utf-8'))
        self.s.write(r_volmsg)
    
    def play_url(self, url):
        
        # appId is a constant for media player
        self.s.write((b'\x00\x00\x00x\x08\x00\x12\x08sender-0\x1a\nreceiver-0"#urn:x-cast:com.google.cast.receiver(\x0027{"type": "LAUNCH", "appId": "CC1AD845", "requestId": 3}'))
        
        # need to loop through this twice before sending message to play audio
        for i in range(2):
            transport_id = None
            while not transport_id:
                try:
                    response = self.read_message()
                    transport_id = response.split(b'"transportId"')[1].split(b'"')[1]
                except:
                    pass
            self.s.write(b'\x00\x00\x01Q\x08\x00\x12\x08sender-0\x1a$%s"(urn:x-cast:com.google.cast.tp.connection(\x002\xf0\x01{"type": "CONNECT", "origin": {}, "userAgent": "PyChromecast", "senderInfo": {"sdkType": 2, "version": "15.605.1.3", "browserVersion": "44.0.2403.30", "platform": 4, "systemVersion": "Macintosh; Intel Mac OS X10_10_3", "connectionType": 1}}' % transport_id)
            self.s.write(b'\x00\x00\x00~\x08\x00\x12\x08sender-0\x1a$%s" urn:x-cast:com.google.cast.media(\x002&{"type": "GET_STATUS", "requestId": 4}' % transport_id)
        
        payload = ujson.dumps({"media": {"contentId": url, "streamType": "BUFFERED", "contentType": "audio/mp3", "metadata": {"title":"Bilal Cast", "metadataType":0, "thumb":THUMB, "images":[{"url": THUMB}]}}, "type": "LOAD", "autoplay": True, "customData": {}, "requestId": 5, "sessionId": transport_id})
        msg = (b'\x08\x00\x12\x08sender-0\x1a$%s" urn:x-cast:com.google.cast.media(\x002' % transport_id) + calc_variant(len(payload)) + payload
        self.s.write(pack(">I", len(msg)) + msg)
    
    def read_message(self):
        siz = unpack('>I',self.s.read(4))[0]
        status = self.s.read(siz)
        return status
    
    def disconnect(self):
        self.s.close()
