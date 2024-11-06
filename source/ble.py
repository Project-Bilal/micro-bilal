import uasyncio as asyncio
import aioble
import ujson as json
from utils import wifi_scan, led_on, led_toggle, set_wifi
import machine
import bluetooth
from micropython import const
import utime as time
import ota.rollback

# advertising interval in milliseconds
_ADV_INTERVAL_MS = const(250_000)

# MTU size
_MTU_SIZE = const(128)

# SERVICE_UUID is the UUID of the service that the phone app will look for
_SERVICE_UUID = bluetooth.UUID("2b45e4e0-af38-4c4a-a4dc-4399a03a7b38")

# CHAR_UUID is the UUID of the characteristic that the phone app will write to
_CHAR_UUID = bluetooth.UUID("97d91c3e-1122-48b8-8b6f-8ffb2daa2bda")
        
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
                    time.sleep(.5) # need this or esp32 may crash
                    char.notify(connection, msg)
                    for ssid in ssid_list:
                        msg = {"HEADER":"wifiList", "MESSAGE":"ssid", "SSID":ssid[0], "SECURITY":ssid[2]}
                        msg = json.dumps(msg).encode('utf-8')
                        char.notify(connection, msg
                        )
                        time.sleep(0.1)
                    msg = b'{"HEADER":"wifiList", "MESSAGE":"end"}'
                    time.sleep(.5) # need this or esp32 may crash
                    char.notify(connection, msg)
                    
                    
                if header == "shareWifi":
                    data = msg.get("MESSAGE")
                    SSID = data.get("SSID")
                    PASSWORD = data.get("PASSWORD")
                    SECURITY = data.get("SECURITY")
                    set_wifi(SSID, SECURITY, PASSWORD)
                    time.sleep(0.5) # need this or esp32 may crash
                    msg = b'{"HEADER":"network_written", "MESSAGE":"success"}'
                    char.notify(connection, msg)
                    time.sleep(2)
                    machine.reset()
                    
                    
    except Exception as e:
        print("An error occurred:", e)
    return

async def run_ble():
    while True:      
        service = aioble.Service(_SERVICE_UUID)
        char = aioble.Characteristic(service, _CHAR_UUID, read=True, write=True, notify=True)

        # Register the service
        aioble.register_services(service)

        # Set MTU Size so we can send longer messages
        aioble.core.ble.gatts_set_buffer(char._value_handle, _MTU_SIZE)
        
        print("Waiting for client to connect")
        led_on()
        connection = await aioble.advertise(
            _ADV_INTERVAL_MS,
            name="blebilal",
            services=[_SERVICE_UUID],
        )
        led_toggle()
        ota.rollback.cancel()
        print("Connection from", connection.device)

        await control_task(connection, char)

        await connection.disconnected()
