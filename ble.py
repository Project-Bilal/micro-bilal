import uasyncio as asyncio
import aioble
import ujson as json
from utils import wifi_scan
from mqtt import mqtt_run
import machine
import bluetooth

# advertising interval in milliseconds
_ADV_INTERVAL_MS = 250_000

# MTU size
MTU_SIZE = 128


def get_UUIDs():
    with open("connection.json", "r") as file:
        data = json.load(file)
        # the following variables are used to advertise BLE
        # SERVICE_UUID is the UUID of the service that the phone app will look for
        # CHAR_UUID is the UUID of the characteristic that the phone app will write to
        # These values were randomly generated and won't change
        SERVICE_UUID = bluetooth.UUID(data["SERVICE"])
        CHAR_UUID = bluetooth.UUID(data["CHAR"])
    return (SERVICE_UUID, CHAR_UUID)


async def control_task(connection, char):
    try:
        with connection.timeout(None):
            while True:
                await char.written()
                msg = char.read().decode()
                msg = json.loads(msg)
                header = msg.get("HEADER")

                if header == "wifiList":
                    ssid_list = wifi_scan()
                    msg = b'{"HEADER":"wifiList", "MESSAGE":"start"}'
                    await asyncio.sleep(0.5)  # need this or esp32 may crash
                    char.notify(connection, msg)
                    for ssid in ssid_list:
                        msg = {
                            "HEADER": "wifiList",
                            "MESSAGE": "ssid",
                            "SSID": ssid[0],
                            "SECURITY": ssid[2],
                        }
                        msg = json.dumps(msg).encode("utf-8")
                        char.notify(connection, msg)
                        await asyncio.sleep(0.1)
                    msg = b'{"HEADER":"wifiList", "MESSAGE":"end"}'
                    await asyncio.sleep(0.5)  # need this or esp32 may crash
                    char.notify(connection, msg)

                if header == "shareWifi":
                    new_data = msg.get("MESSAGE")
                    with open("connection.json", "r") as file:
                        data = json.load(file)
                    data.update(new_data)
                    with open("connection.json", "w") as file:
                        json.dump(data, file)
                    await asyncio.sleep(0.5)  # need this or esp32 may crash
                    msg = b'{"HEADER":"network_written", "MESSAGE":"success"}'
                    char.notify(connection, msg)
                    await asyncio.sleep(2)
                    machine.reset()

    except Exception as e:
        print("An error occurred:", e)
    return


async def run_ble():
    print("here")
    while True:
        SERVICE_UUID, CHAR_UUID = get_UUIDs()

        service = aioble.Service(SERVICE_UUID)
        char = aioble.Characteristic(
            service, CHAR_UUID, read=True, write=True, notify=True
        )

        # Register the service
        aioble.register_services(service)

        # Set MTU Size so we can send longer messages
        aioble.core.ble.gatts_set_buffer(char._value_handle, MTU_SIZE)

        print("Waiting for connection")
        connection = await aioble.advertise(
            _ADV_INTERVAL_MS,
            name="Bilal-Cast",
            services=[SERVICE_UUID],
        )
        print("Connection from", connection.device)

        await control_task(connection, char)

        await connection.disconnected()
