# Description: This file contains the mqtt connection and callback functions
# When a message is sent to the ESP32 via MQTT, a Chromecast object is created

import utime as time
import ujson as json
from umqtt.robust import MQTTClient
from utils import get_mac, led_toggle
import cast
import machine


# create the mqtt connection and subscribe to the topic
def mqtt_connect():
    # get the endpoint from the connection.json file
    with open("connection.json", "r") as file:
        data = json.load(file)
        aws_endpoint = data["mqtt_host"]
    with open("private.pem.key", "r") as f:
        key = f.read()  # private key
    with open("cert.pem.crt", "r") as f:
        cert = f.read()  #  certificate file
    ssl_params = {"key": key, "cert": cert, "server_side": False}

    mqtt = MQTTClient(
        client_id=get_mac(),  # the id of the client is the mac address
        server=aws_endpoint,
        port=8883,
        keepalive=1200,
        ssl=True,
        ssl_params=ssl_params,
    )
    mqtt.connect()
    mqtt.set_callback(sub_cb)  # when we get a message, call the sub_cb function
    mqtt.subscribe(get_mac())  # subscribe to the mac address topic
    led_toggle("mqtt")
    return mqtt


# callback function for when we get a message
def sub_cb(topic, msg):
    msg = json.loads(msg)

    # flash to identify new incoming message
    led_toggle("mqtt")

    # the action determines what we do no next
    action = msg.get("action")
    props = msg.get("props")

    if action and props:
        if action == "play":
            url = props.get("url")
            ip = props.get("ip")
            port = props.get("port")
            volume = props.get("volume")

            if url and ip and port and volume:
                # if we have all the required properties, play the audio
                play(url=url, ip=ip, port=port, vol=volume)


# this function uses cast.py to play the audio
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


# this function is called from main.py and runs the mqtt client indefinitely
def mqtt_run():
    mqtt = mqtt_connect()

    while True:
        time.sleep(1)
        # Check for new messages
        mqtt.check_msg()
