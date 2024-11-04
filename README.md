# micro-bilal
Micropython backend for ESP32/PICO device to interact with chromecast and play athan when triggered via MQTT

## How to setup
Clone the repo to your machine and cd into it
Make sure the bash script is executable `chmod +x build_and_flash.sh`
Then run it `./build_and_flash.sh`
FYI - the script assumes you are running this on an M1/M2/M3 Mac, otherwise change these flags in the code "--platform linux/amd64"

Make sure your ESP32 is plugged in and no other resources are using it like Thonny for example or VSC. The script won't work towards the end where it flahes the .bin onto the device. You'll have to do that yourself.

The script should build and run the docker image that generates the .bin file you can flash to the ESP32 device. Run the commands in the script individually to troubleshoot. More detailed instructions to come.

You can also run this code on an ESP32 without running the script. You'll have to install Micropython onto the device on your own. Then copy the contents of the source folder onto the root of the device. And copy the ota folder into the root of the device as well. Install the aioble package (using Thonny for example). Then running main should run the code properly.

