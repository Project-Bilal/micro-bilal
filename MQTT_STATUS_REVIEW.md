# MQTT / Status Handling — Review & Fix Tracker

Review of how `micro-bilal` handles MQTT, status, and the retained-message
change shipped in firmware 1.7 (commit `1f8b82f`). Working document — we address
these one by one and check them off.

- **Scope:** `source/mqtt.py` (primary), `source/main.py`, and the off-repo
  `bilal-listener` bridge (`/home/rafik_vcu/bilal-listener/bridge.py` on the
  `bilal-cast-mqtt` VM) for #1.
- **Line numbers** refer to `source/mqtt.py` at commit `1f8b82f` unless noted.
- **Status legend:** `[ ]` todo · `[~]` in progress · `[x]` done

---

## Priority A — tied to the retain=True change

### [x] A1. `/status` topic multiplexes two message shapes — RESOLVED (verified safe, no code change)
**Severity:** ~~High~~ → **Low** (latent design smell only) · **Investigated 2026-07-05**

`lwt_topic` (`projectbilal/{id}/status`) receives two different payloads:
- `send_status_update()` → `{"status", "timestamp", "firmware_version"}` — **retained** (line 104)
- `play()` finally block (line 571) and pending-result flush (line 780) →
  `{"type":"playback_result", "confirmed", "label", "timestamp"}` — not retained,
  **no `status` / `firmware_version` fields**

**Verdict: not a bug.** Both consumers type-check before acting:
- **Bridge** (`/home/rafik_vcu/bilal-listener/bridge.py`): guards
  `status = data.get("status"); if not status: return` → skips `playback_result`.
  Writes via `.update()` (merge), and only sets `firmware` when
  `firmware_version` is present → never nulls out other Firestore fields.
- **App** (`components/ChromecastSettings.tsx:157`): handles only
  `message.type === "playback_result"` (subscribes via
  `lib/mqtt.ts:294 subscribeToStatus`) to clear the test-play spinner.

So the topic is intentionally shared and safe. Splitting `playback_result` onto
its own topic would require firmware + app changes for cleanliness only — **not
worth the cross-repo churn.** Closed with no code change. Revisit only if a
future consumer stops type-checking.

---

### [x] A2. Retained `"online"` can become a stale lie — especially during OTA — FIXED
**Severity:** High (false "online" in Firestore/app) · **Fixed 2026-07-05**

**Fix applied:** both OTA handlers (`update` line ~201, `update_app` line ~253)
now call `self.mqtt_disconnect()` instead of the raw `self.mqtt.disconnect()`,
which publishes a retained `"offline"` before the graceful disconnect. Device now
correctly shows offline through flash/reboot, and stays offline if the OTA bricks
it. Verified the two OTA paths were the *only* gap — all other offline paths
(crash, reconnect-reboot, delete, factory-reset) drop TCP ungracefully so the LWT
fires. **Residual (accepted):** if the *broker itself* restarts while a device is
genuinely offline, that device's retained `"online"` persists with no LWT to
correct it until it reconnects. Deferred — would need MQTT v5 message-expiry or a
bridge-side staleness check; not worth it now.

<details><summary>original description</summary>

**Severity:** High (false "online" in Firestore/app)

The LWT only fires on an **ungraceful** disconnect. Both OTA handlers call
`self.mqtt.disconnect()` (graceful) before rebooting — lines 200 (`update`) and
255 (`update_app`) — which **suppresses the LWT**. So the retained status stays
`"online"` for the entire download + flash + reboot window. If an OTA fails to
come back (bad flash, rollback loop, wrong WiFi), Firestore shows the device
online indefinitely. Same failure if the broker restarts while a device is
genuinely offline: its retained `"online"` persists with no LWT to correct it.

**Fix direction:** publish a retained `"offline"` immediately before the OTA
disconnect/reset (see A3), and/or reconsider whether `"online"` should be
retained at all vs. only retaining the offline/LWT.
</details>

---

### [x] A3. `mqtt_disconnect()` is dead code — FIXED (with A2)
**Severity:** Medium (root cause of A2) · **Fixed 2026-07-05**

`mqtt_disconnect()` (line 109) is now live — called by both OTA handlers as part
of the A2 fix. No longer dead code; kept as-is.

---

## Priority B — independent correctness

### [x] B1. No NTP sync → every `timestamp` is meaningless — FIXED (chose "make them real")
**Severity:** Medium · **Fixed 2026-07-05**

Verified first that **nothing currently reads these timestamps** (bridge writes
only `{status, firmware}`; app reads only `type`/`status`). User chose to make
them real rather than remove them (future observability as fleet scales).

**Fix applied:**
- `main.py`: new `_sync_time()` does best-effort `ntptime.settime()` in
  `startup()` right after WiFi connects — non-fatal on failure.
- `mqtt.py`: added `_UNIX_OFFSET = 946684800` + `_unix_now()` helper (MicroPython
  epoch is 2000, so `time.time() + offset` = real Unix seconds). All wall-clock
  emissions now use it: `_status_json()` (status), the LWT, and `playback_result`.
- **LWT timestamp fixed too:** `self.lwt_message` is no longer built once in
  `__init__`; it's rebuilt via `_status_json("offline")` in `mqtt_connect()` each
  connect (after NTP), so it carries a real connect-time timestamp.
