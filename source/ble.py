# Import required libraries
import uasyncio as asyncio  # MicroPython's async IO implementation
import aioble  # Asynchronous BLE library
import ujson as json  # MicroPython's JSON implementation
from utils import (
    led_on,
    led_toggle,
    set_wifi,
    get_mac,
    wifi_connect,
    wifi_connect_with_creds,
    monitor_reset_button,
    ntfy_alert,
)  # Custom utility functions
import machine  # Hardware control
import bluetooth  # BLE functionality
from micropython import const  # Constant definition
import utime as time  # Time functions
import network  # WiFi network management

# Constants for BLE configuration
# Long advertisement interval to conserve power
_ADV_INTERVAL_MS = const(250_000)

# Maximum transmission unit size for BLE packets
_MTU_SIZE = const(128)

# UUID for the main BLE service - must match mobile app
_SERVICE_UUID = bluetooth.UUID("2b45e4e0-af38-4c4a-a4dc-4399a03a7b38")

# UUID for the characteristic handling WiFi configuration
_CHAR_UUID = bluetooth.UUID("97d91c3e-1122-48b8-8b6f-8ffb2daa2bda")

# UUID for the characteristic providing device MAC address
_MAC_UUID = bluetooth.UUID("97d91c3f-1122-48b8-8b6f-8ffb2daa2bda")


async def control_task(connection, char, cached_networks=None):
    """
    Handles BLE communication and WiFi configuration requests.
    Processes incoming messages and sends appropriate responses.

    Args:
        connection: BLE connection object
        char: BLE characteristic for communication
        cached_networks: Pre-scanned WiFi networks from boot (avoids BLE+WiFi radio conflict)
    """
    try:
        # Wait after BLE connection to allow WiFi radio to settle
        # This prevents "0 networks" issue on rapid reconnections
        await asyncio.sleep(2)
        print("BLE: Connection settled, ready for commands")

        with connection.timeout(None):  # No timeout for connection
            while True:
                # Wait for data to be written to characteristic
                await char.written()
                # Read and parse incoming message
                msg = char.read().decode()
                msg = json.loads(msg)
                header = msg.get("HEADER")

                # Handle WiFi scanning request
                if header == "wifiList":
                    ssid_list = cached_networks or []
                    print(f"BLE: Received wifiList request, serving {len(ssid_list)} cached networks")
                    # Send start notification
                    msg = b'{"HEADER":"wifiList", "MESSAGE":"start"}'
                    time.sleep(0.5)  # Prevent ESP32 crashes
                    char.notify(connection, msg)

                    # Send each found network's details
                    for ssid in ssid_list:
                        msg = {
                            "HEADER": "wifiList",
                            "MESSAGE": "ssid",
                            "SSID": ssid[0],
                            "SECURITY": ssid[2],
                            "RSSI": ssid[1],
                        }
                        msg = json.dumps(msg).encode("utf-8")
                        char.notify(connection, msg)
                        time.sleep(0.1)  # Brief delay between messages

                # Handle WiFi credentials configuration
                if header == "shareWifi":
                    data = msg.get("MESSAGE")
                    SSID = data.get("SSID")
                    PASSWORD = data.get("PASSWORD")
                    SECURITY = data.get("SECURITY")
                    print(f"BLE: Received WiFi credentials for SSID: '{SSID}'")
                    print(f"BLE: Security type: {SECURITY}")

                    # TWO-PHASE COMMIT: Test connection BEFORE saving to NVS
                    # This prevents orphaned devices with bad credentials saved
                    time.sleep(0.5)  # Prevent ESP32 crashes

                    # Stop BLE before WiFi attempt — shared radio can't do both
                    print("BLE: Stopping BLE to free radio for WiFi...")
                    try:
                        await connection.disconnect()
                    except Exception:
                        pass
                    time.sleep(1)
                    aioble.stop()
                    time.sleep(1)
                    print("BLE: Radio freed, attempting WiFi...")

                    ip = wifi_connect_with_creds(SSID, PASSWORD, SECURITY)

                    if not ip:
                        # First attempt failed — radio may still be settling after BLE teardown
                        print("BLE: WiFi attempt 1 failed, resetting radio and retrying...")
                        wlan = network.WLAN(network.STA_IF)
                        wlan.active(False)
                        time.sleep(3)
                        wlan.active(True)
                        time.sleep(2)
                        ip = wifi_connect_with_creds(SSID, PASSWORD, SECURITY)

                    if ip:
                        print(f"BLE: Connection successful with IP: {ip}")
                        set_wifi(SSID, SECURITY, PASSWORD)
                        machine.reset()
                    else:
                        print(
                            f"BLE: Failed to connect to '{SSID}' after 2 attempts - credentials NOT saved"
                        )
                        ntfy_alert(
                            "[ESP32] WiFi onboarding failed for SSID: %s (2 attempts)" % SSID,
                            priority=3,
                            tags="warning",
                        )
                        print("BLE: Rebooting to restart BLE for retry...")
                        machine.reset()

    except Exception as e:
        print(f"BLE control_task error: {type(e).__name__}: {repr(e)}")
        import sys

        sys.print_exception(e)
    return


async def run_ble(cached_networks=None):
    """
    Main BLE service loop. Sets up the service, characteristics,
    and handles client connections.

    Args:
        cached_networks: Pre-scanned WiFi networks from boot
    """
    # Start factory reset button monitoring in background
    asyncio.create_task(monitor_reset_button())

    while True:
        # Create BLE service and characteristics
        service = aioble.Service(_SERVICE_UUID)
        char = aioble.Characteristic(
            service, _CHAR_UUID, read=True, write=True, notify=True
        )
        mac_char = aioble.Characteristic(service, _MAC_UUID, read=True)
        # Set device MAC address
        mac_char.write(get_mac().encode())

        # Initialize BLE service
        aioble.register_services(service)

        # Configure MTU size for larger messages
        aioble.core.ble.gatts_set_buffer(char._value_handle, _MTU_SIZE)
        print("Waiting for client to connect")

        # Visual indicator for advertising state
        led_on()
        connection = await aioble.advertise(
            _ADV_INTERVAL_MS,
            name="blebilal",
            services=[_SERVICE_UUID],
        )
        led_toggle()
        print("Connection from", connection.device)

        # Handle client communication
        await control_task(connection, char, cached_networks)

        # Wait for disconnection before restarting advertising
        await connection.disconnected()
