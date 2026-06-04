# nightstand-audio

A local-first bedside audio appliance for Raspberry Pi.

The goal is not to recreate a phone with apps. This is a calm nightstand media player that can start the right audio at 2 AM with one physical button, a rotary knob, and no touchscreen.

## What it does today

- Runs locally on macOS as a simulator.
- Scans the `media/` folders into SQLite.
- Creates demo tracks if no audio files exist.
- Simulates playback with a mock player.
- Renders a model-sized black/white e-ink-style PNG to `data/latest_screen.png`.
- Can optionally push renders to the real Waveshare 4.2 V2 e-paper display on Raspberry Pi.
- Supports Ambient Mode, Active Mode, and Night Mode / Sleep Screen behavior.
- Supports alarm, snooze, sleep timer, preset buttons, and rotary-style menu navigation.
- Treats each button as a persistent resumable folder playlist stream.
- Uses a quiet icon-driven bottom status row for playback, volume, sleep timer, and output.
- Includes structured rotating logs and diagnostics for simulator, display, input, playback, Bluetooth, and state transitions.
- Supports dedicated Bluetooth headphones on Raspberry Pi through PipeWire/WirePlumber A2DP, with BossDAC fallback.

## Intended hardware

- Raspberry Pi Zero 2 W
- InnoMaker DAC Mini HAT PCM5122 / 3.5mm headphone output
- USB sound card feeding MonkMakes amplified speaker for alarm output
- Waveshare 4.2 inch V2 SPI e-paper HAT, 400x300, black/white
- Rotary encoder with push button
- Three momentary preset buttons mapped to folders:
  - Button 1
  - Button 2
  - Button 3
- microSD storage for local audio
- Wi-Fi optional
- Always plugged in for v1

Bluetooth output is now part of the tested appliance path. The current known-good setup uses Nothing Ear (a) as a single dedicated paired device, PipeWire/WirePlumber for the Bluetooth sink, MPV for playback, BossDAC as normal wired fallback, and USB DAC as alarm-only speaker output.

## Known-Good Pi Appliance Setup

This is the current stable hardware/software shape:

- Pi Zero 2 W running headless Raspberry Pi OS / Debian with PipeWire, WirePlumber, BlueZ, MPV, SPI, and lgpio.
- Waveshare 4.2" V2 selected with `DISPLAY_MODEL=waveshare_4in2_v2`.
- BossDAC Mini selected automatically for normal wired playback with `AUDIO_DEVICE=auto`.
- USB DAC selected automatically for wake/alarm speaker playback with `ALARM_AUDIO_DEVICE=auto_usb`.
- Nothing Ear (a) paired/trusted once, then reused as the preferred Bluetooth sink.
- Physical controls use GPIO, while keyboard input remains enabled in `run_pi_appliance` for bench testing.

Run from the Pi:

```bash
cd ~/nightstand-audio
source .venv/bin/activate
GPIOZERO_PIN_FACTORY=lgpio python -m scripts.run_pi_appliance
```

Expected startup/audio behavior:

- Startup renders Ambient or Sleep Screen before any user playback.
- Startup does not launch MPV.
- Pressing Button 1/2/3 launches exactly one MPV process for the selected current track.
- If the preferred Bluetooth earbuds are connected, MPV launches with `--audio-device=pulse/bluez_output...`.
- If Bluetooth is unavailable, MPV launches through BossDAC, typically `--audio-device=alsa/plughw:1,0`.
- Alarm and gentle-wake audio use the USB DAC path only; they should not route to Bluetooth or BossDAC.

Known-good Bluetooth playback log:

```text
[BT] Connected device=Nothing Ear (a) audio_device=pulse/bluez_output.3C_B0_ED_B9_30_FC.1
[BT] Playback sink switched bluetooth success=true
[PLAYBACK] device=pulse/bluez_output.3C_B0_ED_B9_30_FC.1
[PLAYBACK] command=mpv --no-video --no-audio-display --audio-device=pulse/bluez_output.3C_B0_ED_B9_30_FC.1 ...
```

Known-good display safety log:

```text
[EPD] Waveshare GPIO safety check passed; BossDAC I2S pins are protected.
[EPD] GPIO18 pinmux safety check passed: ... GPIO18 = PCM_CLK
```

## Planned Hardware / Shopping List

This is a living hardware plan. Items are not final just because they appear here; status should be updated as parts are confirmed, ordered, received, tested, or replaced.

