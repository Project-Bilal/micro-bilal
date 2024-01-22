# Description: Utility functions for the ESP32

import network
import utime as time
import ujson as json
import machine
from machine import Pin
import urequests
import os

CERT_URL = "https://www.amazontrust.com/repository/AmazonRootCA1.pem"
CERT_FILE = "root-CA.crt"


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


# get mac address for mqtt connection
def get_mac():  # TODO: update this to work with pico
    mac_hex = machine.unique_id()
    mac = "-".join("%02x" % b for b in mac_hex)
    return mac


# connect to wifi and return ip
def wifi_connect():
    with open("connection.json", "r") as file:
        data = json.load(file)
        SSID = data.get("SSID")
        PASS = data.get("PASSWORD")

    # if the SSID or PASSWORD is not set, return None
    if SSID is None or PASS is None:
        return None

    network.hostname("Bilal Cast")
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    wlan.disconnect()
    time.sleep(0.1)
    wlan.connect(SSID, PASS)
    timeout = 10  # Set a timeout (adjust as needed)
    while not wlan.isconnected() and timeout > 0:
        time.sleep(1)
        timeout -= 1

    if not wlan.isconnected():
        return None  # Return None to indicate failed Wi-Fi connection

    led_toggle("wifi")
    return wlan.ifconfig()[0]


def get_aws_cert():
    # skip if file is there
    if CERT_FILE in os.listdir():
        return

    response = urequests.get(CERT_URL)

    if response.status_code == 200:
        with open(CERT_FILE, "wb") as file:
            file.write(response.content)

    response.close()
