from umqtt.robust import MQTTClient
from utils import led_toggle
from cast import Chromecast
import utime as time
import json
from micropython import const
import ota.update
import uasyncio as asyncio
from ble import run_ble
import machine
from version import FIRMWARE_VERSION

_PING_INTERVAL = const(10)  # this needs to be less than keepalive
_KEEPALIVE = const(30)  # Reduced from 120 to 30 seconds for faster offline detection
_MQTT_HOST = const("34.53.103.114")
_MQTT_PORT = const(1883)


class MQTTHandler(object):
    def __init__(self, id):
        self.mqtt = None
        self.id = id
        self.connected = False
        self.reboot_requested = False
        self.lwt_topic = f"projectbilal/{self.id}/status"
        self.lwt_message = json.dumps(
            {
                "status": "offline",
                "timestamp": time.time(),
                "firmware_version": FIRMWARE_VERSION,
            }
        )

    def mqtt_connect(self):
        self.mqtt = MQTTClient(
            client_id=self.id,
            server=_MQTT_HOST,
            port=_MQTT_PORT,
            keepalive=_KEEPALIVE,
        )

        # Configure Last Will and Testament before connecting
        try:
            self.mqtt.set_last_will(
                self.lwt_topic, self.lwt_message, retain=False, qos=1
            )
        except Exception as e:
            print("Warning: set_last_will failed:", e)

        self.mqtt.connect()
        self.mqtt.set_callback(self.sub_cb)
        topic = f"projectbilal/{self.id}"
        self.mqtt.subscribe(topic)
        self.connected = True
        led_toggle("mqtt")

        # Send online status when connecting
        self.send_status_update("online")

        return True

    def send_status_update(self, status):
        """Send status update to the status topic with firmware info"""
        try:
            message = {
                "status": status,
                "timestamp": time.time(),
                "firmware_version": FIRMWARE_VERSION,
            }
            self.mqtt.publish(self.lwt_topic, json.dumps(message))
            print(f"Status update sent: {status} (firmware: {FIRMWARE_VERSION})")
        except Exception as e:
            print(f"Failed to send status update: {e}")

    def mqtt_disconnect(self):
        """Gracefully disconnect and send offline status"""
        try:
            if self.connected and self.mqtt:
                self.send_status_update("offline")
                time.sleep(0.5)  # Give time for message to be sent
                self.mqtt.disconnect()
                self.connected = False
                print("MQTT disconnected gracefully")
        except Exception as e:
            print(f"Error during disconnect: {e}")

    def sub_cb(self, topic, msg):
        try:
            msg = json.loads(msg)
            led_toggle("mqtt")

            action = msg.get("action", {})
            props = msg.get("props", {})
        except (ValueError, TypeError) as e:
            print(f"Message not for process: {msg} (JSON parse error: {e})")
            return

        if action == "play":
            url = props.get("url")
            ip = props.get("ip")
            port = props.get("port")
            volume = props.get("volume")

            if all([url, ip, port, volume]):
                # Clean up the IP string (remove whitespace/newlines)
                ip = str(ip).strip()
                self.play(url=url, ip=ip, port=port, vol=volume)

        if action == "update":
            url = props.get("url")
            if url:
                print(f"Starting OTA update from: {url}")

                # Disconnect from MQTT to free up network resources
                print("Disconnecting from MQTT for OTA update...")
                try:
                    if self.connected and self.mqtt:
                        self.mqtt.disconnect()
                        self.connected = False
                        print("MQTT disconnected successfully")
                except Exception as e:
                    print(f"Error disconnecting MQTT: {e}")

                # Small delay to ensure disconnection is complete
                time.sleep(1)

                # Start OTA update
                print("Starting firmware download and flash...")
                ota.update.from_file(url=url, verify=False, reboot=True)

        if action == "update_app":
            """
            Update individual application files on filesystem

            Expected MQTT message:
            {
                "action": "update_app",
                "props": {
                    "files": ["mqtt.py", "utils.py"],  // or ["*"] or ["all"] for all files
                    "url": "http://your-server.com/app/"
                }
            }
            """
            files = props.get("files", [])
            base_url = props.get("url")

            if not files:
                print("ERROR: No files specified for app update")
                return

            # Handle "update all" shortcut
            if files == ["*"] or files == ["all"]:
                files = [
                    "main.py",
                    "mqtt.py",
                    "utils.py",
                    "cast.py",
                    "ble.py",
                    "version.py",
                ]
                print("Update all files requested - will download all app files")

            if not base_url:
                print("ERROR: No URL specified for app update")
                return

            print(f"Starting app update for files: {files}")
            print(f"Base URL: {base_url}")

            # Disconnect MQTT to free up resources
            try:
                if self.connected and self.mqtt:
                    self.mqtt.disconnect()
                    self.connected = False
                    print("MQTT disconnected for app update")
            except Exception as e:
                print(f"Error disconnecting MQTT: {e}")

            # Import dependencies
            import urequests
            import os
            import gc

            # Phase 1: Download ALL files first (atomic - all or nothing)
            print("Phase 1: Downloading all files...")
            downloaded_files = {}  # filename -> content
            failed_files = []

            for filename in files:
                try:
                    print(f"Downloading {filename}...")
                    file_url = base_url + filename

                    r = urequests.get(file_url)
                    if r.status_code != 200:
                        print(f"Failed to download {filename}: HTTP {r.status_code}")
                        failed_files.append(filename)
                        r.close()
                        break  # Abort on first failure

                    content = r.content
                    r.close()

                    downloaded_files[filename] = content
                    print(f"Downloaded {filename} ({len(content)} bytes)")

                    # Cleanup and delay between downloads
                    gc.collect()
                    time.sleep(0.5)

                except Exception as e:
                    print(f"Error downloading {filename}: {e}")
                    failed_files.append(filename)
                    break  # Abort on first failure

            # Check if all downloads succeeded
            if failed_files:
                print("=" * 40)
                print("Download failed - aborting update")
                print(f"  Failed: {failed_files}")
                print("  No files were modified")
                print("=" * 40)
                print("Reconnecting to MQTT...")
                from utils import wifi_connect

                wifi_connect()
                self.mqtt_connect()
                return

            # Phase 2: All downloads succeeded - now write files
            print("Phase 2: Writing files to filesystem...")
            updated_files = []

            for filename, content in downloaded_files.items():
                backup_path = "/" + filename + ".bak"
                file_path = "/" + filename

                try:
                    # Backup existing file
                    try:
                        os.rename(file_path, backup_path)
                        print(f"Backed up {filename}")
                    except:
                        pass  # File might not exist, that's ok

                    # Write new file
                    with open(file_path, "wb") as f:
                        f.write(content)

                    print(f"Wrote {filename}")
                    updated_files.append(filename)

                except Exception as e:
                    print(f"Error writing {filename}: {e}")
                    # This shouldn't happen, but if it does, try to restore
                    try:
                        os.rename(backup_path, file_path)
                        print(f"Restored backup for {filename}")
                    except:
                        pass

            # Clean up all backup files
            print("Cleaning up backup files...")
            for filename in updated_files:
                try:
                    os.remove("/" + filename + ".bak")
                except:
                    pass

            # Report results
            print("=" * 40)
            print("App update complete - all files updated successfully")
            print(f"  Updated: {updated_files}")
            print("=" * 40)

            if updated_files:
                print("Rebooting with updated files...")
                print("Reboot will occur after returning from callback...")
                self.reboot_requested = True
                return  # Exit callback cleanly, reboot will happen in mqtt_run
            else:
                print("No files were updated. Reconnecting to MQTT...")
                # Reconnect to MQTT
                from utils import wifi_connect

                wifi_connect()
                self.mqtt_connect()

        if action == "ble":
            asyncio.run(run_ble())

        if action == "discover":
            # Import device_scan here to avoid circular imports
            from utils import device_scan

            try:
                print("Starting Chromecast discovery...")

                # Create callback to send devices as they're found
                def send_device_found(device_info):
                    message = {"chromecasts": [device_info]}
                    self.mqtt.publish(topic, json.dumps(message))
                    print(f"Found device: {device_info['name']} at {device_info['ip']}")

                # Run device scan with streaming callback
                devices = asyncio.run(
                    device_scan(device_found_callback=send_device_found)
                )

                # Send final summary
                if len(devices) > 1:
                    summary_message = {
                        "discovery_complete": True,
                        "total_found": len(devices),
                    }
                    self.mqtt.publish(topic, json.dumps(summary_message))
                    print(f"Discovery completed, found {len(devices)} devices total")
                elif len(devices) == 0:
                    no_devices_message = {"chromecasts": []}
                    self.mqtt.publish(topic, json.dumps(no_devices_message))
                    print("Discovery completed, no devices found")

            except Exception as e:
                error_response = {"error": str(e)}
                self.mqtt.publish(topic, json.dumps(error_response))
                print(f"Discovery failed: {e}")

        if action == "delete_device":
            try:
                import esp32

                nvs = esp32.NVS("wifi_creds")
                nvs.erase_key("PASSWORD")
                nvs.erase_key("SSID")
                nvs.erase_key("SECURITY")
                print("WiFi credentials deleted from NVS")

                # Send confirmation back
                message = {"status": "success", "message": "WiFi credentials deleted"}
                self.mqtt.publish(topic, json.dumps(message))

                # Wait a moment for message to be sent, then reboot
                time.sleep(1)
                print("Rebooting ESP32...")
                import machine

                machine.reset()
            except Exception as e:
                error_response = {
                    "status": "error",
                    "message": f"Failed to delete WiFi credentials: {str(e)}",
                }
                self.mqtt.publish(topic, json.dumps(error_response))
                print(f"Failed to delete WiFi credentials: {e}")

    def play(self, url, ip, port, vol):
        device = None
        try:
            print(
                f"MQTT: Playing audio - URL: {url}, IP: {ip}, Port: {port}, Vol: {vol}"
            )

            # Create single Chromecast connection
            device = Chromecast(ip, port)

            # Play URL with volume (volume is set after media loads)
            device.play_url(url, volume=vol)

            # Wait for audio to start before disconnecting
            time.sleep(2)
            print("MQTT: Audio playback completed successfully")

        except Exception as e:
            print(f"MQTT: Chromecast error: {e}")
            import sys

            sys.print_exception(e)

        finally:
            # Always disconnect to clean up resources
            if device:
                try:
                    device.disconnect()
                    print("MQTT: Chromecast connection closed")
                except Exception as disconnect_e:
                    print(f"MQTT: Error during disconnect: {disconnect_e}")

    def mqtt_run(self):
        print("Connected and listening to MQTT Broker")
        counter = 0

        while True:
            time.sleep(1)
            self.mqtt.check_msg()  # robust handles reconnection automatically

            # Check if reboot was requested during message handling
            if self.reboot_requested:
                print("Executing requested reboot...")
                time.sleep(1)
                machine.reset()

            # Ping periodically to keep connection alive
            counter += 1
            if counter >= _PING_INTERVAL:
                counter = 0
                self.mqtt.ping()
