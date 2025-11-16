# Description: Utility functions for the ESP32

import network
import utime as time
import machine
from machine import Pin
from micropython import const
import esp32
import uasyncio as asyncio

_BUFFER_SIZE = const(128)  # Make this big enough for your data
_NVS_NAME = const("wifi_creds")  # NVS namespace

_WIFI_TIMEOUT = const(30)  # WiFi connection timeout in seconds

_LED_PIN = const(2)  # For the ESP32 built-in LED
_BLINK_DELAY = const(0.25)  # Blink delay in seconds
_BLINK_COUNT = {
    "wifi": const(6),
    "mqtt": const(4),
    "default": const(8),
    "reset": const(10),
}
_LED = Pin(_LED_PIN, Pin.OUT)  # Create single LED instance

_BOOT_BUTTON_PIN = const(0)  # Built-in BOOT button on ESP32
_RESET_HOLD_TIME = const(8)  # Seconds to hold button for factory reset


def led_toggle(info=None):
    # Use _LED instead of creating new instance
    blinks = _BLINK_COUNT.get(info, _BLINK_COUNT["default"])
    for x in range(blinks):
        _LED.value(not _LED.value())
        time.sleep(_BLINK_DELAY)
    _LED.off()


def led_on():
    _LED.on()


def led_off():
    _LED.off()


# get mac address for mqtt connection
def get_mac():
    mac_hex = machine.unique_id()
    mac = "-".join("%02x" % b for b in mac_hex)
    return mac


# connect to wifi with provided credentials and return ip
def wifi_connect_with_creds(SSID, PASSWORD, SECURITY):
    """
    Test WiFi connection with provided credentials without saving to NVS.
    Used during onboarding to verify credentials before persisting them.

    Args:
        SSID: WiFi network name
        PASSWORD: WiFi password (can be None for open networks)
        SECURITY: Security type (0 for open, non-zero for secured)

    Returns:
        IP address string if connected, None if failed
    """
    network.hostname("Bilal Cast")
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    wlan.disconnect()
    time.sleep(1)

    # Validate we have required parameters
    if not SSID or SECURITY is None:
        print("WiFi: Invalid credentials provided")
        return None

    # Connect to the WiFi network
    print(f"WiFi: Testing connection to SSID: '{SSID}' (Security: {SECURITY})")
    if SECURITY == 0:
        wlan.connect(SSID)
    else:
        if not PASSWORD:
            print("WiFi: Password required for secured network")
            return None
        wlan.connect(SSID, PASSWORD)

    timeout = _WIFI_TIMEOUT
    print(f"WiFi: Connecting to '{SSID}'... (timeout: {timeout}s)")
    while not wlan.isconnected() and timeout > 0:
        time.sleep(1)
        timeout -= 1

    # if we connected return back with the ip
    if wlan.isconnected():
        led_toggle("wifi")
        ip = wlan.ifconfig()[0]
        print(f"WiFi: Successfully connected to '{SSID}' with IP: {ip}")
        return ip
    else:
        print(f"WiFi: Connection to '{SSID}' timed out after {_WIFI_TIMEOUT} seconds")
        # CRITICAL: Forcefully abort the connection attempt
        # This prevents WiFi radio from staying in stuck/connecting state
        print("WiFi: Aborting connection and resetting WiFi radio...")
        wlan.disconnect()
        wlan.active(False)
        time.sleep(0.5)
        print("WiFi: Radio reset complete")
        return None


# connect to wifi using saved credentials from NVS and return ip
def wifi_connect():
    """
    Connect to WiFi using credentials saved in NVS.
    Used during normal boot to connect to previously configured network.

    Returns:
        IP address string if connected, None if failed or no credentials saved
    """
    nvs = esp32.NVS(_NVS_NAME)
    buffer = bytearray(_BUFFER_SIZE)  # Create a buffer

    try:
        length = nvs.get_blob("SSID", buffer)  # Get actual length of data
        SSID = buffer[:length].decode()
        length = nvs.get_blob("PASSWORD", buffer)  # Get actual length of data
        PASS = buffer[:length].decode()
        SECURITY = nvs.get_i32("SECURITY")
    except:
        # if values do not exist return None
        return None

    # Use the new function to connect with saved credentials
    return wifi_connect_with_creds(SSID, PASS, SECURITY)


# save wifi credentials to nvs
def set_wifi(SSID, SECURITY, PASSWORD=None):
    try:
        print(f"WiFi: Saving credentials to NVS - SSID: '{SSID}', Security: {SECURITY}")
        nvs = esp32.NVS(_NVS_NAME)
        nvs.set_blob("SSID", SSID)
        nvs.set_i32("SECURITY", SECURITY)
        if PASSWORD:
            nvs.set_blob("PASSWORD", PASSWORD)
            print(f"WiFi: Password saved (length: {len(PASSWORD)} chars)")
        else:
            nvs.set_blob("PASSWORD", "nopassword")
            print("WiFi: No password saved (open network)")
        nvs.commit()
        print("WiFi: Credentials committed to NVS successfully")
        return True
    except Exception as e:
        print(f"WiFi: Error saving credentials to NVS: {e}")
        return False


