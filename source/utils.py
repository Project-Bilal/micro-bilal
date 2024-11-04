# Description: Utility functions for the ESP32

import network
import utime as time
import ujson as json
import machine
from machine import Pin
import os
from micropython import const
import esp32

_BUFFER_SIZE = const(128)  # Make this big enough for your data
_NVS_NAME = const("wifi_creds")  # NVS namespace

_WIFI_TIMEOUT = const(10)  # WiFi connection timeout in seconds

_LED_PIN = const(2)  # For the ESP32 built-in LED
_BLINK_DELAY = const(0.25)  # Blink delay in seconds
_BLINK_COUNT = {
    "wifi": const(6),
    "mqtt": const(4),
    "default": const(8)
}
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
def get_mac():  # TODO: update this to work with pico
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
        while not wlan.isconnected() and _WIFI_TIMEOUT > 0:
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

# Scan for available wifi
def wifi_scan():
    try:
        wlan = network.WLAN(network.STA_IF)
        wlan.active(True)
        wlan.disconnect()
        time.sleep(1)
        networks = wlan.scan()
        wifi_list = []
        for wifi_network in networks:
            ssid = wifi_network[0]  # Network name (SSID)
            rssi = wifi_network[3]  # Signal strength (RSSI)
            security = wifi_network[4]
            if ssid and (security != 1):
                wifi_list.append((ssid, rssi, security))  # Append the SSID and RSSI to the list

        # Sort networks by signal strength (RSSI) in descending order
        wifi_list_sorted = sorted(wifi_list, key=lambda x: x[1], reverse=True)

        # return list of wifi networks with their names and security
    except Exception as e:
        print("An error occurred:", e)
    return wifi_list_sorted