- Left monotonic deltas raw on purpose: `uptime`, dedup window, `_start_time`.

**Deferred:** periodic re-sync. ESP32 crystal drift (~20 ppm ≈ 1.7 s/day) is
negligible for observability, so boot-only sync is fine.

---

### [x] B2. Watchdog starvation risk during a long play — FIXED
**Severity:** Medium (device reset mid-Adhan) · **Fixed 2026-07-05**

**Fix applied:** exposed the WDT as `self.wdt` (set in `mqtt_run`, initialized
`None` in `__init__`) and added a guarded `self._feed_wdt()` helper. Fed at every
bounded blocking point in the play path: discovery-wait loop, speaker wake-retry
`sleep(3)`, both confirm sleeps (2 s / 5 s), and around the post-cast
`wifi_connect()` (~30 s, the worst offender). Feeds are placed *after* known
waits complete — a genuine hang (e.g. inside `play_url`) still trips the 120 s
WDT, so we only suppress resets during legitimate progress.

<details><summary>original description</summary>


The WDT (120s, line 590) is fed only in the `mqtt_run` loop — never during the
blocking `sub_cb`/`play()`. Worst-case a single play chains: discovery wait
(≤15s, line 165) + Chromecast connect with 3s wake-retry (line 489) + `play_url`
+ 2–5s confirm sleeps (lines 506/518) + post-cast WiFi recovery in `finally`
(`wifi_connect` up to 2×15s = 30s, line 553). That can approach/exceed 120s and
reset the device mid-playback. (Previously fought in history — this path reopens
it.)

**Fix direction:** feed the WDT at safe points inside `play()` and the discovery
wait loop, or pass the `wdt` into `play()`.
</details>

---

## Priority C — minor / cosmetic

### [x] C1. Reboot threshold vs. comment mismatch — FIXED
Comment now says "3 failures" to match `reconnect_attempts >= 3` (kept the
existing behavior; only corrected the comment).

### [x] C2. `"online"` sent twice on reconnect — FIXED
Removed the redundant `send_status_update("online")` in the reconnect-success
path; `mqtt_connect()` already publishes it (now the only call).

### [x] C3. Retained status never cleared on `delete_device` — FIXED
On delete: publish `(lwt_topic, "", retain=True)` to clear the retained status,
then `self.mqtt.disconnect()` (graceful) so the LWT does **not** re-publish a
retained "offline" on the `machine.reset()` that follows. (Needed the graceful
disconnect — otherwise the ungraceful reset would fire the Will and undo the
clear.)

### [x] C4. `Pin(0, …)` re-created every second — FIXED
`button = Pin(0, Pin.IN, Pin.PULL_UP)` now constructed once before the
`mqtt_run` loop; the loop only polls `button.value()`.

---

## Working order (proposed)

1. ~~**A1** — read `bridge.py`, confirm the null-out risk, then split topics.~~ ✅ done (no change needed)
2. ~~**A2 + A3** — fix the offline/LWT handling together (same code paths).~~ ✅ done (`mqtt.py`)
3. ~~**B1** — NTP + lazy LWT timestamp.~~ ✅ done (`main.py`, `mqtt.py`)
4. ~~**B2** — watchdog feeding in `play()`.~~ ✅ done (`mqtt.py`)
5. ~~**C1–C4** — cleanups, batched.~~ ✅ done (`mqtt.py`)

> **All code items landed.** Remaining: bump `FIRMWARE_VERSION` (→ 1.8), then
> flash/upgrade the plugged-in device. ← **final step, needs your go-ahead**

## Notes / decisions log
- **2026-07-05 — A1 closed, no code change.** Pulled `bridge.py` off the VM and
  grepped the app. Both the bridge and the app type-check `/status` messages, so
  the shared topic is safe. Splitting would be firmware+app churn for cosmetics
  only. Left as-is; downgraded to Low.
- **2026-07-05 — A2 + A3 fixed in `mqtt.py`.** Swapped the raw
  `self.mqtt.disconnect()` in both OTA handlers for `self.mqtt_disconnect()`
  (publishes retained `"offline"` first). Fixes stale-online during OTA and makes
  `mqtt_disconnect()` live code. Accepted residual: broker-restart stale-online
  (needs v5 expiry / bridge staleness check — deferred). Left the reconnect-
  cleanup disconnect (line ~712) raw on purpose: socket is already dead there.
- **2026-07-05 — B1 fixed (user chose "make them real").** Confirmed no consumer
  reads the timestamps today, but added NTP + real Unix-epoch emission anyway for
  future observability. `main._sync_time()` (boot, best-effort) + `_unix_now()`
  in `mqtt.py`. LWT now rebuilt per-connect for an honest timestamp. Boot-only
  sync (drift negligible).
- **2026-07-05 — B2 fixed in `mqtt.py`.** WDT now `self.wdt`, fed via
  `_feed_wdt()` at bounded blocking points in the play path. Real hangs still trip
  the 120 s WDT (feeds only after known waits).
- **2026-07-05 — C1–C4 fixed in `mqtt.py`.** Comment→code (3 failures); dropped
  duplicate online publish; clear retained + graceful disconnect on delete;
  hoisted `Pin(0)` out of the loop. All behavior-preserving except C3 (adds
  retained-clear) and C2 (one fewer publish).
