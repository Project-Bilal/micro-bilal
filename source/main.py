from utils import (
    wifi_connect,
    led_toggle,
    get_mac,
    check_reset_button,
    clear_device_state,
)
from ble import run_ble
import machine
import ota.rollback
import utime as time
import uasyncio as asyncio
import mqtt


def startup():
    # Check for factory reset button on boot
    print("Checking for factory reset button...")
    if check_reset_button():
        print("Factory reset triggered on boot!")
        clear_device_state()
        time.sleep(1)
        machine.reset()

    led_toggle()
    ip = wifi_connect()
    if ip:
        print("connected: ", ip)
        return True
    
    # WiFi connection failed or no credentials
    print("no WiFi connection")
    
    # Properly deactivate WiFi before entering BLE mode
    # This ensures clean state for BLE WiFi scanning
    import network
    wlan = network.WLAN(network.STA_IF)
    wlan.active(False)
    print("WiFi radio deactivated, preparing for BLE mode")
    time.sleep(0.5)  # Brief delay to ensure radio is fully deactivated
    
    return False


def main():
    import ota.status

    ota.status.status()

    wifi_success = startup()
    if wifi_success:
        client = mqtt.MQTTHandler(get_mac())
        conn = client.mqtt_connect()
        if conn:
            client.mqtt_run()
    else:
        print("Starting bluetooth advertising...")
        asyncio.run(run_ble())


try:
    ota.rollback.cancel()
    main()
except Exception as e:
    time.sleep(1)
    machine.reset()
