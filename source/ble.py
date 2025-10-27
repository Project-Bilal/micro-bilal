# Import required libraries
import uasyncio as asyncio  # MicroPython's async IO implementation
import aioble  # Asynchronous BLE library
import ujson as json  # MicroPython's JSON implementation
from utils import (
    wifi_scan,
    led_on,
    led_toggle,
    set_wifi,
    get_mac,
    wifi_connect,
)  # Custom utility functions
import machine  # Hardware control
import bluetooth  # BLE functionality
from micropython import const  # Constant definition
import utime as time  # Time functions

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


async def control_task(connection, char):
    """
    Handles BLE communication and WiFi configuration requests.
    Processes incoming messages and sends appropriate responses.

    Args:
        connection: BLE connection object
        char: BLE characteristic for communication
    """
    try:
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
                    ssid_list = wifi_scan()  # Scan for available networks
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
                    # Save WiFi configuration
                    set_wifi(SSID, SECURITY, PASSWORD)
                    time.sleep(0.5)  # Prevent ESP32 crashes
                    ip = wifi_connect()
                    # Confirm successful configuration
                    # then reset device
                    if ip:
                        msg = b'{"HEADER":"network_written", "MESSAGE":"success"}'
                        char.notify(connection, msg)
                        time.sleep(2)
                        machine.reset()  # Restart device to apply new WiFi settings
                    # Inform client of failed configuration
                    # and wait before next attempt
                    print("Failed to connect to WiFi")
                    
                    # Clear the faulty WiFi credentials from NVS
                    try:
                        import esp32
                        nvs = esp32.NVS("wifi_creds")
                        nvs.erase_key("PASSWORD")
                        nvs.erase_key("SSID")
                        nvs.erase_key("SECURITY")
                        print("Cleared faulty WiFi credentials from NVS")
                    except Exception as e:
                        print(f"Error clearing WiFi credentials: {e}")
                    
                    msg = b'{"HEADER":"network_written", "MESSAGE":"fail"}'
                    char.notify(connection, msg)
                    time.sleep(2)

    except Exception as e:
        print("An error occurred:", e)
    return


async def run_ble():
    """
    Main BLE service loop. Sets up the service, characteristics,
    and handles client connections.
    """
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
        await control_task(connection, char)

        # Wait for disconnection before restarting advertising
        await connection.disconnected()
