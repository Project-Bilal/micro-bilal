# Description: Utility functions for the ESP32

import network
import utime as time
import ujson as json
import machine
from machine import Pin
import os


# toggle the LED for certain situations
def led_toggle(info=None):
    # TODO: update this to work with pico
    LED = Pin(2, Pin.OUT)

    if not info:
        i = 8
    elif info == "wifi":
        i = 6
    elif info == "mqtt":
        i = 4
    else:
        i = 8
    for x in range(i):
        LED.value(not LED.value())
        time.sleep(0.25)
    LED.off()


# turn the LED on
def led_on():
    # TODO: update this to work with pico
    Pin(2, Pin.OUT).on()
    
    # turn the LED on
def led_off():
    # TODO: update this to work with pico
    Pin(2, Pin.OUT).off()


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
    
    with open("connection.json", "r") as file:
        data = json.load(file)
        SSID = data.get("SSID", None)
        PASS = data.get("PASSWORD", None)
    

    # Make sure we have both SSID and PASS
    if SSID and PASS:
        wlan.connect(SSID, PASS)
        timeout = 10  # Set a timeout (adjust as needed)
        while not wlan.isconnected() and timeout > 0:
            time.sleep(1)
            timeout -= 1
            
        # if we conected return back with the ip
        if wlan.isconnected():
            led_toggle("wifi")
            return wlan.ifconfig()[0]
    
    # if connection does not succeed
    return None


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