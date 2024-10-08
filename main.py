# Description: This is the main file that is run on startup. It will either connect to WiFi or advertise BLE.
# If it connects to WiFi, it will start the MQTT client and listen for messages. If it advertises BLE, it will
# wait for a message from the phone app with the WiFi credentials and then connect to WiFi and start the MQTT client.
# If the WiFi credentials are incorrect, it will restart and advertise BLE again.

# We use the aioble library to advertise BLE and connect to the phone app

import aioble
import uasyncio as asyncio
import bluetooth
import utime as time
import ujson as json
from utils import wifi_connect, led_on, get_aws_cert
from mqtt import mqtt_run
import machine
import ntptime


async def startup():
    # first try to connect to WiFi
    ip = wifi_connect()

    # if we are connected to WiFi, start the MQTT client and listen for messages
    if ip:
        return ip

    # if we are not connected to WiFi, advertise BLE and wait for a message from the phone app
    with open("connection.json", "r") as file:
        data = json.load(file)
        # the following variables are used to advertise BLE
        # SERVICE_UUID is the UUID of the service that the phone app will look for
        # CHAR_UUID is the UUID of the characteristic that the phone app will write to
        # KEY is the key that the phone app will send to us to make sure that the message is from the phone app
        # These values were randomly generated and won't change
        SERVICE_UUID = bluetooth.UUID(data["SERVICE"])
        CHAR_UUID = bluetooth.UUID(data["CHAR"])
        KEY = data["KEY"]

    # advertising interval in milliseconds
    _ADV_INTERVAL_MS = 250_000

    # Create the service and characteristic
    service = aioble.Service(SERVICE_UUID)
    char = aioble.Characteristic(service, CHAR_UUID, read=True, write=True, notify=True)

    # Register the service
    aioble.register_services(service)

    # need to write a message with max length so that we phone app can send us a message longer than default 20 bytes
    longest_message = b'{"SSID":"TheLongestPossibleSSIDhas32Chars", "PASSWORD":"The_longest_possible_password__can__have_up_to__63__characters", "KEY":"increaseMTU"}'

    # Start advertising
    led_on()  # just a visual indicator that we are advertising BLE
    while not ip:
        async with await aioble.advertise(
            _ADV_INTERVAL_MS,
            name="Bilal-Cast",
            services=[SERVICE_UUID],
        ) as connection:
            char.write(
                longest_message
            )  # write the longest message so that the phone app can send us a longer message
            while True:
                time.sleep(1)
                message = (
                    char.read()
                )  # read the message from the phone app looking for WiFi credentials
                if message and message != longest_message:
                    message = message.decode()
                    new_data = json.loads(message)
                    if (
                        new_data["KEY"] == KEY
                    ):  # make sure that the message is from the phone app
                        del new_data["KEY"]
                        with open("connection.json", "r") as file:
                            data = json.load(file)
                        data.update(
                            new_data
                        )  # update the credentials in the connection.json file
                        with open("connection.json", "w") as file:
                            json.dump(
                                data, file
                            )  # write the updated credentials to the connection.json file
                        ip = (
                            wifi_connect()
                        )  # try to connect to WiFi with the new credentials
                        if (
                            ip
                        ):  # if we are connected to WiFi, restart the device so that we can connect to MQTT
                            # notify the phone app that we are restarting
                            char.notify(
                                connection,
                                b"Updated the credentials, restarting device...",
                            )
                            time.sleep(
                                1
                            )  # let the phone app recieve the message before disconnecting
                            machine.reset()  # restart so that we can connect to WiFi
                        else:  # if we are not connected to WiFi, notify the phone app that the credentials are incorrect
                            char.notify(connection, b"wifiError")
                            time.sleep(
                                1
                            )  # let the phone app recieve the message before disconnecting
                            machine.reset()  # restart so the user can attempt to connect again


def main():
    ip = asyncio.run(startup())  # connect to WiFi or advertise BLE
    get_aws_cert()  # downloads aws root-CA.crt if not here
    ntptime.settime()  # sync time to make sure the cert is valid
    mqtt_run()  # startup MQTT client listening for messagses


try:
    main()
except Exception as e:
    machine.reset()
