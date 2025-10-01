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
        if SECURITY == 0:
            wlan.connect(SSID)
        else:
            wlan.connect(SSID, PASS)
        timeout = _WIFI_TIMEOUT
        print("connecting to WiFi...")
        while not wlan.isconnected() and timeout > 0:
            time.sleep(1)
            timeout -= 1

        # if we conected return back with the ip
        if wlan.isconnected():
            led_toggle("wifi")
            return wlan.ifconfig()[0]

    # if connection does not succeed
    return None


# save wifi credentials to nvs
def set_wifi(SSID, SECURITY, PASSWORD=None):
    try:
        nvs = esp32.NVS(_NVS_NAME)
        nvs.set_blob("SSID", SSID)
        nvs.set_i32("SECURITY", SECURITY)
        if PASSWORD:
            nvs.set_blob("PASSWORD", PASSWORD)
        else:
            nvs.set_blob("PASSWORD", "nopassword")
        nvs.commit()
        return True
    except:
        return False


def wifi_scan():
    try:
        wlan = network.WLAN(network.STA_IF)
        wlan.active(True)
        wlan.disconnect()
        time.sleep(1)
        networks = wlan.scan()

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
        wifi_list_sorted = sorted(wifi_dict.values(), key=lambda x: x[1], reverse=True)

    except Exception as e:
        print("An error occurred:", e)
        wifi_list_sorted = []

    return wifi_list_sorted


# Scan for chromecast devices with simulated streaming
async def device_scan(device_found_callback=None):
    # Import mDNS client libraries only when needed
    from mdns_client.service_discovery.txt_discovery import TXTServiceDiscovery
    from mdns_client.client import Client
    import utime as time

    wlan = network.WLAN(network.STA_IF)
    ip = wlan.ifconfig()[0]
    client = Client(ip)
    discovery = TXTServiceDiscovery(client)

    # Simulate streaming by doing multiple shorter queries
    all_devices = []
    seen_devices = set()  # Track devices we've already sent
    start_time = time.time()
    timeout = 10.0  # 10 second total timeout
    query_interval = 1.0  # Query every 1 second
    
    print(f"Starting streaming discovery for {timeout} seconds...")
    
    while time.time() - start_time < timeout:
        try:
            # Do short query to find new devices
            results = await discovery.query_once("_googlecast", "_tcp", timeout=query_interval)
            
            # Process any new devices found
            for device in results:
                device_key = f"{device.txt_records['fn'][0]}_{device.ips[0]}"
                
                # Only process if we haven't seen this device before
                if device_key not in seen_devices:
                    device_info = {
                        "name": device.txt_records["fn"][0],
                        "ip": device.ips.pop(),
                        "port": device.port,
                    }
                    all_devices.append(device_info)
                    seen_devices.add(device_key)
                    
                    # Send device immediately as it's found
                    if device_found_callback:
                        device_found_callback(device_info)
                        print(f"Streaming: Found {device_info['name']} at {device_info['ip']}")
            
            # Small delay between queries
            await asyncio.sleep(0.1)
            
        except Exception as e:
            print(f"Query error: {e}")
            await asyncio.sleep(0.5)
    
    print(f"Discovery complete, found {len(all_devices)} total devices")
    return all_devices
