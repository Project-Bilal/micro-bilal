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
_BLINK_COUNT = {"wifi": const(6), "mqtt": const(4), "default": const(8)}
_LED = Pin(_LED_PIN, Pin.OUT)  # Create single LED instance


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


# connect to wifi and return ip
def wifi_connect():
    network.hostname("Bilal Cast")
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    wlan.disconnect()
    time.sleep(1)
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

    # Make sure we have both SSID and PASS
    if SSID and PASS and SECURITY:
        # Connect to the WiFi network
        print(f"WiFi: Attempting to connect to SSID: '{SSID}' (Security: {SECURITY})")
        if SECURITY == 0:
            wlan.connect(SSID)
        else:
            wlan.connect(SSID, PASS)
        timeout = _WIFI_TIMEOUT
        print(f"WiFi: Connecting to '{SSID}'... (timeout: {timeout}s)")
        while not wlan.isconnected() and timeout > 0:
            time.sleep(1)
            timeout -= 1

        # if we conected return back with the ip
        if wlan.isconnected():
            led_toggle("wifi")
            ip = wlan.ifconfig()[0]
            print(f"WiFi: Successfully connected to '{SSID}' with IP: {ip}")
            return ip
        else:
            print(
                f"WiFi: Connection to '{SSID}' timed out after {_WIFI_TIMEOUT} seconds"
            )

    # if connection does not succeed
    return None


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
    wlan = network.WLAN(network.STA_IF)

    # Try up to 5 times with full WiFi reset on each attempt
    for attempt in range(5):
        try:
            if attempt > 0:
                print(f"WiFi scan retry attempt {attempt + 1}")

            # Fully reset WiFi interface to ensure clean state
            wlan.active(False)
            time.sleep(0.5)
            wlan.active(True)

            # Explicitly disconnect to prevent auto-connect conflicts
            wlan.disconnect()
            # Increase delay on retries to allow hardware to stabilize
            time.sleep(1.5 if attempt == 0 else 2.5)

            # Double-check we're not connected
            if wlan.isconnected():
                print("WARNING: Still connected after disconnect, forcing...")
                wlan.disconnect()
                time.sleep(1)

            networks = wlan.scan()

            # If scan returned empty results, treat as failure and retry
            if len(networks) == 0:
                raise RuntimeError(
                    "WiFi scan returned 0 networks - interface may be in bad state"
                )

            # Success! Process the results
            wifi_dict = {}
            for wifi_network in networks:
                ssid = (
                    wifi_network[0].decode("utf-8")
                    if isinstance(wifi_network[0], bytes)
                    else wifi_network[0]
                )
                rssi = wifi_network[3]  # Signal strength
                security = wifi_network[4]

                if ssid and (security != 1):  # skip empty / open
                    # Keep only the strongest signal per SSID
                    if ssid not in wifi_dict or rssi > wifi_dict[ssid][1]:
                        wifi_dict[ssid] = (ssid, rssi, security)
                        print(repr(ssid))

            # Convert dict back to list and sort
            wifi_list_sorted = sorted(
                wifi_dict.values(), key=lambda x: x[1], reverse=True
            )

            # Final check - if filtering resulted in 0 networks, retry
            if len(wifi_list_sorted) == 0:
                raise RuntimeError("WiFi scan found networks but all were filtered out")

            print(f"WiFi scan successful: found {len(wifi_list_sorted)} networks")
            return wifi_list_sorted

        except Exception as e:
            print(
                f"WiFi scan error (attempt {attempt + 1}): {type(e).__name__}: {repr(e)}"
            )
            if attempt < 4:  # Not the last attempt (0-3 have more retries)
                time.sleep(1)  # Wait before retry
            else:  # Last attempt failed
                print("WiFi scan failed after 5 attempts")
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
