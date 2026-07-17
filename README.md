# VHF_Dashboard

GNU Radio VHF AM receiver setup for USB SDR sticks (including AirNav/RTL-class devices).

## Files

- `vhf_am_receiver.py`: PyQt + GNU Radio receiver with live controls
- `vhf_am_config.json`: JSON config with defaults and named presets

## Features

- Tune VHF air band frequency (118-137 MHz)
- Adjust squelch
- Toggle AGC on/off
- Set carrier gain
- Set receiver bandwidth
- Save/load/delete presets in JSON

## Requirements

Install GNU Radio, osmosdr, and PyQt5 from your distro packages.

Example on Ubuntu:

```bash
sudo apt update
sudo apt install -y gnuradio gr-osmosdr python3-pyqt5
```

## Run

```bash
python3 vhf_am_receiver.py
```

By default the script reads/writes `vhf_am_config.json` in this repository.

You can override config location:

```bash
VHF_AM_CONFIG=/path/to/config.json python3 vhf_am_receiver.py
```

## SDR Device Notes

- Default `device_args` is `rtl=0`.
- If your AirNav stick needs a different driver string, edit `device_args` in `vhf_am_config.json`.
- AGC toggle controls source gain mode and internal signal path selection.

## Presets

Use the Presets panel:

1. Set controls to desired values.
2. Enter preset name.
3. Click **Save Current**.
4. Select and click **Load** when needed.

Presets are stored in the `presets` section in `vhf_am_config.json`.