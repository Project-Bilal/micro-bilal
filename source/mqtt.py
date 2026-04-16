from umqtt.simple import MQTTClient
from utils import led_toggle, check_reset_button, clear_device_state, ntfy_alert
import utime as time
import json
from micropython import const
import ota.update
import uasyncio as asyncio
from ble import run_ble
import machine
from version import FIRMWARE_VERSION

_PING_INTERVAL = const(15)  # this needs to be less than keepalive
_KEEPALIVE = const(45)  # Relaxed now that mDNS is disabled — less overhead
_MQTT_HOST = const("34.53.103.114")
_MQTT_PORT = const(1883)


class MQTTHandler(object):
    def __init__(self, id):
        self.mqtt = None
        self.id = id
        self.connected = False
        self.reboot_requested = False
        self.discovery_in_progress = False
        self._play_in_progress = False
        self._last_play_url = None
        self._last_play_time = 0
        self._dedup_window = 10  # seconds
        self._play_count = 0
        self._play_confirmed_count = 0
        self._error_count = 0
        self._start_time = time.time()
        self._pending_playback_result = None
        self._post_cast_reconnect = False
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

            # Immediately ignore keepalive messages to prevent interference
            # These come from the phone app every 30 seconds and don't need processing
            if msg.get("type") == "keepalive":
                return

            led_toggle("mqtt")

            action = msg.get("action", {})
            props = msg.get("props", {})
        except (ValueError, TypeError) as e:
            print(f"Message not for process: {msg} (JSON parse error: {e})")
            return

        if action == "play":
            # Reject if a play is already in progress
            if self._play_in_progress:
                print("MQTT: Play already in progress, ignoring")
                return

            url = props.get("url")

            # Deduplication: reject duplicate play commands within window
            now = time.time()
            if url == self._last_play_url and (now - self._last_play_time) < self._dedup_window:
                print("MQTT: Ignoring duplicate play command (within %ds window)" % self._dedup_window)
                ntfy_alert(
                    "[ESP32 %s] Duplicate play rejected: %s" % (self.id, props.get("label", "audio")),
                    topic="projectbilal-events",
                )
                return
            self._last_play_url = url
            self._last_play_time = now

            # Wait if discovery is in progress to prevent socket exhaustion
            if self.discovery_in_progress:
                print("Waiting for discovery to complete before playing...")
                max_wait = 15  # Max 15 seconds wait
                wait_count = 0
                while self.discovery_in_progress and wait_count < max_wait:
                    time.sleep(1)
                    wait_count += 1
                if self.discovery_in_progress:
                    print("Discovery still in progress, proceeding anyway")

            ip = props.get("ip")
            port = props.get("port")
            volume = props.get("volume")
            label = props.get("label", "audio")

            if all([url, ip, port]):
                ntfy_alert(
                    "[ESP32 %s] Received play: %s" % (self.id, label),
                    topic="projectbilal-events",
                )
                # Clean up the IP string (remove whitespace/newlines)
                ip = str(ip).strip()
                self._play_in_progress = True
                try:
                    self.play(url=url, ip=ip, port=port, vol=volume, label=label)
                finally:
                    self._play_in_progress = False

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
                print("  Failed: %s" % failed_files)
                ntfy_alert(
                    "[ESP32 %s] App update failed: %s" % (self.id, failed_files),
                )
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
            print("  Updated: %s" % updated_files)
            print("=" * 40)

            if updated_files:
                ntfy_alert(
                    "[ESP32 %s] App updated: %s" % (self.id, ", ".join(updated_files)),
                    topic="projectbilal-events",
                )
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
            # mDNS discovery moved to mobile app to prevent WiFi instability.
            # Respond immediately so older app versions don't hang.
            response = {"discovery_complete": True, "total_found": 0}
            self.mqtt.publish(topic, json.dumps(response))
            print("Discovery delegated to mobile app")

            # TODO: Cleanup — remove this commented block and device_scan() in utils.py
            # once phone-side discovery is confirmed stable in production.
            # Also remove mdns_client library files from device filesystem.
            # --- Original ESP32 mDNS discovery (disabled) ---
            # from utils import device_scan
            # import gc
            # self.mqtt.ping()
            # self.discovery_in_progress = True
            # try:
            #     print("Starting Chromecast discovery...")
            #     def send_device_found(device_info):
            #         message = {"chromecasts": [device_info]}
            #         self.mqtt.publish(topic, json.dumps(message))
            #     devices = asyncio.run(
            #         device_scan(device_found_callback=send_device_found)
            #     )
            #     summary_message = {"discovery_complete": True, "total_found": len(devices)}
            #     self.mqtt.publish(topic, json.dumps(summary_message))
            # except Exception as e:
            #     error_response = {"error": str(e)}
            #     self.mqtt.publish(topic, json.dumps(error_response))
            # finally:
            #     self.discovery_in_progress = False
            #     gc.collect()

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
                ntfy_alert(
                    "[ESP32 %s] WiFi credentials deleted" % self.id,
                    topic="projectbilal-events",
                )

                # Wait a moment for message to be sent, then reboot
                time.sleep(3)
                print("Rebooting ESP32...")
                import machine

                machine.reset()
            except Exception as e:
                error_response = {
                    "status": "error",
                    "message": "Failed to delete WiFi credentials: %s" % str(e),
                }
                self.mqtt.publish(topic, json.dumps(error_response))
                print("Failed to delete WiFi credentials: %s" % e)
                ntfy_alert(
                    "[ESP32 %s] Delete WiFi credentials failed: %s" % (self.id, e),
                )

    def play(self, url, ip, port, vol, label="audio"):
        import gc
        device = None
        playback_confirmed = False
        self._play_count += 1
        try:
            print(
                f"MQTT: Playing audio - URL: {url}, IP: {ip}, Port: {port}, Vol: {vol}"
            )

            # Free memory before allocating cast sockets
            gc.collect()

            # Lazy import to save baseline RAM
            from cast import Chromecast

            gc.collect()

            # Create single Chromecast connection
            device = Chromecast(ip, port)

            # Play URL with volume (volume is set after app launch, before media load)
            if vol is not None:
                ntfy_alert(
                    "[ESP32 %s] Volume set to %s for %s" % (self.id, vol, label),
                    topic="projectbilal-events",
                )
            playback_confirmed = device.play_url(url, volume=vol)

            if playback_confirmed:
                self._play_confirmed_count += 1
                time.sleep(2)
                print("MQTT: Audio playback confirmed, starting...")
                ntfy_alert(
                    "[ESP32 %s] Playback confirmed: %s" % (self.id, label),
                    topic="projectbilal-events",
                )
            else:
                print(
                    "MQTT: Playback not confirmed, waiting longer for Chromecast to start..."
                )
                time.sleep(5)
                ntfy_alert(
                    "[ESP32 %s] Playback NOT confirmed: %s" % (self.id, label),
                    topic="projectbilal-events",
                )

        except Exception as e:
            self._error_count += 1
            print("MQTT: Chromecast error: %s" % e)
            ntfy_alert("[ESP32 %s] Chromecast play failed: %s" % (self.id, e))
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

            # Free SSL memory immediately
            gc.collect()

            # Proactive WiFi health check after casting
            # Casting often kills WiFi — detect and recover immediately
            # instead of waiting for the next MQTT ping to fail
            import network
            wlan = network.WLAN(network.STA_IF)
            if not wlan.isconnected():
                print("MQTT: WiFi dropped after cast, resetting radio...")
                from utils import wifi_connect
                wifi_ip = wifi_connect()
                if wifi_ip:
                    print(f"MQTT: WiFi recovered with IP: {wifi_ip}")
                    # Flag for fast reconnect in mqtt_run loop
                    self._post_cast_reconnect = True
                else:
                    print("MQTT: WiFi recovery failed, will retry in main loop")

            # Report playback result to MQTT status topic
            # MQTT often drops after cast, so queue for reconnection if needed
            result = json.dumps({
                "type": "playback_result",
                "confirmed": playback_confirmed,
                "label": label,
                "timestamp": time.time(),
            })
            try:
                if self.connected and self.mqtt:
                    self.mqtt.publish(self.lwt_topic, result)
                    print("MQTT: Playback result sent")
                else:
                    self._pending_playback_result = result
                    print("MQTT: Playback result queued for after reconnect")
            except Exception:
                self._pending_playback_result = result

    def mqtt_run(self):
        print("Connected and listening to MQTT Broker")
        counter = 0
        health_counter = 0
        reconnect_attempts = 0
        reconnect_delay = 5  # Start with 5 seconds
        max_reconnect_delay = 60  # Max 60 seconds between attempts
        _HEALTH_INTERVAL = 600  # Publish health every ~600 seconds (~10 minutes)

        # Enable hardware watchdog (120s timeout)
        from machine import WDT, Pin
        wdt = WDT(timeout=120000)

        while True:
            try:
                time.sleep(1)
                wdt.feed()

                # Check if reboot was requested during message handling
                if self.reboot_requested:
                    print("Executing requested reboot...")
                    time.sleep(1)
                    machine.reset()

                # Check for factory reset button (non-blocking check every second)
                button = Pin(0, Pin.IN, Pin.PULL_UP)
                if button.value() == 0:  # Button pressed
                    if check_reset_button():
                        print("Factory reset confirmed during MQTT operation!")
                        clear_device_state()
                        time.sleep(1)
                        machine.reset()

                # Check for messages
                try:
                    self.mqtt.check_msg()
                except OSError as e:
                    # All OSErrors from check_msg indicate connection issues
                    print(f"Network error during check_msg: {e}")
                    raise Exception(f"Network error: {e}")
                except Exception as e:
                    error_str = str(e)
                    if "index out of range" in error_str or "bytes index" in error_str:
                        print(f"MQTT library error (malformed packet): {e}")
                        raise Exception("Library error - reconnecting")
                    else:
                        raise

                counter += 1
                health_counter += 1

                # Periodic health reporting
                if health_counter >= _HEALTH_INTERVAL:
                    health_counter = 0
                    try:
                        import gc
                        health = json.dumps({
                            "type": "health",
                            "uptime": int(time.time() - self._start_time),
                            "plays": self._play_count,
                            "confirmed": self._play_confirmed_count,
                            "errors": self._error_count,
                            "free_mem": gc.mem_free(),
                            "firmware": FIRMWARE_VERSION,
                        })
                        self.mqtt.publish(f"projectbilal/{self.id}/health", health)
                    except Exception:
                        pass  # Best-effort

                if counter >= _PING_INTERVAL:
                    counter = 0

                    if not self.connected or not self.mqtt:
                        raise Exception("Connection not established")

                    ping_failed = False
                    for ping_attempt in range(2):
                        try:
                            self.mqtt.ping()
                            reconnect_attempts = 0
                            reconnect_delay = 5
                            ping_failed = False
                            break
                        except Exception as ping_error:
                            ping_failed = True
                            if ping_attempt == 0:
                                print(
                                    f"Ping failed (attempt 1/2): {ping_error}, retrying..."
                                )
                                time.sleep(1)
                            else:
                                print(f"Ping failed (attempt 2/2): {ping_error}")

                    if ping_failed:
                        raise Exception("Connection lost - ping failed after retries")

            except Exception as e:
                self.connected = False  # Mark disconnected immediately
                self._error_count += 1
                error_str = str(e)

                if (
                    "bytes index out of range" in error_str
                    or "index out of range" in error_str
                ):
                    print(f"MQTT library error (likely malformed packet): {e}")
                    print("Attempting to recover by reconnecting...")
                else:
                    print(f"MQTT connection lost: {e}")

                reconnect_attempts += 1
                print(f"Attempting to reconnect (attempt {reconnect_attempts})")

                # Reboot safety valve — with mDNS disabled, 5 failures means
                # something is seriously wrong
                if reconnect_attempts >= 5:
                    print("Too many reconnect failures, rebooting...")
                    ntfy_alert(
                        "[ESP32 %s] Rebooting after %d reconnect failures" % (self.id, reconnect_attempts)
                    )
                    time.sleep(2)
                    machine.reset()

                # Clean up current connection
                try:
                    if self.mqtt:
                        self.mqtt.disconnect()
                except:
                    pass

                # Fast reconnect after casting (WiFi already recovered in play())
                if self._post_cast_reconnect:
                    self._post_cast_reconnect = False
                    reconnect_delay = 2
                    print("Fast reconnect after cast (2s)...")
                    time.sleep(2)
                else:
                    print(f"Waiting {reconnect_delay} seconds before reconnect...")
                    time.sleep(reconnect_delay)
                wdt.feed()

                # Verify WiFi before MQTT reconnect
                import network
                wlan = network.WLAN(network.STA_IF)
                if not wlan.isconnected():
                    print("WiFi disconnected, reconnecting WiFi first...")
                    from utils import wifi_connect
                    wifi_ip = wifi_connect()
                    if not wifi_ip:
                        print("WiFi reconnect failed, will retry...")
                        ntfy_alert(
                            "[ESP32 %s] WiFi reconnect failed (attempt %d)" % (self.id, reconnect_attempts),
                        )
                        reconnect_delay = min(reconnect_delay * 2, max_reconnect_delay)
                        continue
                    ntfy_alert(
                        "[ESP32 %s] WiFi reconnected before MQTT" % self.id,
                        topic="projectbilal-events",
                    )

                # Attempt to reconnect MQTT
                try:
                    success = self.mqtt_connect()
                    if success:
                        print("Reconnection successful!")
                        ntfy_alert(
                            "[ESP32 %s] Reconnected after disconnect" % self.id,
                            topic="projectbilal-events",
                        )
                        self.send_status_update("online")

                        # Flush any pending playback result from before disconnect
                        if self._pending_playback_result:
                            try:
                                self.mqtt.publish(self.lwt_topic, self._pending_playback_result)
                                print("MQTT: Sent pending playback result after reconnect")
                            except Exception:
                                pass
                            self._pending_playback_result = None

                        reconnect_attempts = 0
                        reconnect_delay = 5
                        counter = 0
                        health_counter = 0
                    else:
                        print("Reconnection failed")
                        ntfy_alert(
                            "[ESP32 %s] MQTT reconnect failed after %s attempts"
                            % (self.id, reconnect_attempts),
                        )
                        reconnect_delay = min(reconnect_delay * 2, max_reconnect_delay)

                except Exception as reconnect_error:
                    print("Reconnection attempt failed: %s" % reconnect_error)
                    ntfy_alert(
                        "[ESP32 %s] MQTT reconnect failed after %s attempts"
                        % (self.id, reconnect_attempts),
                    )
                    reconnect_delay = min(reconnect_delay * 2, max_reconnect_delay)
