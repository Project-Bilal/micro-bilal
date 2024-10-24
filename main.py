import ujson as json
from utils import wifi_connect, led_off
from ble import run_ble
from mqtt import mqtt_run
import machine
import gc


def startup():
    led_off()
    ip = wifi_connect()
    if ip:
        print("connected: ", ip)
        return True
    return False


def main():
    wifi_success = startup()
    if wifi_success:
        gc.collect()
        try:
            mqtt_run()
        except Exception as e:
            print("MQTT failed, retrying:", e)
            return  # Return to the main loop instead of propagating the error
    else:
        run_ble()


while True:
    try:
        main()
    except Exception as e:
        print("An error occured", e)
        led_off()
        machine.reset()
