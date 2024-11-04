from utils import wifi_connect, led_toggle, get_mac
from ble import run_ble
import machine
import utime as time
import asyncio
import mqtt


def startup():
    led_toggle()
    ip = wifi_connect()
    if ip:
        print("connected: ", ip)
        return True
    print("failed to connect")
    return False


def main():
    wifi_success = startup()
    if wifi_success:
        client = mqtt.MQTTHandler(get_mac())
        conn = client.mqtt_connect()
        if conn:
            client.mqtt_run()
    else:
        asyncio.run(run_ble())

try:
    main()
except Exception as e:
    time.sleep(1)
    machine.reset()