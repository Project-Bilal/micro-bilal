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
# Consecutive out-of-memory cast failures before the device reboots itself.
# Dispatches arrive in pairs (reminder ~15 min before the prayer), so 2 means
# recovery inside ~15 minutes, losing at most two sounds.
_MEM_FAILURE_REBOOT_THRESHOLD = const(2)
# MicroPython's epoch is 2000-01-01; add this to reach the Unix (1970) epoch.
_UNIX_OFFSET = const(946684800)
# Connect failures worth a second attempt. A sleeping single speaker times out;
# a speaker group that is reforming resets the connection outright. Both are
# transient. Before 1.11 only ETIMEDOUT was retried, so an ECONNRESET went
# straight to silence with no retry at all — that cost a full Dhuhr on 2026-08-04.
_TRANSIENT_CONNECT_ERRORS = ("ETIMEDOUT", "ECONNRESET")


def _unix_now():
    """Current time as Unix epoch seconds. NTP is synced at boot
    (see main._sync_time); until then this is boot-relative + offset."""
    return time.time() + _UNIX_OFFSET


class MQTTHandler(object):
    def __init__(self, id):
        self.mqtt = None
        self.id = id
        self.device_name = self._load_device_name()
        self.connected = False
        self.reboot_requested = False
        self.wdt = None  # set in mqtt_run(); fed from play() to avoid resets
        self.discovery_in_progress = False
        self._play_in_progress = False
        self._last_play_url = None
        self._last_play_time = 0
        self._dedup_window = 10  # seconds
        self._play_count = 0
        self._play_confirmed_count = 0
        self._error_count = 0
        self._errors_total = 0  # cumulative; _error_count is zeroed each report
        # errors_total mixes two unrelated failures, which makes a device on a
        # flaky network look like it is failing to cast. Split them, but keep
        # the combined counter so existing history stays comparable.
        self._cast_errors_total = 0
        self._conn_errors_total = 0
        self._consecutive_mem_failures = 0
        self._start_time = time.time()
        self._pending_playback_result = None
        self._post_cast_reconnect = False
        self.lwt_topic = f"projectbilal/{self.id}/status"
        # LWT payload is (re)built at connect time in mqtt_connect() so its
        # timestamp reflects the actual connection (after NTP sync), not boot.
        self.lwt_message = None

    def _load_device_name(self):
        """Load device name from NVS, fallback to MAC address."""
        try:
            import esp32
            nvs = esp32.NVS("device")
            buf = bytearray(128)
            length = nvs.get_blob("name", buf)
            name = buf[:length].decode()
            if name:
                print(f"Device name loaded from NVS: {name}")
                return name
        except Exception:
            pass
        return self.id

    @property
    def _label(self):
        """Short label for ntfy messages: name if set, otherwise MAC."""
        if self.device_name != self.id:
            return '"%s"' % self.device_name
        return self.id

    def _feed_wdt(self):
        """Feed the watchdog if it's running. Callable from the blocking
        play()/discovery paths, which otherwise starve the WDT (fed only in
        the mqtt_run loop) and cause a reset mid-playback."""
        if self.wdt:
            self.wdt.feed()

    @staticmethod
    def _is_memory_error(e):
        """True if an exception means WE ran out of memory, as opposed to the
        cast target being unreachable.

        mbedTLS allocates from the ESP-IDF internal DRAM heap, which is a
        separate pool from the MicroPython GC heap that gc.mem_free() reports —
        so a device can show ~78 KB free and still fail every TLS handshake.
        Once that pool is exhausted the failure is permanent until reboot.

        Only these errors justify the reboot valve. Connectivity failures
        (ETIMEDOUT/EHOSTUNREACH — speaker off or unplugged) must NOT count, or
        a user with an unplugged Chromecast gets a device that reboots itself
        several times a day forever."""
        if isinstance(e, MemoryError):
            return True
        s = str(e)
        # "ALLOC_FAILED" covers MBEDTLS_ERR_MPI_ALLOC_FAILED (observed in the
        # field) and MBEDTLS_ERR_SSL_ALLOC_FAILED.
        return "ALLOC_FAILED" in s or "ENOMEM" in s or "memory allocation failed" in s

    @staticmethod
    def _dram_stats():
        """Free ESP-IDF internal DRAM as (free, largest_free_block, min_free).

        largest_free_block is the metric that predicts cast failure: mbedTLS
        needs a CONTIGUOUS allocation, so fragmentation shows up here as the
        largest block shrinking while total free still looks healthy.
        Best-effort — returns Nones on builds without idf_heap_info."""
        try:
            import esp32

            # Each region is (total, free, largest_free, min_free).
            regions = esp32.idf_heap_info(esp32.HEAP_DATA)
            # min_free is summed, not min()'d: taking the minimum across regions
            # just reports whichever tiny region sits lowest, which pinned this
            # at 4 bytes on every device forever. Summed, it is a low-water mark
            # for total free DRAM and actually moves.
            return (
                sum(r[1] for r in regions),
                max(r[2] for r in regions),
                sum(r[3] for r in regions),
            )
        except Exception:
            return None, None, None

    @staticmethod
    def _net_stats():
        """Station-side network identity as (ip, ssid, bssid, rssi).

        Casting is a LAN operation, so when a cast times out the first
        question is whether the ESP32 and the speaker are even on the same
        subnet, and which access point the ESP32 is associated to. Neither
        was reportable before 1.14, which cost a full day of guessing on
        2026-08-05 when Kirkland could reach the internet fine but could not
        open a socket to a speaker its own phone could resolve.

        Every field is fetched independently: WLAN.config() raises for keys a
        given port does not implement, and one unsupported key must not take
        the rest of the report down with it. Read-only — nothing here touches
        the radio state."""
        ip = ssid = bssid = rssi = None
        try:
            import network

            wlan = network.WLAN(network.STA_IF)
            try:
                ip = wlan.ifconfig()[0]
            except Exception:
                pass
            for key in ("essid", "ssid"):
                try:
                    ssid = wlan.config(key)
                    break
                except Exception:
                    continue
            try:
                import binascii

                bssid = binascii.hexlify(wlan.config("bssid"), ":").decode()
            except Exception:
                pass
            try:
                rssi = wlan.status("rssi")
            except Exception:
                pass
        except Exception:
            pass
        return ip, ssid, bssid, rssi

    def _publish_health(self, reset_counters=True):
        """Build and publish one health report. Best-effort by design — a
        failed report must never disturb the MQTT loop that plays the athan.

        reset_counters is False for on-demand polls so that asking for a
        report does not blank the interval counters the next periodic report
        would have carried."""
        try:
            import gc

            # free_mem is the MicroPython GC heap; dram_* is the ESP-IDF
            # internal heap mbedTLS actually allocates from. They move
            # independently — a wedged device has been observed with MORE
            # free_mem than a healthy one — so dram_largest is the number to
            # watch for cast health.
            dram_free, dram_largest, dram_min = self._dram_stats()
            ip, ssid, bssid, rssi = self._net_stats()
            health = json.dumps({
                "type": "health",
                "uptime": int(time.time() - self._start_time),
                "plays": self._play_count,
                "confirmed": self._play_confirmed_count,
                "errors": self._error_count,
                "errors_total": self._errors_total,
                "cast_errors_total": self._cast_errors_total,
                "conn_errors_total": self._conn_errors_total,
                "free_mem": gc.mem_free(),
                "dram_free": dram_free,
                "dram_largest": dram_largest,
                "dram_min": dram_min,
                "reset_cause": machine.reset_cause(),
                "firmware": FIRMWARE_VERSION,
                "ip": ip,
                "ssid": ssid,
                "bssid": bssid,
                "rssi": rssi,
            })
            self.mqtt.publish(f"projectbilal/{self.id}/health", health)
            if reset_counters:
                self._play_count = 0
                self._play_confirmed_count = 0
                self._error_count = 0
            return True
        except Exception:
            return False  # Best-effort

    def mqtt_connect(self):
        self.mqtt = MQTTClient(
            client_id=self.id,
            server=_MQTT_HOST,
            port=_MQTT_PORT,
            keepalive=_KEEPALIVE,
        )

        # Build the LWT fresh so its timestamp reflects this connection (after
        # NTP sync), then register it before connecting.
        self.lwt_message = self._status_json("offline")
        try:
            self.mqtt.set_last_will(
                self.lwt_topic, self.lwt_message, retain=True, qos=1
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

    def _status_json(self, status):
        """Build a status payload with a real Unix-epoch timestamp."""
        return json.dumps({
            "status": status,
            "timestamp": _unix_now(),
            "firmware_version": FIRMWARE_VERSION,
        })

    def send_status_update(self, status):
        """Send status update to the status topic with firmware info"""
        try:
            # Retained so the app sees the device's last known state on subscribe.
            self.mqtt.publish(self.lwt_topic, self._status_json(status), retain=True)
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
                    "[ESP32 %s] Duplicate play rejected: %s" % (self._label, props.get("label", "audio")),
                    topic="projectbilal-events",
                    priority=2,
                    tags="speaker",
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
                    self._feed_wdt()
                if self.discovery_in_progress:
                    print("Discovery still in progress, proceeding anyway")

            ip = props.get("ip")
            port = props.get("port")
            volume = props.get("volume")
            label = props.get("label", "audio")

            if all([url, ip, port]):
                ntfy_alert(
                    "[ESP32 %s] Received play: %s" % (self._label, label),
                    topic="projectbilal-events",
                    priority=2,
                    tags="speaker",
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

                # Publish a retained "offline" before disconnecting. A graceful
                # MQTT disconnect suppresses the LWT, so without this the retained
                # status would stay "online" through the whole flash + reboot
                # (and forever if the OTA bricks the device).
                print("Disconnecting from MQTT for OTA update...")
                self.mqtt_disconnect()

                # Small delay to ensure disconnection is complete
                time.sleep(1)

                # Start OTA update
                print("Starting firmware download and flash...")
                ota.update.from_file(url=url, verify=True, reboot=True)

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

            # Publish a retained "offline" before disconnecting. A graceful
            # disconnect suppresses the LWT, so without this the retained status
            # would stay "online" through the download + reboot window.
            print("Disconnecting from MQTT for app update...")
            self.mqtt_disconnect()

            # Import dependencies
            import urequests
            import os
            import gc

            # Download and write each file with streaming to avoid RAM exhaustion.
            # Files are backed up first so a failed download can be rolled back.
            updated_files = []
            failed_files = []

            for filename in files:
                file_path = "/" + filename
                backup_path = "/" + filename + ".bak"
                gc.collect()

                try:
                    print(f"Downloading {filename}...")
                    file_url = base_url + filename

                    r = urequests.get(file_url)
                    if r.status_code != 200:
                        print(f"Failed to download {filename}: HTTP {r.status_code}")
                        failed_files.append(filename)
                        r.close()
                        break

                    # Backup existing file before overwriting
                    try:
                        os.rename(file_path, backup_path)
                    except Exception as e:
                        print(f"No existing file to backup ({filename}): {e}")

                    # Stream response to file in chunks to avoid OOM
                    total = 0
                    with open(file_path, "wb") as f:
                        while True:
                            chunk = r.raw.read(1024)
                            if not chunk:
                                break
                            f.write(chunk)
                            total += len(chunk)
                    r.close()

                    print(f"Downloaded and wrote {filename} ({total} bytes)")
                    updated_files.append(filename)

                    time.sleep(0.5)

                except Exception as e:
                    print(f"Error updating {filename}: {e}")
                    failed_files.append(filename)
                    # Restore backup if download/write failed
                    try:
                        os.rename(backup_path, file_path)
                        print(f"Restored backup for {filename}")
                    except Exception as e:
                        print(f"WARNING: Could not restore backup for {filename}: {e}")
                    break

            # If any file failed, roll back all updated files
            if failed_files:
                print("=" * 40)
                print("Update failed, rolling back...")
                for fn in updated_files:
                    try:
                        os.rename("/" + fn + ".bak", "/" + fn)
                        print(f"  Rolled back {fn}")
                    except Exception as e:
                        print(f"  WARNING: Rollback failed for {fn}: {e}")
                print("  Failed: %s" % failed_files)
                ntfy_alert(
                    "[ESP32 %s] App update failed: %s" % (self._label, failed_files),
                    priority=4,
                    tags="warning",
                )
                print("=" * 40)
                print("Reconnecting to MQTT...")
                from utils import wifi_connect

                wifi_connect()
                self.mqtt_connect()
                return

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
                    "[ESP32 %s] App updated: %s" % (self._label, ", ".join(updated_files)),
                    topic="projectbilal-events",
                    priority=2,
                    tags="package",
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

        if action == "reboot":
            """
            Reboot the device.

            {"action": "reboot"}

            Exists so recovering a wedged device doesn't require the update_app
            path, which rewrites files on what may already be a memory-starved
            device. Reset happens in mqtt_run, not here, so the callback can
            return cleanly first.
            """
            print("Reboot requested via MQTT")
            ntfy_alert(
                "[ESP32 %s] Reboot requested via MQTT" % self._label,
                topic="projectbilal-events",
                priority=2,
                tags="arrows_counterclockwise",
            )
            self.mqtt_disconnect()
            self.reboot_requested = True
            return

        if action == "ble":
            asyncio.run(run_ble())

        if action == "discover":
            # mDNS discovery moved to mobile app to prevent WiFi instability.
            # Respond immediately so older app versions don't hang.
            response = {"discovery_complete": True, "total_found": 0}
            self.mqtt.publish(topic, json.dumps(response))
            print("Discovery delegated to mobile app")

        if action == "health":
            """
            Publish a health report immediately.

            {"action": "health"}

            Diagnostic escape hatch: the periodic report only fires every
            ~10 minutes, and a device that just rebooted is 10 minutes from
            saying anything at all. Counters are left untouched so polling
            does not distort the periodic series.
            """
            print("Health report requested via MQTT")
            self._publish_health(reset_counters=False)

        if action == "set_device_name":
            name = props.get("name")
            if not name:
                print("MQTT: set_device_name missing name")
                return
            try:
                import esp32
                nvs = esp32.NVS("device")
                nvs.set_blob("name", name)
                nvs.commit()
                self.device_name = name
                print(f"MQTT: Device name saved to NVS: {name}")
                ntfy_alert(
                    "[ESP32 %s] Device name set: %s" % (self.id, name),
                    topic="projectbilal-events",
                    priority=2,
                    tags="label",
                )
            except Exception as e:
                print(f"MQTT: Failed to save device name to NVS: {e}")
                ntfy_alert("[ESP32 %s] Failed to save device name: %s" % (self.id, e), priority=4, tags="warning")

        if action == "delete_device":
            try:
                import esp32

                nvs = esp32.NVS("wifi_creds")
                nvs.erase_key("PASSWORD")
                nvs.erase_key("SSID")
                nvs.erase_key("SECURITY")
                nvs.commit()
                print("WiFi credentials deleted from NVS")

                # Clear device name
                try:
                    nvs_device = esp32.NVS("device")
                    nvs_device.erase_key("name")
                    nvs_device.commit()
                    print("Device name deleted from NVS")
                except Exception:
                    pass

                # Send confirmation back
                message = {"status": "success", "message": "WiFi credentials deleted"}
                self.mqtt.publish(topic, json.dumps(message))
                ntfy_alert(
                    "[ESP32 %s] WiFi credentials deleted" % self._label,
                    topic="projectbilal-events",
                    priority=2,
                    tags="wastebasket",
                )

                # Wait a moment for message to be sent, then reboot
                time.sleep(3)

                # Clear the retained status so a deleted device leaves no ghost
                # on the broker, and disconnect gracefully so the LWT does NOT
                # re-publish a retained "offline" on reset (ungraceful drop).
                try:
                    self.mqtt.publish(self.lwt_topic, "", retain=True)
                    self.mqtt.disconnect()
                    self.connected = False
                except Exception as e:
                    print("Cleanup before delete-reset failed:", e)

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
                    "[ESP32 %s] Delete WiFi credentials failed: %s" % (self._label, e),
                    priority=4,
                    tags="warning",
                )

    def play(self, url, ip, port, vol, label="audio"):
        import gc
        device = None
        playback_confirmed = False
        # Set before the try: the finally block reports these, and an exception
        # from Chromecast() would otherwise leave them undefined.
        reason = None
        retried = False
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

            # Create Chromecast connection (retry once if speaker is asleep)
            try:
                device = Chromecast(ip, port)
            except OSError as e:
                reason = None
                for candidate in _TRANSIENT_CONNECT_ERRORS:
                    if candidate in str(e):
                        reason = candidate
                        break
                if reason:
                    # A reforming group takes longer to come back than a
                    # sleeping speaker, so give ECONNRESET a longer breath.
                    backoff = 5 if reason == "ECONNRESET" else 3
                    print(
                        "MQTT: connect failed (%s), retrying in %ds..."
                        % (reason, backoff)
                    )
                    ntfy_alert(
                        "[ESP32 %s] Speaker connect retry (%s): %s"
                        % (self._label, reason, label),
                        topic="projectbilal-events",
                        priority=2,
                        tags="speaker",
                    )
                    gc.collect()
                    time.sleep(backoff)
                    self._feed_wdt()
                    device = Chromecast(ip, port)
                else:
                    raise

            # Play URL with volume (volume is set after app launch, before media load)
            print("MQTT: connected to speaker, loading media (vol %s)" % vol)
            playback_confirmed = device.play_url(url, volume=vol)
            reason = getattr(device, "last_error", None)

            # An idle speaker can be slower than one timeout window. The connect
            # path above already retries on ETIMEDOUT; this is the same problem
            # one step later. How we retry depends on how far we got.
            if not playback_confirmed:
                retried = True
                if reason == "no_media_ack":
                    # Media was already handed over and is probably playing.
                    # Listen longer on the SAME connection — re-sending LOAD
                    # would restart audio mid-adhan.
                    print("MQTT: no ack yet, listening for another window")
                    self._feed_wdt()
                    playback_confirmed = device.wait_for_media_ack(8000)
                else:
                    # no_session (or unknown): nothing was ever requested, so a
                    # fresh connection can't interrupt anything.
                    print("MQTT: no session (%s), retrying on a new connection" % reason)
                    try:
                        device.disconnect()
                    except Exception:
                        pass
                    device = None
                    gc.collect()
                    time.sleep(3)
                    self._feed_wdt()
                    device = Chromecast(ip, port)
                    playback_confirmed = device.play_url(url, volume=vol)
                reason = getattr(device, "last_error", None)
                self._feed_wdt()

            if playback_confirmed:
                self._play_confirmed_count += 1
                self._consecutive_mem_failures = 0
                time.sleep(2)
                self._feed_wdt()
                print("MQTT: Audio playback confirmed, starting...")
                # "after retry" is deliberately its own message: a rise in these
                # means speakers are going idle, which is worth seeing BEFORE
                # anyone actually misses a prayer.
                if retried:
                    # Carries the diagnostic too: a slow play that succeeded is
                    # the clearest place to see whether heartbeats were in play.
                    try:
                        diag_fn = getattr(device, "diagnostics", None)
                        retry_diag = diag_fn() if diag_fn else ""
                    except Exception:
                        retry_diag = ""
                    ntfy_alert(
                        "[ESP32 %s] Playback confirmed after retry: %s | %s"
                        % (self._label, label, retry_diag),
                        topic="projectbilal-events",
                        priority=2,
                        tags="speaker",
                    )
                else:
                    ntfy_alert(
                        "[ESP32 %s] Playback confirmed: %s" % (self._label, label),
                        topic="projectbilal-events",
                        priority=2,
                        tags="speaker",
                    )
            else:
                print("MQTT: playback failed (%s)" % reason)
                time.sleep(5)
                self._feed_wdt()
                # What the speaker actually sent back. Defensive getattr: an OTA
                # can land mqtt.py and cast.py out of step, and a missing
                # diagnostic must never turn a failed play into a crash.
                try:
                    diag_fn = getattr(device, "diagnostics", None)
                    diag = diag_fn() if diag_fn else ""
                except Exception:
                    diag = ""
                if reason == "no_media_ack":
                    # We sent LOAD and never saw MEDIA_STATUS come back. This
                    # was previously filed as "probably played, priority 3" —
                    # a confirmed-silent Asr on 2026-08-04 disproved that, and
                    # nothing here ever verified the receiver accepted the LOAD.
                    ntfy_alert(
                        "[ESP32 %s] Sent to speaker, never confirmed playing: %s | %s"
                        % (self._label, label, diag),
                        topic="projectbilal-events",
                        priority=4,
                        tags="warning",
                    )
                else:
                    # Speaker never gave us a session, even after a retry. The
                    # audio was never requested, so this is real silence.
                    ntfy_alert(
                        "[ESP32 %s] No response from speaker, nothing played: %s | %s"
                        % (self._label, label, diag),
                        priority=4,
                        tags="warning",
                    )

        except Exception as e:
            self._error_count += 1
            self._errors_total += 1
            self._cast_errors_total += 1
            print("MQTT: Chromecast error: %s" % e)
            ntfy_alert("[ESP32 %s] Chromecast play failed: %s" % (self._label, e), priority=4, tags="warning")
            import sys
            sys.print_exception(e)

            # Only out-of-memory failures count toward the reboot valve; a
            # connectivity failure means the speaker is off, and rebooting
            # ourselves would neither help nor stop. Deliberately not reset
            # here — the counter clears only on a confirmed playback.
            if self._is_memory_error(e):
                self._consecutive_mem_failures += 1
                print(
                    "MQTT: memory-class cast failure %d/%d"
                    % (self._consecutive_mem_failures, _MEM_FAILURE_REBOOT_THRESHOLD)
                )

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
                self._feed_wdt()  # wifi_connect can block ~30s
                wifi_ip = wifi_connect()
                self._feed_wdt()
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
                "reason": reason,
                "retried": retried,
                "label": label,
                "timestamp": _unix_now(),
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

            # Reboot valve. Internal DRAM exhaustion is unrecoverable in
            # software — gc.collect() cannot touch that pool — and MQTT stays
            # healthy throughout, so nothing else would ever restart us. Left
            # alone, the device sits "online", ACKs every command and silently
            # plays nothing indefinitely.
            if self._consecutive_mem_failures >= _MEM_FAILURE_REBOOT_THRESHOLD:
                print("MQTT: out of memory for casting, rebooting to recover")
                ntfy_alert(
                    "[ESP32 %s] Rebooting: %d consecutive out-of-memory cast failures"
                    % (self._label, self._consecutive_mem_failures),
                    priority=4,
                    tags="warning",
                )
                # Retained "offline" + graceful disconnect (suppresses the LWT)
                # so the status doesn't read "online" through the reboot window.
                self.mqtt_disconnect()
                self.reboot_requested = True

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
        self.wdt = wdt  # expose so play()/discovery can feed it too

        # Factory-reset button (GPIO0) — construct once, polled each tick below.
        button = Pin(0, Pin.IN, Pin.PULL_UP)

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
                    err = e.errno if hasattr(e, 'errno') else 0
                    if err == 9 or err == 113:  # EBADF or ECONNABORTED
                        print(f"Socket corruption detected (errno {err}), forcing WiFi reset...")
                        import network
                        wlan = network.WLAN(network.STA_IF)
                        wlan.disconnect()
                        wlan.active(False)
                        time.sleep(3)
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
                    self._publish_health()

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
                self._errors_total += 1
                self._conn_errors_total += 1
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

                # Reboot safety valve — with mDNS disabled, 3 failures means
                # something is seriously wrong
                if reconnect_attempts >= 3:
                    print("Too many reconnect failures, rebooting...")
                    ntfy_alert(
                        "[ESP32 %s] Rebooting after %d reconnect failures" % (self._label, reconnect_attempts),
                        priority=4,
                        tags="warning",
                    )
                    time.sleep(2)
                    machine.reset()

                # Clean up current connection
                try:
                    if self.mqtt:
                        self.mqtt.disconnect()
                except Exception as e:
                    print(f"MQTT disconnect error during cleanup: {e}")
                self.mqtt = None  # Free socket even if disconnect failed

                # Fast reconnect after casting (WiFi already recovered in play())
                if self._post_cast_reconnect:
                    self._post_cast_reconnect = False
                    reconnect_delay = 2
                    print("Fast reconnect after cast (2s)...")
                    time.sleep(2)
                else:
                    # Sleep in chunks to keep watchdog fed
                    print(f"Waiting {reconnect_delay} seconds before reconnect...")
                    remaining = reconnect_delay
                    while remaining > 0:
                        time.sleep(min(remaining, 30))
                        remaining -= 30
                        wdt.feed()
                wdt.feed()

                # Verify WiFi before MQTT reconnect
                import network
                wlan = network.WLAN(network.STA_IF)
                if not wlan.isconnected():
                    print("WiFi disconnected, reconnecting WiFi first...")
                    wdt.feed()
                    from utils import wifi_connect
                    wifi_ip = wifi_connect()
                    if not wifi_ip:
                        print("WiFi reconnect failed, will retry...")
                        ntfy_alert(
                            "[ESP32 %s] WiFi reconnect failed (attempt %d)" % (self._label, reconnect_attempts),
                            priority=4,
                            tags="warning",
                        )
                        reconnect_delay = min(reconnect_delay * 2, max_reconnect_delay)
                        continue
                    ntfy_alert(
                        "[ESP32 %s] WiFi reconnected before MQTT" % self._label,
                        topic="projectbilal-events",
                        priority=2,
                        tags="electric_plug",
                    )

                # Attempt to reconnect MQTT
                wdt.feed()
                try:
                    success = self.mqtt_connect()
                    if success:
                        print("Reconnection successful!")
                        ntfy_alert(
                            "[ESP32 %s] Reconnected after disconnect" % self._label,
                            topic="projectbilal-events",
                            priority=2,
                            tags="electric_plug",
                        )
                        # Note: mqtt_connect() already published the "online"
                        # status, so no second send_status_update here.

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
                        # health_counter is deliberately NOT reset here. It used
                        # to be, which meant a device dropping more often than
                        # every _HEALTH_INTERVAL ticks never published health at
                        # all — silencing exactly the flaky devices whose
                        # telemetry is most worth having.
                    else:
                        print("Reconnection failed")
                        ntfy_alert(
                            "[ESP32 %s] MQTT reconnect failed after %s attempts"
                            % (self._label, reconnect_attempts),
                            priority=4,
                            tags="warning",
                        )
                        reconnect_delay = min(reconnect_delay * 2, max_reconnect_delay)

                except Exception as reconnect_error:
                    print("Reconnection attempt failed: %s" % reconnect_error)
                    ntfy_alert(
                        "[ESP32 %s] MQTT reconnect failed after %s attempts"
                        % (self._label, reconnect_attempts),
                        priority=4,
                        tags="warning",
                    )
                    reconnect_delay = min(reconnect_delay * 2, max_reconnect_delay)
