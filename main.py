import aioble
import uasyncio as asyncio
import bluetooth
import utime as time
import ujson as json
from utils import wifi_connect, led_on, get_aws_cert
from mqtt import mqtt_run
import machine

async def startup():
    
    ip = wifi_connect()
    
    if ip:
        return ip
    
    
    print("Starting BLE server, was not able to connect to WiFi")
    
    with open("connection.json", "r") as file:
        data = json.load(file)
        SERVICE_UUID = bluetooth.UUID(data['SERVICE'])
        CHAR_UUID = bluetooth.UUID(data['CHAR'])
        KEY = data['KEY']

    _ADV_INTERVAL_MS = 250_000

    service = aioble.Service(SERVICE_UUID)
    char = aioble.Characteristic(service, CHAR_UUID, read=True, write=True, notify=True)
    
    aioble.register_services(service)
    
    # need to write a message with max length so that we phone app can send us a message longer than default 20 bytes
    longest_message = b'{"SSID":"TheLongestPossibleSSIDhas32Chars", "PASSWORD":"The_longest_possible_password__can__have_up_to__63__characters", "KEY":"increaseMTU"}'

    # Start advertising
    led_on()
    while not ip:
        async with await aioble.advertise(
            _ADV_INTERVAL_MS,
            name='Bilal-Cast',
            services=[SERVICE_UUID],
        ) as connection:
            char.write(longest_message)
            print("Connection from", connection.device)
            while True:
                time.sleep(1)
                message = char.read()
                if message and message != longest_message:
                    message = message.decode()
                    try:
                        new_data = json.loads(message)
                        if new_data["KEY"] == KEY:
                            del new_data["KEY"]
                            with open("connection.json", "r") as file:
                                data = json.load(file)
                            data.update(new_data)
                            with open("connection.json", "w") as file:
                                json.dump(data, file)
                            ip = wifi_connect()
                            if ip:
                                char.notify(connection, b"Updated the credentials, restarting device...")
                                time.sleep(1) # let the phone app recieve the message before disconnecting
                                machine.reset() # restart so that we can connect to WiFi
                            char.notify(connection, b"wifiError")
                            time.sleep(1) # let the phone app recieve the message before disconnecting
                            machine.reset() # restart so the user can attempt to connect again
                    except Exception as e:
                        print(e)
                        print("need to handle me somehow")
    
def main():
    ip = asyncio.run(startup()) # connect to WiFi or advertise BLE
    get_aws_cert() # downloads aws root-CA.crt if not here
    mqtt_run() # startup MQTT client listening for messagses
    
main()
    
    


