# micro-bilal
Micropython backend for ESP32/PICO device to interact with chromecast and play athan when triggered via MQTT

## How to setup
Clone the repo to your machine and cd into it
Make sure the bash script is executable `chmod +x build_and_flash.sh`
Then run it `./build_and_flash.sh`

The script should build and run the docker image that generates the .bin file you can flash to the ESP32 device. Run the commands in the script individually to troubleshoot. More detailed instructions to come.

You can also run this code on an ESP32 without running the script. You'll have to install Micropython onto the device on your own. Then copy the contents of the source folder onto the root of the device. And copy the ota folder into the root of the device as well. Install the aioble package (using Thonny for example). Then running main should run the code properly.

