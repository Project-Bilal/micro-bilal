from umqtt.robust import MQTTClient
from utils import led_toggle
import cast
import utime as time
import json
from micropython import const
import ota.update
import uasyncio as asyncio
from ble import run_ble

_PING_INTERVAL = const(60)
_KEEPALIVE = const(120)
_MQTT_HOST = const("broker.hivemq.com")
_MQTT_PORT = const(1883)


class MQTTHandler(object):
    def __init__(self, id):
        self.mqtt = None
        self.id = id
        self.connected = False

    def mqtt_connect(self):
        self.mqtt = MQTTClient(
            client_id=self.id, server=_MQTT_HOST, port=_MQTT_PORT, keepalive=_KEEPALIVE
        )

        self.mqtt.connect()
        self.mqtt.set_callback(self.sub_cb)
        topic = f"projectbilal/{self.id}"
        self.mqtt.subscribe(topic)
        self.connected = True
        led_toggle("mqtt")
        return True

    def sub_cb(self, topic, msg):
        msg = json.loads(msg)
        led_toggle("mqtt")

        action = msg.get("action", {})
        props = msg.get("props", {})

        if action == "play":
            url = props.get("url")
            ip = props.get("ip")
            port = props.get("port")
            volume = props.get("volume")

            if all([url, ip, port, volume]):
                self.play(url=url, ip=ip, port=port, vol=volume)

        if action == "update":
            url = props.get("url")
            if url:
                ota.update.from_file(url=url, reboot=True)

        if action == "ble":
            asyncio.run(run_ble())
            
        if action == "discover":
            # Import device_scan here to avoid circular imports
            from utils import device_scan
            import uasyncio as asyncio
            
            try:
                # Run device scan asynchronously
                devices = asyncio.run(device_scan())
                # Publish results to projectbilal/channel
                response = {"action": "discover_response", "devices": devices}
                self.mqtt.publish("projectbilal/channel", json.dumps(response))
                print(f"Discovery completed, found {len(devices)} devices")
            except Exception as e:
                error_response = {"action": "discover_response", "error": str(e)}
                self.mqtt.publish("projectbilal/channel", json.dumps(error_response))
                print(f"Discovery failed: {e}")

    def play(self, url, ip, port, vol):
        # Handle volume
        device = cast.Chromecast(ip, port)
        device.set_volume(vol)
        device.disconnect()

        # Handle casting
        device = cast.Chromecast(ip, port)
        device.play_url(url)
        device.disconnect()

    def mqtt_run(self):
        print("Connected and listening to MQTT Broker")
        counter = 0
        while True:
            time.sleep(1)
            self.mqtt.check_msg()

            counter += 1
            if counter >= _PING_INTERVAL:
                counter = 0
                self.mqtt.ping()
