import utime as time
import ujson as json
from umqtt.simple import MQTTClient
from utils import get_mac, led_toggle
import cast

def mqtt_connect():
    with open("connection.json", "r") as file:
        data = json.load(file)
        aws_endpoint = data['mqtt_host']
    with open("private.pem.key", 'r') as f:
        key = f.read()
    with open("cert.pem.crt", 'r') as f:
        cert = f.read()
    ssl_params = {"key":key, "cert":cert, "server_side":False}
    
    mqtt = MQTTClient(client_id=get_mac(), server=aws_endpoint, port=8883, keepalive=1200, ssl=True, ssl_params=ssl_params)
    mqtt.connect()
    mqtt.set_callback(sub_cb)
    mqtt.subscribe(get_mac())
    led_toggle('mqtt')
    return mqtt

def sub_cb(topic, msg):
    
    global processing_message
    
    msg = json.loads(msg)
    
    # Set the flag to indicate that we are processing a message
    processing_message = True
    
    # flash to identify new incoming message
    led_toggle('mqtt')
    
    # the action determines what we do no next
    action = msg.get('action')
    props = msg.get('props')
    
    if action and props:
        if action == "play":
            url = props.get('url')
            ip = props.get('ip')
            port = props.get('port')
            volume = props.get('volume')
            
            if url and ip and port and volume:
                play(url=url, ip=ip, port=port, vol=volume)
            else:
                print("missing one of: url, ip, port, volume")
                
        else:
            print("invalid action")
    else:
        print("action and prop needed in message")
    
    processing_message = False
    return
    
def play(url, ip, port, vol):
    # handle volume and casting in seperate connections
    
    # change volume
    device = cast.Chromecast(ip, port)
    device.set_volume(vol)
    device.disconnect()
    
    # cast audio
    device = cast.Chromecast(ip, port)
    device.play_url(url)
    device.disconnect()
    


def mqtt_run():
    
    global processing_message
    processing_message = False
    
    mqtt = mqtt_connect()

    while True:
        time.sleep(1)
    
        # Check if we are currently processing a message, if not, check for new messages
        if not processing_message:
            mqtt.check_msg()

