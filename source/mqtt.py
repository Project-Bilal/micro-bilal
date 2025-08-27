from umqtt.robust import MQTTClient
from utils import led_toggle
import cast
import utime as time
import json
from micropython import const
import ota.update
import uasyncio as asyncio
from ble import run_ble
import machine

_PING_INTERVAL = const(60)
_KEEPALIVE = const(30)  # Reduced from 120 to 30 seconds for faster offline detection
_MQTT_HOST = const("broker.hivemq.com")
_MQTT_PORT = const(1883)


class MQTTHandler(object):
    def __init__(self, id):
        self.mqtt = None
        self.id = id
        self.connected = False
        self.lwt_topic = f"projectbilal/{self.id}/status"
        self.lwt_message = "offline"

    def mqtt_connect(self):
        self.mqtt = MQTTClient(
            client_id=self.id,
            server=_MQTT_HOST,
            port=_MQTT_PORT,
            keepalive=_KEEPALIVE,
        )

        # Configure Last Will and Testament before connecting
        try:
            self.mqtt.set_last_will(
                self.lwt_topic, self.lwt_message, retain=False, qos=1
            )
        except Exception as e:
            print("Warning: set_last_will failed:", e)

        self.mqtt.connect()
        self.mqtt.set_callback(self.sub_cb)
        topic = f"projectbilal/{self.id}"
        self.mqtt.subscribe(topic)
        self.connected = True
        led_toggle("mqtt")

        # Send online status when connecting
        self.send_status_update("online")

        return True

    def send_status_update(self, status):
        """Send status update to the status topic"""
        try:
            self.mqtt.publish(self.lwt_topic, status)
            print(f"Status update sent: {status}")
        except Exception as e:
            print(f"Failed to send status update: {e}")

    def mqtt_disconnect(self):
        """Gracefully disconnect and send offline status"""
        try:
            if self.connected and self.mqtt:
                self.send_status_update("offline")
                time.sleep(0.5)  # Give time for message to be sent
                self.mqtt.disconnect()
                self.connected = False
                print("MQTT disconnected gracefully")
        except Exception as e:
            print(f"Error during disconnect: {e}")

    def sub_cb(self, topic, msg):
        try:
            msg = json.loads(msg)
            led_toggle("mqtt")

            action = msg.get("action", {})
            props = msg.get("props", {})
        except (ValueError, TypeError) as e:
            print(f"Message not for process: {msg} (JSON parse error: {e})")
            return

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

            try:
                # Run device scan asynchronously
                devices = asyncio.run(device_scan())
                message = {"chromecasts": devices}
                self.mqtt.publish(topic, json.dumps(message))
                print(f"Discovery completed, found {len(devices)} devices")
            except Exception as e:
                error_response = {"error": str(e)}
                self.mqtt.publish(topic, json.dumps(error_response))
                print(f"Discovery failed: {e}")

        if action == "delete_device":
            try:
                import esp32

                nvs = esp32.NVS("wifi_creds")
                nvs.erase_key("PASSWORD")
                nvs.erase_key("SSID")
                nvs.erase_key("SECURITY")
                print("WiFi credentials deleted from NVS")

                # Send confirmation back
                message = {"status": "success", "message": "WiFi credentials deleted"}
                self.mqtt.publish(topic, json.dumps(message))

                # Wait a moment for message to be sent, then reboot
                time.sleep(1)
                print("Rebooting ESP32...")
                import machine

                machine.reset()
            except Exception as e:
                error_response = {
                    "status": "error",
                    "message": f"Failed to delete WiFi credentials: {str(e)}",
                }
                self.mqtt.publish(topic, json.dumps(error_response))
                print(f"Failed to delete WiFi credentials: {e}")

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

        print("MQTT run loop ended")
