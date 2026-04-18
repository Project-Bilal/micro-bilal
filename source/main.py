from utils import (
    wifi_connect,
    led_toggle,
    get_mac,
    check_reset_button,
    clear_device_state,
    ntfy_alert,
)
from ble import run_ble
import machine
import ota.rollback
import utime as time
import uasyncio as asyncio
import mqtt
from version import FIRMWARE_VERSION


def _get_device_label():
    """Get device name from NVS for boot-time alerts, fallback to MAC."""
    try:
        import esp32
        nvs = esp32.NVS("device")
        buf = bytearray(128)
        length = nvs.get_blob("name", buf)
        name = buf[:length].decode()
        if name:
            return '"%s"' % name
    except Exception:
        pass
    return get_mac()


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
    print("no WiFi connection")
    ntfy_alert("[ESP32 %s] WiFi failed at boot" % _get_device_label(), priority=4, tags="warning")
    return False


def main():
    import ota.status

    ota.status.status()

    wifi_success = startup()
    if wifi_success:
        device_id = get_mac()
        label = _get_device_label()
        client = mqtt.MQTTHandler(device_id)
        conn = client.mqtt_connect()
        if conn:
            ntfy_alert("[ESP32 %s] Online (v%s)" % (label, FIRMWARE_VERSION), topic="projectbilal-events", priority=2, tags="electric_plug")
            client.mqtt_run()
        else:
            ntfy_alert("[ESP32 %s] MQTT connect failed" % label, priority=4, tags="warning")
    else:
        print("Starting bluetooth advertising...")
        asyncio.run(run_ble())


try:
    ota.rollback.cancel()
    main()
except Exception as e:
    ntfy_alert("[ESP32 %s] Boot crash: %s" % (_get_device_label(), e), priority=4, tags="warning")
    time.sleep(1)
    machine.reset()
