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
            return
    else:
        run_ble()


error_count = 0
max_errors = 3

while True:
    try:
        main()
    except Exception as e:
        print("An error occurred", e)
        led_off()
        error_count += 1
        if error_count > max_errors:
            run_ble()  # Fall back to BLE instead of resetting again
            break  # Exit the reset loop
        machine.reset()
