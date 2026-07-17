#!/usr/bin/env python3
"""Simple GNU Radio VHF AM receiver for RTL/airnav-class USB SDR sticks.

Features:
- Live controls for frequency, squelch, AGC enable, carrier gain, and bandwidth
- JSON-based configuration with user presets
"""

import json
import os
import signal
import sys
from pathlib import Path

from gnuradio import analog
from gnuradio import audio
from gnuradio import filter
from gnuradio import gr
from gnuradio.filter import firdes
from gnuradio.qtgui import Range
from gnuradio.qtgui import RangeWidget
from PyQt5 import QtCore, QtWidgets
import osmosdr


DEFAULT_CONFIG = {
    "device_args": "rtl=0",
    "sample_rate": 240000,
    "audio_rate": 48000,
    "defaults": {
        "frequency_mhz": 121.5,
        "squelch_db": -55.0,
        "agc_enabled": True,
        "carrier_gain_db": 20.0,
        "bandwidth_hz": 9000.0,
    },
    "presets": {
        "Guard": {
            "frequency_mhz": 121.5,
            "squelch_db": -55.0,
            "agc_enabled": True,
            "carrier_gain_db": 20.0,
            "bandwidth_hz": 9000.0,
        }
    },
}


class VhfAmReceiver(gr.top_block, QtWidgets.QWidget):
    def __init__(self, config_path: Path):
        gr.top_block.__init__(self, "VHF AM Receiver")
        QtWidgets.QWidget.__init__(self)

        self.setWindowTitle("VHF AM Receiver")
        self._config_path = config_path
        self._config = self._load_or_create_config(config_path)

        self.sample_rate = float(self._config.get("sample_rate", 240000))
        self.audio_rate = float(self._config.get("audio_rate", 48000))
        self.audio_decim = int(self.sample_rate / self.audio_rate)
        if self.audio_decim <= 0:
            raise ValueError("audio_decim must be > 0. Check sample_rate/audio_rate in config")

        defaults = self._config.get("defaults", {})
        self.frequency_mhz = float(defaults.get("frequency_mhz", 121.5))
        self.squelch_db = float(defaults.get("squelch_db", -55.0))
        self.agc_enabled = bool(defaults.get("agc_enabled", True))
        self.carrier_gain_db = float(defaults.get("carrier_gain_db", 20.0))
        self.bandwidth_hz = float(defaults.get("bandwidth_hz", 9000.0))

        self._build_ui()
        self._build_flowgraph()
        self._apply_runtime_settings()
        self._refresh_preset_list()

    def _build_ui(self):
        outer = QtWidgets.QVBoxLayout(self)
        controls = QtWidgets.QGroupBox("Receiver Controls")
        controls_layout = QtWidgets.QVBoxLayout(controls)

        self.freq_range = Range(118.0, 137.0, 0.005, self.frequency_mhz, 2)
        self.freq_widget = RangeWidget(
            self.freq_range,
            self.set_frequency_mhz,
            "Frequency (MHz)",
            "counter_slider",
            float,
        )
        controls_layout.addWidget(self.freq_widget)

        self.squelch_range = Range(-110.0, 0.0, 1.0, self.squelch_db, 2)
        self.squelch_widget = RangeWidget(
            self.squelch_range,
            self.set_squelch_db,
            "Squelch (dB)",
            "counter_slider",
            float,
        )
        controls_layout.addWidget(self.squelch_widget)

        self.gain_range = Range(0.0, 50.0, 1.0, self.carrier_gain_db, 2)
        self.gain_widget = RangeWidget(
            self.gain_range,
            self.set_carrier_gain_db,
            "Carrier Gain (dB)",
            "counter_slider",
            float,
        )
        controls_layout.addWidget(self.gain_widget)

        self.bw_range = Range(5000.0, 20000.0, 500.0, self.bandwidth_hz, 2)
        self.bw_widget = RangeWidget(
            self.bw_range,
            self.set_bandwidth_hz,
            "Bandwidth (Hz)",
            "counter_slider",
            float,
        )
        controls_layout.addWidget(self.bw_widget)

        agc_row = QtWidgets.QHBoxLayout()
        self.agc_check = QtWidgets.QCheckBox("Enable AGC")
        self.agc_check.setChecked(self.agc_enabled)
        self.agc_check.toggled.connect(self.set_agc_enabled)
        agc_row.addWidget(self.agc_check)
        agc_row.addStretch(1)
        controls_layout.addLayout(agc_row)

        presets_group = QtWidgets.QGroupBox("Presets")
        presets_layout = QtWidgets.QVBoxLayout(presets_group)

        row1 = QtWidgets.QHBoxLayout()
        self.preset_combo = QtWidgets.QComboBox()
        row1.addWidget(self.preset_combo)

        self.load_btn = QtWidgets.QPushButton("Load")
        self.load_btn.clicked.connect(self.load_selected_preset)
        row1.addWidget(self.load_btn)

        self.delete_btn = QtWidgets.QPushButton("Delete")
        self.delete_btn.clicked.connect(self.delete_selected_preset)
        row1.addWidget(self.delete_btn)
        presets_layout.addLayout(row1)

        row2 = QtWidgets.QHBoxLayout()
        self.preset_name = QtWidgets.QLineEdit()
        self.preset_name.setPlaceholderText("Preset name")
        row2.addWidget(self.preset_name)

        self.save_btn = QtWidgets.QPushButton("Save Current")
        self.save_btn.clicked.connect(self.save_current_preset)
        row2.addWidget(self.save_btn)
        presets_layout.addLayout(row2)

        outer.addWidget(controls)
        outer.addWidget(presets_group)

    def _build_flowgraph(self):
        device_args = str(self._config.get("device_args", "rtl=0"))

        try:
            self.src = osmosdr.source(args=f"numchan=1 {device_args}")
        except RuntimeError as exc:
            raise RuntimeError(
                "Failed to open RTL-SDR device. Ensure no other SDR app is using it, "
                "and blacklist DVB kernel drivers on Raspberry Pi if needed."
            ) from exc
        self.src.set_sample_rate(self.sample_rate)
        self.src.set_freq_corr(0, 0)
        self.src.set_dc_offset_mode(0, 0)
        self.src.set_iq_balance_mode(0, 0)
        self.src.set_antenna("", 0)

        self.squelch = analog.simple_squelch_cc(self.squelch_db, 0.1)

        self.rf_lpf = filter.fir_filter_ccf(1, self._make_rf_lpf_taps(self.bandwidth_hz))

        self.am_demod = analog.am_demod_cf(
            channel_rate=self.sample_rate,
            audio_decim=self.audio_decim,
            audio_pass=5000,
            audio_stop=7000,
        )

        self.audio_sink = audio.sink(int(self.audio_rate), "", True)

        self.connect((self.src, 0), (self.squelch, 0))
        self.connect((self.squelch, 0), (self.rf_lpf, 0))
        self.connect((self.rf_lpf, 0), (self.am_demod, 0))
        self.connect((self.am_demod, 0), (self.audio_sink, 0))

    def _apply_runtime_settings(self):
        self.src.set_center_freq(self.frequency_mhz * 1e6, 0)
        self.src.set_gain_mode(self.agc_enabled, 0)
        self.src.set_gain(self.carrier_gain_db, 0)
        self.squelch.set_threshold(self.squelch_db)

        if hasattr(self.src, "set_bandwidth"):
            self.src.set_bandwidth(self.bandwidth_hz, 0)

    def _make_rf_lpf_taps(self, bandwidth_hz: float):
        cutoff = max(2000.0, bandwidth_hz * 0.55)
        transition = max(1000.0, bandwidth_hz * 0.2)
        return firdes.low_pass(
            1.0,
            self.sample_rate,
            cutoff,
            transition,
            firdes.WIN_HAMMING,
            6.76,
        )

    def set_frequency_mhz(self, value):
        self.frequency_mhz = float(value)
        self.src.set_center_freq(self.frequency_mhz * 1e6, 0)

    def set_squelch_db(self, value):
        self.squelch_db = float(value)
        self.squelch.set_threshold(self.squelch_db)

    def set_agc_enabled(self, enabled):
        self.agc_enabled = bool(enabled)
        self.src.set_gain_mode(self.agc_enabled, 0)

    def set_carrier_gain_db(self, value):
        self.carrier_gain_db = float(value)
        self.src.set_gain(self.carrier_gain_db, 0)

    def set_bandwidth_hz(self, value):
        self.bandwidth_hz = float(value)
        self.rf_lpf.set_taps(self._make_rf_lpf_taps(self.bandwidth_hz))
        if hasattr(self.src, "set_bandwidth"):
            self.src.set_bandwidth(self.bandwidth_hz, 0)

    def _load_or_create_config(self, path: Path):
        if path.exists():
            with path.open("r", encoding="utf-8") as fh:
                return json.load(fh)

        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as fh:
            json.dump(DEFAULT_CONFIG, fh, indent=2)
        return json.loads(json.dumps(DEFAULT_CONFIG))

    def _save_config(self):
        self._config["sample_rate"] = self.sample_rate
        self._config["audio_rate"] = self.audio_rate
        self._config.setdefault("defaults", {})
        self._config["defaults"].update(self.current_state())

        self._config_path.parent.mkdir(parents=True, exist_ok=True)
        with self._config_path.open("w", encoding="utf-8") as fh:
            json.dump(self._config, fh, indent=2)

    def current_state(self):
        return {
            "frequency_mhz": round(self.frequency_mhz, 6),
            "squelch_db": round(self.squelch_db, 2),
            "agc_enabled": bool(self.agc_enabled),
            "carrier_gain_db": round(self.carrier_gain_db, 2),
            "bandwidth_hz": round(self.bandwidth_hz, 2),
        }

    def _refresh_preset_list(self):
        current = self.preset_combo.currentText()
        self.preset_combo.blockSignals(True)
        self.preset_combo.clear()

        presets = self._config.get("presets", {})
        for name in sorted(presets.keys()):
            self.preset_combo.addItem(name)

        idx = self.preset_combo.findText(current)
        if idx >= 0:
            self.preset_combo.setCurrentIndex(idx)
        elif self.preset_combo.count() > 0:
            self.preset_combo.setCurrentIndex(0)
        self.preset_combo.blockSignals(False)

    def save_current_preset(self):
        name = self.preset_name.text().strip()
        if not name:
            name = self.preset_combo.currentText().strip()
        if not name:
            return

        self._config.setdefault("presets", {})
        self._config["presets"][name] = self.current_state()
        self._config.setdefault("defaults", {})
        self._config["defaults"].update(self.current_state())

        self._save_config()
        self._refresh_preset_list()

        idx = self.preset_combo.findText(name)
        if idx >= 0:
            self.preset_combo.setCurrentIndex(idx)
        self.preset_name.clear()

    def load_selected_preset(self):
        name = self.preset_combo.currentText().strip()
        if not name:
            return

        preset = self._config.get("presets", {}).get(name)
        if not preset:
            return

        self.set_frequency_mhz(preset.get("frequency_mhz", self.frequency_mhz))
        self.set_squelch_db(preset.get("squelch_db", self.squelch_db))
        self.set_agc_enabled(preset.get("agc_enabled", self.agc_enabled))
        self.set_carrier_gain_db(preset.get("carrier_gain_db", self.carrier_gain_db))
        self.set_bandwidth_hz(preset.get("bandwidth_hz", self.bandwidth_hz))

        # Keep UI controls in sync when values are loaded programmatically.
        self.freq_widget.setValue(self.frequency_mhz)
        self.squelch_widget.setValue(self.squelch_db)
        self.agc_check.setChecked(self.agc_enabled)
        self.gain_widget.setValue(self.carrier_gain_db)
        self.bw_widget.setValue(self.bandwidth_hz)

        self._config.setdefault("defaults", {})
        self._config["defaults"].update(self.current_state())
        self._save_config()

    def delete_selected_preset(self):
        name = self.preset_combo.currentText().strip()
        if not name:
            return

        presets = self._config.get("presets", {})
        if name in presets:
            del presets[name]
            self._save_config()
            self._refresh_preset_list()

    def closeEvent(self, event):
        self._save_config()
        self.stop()
        self.wait()
        event.accept()


def main():
    default_cfg = Path(__file__).resolve().parent / "vhf_am_config.json"
    config_path = Path(os.environ.get("VHF_AM_CONFIG", str(default_cfg))).expanduser()

    app = QtWidgets.QApplication(sys.argv)
    tb = VhfAmReceiver(config_path)
    tb.start()
    tb.show()

    def _shutdown(*_args):
        tb.close()
        QtWidgets.QApplication.quit()

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    timer = QtCore.QTimer()
    timer.start(250)
    timer.timeout.connect(lambda: None)

    app.exec_()


if __name__ == "__main__":
    main()
