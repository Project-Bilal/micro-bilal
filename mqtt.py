import utime as time
import ujson as json
import ssl
from umqtt.robust import MQTTClient
from utils import get_mac, led_toggle
import cast
import gc
import ntptime

PING_INTERVAL = const(60)  # Define as constant


def mqtt_connect():
    ntptime.settime()

    thing_name = get_mac()

    # Get the endpoint
    with open("connection.json", "r") as file:
        data = json.load(file)
        aws_endpoint = data["mqtt_host"]

    # Create SSL context
    ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ssl_context.verify_mode = ssl.CERT_REQUIRED
    ssl_context.load_verify_locations("root-CA.crt")
    ssl_context.load_cert_chain("cert.pem.crt", "private.pem.key")

    mqtt = MQTTClient(
        client_id=thing_name,
        server=aws_endpoint,
        port=8883,
        keepalive=120,
        ssl=ssl_context,
    )

    gc.collect()
    mqtt.connect()

    mqtt.set_callback(sub_cb)
    mqtt.subscribe(thing_name)
    led_toggle("mqtt")
    return mqtt


def sub_cb(topic, msg):
    msg = json.loads(msg)
    led_toggle("mqtt")

    action = msg.get("action")
    props = msg.get("props")

    if action and props and action == "play":
        url = props.get("url")
        ip = props.get("ip")
        port = props.get("port")
        volume = props.get("volume")

        if all([url, ip, port, volume]):
            play(url=url, ip=ip, port=port, vol=volume)


def play(url, ip, port, vol):
    # Handle volume
    device = cast.Chromecast(ip, port)
    device.set_volume(vol)
    device.disconnect()

    # Handle casting
    device = cast.Chromecast(ip, port)
    device.play_url(url)
    device.disconnect()


def mqtt_run():
    gc.collect()  # Clear memory

    mqtt = mqtt_connect()
    counter = 0

    while True:
        time.sleep(1)
        mqtt.check_msg()
        counter += 1
        if counter >= PING_INTERVAL:
            counter = 0
            mqtt.ping()  # Using ping for keepalive