| Category | Planned Item | Purpose | Link | Status | Notes |
| --- | --- | --- | --- | --- | --- |
| Compute | Raspberry Pi Zero 2 W with headers | Main computer; Bluetooth/Wi-Fi/GPIO | [Raspberry Pi Zero 2 W](https://www.raspberrypi.com/products/raspberry-pi-zero-2-w/) | Planned | Prefer pre-soldered headers if available |
| Audio DAC / wired headphones | InnoMaker DAC Mini HAT PCM5122 for Raspberry Pi Zero 2W with RCA + 3.5mm output | 3.5mm headphone output / wired listening sink | TODO | Ordered | Ordered May 25, 2026; expected May 28, 2026. Used for private wired listening, not the speaker/alarm path |
| USB audio adapter | QAJOPFN USB Audio Adapter, External Sound Card, USB Microphone Adapter to 3.5mm headphone/speaker/microphone jack | Speaker / alarm / fallback audio sink | TODO | Ordered | Ordered May 25, 2026; expected May 28, 2026. Drives the MonkMakes amplified speaker path through 3.5mm output; avoids sharing I2S with the InnoMaker DAC HAT |
| Micro USB OTG adapter | Posdou USB 2.0 Micro USB Male to USB Female OTG Adapter, 2 pack | Connect USB audio adapter or other USB accessories to Pi Zero 2 W | TODO | Ordered | Ordered May 25, 2026; expected May 28, 2026 |
| Audio patch cable | CNCESS CESS-067 Short 3.5mm Audio Shielded Patch Cable, right-angle, 3 inch | Short internal/external headphone/audio patching during prototyping | TODO | Ordered | Ordered May 25, 2026; expected May 28, 2026 |
| Display | Waveshare 4.2inch e-Paper V2, 400x300, black/white, SPI interface | Main e-ink UI | TODO | Tested | Stable appliance display with `DISPLAY_MODEL=waveshare_4in2_v2`; safe wiring keeps Waveshare control pins off BossDAC I2S pins |
| Alternate display | Waveshare 5.83inch E-Paper E-Ink Display HAT, 600x448, black/white, SPI interface | Earlier larger e-ink option | TODO | Replaced | Supported only as an explicit non-default model with `DISPLAY_MODEL=waveshare_5in83_v2` |
| Bluetooth earbuds | Nothing Ear (a) | Dedicated Bluetooth sleep earbuds | [Nothing Ear (a)](https://us.nothing.tech/products/ear-a) | Planned | Dedicated pairing to this appliance preferred |
| Amplified speaker | MonkMakes amplified speaker | v1 alarm/fallback speaker | TODO | Ordered | Driven from the USB sound card 3.5mm output; this is the dedicated alarm/fallback speaker path |
| Internal speaker amp | Adafruit MAX98357A I2S 3W Class D Amplifier Breakout | Former internal speaker amp plan | [Adafruit MAX98357A](https://www.adafruit.com/product/3006) | Replaced | Replaced by the USB sound card + MonkMakes speaker path for v1, keeping I2S dedicated to the InnoMaker DAC HAT |
| Internal speaker | 40mm 4 Ohm 3W speaker | Former raw internal speaker plan | [Adafruit 40mm 4 Ohm 3W speaker](https://www.adafruit.com/product/3968) | Replaced | Current v1 plan uses the MonkMakes amplified speaker through the USB sound card; a passive 4 Ohm speaker would still need an amplifier |
| Rotary control | DIYhz 4 Pack EC11 Rotary Encoder Switch, 20mm half shaft, 20 detents, 20 pulses, 5-pin PCB mount with push button | Volume, menu navigation, play/pause, select | TODO | Ordered | Ordered May 25, 2026; expected May 28, 2026. PCB mount; may need panel-mount adaptation |
| Knob cap | Taiss 2pcs Black Aluminum Rotary Electronic Control Potentiometer Knob, 6mm shaft, 20mm x 15.5mm | Physical dial users touch | TODO | Ordered | Ordered May 25, 2026; expected May 28, 2026. Confirm fit on EC11 shaft |
| Three source buttons | STARELO 5pcs 16mm Momentary Push Button Switch, black shell, IP65, normally open, without LED | Physical source presets | TODO | Ordered | Ordered May 25, 2026; expected May 28, 2026. Button 1/2/3 map to `media/buttons/button-1`, `button-2`, `button-3`; not hardcoded to Bible/Baseball/Music |
| Storage | 128GB microSD card, preferably endurance-rated | OS, SQLite state, local podcast/music files | TODO | Planned | 64GB is probably enough, 128GB gives headroom |
| Power | Raspberry Pi-compatible 5V power supply | Always-on wall power | TODO | Planned | No battery for v1 |
| Prototyping wiring | ELEGOO 120pcs Multicolored Dupont Wire 40pin Male-to-Female, Male-to-Male, Female-to-Female Breadboard Jumper Ribbon Cables Kit | GPIO/buttons/encoder/audio prototyping | TODO | Ordered | Ordered May 25, 2026; expected May 28, 2026 |
| Breadboards | DEYUE breadboard set, 6 pcs 400 pin solderless breadboard kit | Breadboard prototyping for controls/audio experiments | TODO | Ordered | Ordered May 25, 2026; expected May 28, 2026 |
| GPIO stacking / headers | Frienda 8 pcs 2 x 20 (40 pin) extra tall female 0.1 inch pitch stacking header compatible with Raspberry Pi | Clearance and GPIO access with DAC/display/controls | TODO | Ordered | Ordered May 25, 2026; expected May 28, 2026. Likely needed because the InnoMaker DAC HAT and e-paper HAT both interact with GPIO |
| Enclosure | ABS project box or sloped desktop enclosure | First physical case | TODO | Planned | Drill/cut for display, knob, 3 buttons, headphone jack, USB-C, and any speaker/output access needed |
| Frontlight / optional later | Warm white LED strip or edge-lit diffuser parts | Optional e-ink frontlight | TODO | Planned | Not required for v1; e-ink needs frontlight, not backlight |

### Final GPIO / Wiring Plan

The app-owned pin map lives in `app/hardware/pin_map.py`. BCM GPIO numbers are shown first; physical header pins are included to reduce bench wiring mistakes.

| Function | BCM GPIO | Physical Pin | Notes |
| --- | ---: | ---: | --- |
| Waveshare RST | GPIO17 | 11 | e-paper reset |
| Waveshare BUSY | GPIO24 | 18 | e-paper busy/status |
| Waveshare DIN / MOSI | GPIO10 | 19 | SPI MOSI |
| Waveshare DC | GPIO25 | 22 | e-paper data/command |
| Waveshare CLK / SCLK | GPIO11 | 23 | SPI clock |
| Waveshare CS / CE0 | GPIO8 | 24 | SPI chip select |
| Waveshare PWR safety config | GPIO5 | 29 | Software `PWR_PIN`; no physical PWR wire currently used |
| BossDAC PCM_CLK | GPIO18 | 12 | Reserved I2S; never use for display/buttons/rotary |
| BossDAC PCM_FS | GPIO19 | 35 | Reserved I2S; never use for display/buttons/rotary |
| BossDAC PCM_DIN | GPIO20 | 38 | Reserved I2S; never use for display/buttons/rotary |
| BossDAC PCM_DOUT | GPIO21 | 40 | Reserved I2S; never use for display/buttons/rotary |
| Rotary A / CLK | GPIO12 | 32 | Internal pull-up; turn maps to volume/menu movement |
| Rotary B / DT | GPIO13 | 33 | Internal pull-up; turn maps to volume/menu movement |
| Rotary SW / click | GPIO16 | 36 | Knob press/long press |
| Button 1 | GPIO22 | 15 | GPIO-to-GND; maps to `media/buttons/button-1` |
| Button 2 | GPIO23 | 16 | GPIO-to-GND; maps to `media/buttons/button-2` |
| Button 3 | GPIO26 | 37 | GPIO-to-GND; maps to `media/buttons/button-3` |
| MonkMakes speaker | none | n/a | 5V + GND + USB DAC audio; no GPIO power switching |

Waveshare stock `epdconfig.py` may use `PWR_PIN=18`, which conflicts with BossDAC `PCM_CLK`. Use `PWR_PIN=5` or another safe non-I2S GPIO. If `pinctrl get 18` shows output instead of `PCM_CLK`, stop and fix the Waveshare config before running audio.

### Audio Architecture

Normal playback outputs:

- Bluetooth sink -> paired headphones
- InnoMaker DAC headphone sink -> wired headphone jack
- USB sound card sink -> MonkMakes amplified speaker / alarm fallback speaker

Alarm policy:

- Alarm pauses any active playback first.
- Alarm always routes to the dedicated USB sound card -> MonkMakes speaker sink.
- Alarm must not use Bluetooth earbuds, the BossDAC, or the normal headphone jack.

Bluetooth headphone policy:

- The app is optimized for one permanent headphone device.
- Pair once from `Home -> Output -> Pair Headphones`; the app stores the preferred device name and MAC address.
- While Nightstand Audio is running, a background presence monitor checks for the preferred device about every 15 seconds.
- When the earbuds leave the case and become available, the app attempts reconnect automatically.
- If an automatic reconnect window fails, the app waits before trying again so it does not churn on a cached BlueZ device record while the earbuds are still in the case. Tune this with `BLUETOOTH_AUTO_RECONNECT_COOLDOWN_SECONDS`.
- On successful connection, normal playback routes to the PipeWire Bluetooth sink, for example `pulse/bluez_output.3C_B0_ED_B9_30_FC.1`.
- On disconnect, normal playback falls back to BossDAC without stopping playback.
- Triple-clicking any source button starts a manual reconnect window.
- Alarm playback always routes to the USB sound card -> MonkMakes speaker sink.

Output priority:

1. Bluetooth if the trusted earbuds are connected.
2. InnoMaker DAC wired headphones if selected.
3. USB sound card -> MonkMakes speaker fallback.
4. Alarm always uses the USB sound card -> MonkMakes speaker sink.

Bluetooth implementation notes:

- The Pi path uses `bluetoothctl` for pair/trust/connect fallback.
- PipeWire/PulseAudio sink detection uses `pactl` first and `wpctl inspect` as a fallback.
- MPV receives Bluetooth devices as `pulse/<pipewire sink name>`, not ALSA devices.
- The manager persists `preferred_bluetooth_device_name`, `preferred_bluetooth_device_mac`, `preferred_bluetooth_last_connected_at`, and `preferred_output`.
- Reconnect is device-level, not tied to a specific media source.
- A cached `bluetoothctl info <MAC>` record is not treated as presence by itself; the device must be connected, have live discovery/RSSI data, or appear during the monitor's scan sample.
- The app does not expose multi-device Bluetooth management.

Bluetooth headphone media controls are a secondary input path. Physical box controls remain primary, and the device must work completely from the knob/buttons if Bluetooth controls are unavailable.

### Headless Bluetooth A2DP Setup

On the tested headless Pi, BlueZ pairing worked before audio worked. The real blocker was that WirePlumber did not register an A2DP source endpoint for the normal headless `main` profile. The symptom was:

```text
bluetoothctl connect <MAC>
Failed to connect: org.bluez.Error.Failed br-connection-profile-unavailable
```

and:

```text
bluetoothctl show
# missing UUID: Audio Source

wpctl status
# missing bluez / Nothing Ear sink
```

The working fix is two app-adjacent user-level WirePlumber drop-ins on the Pi. Keep these files in place.

```bash
mkdir -p ~/.config/wireplumber/wireplumber.conf.d
```

`~/.config/wireplumber/wireplumber.conf.d/51-bluez-headless-main.conf`:

```text
wireplumber.profiles = {
  main = {
    monitor.bluez.seat-monitoring = disabled
  }
}
```

`~/.config/wireplumber/wireplumber.conf.d/53-bluez-a2dp-only.conf`:

```text
monitor.bluez.properties = {
  bluez5.roles = [ a2dp_source ]
  bluez5.codecs = [ sbc sbc_xq ]
  bluez5.enable-sbc-xq = true
  bluez5.hfphsp-backend = "none"
}
```

Restart order matters when testing manually:

```bash
pkill -u "$USER" wireplumber
systemctl --user stop wireplumber
sudo systemctl restart bluetooth
sleep 3
systemctl --user restart pipewire pipewire-pulse wireplumber
sleep 6
```

Confirm the Pi is advertising A2DP source support:

```bash
bluetoothctl show | grep -E 'UUID|Audio|A/V'
```

Expected:

```text
UUID: Audio Source              (0000110a-0000-1000-8000-00805f9b34fb)
```

Connect and verify a real sink appears:

```bash
bluetoothctl connect 3C:B0:ED:B9:30:FC
sleep 8
bluetoothctl info 3C:B0:ED:B9:30:FC | grep -E 'Connected|UUID|Audio'
wpctl status | grep -iE 'bluez|nothing|sink|device'
pactl list sinks short
```

Known-good output includes:

```text
Connected: yes
Nothing Ear (a) [bluez5]
bluez_output.3C_B0_ED_B9_30_FC.1
```

Direct MPV test:

```bash
mpv --no-video --audio-device=pulse/bluez_output.3C_B0_ED_B9_30_FC.1 \
  "/home/bzavadil/nightstand-audio/media/buttons/button-1/001 - Day 1 - In the Beginning.mp3"
```

If `Audio Source` is missing from `bluetoothctl show`, app changes will not fix Bluetooth audio. Fix the WirePlumber/BlueZ endpoint registration first.

Typical Bluetooth headphone media-control mapping:

- Pinch / play-pause command -> toggle play/pause.
- Double pinch / next command -> next track.
- Triple pinch / previous command -> restart current track if position is over 5 seconds, else previous track.
- Pinch-hold is not captured; leave it for ANC/transparency on the earbuds.

Implementation notes:

- Bluetooth media controls normalize into `MediaCommand` values: `PLAY_PAUSE`, `NEXT_TRACK`, `PREVIOUS_TRACK`, `VOLUME_UP`, and `VOLUME_DOWN`.
- `BluetoothMediaInputAdapter` is a stub for future Linux/Pi integration.
- Future implementation may use BlueZ, PipeWire, MPRIS, and/or MPD media-control integration.
- Earbud controls route into the same playback methods as the physical knob.
- Earbud controls never navigate menus.

### Button / Media Slot Architecture

- Buttons are physical media slots, not hardcoded source names.
- Button 1 maps to `media/buttons/button-1`.
- Button 2 maps to `media/buttons/button-2`.
- Button 3 maps to `media/buttons/button-3`.
- To change what a button plays, replace the contents of that folder.
- Optional `.source.json` inside each folder controls `display_name`, `source_type`, `ordering`, `resume_policy`, and `end_behavior`.

Example:

```text
media/buttons/button-1/.source.json
```

```json
{
  "display_name": "Bible in a Year",
  "source_type": "podcast",
  "ordering": "filename_asc",
  "resume_policy": "resume_playlist",
  "completion_threshold_percent": 95,
  "end_behavior": "stop"
}
```

## Local Mac Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
python -m scripts.seed_library
python -m scripts.render_once
python -m scripts.run_simulator
```

The latest e-ink preview is written to:

```text
data/latest_screen.png
```

To watch for screen updates in another terminal:

```bash
python -m scripts.watch_screen
python -m scripts.watch_screen --open
```

## Live Raspberry Pi E-Paper Mode

On Raspberry Pi, the same simulator can write `data/latest_screen.png` and push every render directly to the physical Waveshare display.

```bash
GPIOZERO_PIN_FACTORY=lgpio python -m scripts.run_live_epd
```

Stable Pi appliance command:

```bash
GPIOZERO_PIN_FACTORY=lgpio python -m scripts.run_pi_appliance
```

`run_live_epd` keeps keyboard input for display bench testing. `run_pi_appliance` uses `INPUT_BACKEND=gpio_keyboard`: the final GPIO22/23/26 source buttons plus GPIO12/13/16 rotary mapping remain active, and keyboard commands still work as a temporary bench fallback.

Equivalent manual form:

```bash
USE_REAL_EPD=true GPIOZERO_PIN_FACTORY=lgpio python -m scripts.run_simulator
```

Required/optional environment variables:

```text
RUNTIME_MODE=appliance
DISPLAY_BACKEND=waveshare
HARDWARE_FALLBACK_TO_SIMULATOR=false
USE_REAL_EPD=true
DISPLAY_MODEL=waveshare_4in2_v2
INPUT_BACKEND=gpio_keyboard
FORCE_EPD_UPDATE=false
EPD_REINIT_EVERY_UPDATE=false
CLEAR_BEFORE_EPD_UPDATE=false
GPIOZERO_PIN_FACTORY=lgpio
NIGHTSTAND_DISPLAY_WIDTH=400
NIGHTSTAND_DISPLAY_HEIGHT=300
NIGHTSTAND_EPD_ROTATE=0
CLEAR_EPD_ON_EXIT=false
EPD_FULL_CLEAR_INTERVAL=50
EPD_RENDER_DEBOUNCE_MS=750
EPD_VOLUME_REFRESH_DEBOUNCE_MS=600
EPD_REFRESH_ON_VOLUME_CHANGE=true
EPD_PARTIAL_UPDATE_ENABLED=true
EPD_DISABLE_PARTIAL=false
EPD_ONE_SHOT_MAJOR_TRANSITIONS=true
EPD_REGION_PARTIAL_ENABLED=true
EPD_PARTIAL_STREAK_LIMIT=8
EPD_PARTIAL_REFRESH_MIN_INTERVAL_MS=500
EPD_MENU_NAVIGATION_UPDATE_MODE=full
EPD_CLOCK_PARTIAL_UPDATE_ENABLED=false
EPD_FORCE_FULL_REFRESH=false
EPD_FORCE_CLEAN_REFRESH=false
EPD_CLOCK_REFRESH_SECONDS=60
EPD_DISABLE_CLOCK_AUTO_REFRESH=false
NIGHT_MODE_ENABLED=true
NIGHT_MODE_START=22:00
NIGHT_MODE_END=06:00
NIGHT_MODE_WAKE_TIMEOUT_SECONDS=30
NIGHT_MODE_DISPLAY_LOCK=true
AMBIENT_MODE_ENABLED=true
ACTIVE_MODE_TIMEOUT_SECONDS=30
AMBIENT_CLOCK_REFRESH_SECONDS=60
AMBIENT_SHOW_PLAYBACK_GLYPH=true
WAVESHARE_EPD_PYTHON_PATH=/home/pi/e-Paper/RaspberryPi_JetsonNano/python
AUDIO_BACKEND=alsa
AUDIO_DEVICE=auto
```

Notes:

- `DISPLAY_MODEL=waveshare_4in2_v2` uses the Waveshare `epd4in2_V2` driver and defaults to `400x300`; this is the stable appliance default.
- `DISPLAY_MODEL=waveshare_5in83_v2` remains available only as an explicit larger-display override.
- `scripts.run_live_epd` defaults to appliance mode: `RUNTIME_MODE=appliance`, `DISPLAY_BACKEND=waveshare`, `AUDIO_BACKEND=alsa`, `AUDIO_DEVICE=auto`, and `HARDWARE_FALLBACK_TO_SIMULATOR=false`.
- Appliance live mode initializes the selected Waveshare driver once, keeps the panel awake during the simulator session, and sleeps the display on shutdown.
- Physical e-paper writes are skipped when the rendered image is unchanged.
- Rapid sequential renders are coalesced with `EPD_RENDER_DEBOUNCE_MS`, default `750`.
- Volume changes update software/audio immediately, then physical e-paper refreshes the final settled value by default with `EPD_REFRESH_ON_VOLUME_CHANGE=true`.
- `EPD_VOLUME_REFRESH_DEBOUNCE_MS=600` waits briefly until knob movement settles, then pushes a bottom-bar partial refresh instead of flashing for every tick.
- Partial refresh is enabled by default, but only for same-layout changes after a clean screen is already established.
- Same-layout partial refreshes carry a named dirty region such as `clock`, `menu_list`, `sleep_timer_value`, `bottom_bar`, or `main_content`; the adapter logs `region_emulated=true` when the selected driver only accepts a full-buffer partial write.
- Menu highlight movement may use partial refresh only within the same menu. Opening the menu from HOME, returning HOME, entering Sleep Timer, changing to a source/track list, or changing any screen title/layout uses a full clean refresh.
- Volume on HOME, sleep timer changes while already on the Sleep Timer screen, alarm toggle while already on the Alarm screen, play/pause, clock minute updates on HOME, and playlist switches while already on the playback home layout are partial candidates.
- Full refresh is used for startup, source changes, playback start/stop, major layout transitions, screen mode/title changes, playlist completion, and periodic ghosting cleanup.
- `EPD_PARTIAL_STREAK_LIMIT=8` forces a clean full refresh after eight consecutive partial updates.
- The adapter tracks physical display mode explicitly: true full updates use `epd.init()` plus `display()`, while partial updates discover the installed driver's partial method (`display_Partial`, `display_part`, `DisplayPart`, or similar) and call the matching partial init method when available.
- Inspect the installed Waveshare driver methods with `python -m scripts.inspect_epd_driver --model waveshare_4in2_v2`; logs include `partial_supported`, `partial_api`, `init_part_called`, `selected_policy`, and the actual `physical_mode`.
- Any clean/full major transition switches out of partial mode before `Clear()` and `display()` so the panel does not keep using the fast partial LUT.
- `EPD_ONE_SHOT_MAJOR_TRANSITIONS=true` uses the known-good manual push lifecycle for major transitions: fresh `EPD()`, `init()`, open `latest_screen.png`, convert to 1-bit, resize to panel size, `display()`, then `sleep()`.
- One-shot major transitions cancel any pending debounced physical update before pushing the final rendered frame.
- `EPD_PARTIAL_REFRESH_MIN_INTERVAL_MS=500` prevents rapid partial refresh bursts.
- `EPD_MENU_NAVIGATION_UPDATE_MODE=full` is the current 4.2" default because menu/list partial refresh uses a full-buffer partial LUT and can muddy text. Set it to `partial` only for speed experiments, or `skip` to avoid physical updates while scrolling.
- Clock-driven e-paper refreshes happen at `EPD_CLOCK_REFRESH_SECONDS`, default `60`; set `EPD_DISABLE_CLOCK_AUTO_REFRESH=true` to disable automatic clock refreshes.
- `EPD_CLOCK_PARTIAL_UPDATE_ENABLED=false` keeps idle minute updates out of the 4.2" V2 panel's full-buffer partial LUT path. Set it to `true` only if you want to test faster clock updates and can tolerate ghosting.
- SPI must already be enabled and working.
- `GPIOZERO_PIN_FACTORY=lgpio` is required on the current Raspberry Pi OS setup.
- Set `EPD_REINIT_EVERY_UPDATE=true` only for hardware debugging if you need the old bring-up behavior.
- E-paper keeps the last image visible after sleep. This is normal and intentional.
- The simulator does not clear the display on exit by default. Set `CLEAR_EPD_ON_EXIT=true` if you want it cleared before sleep.
- `CLEAR_BEFORE_EPD_UPDATE=false` by default. Set it to `true` only when you intentionally want a `Clear()` before each forced display write.
- `EPD_FORCE_FULL_REFRESH=true` sends all changes through the full path for debugging.
- `EPD_FORCE_CLEAN_REFRESH=true` forces a clean refresh on every physical update and should be used sparingly.
- Set `EPD_ONE_SHOT_MAJOR_TRANSITIONS=false` only when returning to the persistent live-display lifecycle for major transitions.
- Set `EPD_DISABLE_PARTIAL=true` to avoid `init_Part()` and `display_Partial()` entirely if partial refresh artifacts appear during real use.
- Set `EPD_PARTIAL_UPDATE_ENABLED=false` to keep app policy from requesting partial updates.
- In appliance mode, hardware display init failures are not silently treated as success when `HARDWARE_FALLBACK_TO_SIMULATOR=false`.
- If `USE_REAL_EPD` is false or `DISPLAY_BACKEND=png`, the Mac/dev simulator remains PNG-only.

To manually clear the physical panel:

```bash
GPIOZERO_PIN_FACTORY=lgpio python -m scripts.clear_epd
```

To push the current `data/latest_screen.png` once through the same adapter path:

```bash
GPIOZERO_PIN_FACTORY=lgpio python -m scripts.push_latest_epd
GPIOZERO_PIN_FACTORY=lgpio python -m scripts.push_latest_epd --full
```

To render 4.2-inch preview screens locally:

```bash
python -m scripts.render_display_previews --model waveshare_4in2_v2
```

Ghosting notes:

- E-paper refreshes can leave temporary artifacts, especially after large black regions or repeated updates.
- `scripts.clear_epd` is the hard reset for the panel image.
- If `EPD_REINIT_EVERY_UPDATE=false` and you still see vertical bars during live testing, run `python -m scripts.clear_epd` or temporarily lower `EPD_FULL_CLEAR_INTERVAL`.
- The default periodic clean interval is `EPD_FULL_CLEAR_INTERVAL=50`, so normal menu use should not produce repeated full-panel clears.
- A clean full refresh is also forced after 8 partial refreshes or when the display policy switches from partial back to full.

## Logging And Diagnostics

Logs go to both the console and a rotating file:

```text
~/nightstand-audio/logs/nightstand.log
```

The file log keeps 5 rotated files at 1MB each. Every line includes a timestamp, level, and subsystem prefix such as `[DISPLAY]`, `[EPD]`, `[INPUT]`, `[STATE]`, `[PLAYBACK]`, `[AUDIO]`, or `[SIM]`.

Tail logs while the appliance is running:

```bash
scripts/tail_logs.sh
```

Capture a current diagnostic snapshot:

```bash
python -m scripts.log_snapshot
```

Logging environment variables:

```text
LOG_LEVEL=INFO
DEBUG_EPD=false
DEBUG_INPUT=false
DEBUG_AUDIO=false
SHOW_RENDER_TIMINGS=false
```

Useful debug runs:

```bash
LOG_LEVEL=DEBUG GPIOZERO_PIN_FACTORY=lgpio python -m scripts.run_live_epd
DEBUG_EPD=true SHOW_RENDER_TIMINGS=true GPIOZERO_PIN_FACTORY=lgpio python -m scripts.run_live_epd
DEBUG_INPUT=true python -m scripts.run_simulator
```

Startup logs include a banner with display type, resolution, GPIO backend, audio mode, and whether live EPD output is enabled. They also include `[STARTUP]` timing spans for `config_load`, `audio_device_detection`, `media_cache_load`, `background_media_scan_start`, `playback_service_init`, `display_driver_import`, `display_init`, `initial_render_png`, `first_physical_epd_update`, and final `total_ms`.

Troubleshooting:

- If the display does not update, check for `[EPD]` initialization failures and confirm `WAVESHARE_EPD_PYTHON_PATH`.
- If the Waveshare driver logs `GPIO busy` on GPIO17/RST or another display pin, run `python -m scripts.diagnose_gpio`; the app disables physical EPD writes for that run and continues PNG/input/playback so you can debug without a crash.
- If the display updates too often, confirm `EPD_REINIT_EVERY_UPDATE=false` and `EPD_RENDER_DEBOUNCE_MS=750`.
- If you see ghosting or vertical bars, run `python -m scripts.clear_epd`; use `EPD_REINIT_EVERY_UPDATE=true` only for hardware debugging.
- If keyboard controls feel wrong, enable `DEBUG_INPUT=true`.
- If startup logs `GPIO busy` for a control pin, another process or kernel consumer is holding that line. Run `python -m scripts.diagnose_gpio`; for manual checks use one `pinctrl get <gpio>` command per pin, `gpioinfo` or `gpioinfo /dev/gpiochip0`, and `fuser -v /dev/gpiochip*`. In `INPUT_BACKEND=gpio_keyboard` mode the app falls back to keyboard input if GPIO cannot be claimed.
- If Bluetooth/output routing is confusing, enable `DEBUG_AUDIO=true` and run `python -m scripts.log_snapshot`.
- If Bluetooth pairing logs `Powered: no`, `org.bluez.Error.NotReady`, or an rfkill permission error, run `sudo rfkill unblock bluetooth`, `sudo systemctl restart bluetooth`, then confirm `bluetoothctl show` reports `Powered: yes` before starting the app again.
- If UI latency is unclear, set `SHOW_RENDER_TIMINGS=true` to print render time, display push time, and total refresh latency.

## Audio Output Testing

The app keeps output routing abstract while the exact Pi audio stack is validated. For the InnoMaker PCM5122 DAC HAT, start with ALSA/PipeWire device discovery and test tones before wiring MPD into it.

Environment overrides:

```text
AUDIO_BACKEND=alsa
AUDIO_DEVICE=auto
PLAYBACK_BACKEND=mpv
# AUDIO_DEVICE=plughw:1,0
# AUDIO_DEVICE=hw:1,0
```

List devices and play a short tone:

```bash
python -m scripts.test_audio_output --list-only
python -m scripts.test_audio_output
python -m scripts.test_audio_output --device plughw:1,0
python -m scripts.test_audio_output --device hw:1,0
```

For the tested InnoMaker HiFi DAC HAT, use this `/boot/firmware/config.txt` audio setup:

```text
dtparam=i2s=on
dtoverlay=allo-boss-dac-pcm512x-audio
#dtparam=audio=on
```

The script uses `aplay` when `AUDIO_BACKEND=alsa`, prints `aplay -l` and `aplay -L`, auto-detects the InnoMaker/PCM512x card when it appears as `BossDAC` or `snd_rpi_hifiberry_dacplus`, and plays a short stereo generated WAV through the selected ALSA device. `AUDIO_DEVICE=auto` prefers `plughw:1,0` for compatibility when the working `BossDAC` card is detected. The hifiberry overlay may enumerate this board but has produced I2S SYNC errors, so the Boss DAC overlay is the current known-good path. This does not affect the main simulator or MPD adapter, and explicit `AUDIO_DEVICE=...` overrides still win.

Appliance playback uses `MPVPlayer`, not the simulator player. The production launch path logs `[PLAYBACK] backend=mpv`, `[PLAYBACK] device=<alsa device>`, `[PLAYBACK] file=<resolved path>`, and `[PLAYBACK] command=<full command>`. Test a single file through the same MPV adapter:

```bash
python -m scripts.play_file_test "media/buttons/button-1/example.mp3"
```

Startup restore is metadata-only. The appliance may restore the last source, title, track index, and position for the UI, but it must not launch `mpv`, open ALSA, or create an MPV IPC socket until a real user action happens. Keep these defaults unless intentionally debugging restore behavior:

```text
RESTORE_PLAYBACK_ON_STARTUP=true
RESUME_ON_STARTUP=false
PLAYBACK_RESTORE_LAUNCH=false
VALIDATE_PLAYLIST_ON_PLAY=false
BACKGROUND_MEDIA_SCAN=false
```

The expected startup log is `[PLAYBACK] restored_state ... launch=false`. After startup, `ps aux | grep mpv` should show no Nightstand-launched player process.

Source button presses use the cached playlist index and resolve only the selected/current track before launching MPV. Leave `VALIDATE_PLAYLIST_ON_PLAY=false` for appliance use so Button 1/2/3 do not synchronously stat or resolve every file in a long playlist. Full playlist validation belongs in `python -m scripts.rebuild_media_index` or the background media scan after playback is already stable.

For isolated audio debugging on the Pi, set `BACKGROUND_MEDIA_SCAN=false`. That keeps the startup background scanner from running before playback tests. Even when enabled, the app skips starting a background scan while audio is active and asks any running background scan to cancel before MPV launch.

Physical Waveshare refreshes are deferred briefly when audio first transitions from stopped/paused to playing. This protects I2S startup on the Pi Zero 2 W while still rendering `data/latest_screen.png` immediately:

```text
AUDIO_START_DISPLAY_GRACE_MS=5000
EPD_SUPPRESS_WHILE_AUDIO_PLAYING=false
```

Set `AUDIO_START_DISPLAY_GRACE_MS=0` to disable the grace period. When active, logs show `[DISPLAY] Physical update deferred during audio startup grace period remaining_ms=...` followed by `[DISPLAY] Audio startup grace period expired; applying deferred display update`.

The current stable Pi command sets `EPD_SUPPRESS_WHILE_AUDIO_PLAYING=false` because the GPIO conflict is fixed. Keep `AUDIO_START_DISPLAY_GRACE_MS=5000`, avoid second-by-second physical progress refreshes, and use the suppression flag only if hardware testing shows I2S contention again.

Sleep timer shutdown is a dedicated transition, not a natural EOF. When sleep triggers, the app saves the current track position immediately, fades audio down, stops MPV after the fade, and keeps the saved session marked as paused due to sleep:

```text
SLEEP_FADE_SECONDS=30
SLEEP_FADE_STEPS=20
```

The saved resume position is the start of the fade, so pressing the same source button later resumes intentionally from that point instead of advancing because the fade ran for several more seconds.

Gentle wake alarms treat the configured alarm time as the target wake time. By default, wake staging starts 30 minutes before the target and moves through four quiet stages before the final alarm:

```text
wake_enabled=true
wake_lead_minutes=30
wake_stages=4
stage_volume_curve=[5, 10, 20, 35]
stage_source=sounds
```

Wake stages use the dedicated alarm speaker path, keep display updates to stage transitions, and avoid interrupting active user playback unless `interrupt_active_playback` is enabled in the alarm profile. The final alarm stage fades to the configured target volume. Knob press stops and returns to ambient, a source button snoozes, and long press dismisses the alarm for the day.

Wake/alarm audio is isolated from normal playback. In appliance mode, `ALARM_AUDIO_DEVICE=auto_usb` detects a separate USB audio card and routes wake/alarm output to that USB DAC headphone jack, which feeds the internal speaker. Alarm audio should never intentionally use Bluetooth or the BossDAC normal listening path.

```text
AUDIO_DEVICE=auto
ALARM_AUDIO_DEVICE=auto_usb
```

If the USB DAC is not detected, wake/alarm playback is disabled and logged instead of falling back to the BossDAC or Bluetooth. Use an explicit override such as `ALARM_AUDIO_DEVICE=plughw:2,0` only after confirming the USB DAC card number with `aplay -l`.

Ambient mode is alarm-aware. If an enabled alarm’s wake sequence starts earlier than the standard morning ambient boundary, the display wakes at the wake sequence start. For example, a 4:00 AM alarm with a 30 minute wake lead starts the ambient/gentle wake display at 3:30 AM.

GPIO root cause note: Waveshare stock `epdconfig.py` used `PWR_PIN=18`, which conflicts with BossDAC `GPIO18 = PCM_CLK` and caused `bcm2835-i2s 3f203000.i2s: I2S SYNC error!`. Use `PWR_PIN=5` or another safe non-I2S GPIO. Never assign Waveshare control pins, rotary pins, button pins, or speaker-control pins to GPIO18, GPIO19, GPIO20, or GPIO21.

When BossDAC is detected and real EPD is enabled, the app checks the app-owned pin map plus Waveshare `epdconfig.py` and refuses physical display/input startup if `PWR_PIN`, `RST_PIN`, `DC_PIN`, `CS_PIN`, `BUSY_PIN`, rotary, button, or speaker-control config uses GPIO18/19/20/21. The only override is explicit bench-mode `ALLOW_UNSAFE_GPIO=true`.

Rebuild the portable media cache after changing files:

```bash
python -m scripts.rebuild_media_index
```

## Simulator Controls

The rotary encoder is the primary navigation control:

- `Up` / `Down`: turn knob
- `Enter`: press knob
- `Backspace`: long-press knob

Home mode:

- knob turn changes volume
- knob single press toggles play/pause
- knob double press skips to the next track
- knob triple press restarts the current track, or goes to the previous track if the current position is at 5 seconds or less
- knob long-press opens the main menu

Menu mode:

- knob turn moves the highlight
- knob press selects the highlighted item
- knob long-press returns home
- menus auto-timeout back to home after about 15 seconds

Night Mode / Sleep Screen:

- Default hours are `NIGHT_MODE_START=22:00` through `NIGHT_MODE_END=06:00`, using the system timezone.
- During Night Mode the display locks to a minimal Sleep Screen with the large clock and optional sleep timer status.
- Preset buttons, same-button pause/resume, volume turns, Bluetooth media commands, double-click next, triple-click previous/restart, and sleep timer behavior still work without waking or changing the display.
- A single knob press wakes the normal UI. If there is no activity for `NIGHT_MODE_WAKE_TIMEOUT_SECONDS`, default `30`, the display returns to the Sleep Screen.
- Set `NIGHT_MODE_DISPLAY_LOCK=false` to keep Night Mode detection enabled without locking the display.

Ambient / Active Mode:

- Daytime passive state is Ambient Mode when `AMBIENT_MODE_ENABLED=true`.
- Ambient shows a calm clock with day/date, no menus, and no playback metadata. It refreshes at minute-level cadence with `AMBIENT_CLOCK_REFRESH_SECONDS=60`.
- A single knob press from Ambient enters Active Mode without starting playback.
- Pressing `1`, `2`, or `3` during daytime enters Active Mode and starts that folder playlist.
- Active Mode keeps the normal playback/menu UI visible while playback is playing.
- If no playback is playing, Active Mode returns to Ambient after `ACTIVE_MODE_TIMEOUT_SECONDS=30`.
- At night, the same timeout returns Active Mode to the locked Sleep Screen instead of Ambient.

Quick controls:

- `1`: Button 1 folder
- `2`: Button 2 folder
- `3`: Button 3 folder
- `Space`: play/pause
- `t`: cycle sleep timer
- `a`: toggle alarm
- `[`: alarm time minus 5 minutes
- `]`: alarm time plus 5 minutes
- `s`: snooze alarm
- `x`: stop alarm
- `p`: simulate Bluetooth play/pause media command
- `n`: simulate Bluetooth next-track media command
- `b`: simulate Bluetooth previous/restart media command
- `y`: fake Bluetooth reconnect success in the simulator
- `u`: fake Bluetooth reconnect failure in the simulator
- `r`: render once
- `q`: quit

Preset buttons work from any state. If a menu is open, pressing `1`, `2`, or `3` immediately starts that source and returns to the home screen.
Triple-clicking any source button starts the Bluetooth reconnect workflow. This is device-level behavior, not tied to a specific source.

Each preset button is a persistent playlist stream:

- `1` resumes the Button 1 playlist from its saved track and position.
- `2` resumes the Button 2 playlist from its saved track and position.
- `3` resumes the Button 3 playlist from its saved track and position.

Playback continues track-to-track until paused, the same source button is pressed again, another source is selected, the sleep timer fades out and stops playback, or the playlist ends. Podcast-style folders stop when fully completed and do not loop automatically. Sleep sounds loop forever.

## Folder-Based Button Sources

The three physical buttons are folder slots. To change what a button plays, change the files in its folder.

```text
media/
  buttons/
    button-1/
    button-2/
    button-3/
  sounds/
```

Button mapping is fixed:

- `1` / Button 1 plays `media/buttons/button-1`
- `2` / Button 2 plays `media/buttons/button-2`
- `3` / Button 3 plays `media/buttons/button-3`

The app does not assign content to buttons through a settings menu. Labels and playback policy can come from an optional `.source.json` file in the folder:

```json
{
  "display_name": "Bible in a Year",
  "source_type": "podcast",
  "ordering": "filename_asc",
  "resume_policy": "resume_playlist",
  "completion_threshold_percent": 95,
  "end_behavior": "stop"
}
```

If `.source.json` is missing, the app falls back to the folder name, filename ordering, playlist resume, and stop-at-end behavior. Ambient folders can opt into looping with `"loop_enabled": true` or `"end_behavior": "loop"`.

Display labels are cleaned at scan time while filenames stay unchanged on disk. The scanner URL-decodes filenames and audio tag strings, reads MP3/M4A-style `title`, `album`, and `artist` metadata when available, and strips repeated podcast branding from episode titles. For example, `Ep%20040%20-%20Northwoods%20Baseball%20Sleep%20Radio%20-%20Lake%20City%20Loons%20vs.%20South%20Haven%20Ravens` displays as `Sleep Baseball`, `Lake City Loons vs South Haven Ravens`, and `Ep 040`.

Podcast completion behavior:

- Podcast sources stop cleanly when all episodes are completed.
- Completed podcast sources do not auto-restart and do not loop.
- The home screen shows `Completed` plus the listened count, such as `365 / 365 listened`.
- Restarting requires intentional user action from the source track menu via `Restart Playlist`.
- Restart clears completion state, resets playback positions, and starts again from the first episode.

Music and sleep behavior:

- Music folders continue track-to-track and stop at the end for now.
- Music folders do not show permanent source completion.
- `media/sounds` loops forever for sleep sound use.

## Main Menu

```text
Home
  Resume Last
  Button 1 -> track list
  Button 2 -> track list
  Button 3 -> track list
  Sleep Sounds -> sound list
  Sleep Timer
  Alarm
  Output
```

The menu is deliberately shallow. The device should work like an old iPod, car stereo, or audio receiver: turn, press, long-press.

Selecting a source from the menu opens a simple track list. Selecting a track moves that source's saved cursor to the chosen item, starts playback there, and continues sequentially afterward.

There is no touchscreen requirement. The real device maps the same interaction model to one rotary encoder and three physical preset buttons, so the bedside workflow stays tactile and predictable.

Supported extensions:

```text
.mp3 .m4a .aac .flac .wav
```

Button folders and sounds are stored as persistent queues. The app remembers the current source, current track, track index, position, play/pause state, and queue order in SQLite. Files default to filename/path order, so podcast dates or album folders can be arranged naturally on disk.

The media index cache lives at `data/media_index.json` and stores file paths relative to `media/`, not absolute Mac or Pi paths. On appliance startup the app loads this cache quickly, renders Ambient/Night mode, then refreshes the full media scan in the background. Pressing a source button can also lazily scan just that button folder if its queue is empty or contains stale paths. Cache entries with absolute paths, `/Users/...` paths, or missing resolved files are invalidated.

Playback persistence includes:

- `current source_id`
- `current track_id`
- `current track_index`
- `last_position_seconds`
- `queue ordering`
- `is_playing`

For compatibility while developing, the scanner also reads the old folders as fallback sources when the matching button folder is empty:

- `media/podcasts/bible-in-a-year` -> `media/buttons/button-1`
- `media/podcasts/sleep-baseball` -> `media/buttons/button-2`
- `media/music/night-albums` -> `media/buttons/button-3`

## Raspberry Pi Deployment Direction

The app is structured so hardware behavior stays behind adapters:

- `PlaybackAdapter`: mock player for Mac/dev simulator, MPV player for current Raspberry Pi appliance audio, MPD adapter stubbed for later.
- `DisplayAdapter`: PNG simulator now, optional live Waveshare e-paper output on Raspberry Pi.
- `InputAdapter`: keyboard for Mac/dev simulator, GPIO rotary/buttons plus temporary keyboard fallback for `scripts.run_pi_appliance`.

Expected Pi path:

1. Install Raspberry Pi OS Bookworm.
2. Configure the InnoMaker DAC Mini HAT for wired headphones and the USB sound card for speaker/alarm output.
3. Confirm PipeWire can see and switch between InnoMaker DAC, USB sound card, and Bluetooth sinks.
4. Use `MPVPlayer` with `--audio-device=alsa/plughw:1,0` for the confirmed BossDAC path.
5. Enable live Waveshare output with `USE_REAL_EPD=true` while keeping PNG output for debugging.
6. Replace `KeyboardInput` with a GPIO adapter for the rotary encoder and buttons.
7. Package as a systemd service.

Adapter TODO contracts:

- GPIO rotary encoder: turn emits `InputEvent("turn", +/-1)`, short press emits `press`, long press emits `long_press`.
- GPIO buttons: preset buttons emit `InputEvent("source", source_id)`, and long-press Button 3 emits `sleep_timer`.
- Waveshare e-paper: `SimulatorDisplay` renders the selected model's 1-bit image to PNG and forwards it to `WaveshareDisplay` when `USE_REAL_EPD=true`.
- MPV playback: current appliance backend; launch local files with `mpv --no-video --no-audio-display --audio-device=alsa/<AUDIO_DEVICE>`.
- MPD playback: later backend; match the `PlaybackAdapter` API used by `MockPlayer` and `MPVPlayer`.
- InnoMaker DAC audio: document and validate the exact Bookworm overlay/config separately from app logic.
- USB speaker output: route alarm/fallback audio to the USB sound card -> MonkMakes speaker sink by default.
- Bluetooth output: add as a later output target behind playback/output selection.
- Bluetooth reconnect: `BluetoothManager` owns trusted-device tracking, reconnect state, reconnect timeout, output sink switching, and future `bluetoothctl` integration.

Detailed hardware bring-up notes live in [docs/pi-bringup.md](docs/pi-bringup.md), including GPIO tables, wiring diagrams, first boot steps, Waveshare setup, DAC setup, USB sound-card setup, PipeWire setup, Bluetooth pairing, debugging commands, and known pin usage.

## Project Structure

```text
app/
  main.py
  config.py
  models.py
  state_store.py
  media_library.py
  media_metadata.py
  playback/
  display/
  input/
  services/
docs/
  pi-bringup.md
scripts/
  clear_epd.py
  download_bible_in_year.py
  import_gpodder_to_button2.py
  log_snapshot.py
  push_latest_epd.py
  render_display_previews.py
  seed_library.py
  render_once.py
  run_live_epd.py
  run_simulator.py
  tail_logs.sh
  test_audio_output.py
  watch_screen.py
tests/
  test_*.py
```

## Verification

```bash
python -m unittest
python -m scripts.seed_library
python -m scripts.render_once
NIGHTSTAND_SIM_STEPS=2 python -m scripts.run_simulator
```

## Next Steps

- Implement real MPD commands in `app/playback/mpd_player.py`.
- Implement GPIO rotary encoder and button handling in `app/input/gpio_input_stub.py`.
- Validate live Waveshare refresh on the Pi with `GPIOZERO_PIN_FACTORY=lgpio python -m scripts.run_live_epd`.
- Add Bluetooth output selection once the local playback path is solid.
