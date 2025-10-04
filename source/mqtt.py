from umqtt.simple import MQTTClient
from utils import led_toggle
from cast import Chromecast
import utime as time
import json
from micropython import const
import ota.update
import uasyncio as asyncio
from ble import run_ble
import machine
import socket
import ssl
from version import FIRMWARE_VERSION


def ota_update_raw_socket(url, max_size_mb=2):
    """OTA update using raw socket to avoid memory allocation issues"""
    import gc
    from esp32 import Partition
    from ota.blockdev_writer import BlockDevWriter

    # Start timing
    ota_start_time = time.time()
    print(f"Starting raw socket OTA from: {url}")
    print(f"OTA start time: {time.time()}")

    # Parse URL
    if url.startswith("https://"):
        url = url[8:]  # Remove https://
        port = 443
    elif url.startswith("http://"):
        url = url[7:]  # Remove http://
        port = 80
    else:
        print("ERROR: Only HTTP/HTTPS URLs supported")
        return False

    # Extract host and path
    if "/" in url:
        host, path = url.split("/", 1)
        path = "/" + path
    else:
        host = url
        path = "/"

    try:
        # Free up memory
        gc.collect()
        print(f"Free memory before OTA: {gc.mem_free()} bytes")

        # Create socket connection with longer timeout and retry
        connection_start = time.time()
        print(f"Connecting to {host}:{port}...")

        # Try connection up to 3 times
        for attempt in range(3):
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(30)  # 30 second timeout for connection
                sock.connect((host, port))
                print(f"✓ Connected on attempt {attempt + 1}")
                break
            except Exception as e:
                print(f"Connection attempt {attempt + 1} failed: {e}")
                if attempt < 2:  # Don't sleep on last attempt
                    print("Retrying in 2 seconds...")
                    time.sleep(2)
                else:
                    raise e

        # Wrap with SSL if HTTPS
        if port == 443:
            ssl_start = time.time()
            print("Establishing SSL connection...")
            ssl_sock = ssl.wrap_socket(sock)
            ssl_time = time.time() - ssl_start
            print(f"SSL connection established in {ssl_time:.2f} seconds")
        else:
            ssl_sock = sock

        connection_time = time.time() - connection_start
        print(f"Connection established in {connection_time:.2f} seconds")

        # Send HTTP request with proper browser headers
        request_start = time.time()
        print("Sending HTTP request...")
        request = (
            "GET "
            + path
            + " HTTP/1.1\r\n"
            + "Host: "
            + host
            + "\r\n"
            + "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36\r\n"
            + "Accept: */*\r\n"
            + "Accept-Language: en-US,en;q=0.9\r\n"
            + "Accept-Encoding: identity\r\n"
            + "Connection: close\r\n"
            + "Cache-Control: no-cache\r\n\r\n"
        )
        ssl_sock.write(request.encode())

        # Read response header
        header_start = time.time()
        print("Reading response header...")
        header = b""
        while b"\r\n\r\n" not in header:
            chunk = ssl_sock.read(1)
            if not chunk:
                break
            header += chunk

        header_time = time.time() - header_start
        print(f"Header received in {header_time:.2f} seconds")

        # Check response status
        if b"200 OK" not in header:
            print(f"ERROR: HTTP error in response: {header[:200]}")
            ssl_sock.close()
            return False

        print("✓ HTTP 200 OK received")

        # Get content length
        content_length = None
        for line in header.split(b"\r\n"):
            if line.lower().startswith(b"content-length:"):
                content_length = int(line.split(b":")[1].strip())
                break

        if content_length:
            print(
                f"Firmware size: {content_length:,} bytes ({content_length/1024/1024:.2f} MB)"
            )
            if content_length > max_size_mb * 1024 * 1024:
                print(f"WARNING: Firmware larger than {max_size_mb}MB limit")

        # Get OTA partition
        ota_part = Partition(Partition.RUNNING).get_next_update()
        writer = BlockDevWriter(ota_part, verify=False, verbose=True)

        # Download and write firmware
        downloaded = 0
        chunk_count = 0
        chunk_size = 4096  # 4KB chunks (optimal size for ESP32)
        max_bytes = max_size_mb * 1024 * 1024

        # Start download timing
        download_start = time.time()
        print("Downloading and writing firmware...")
        print(f"Download start time: {time.time()}")

        while downloaded < max_bytes:
            try:
                # Set socket timeout on the underlying socket for each read operation
                if port == 443:
                    sock.settimeout(10)  # Set timeout on underlying socket for SSL
                else:
                    ssl_sock.settimeout(10)  # For HTTP, SSL socket is the main socket

                chunk = ssl_sock.read(chunk_size)
                if not chunk:
                    print("End of data reached")
                    break

                downloaded += len(chunk)
                chunk_count += 1

                # Write to partition
                bytes_written = writer.write(chunk)
                if bytes_written != len(chunk):
                    print(f"WARNING: Only wrote {bytes_written}/{len(chunk)} bytes")

                # Progress update (less frequent since chunks are larger)
                if chunk_count % 25 == 0:  # Every 25 chunks = ~100KB
                    elapsed = time.time() - download_start
                    rate = downloaded / elapsed if elapsed > 0 else 0
                    print(
                        f"  Downloaded: {downloaded:,} bytes ({downloaded/1024/1024:.2f} MB) - Rate: {rate/1024:.1f} KB/s - Time: {elapsed:.1f}s"
                    )

                # Memory management (less frequent since chunks are larger)
                if chunk_count % 25 == 0:  # Every 25 chunks
                    gc.collect()

                # Small delay between chunks to look more human-like
                time.sleep(0.01)  # 10ms delay between chunks

            except Exception as e:
                print(f"ERROR reading chunk {chunk_count}: {e}")
                # Try to continue if it's a timeout error
                if "timed out" in str(e).lower() or "timeout" in str(e).lower():
                    print("Timeout error - attempting to continue...")
                    time.sleep(1)  # Brief pause before retry
                    continue
                else:
                    break

        # Close writer and connection
        writer.close()
        ssl_sock.close()
        sock.close()

        # Calculate final timing
        download_time = time.time() - download_start
        total_time = time.time() - ota_start_time
        avg_rate = downloaded / download_time if download_time > 0 else 0

        print(f"OTA completed: {downloaded:,} bytes downloaded")
        print(f"Download time: {download_time:.2f} seconds")
        print(f"Average rate: {avg_rate/1024:.1f} KB/s")
        print(f"Total OTA time: {total_time:.2f} seconds")

        # Set as boot partition
        try:
            ota_part.set_boot()
            print("✓ OTA partition set as boot partition")

            # Verify the boot partition was set correctly
            boot_partition = Partition(Partition.BOOT)
            boot_name = boot_partition.info()[4]
            ota_name = ota_part.info()[4]

            if boot_name == ota_name:
                print(
                    f"✓ Micropython will boot from '{boot_name}' partition on next boot"
                )
                print("✓ OTA function returning True")
                return True
            else:
                print(
                    f"❌ Warning: Boot partition is '{boot_name}', expected '{ota_name}'"
                )
                print("✗ OTA function returning False")
                return False

        except Exception as e:
            print(f"ERROR setting boot partition: {e}")
            print("✗ OTA function returning False")
            return False

    except Exception as e:
        print(f"ERROR: OTA failed: {e}")
        return False
    finally:
        gc.collect()