def wifi_scan():
    """Scan for available WiFi networks with simple retry logic."""
    wlan = network.WLAN(network.STA_IF)

    # Try up to 3 times on hardware errors only
    for attempt in range(3):
        try:
            if attempt > 0:
                print(f"WiFi scan retry {attempt + 1}/3")
                time.sleep(2)

            # Aggressive radio reset to ensure clean state
            # This is critical after failed connection attempts that leave radio in bad state
            print("WiFi: Resetting radio for scan...")
            wlan.disconnect()
            wlan.active(False)
            time.sleep(0.5)
            wlan.active(True)
            time.sleep(1.5)

            networks = wlan.scan()
            print(f"WiFi: Scan returned {len(networks)} raw networks")

            # If scan returned 0 networks and we have retries left, try again
            if len(networks) == 0 and attempt < 2:
                print("WiFi: Scan returned 0 networks, will retry...")
                time.sleep(1)
                continue

            # Process results
            wifi_dict = {}
            for wifi_network in networks:
                ssid = (
                    wifi_network[0].decode("utf-8")
                    if isinstance(wifi_network[0], bytes)
                    else wifi_network[0]
                )
                rssi = wifi_network[3]
                security = wifi_network[4]

                if ssid and (security != 1):  # skip empty / open
                    if ssid not in wifi_dict or rssi > wifi_dict[ssid][1]:
                        wifi_dict[ssid] = (ssid, rssi, security)
                        print(repr(ssid))

            wifi_list_sorted = sorted(
                wifi_dict.values(), key=lambda x: x[1], reverse=True
            )

            print(f"WiFi: Found {len(wifi_list_sorted)} networks after filtering")
            return wifi_list_sorted

        except Exception as e:
            print(
                f"WiFi scan error (attempt {attempt + 1}): {type(e).__name__}: {repr(e)}"
            )
            if attempt == 2:
                import sys

                sys.print_exception(e)
                return []

    return []


# Scan for chromecast devices
async def device_scan(device_found_callback=None):
    # Import mDNS client libraries only when needed
    from mdns_client.service_discovery.txt_discovery import TXTServiceDiscovery
    from mdns_client.client import Client

    wlan = network.WLAN(network.STA_IF)
    ip = wlan.ifconfig()[0]
    client = Client(ip)
    discovery = TXTServiceDiscovery(client)

    # Do one-time query for Chromecasts with 10-second timeout
    devices = []
    print("Starting Chromecast discovery for 10 seconds...")

    results = await discovery.query_once("_googlecast", "_tcp", timeout=10.0)

    for device in results:
        device_info = {
            "name": device.txt_records["fn"][0],
            "ip": device.ips.pop(),
            "port": device.port,
        }
        devices.append(device_info)

        # Send device immediately as it's found (if callback provided)
        if device_found_callback:
            device_found_callback(device_info)

    print(f"Discovery complete, found {len(devices)} devices")
    return devices


def clear_device_state():
    """
    Clear all device configuration from NVS (factory reset).
    This removes WiFi credentials and any other saved state.
    """
    try:
        print("Factory reset: Clearing all device state from NVS...")
        nvs = esp32.NVS(_NVS_NAME)

        # Clear WiFi credentials
        try:
            nvs.erase_key("SSID")
            print("  - Cleared SSID")
        except:
            pass

        try:
            nvs.erase_key("PASSWORD")
            print("  - Cleared PASSWORD")
        except:
            pass

        try:
            nvs.erase_key("SECURITY")
            print("  - Cleared SECURITY")
        except:
            pass

        nvs.commit()
        print("Factory reset: NVS cleared successfully")
        return True
    except Exception as e:
        print(f"Factory reset: Error clearing NVS: {e}")
        return False


def check_reset_button():
    """
    Check if BOOT button is held for factory reset.
    Returns True if button held for required duration.
    """
    button = Pin(_BOOT_BUTTON_PIN, Pin.IN, Pin.PULL_UP)

    # Button is pressed when value is 0 (active low with pull-up)
    if button.value() == 0:
        print(f"Reset button pressed, checking hold time ({_RESET_HOLD_TIME}s)...")

        # Visual feedback - rapid blink while waiting
        start_time = time.time()
        while time.time() - start_time < _RESET_HOLD_TIME:
            if button.value() == 1:
                # Button released early
                print("Reset button released early, reset cancelled")
                _LED.off()
                return False

            # Blink LED rapidly to show we're counting
            _LED.value(not _LED.value())
            time.sleep(0.2)

        # Button held for full duration
        print("Reset button held for required time!")

        # Confirmation pattern - fast blinks
        led_toggle("reset")
        return True

    return False


async def monitor_reset_button():
    """
    Background task to continuously monitor for factory reset button press.
    Runs during normal operation (WiFi/MQTT mode).
    """
    button = Pin(_BOOT_BUTTON_PIN, Pin.IN, Pin.PULL_UP)
    print("Reset button monitoring started (hold BOOT button 8s for factory reset)")

    while True:
        await asyncio.sleep(0.5)  # Check every 500ms

        if button.value() == 0:  # Button pressed
            print("Reset button detected during operation...")
            if check_reset_button():
                # Factory reset confirmed
                print("Factory reset confirmed! Clearing device and rebooting...")
                clear_device_state()
                time.sleep(1)
                machine.reset()

        await asyncio.sleep(0.5)  # Additional delay between checks