_PING_INTERVAL = const(10)  # this needs to be less than keepalive
_KEEPALIVE = const(30)  # Reduced from 120 to 30 seconds for faster offline detection
_MQTT_HOST = const("34.53.103.114")
_MQTT_PORT = const(1883)


class MQTTHandler(object):
    def __init__(self, id):
        self.mqtt = None
        self.id = id
        self.connected = False
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

                # Start OTA update using raw socket approach
                print("Starting firmware download and flash...")
                success = ota_update_raw_socket(url, max_size_mb=2)
                print(f"OTA function returned: {success}")

                if success:
                    print("OTA update successful! Rebooting...")
                    time.sleep(2)
                    print("Calling ota_reboot()...")
                    from ota.status import ota_reboot

                    ota_reboot()
                else:
                    print("OTA update failed!")

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

            # Perform all operations on the single connection
            device.set_volume(vol)
            device.play_url(url)

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
        reconnect_attempts = 0
        reconnect_delay = 5  # Start with 5 seconds
        max_reconnect_delay = 60  # Max 60 seconds between attempts

        while True:
            try:
                time.sleep(1)
                self.mqtt.check_msg()

                counter += 1
                if counter >= _PING_INTERVAL:
                    counter = 0
                    try:
                        self.mqtt.ping()
                        # Reset reconnect attempts on successful ping
                        reconnect_attempts = 0
                        reconnect_delay = 5
                    except Exception as ping_error:
                        print(f"Ping failed: {ping_error}")
                        raise Exception("Connection lost - ping failed")

            except Exception as e:
                print(f"MQTT connection lost: {e}")

                reconnect_attempts += 1
                print(f"Attempting to reconnect (attempt {reconnect_attempts})")

                # Clean up current connection
                try:
                    if self.mqtt:
                        self.mqtt.disconnect()
                except:
                    pass

                # Wait before reconnecting
                print(f"Waiting {reconnect_delay} seconds before reconnect...")
                time.sleep(reconnect_delay)

                # Attempt to reconnect
                try:
                    success = self.mqtt_connect()
                    if success:
                        print("Reconnection successful!")
                        # Send online status after successful reconnection
                        self.send_status_update("online")
                        reconnect_attempts = 0
                        reconnect_delay = 5
                        counter = 0  # Reset counter
                    else:
                        print("Reconnection failed")
                        # Exponential backoff: double the delay for next attempt
                        reconnect_delay = min(reconnect_delay * 2, max_reconnect_delay)

                except Exception as reconnect_error:
                    print(f"Reconnection attempt failed: {reconnect_error}")
                    # Exponential backoff: double the delay for next attempt
                    reconnect_delay = min(reconnect_delay * 2, max_reconnect_delay)

        print("MQTT run loop ended")
