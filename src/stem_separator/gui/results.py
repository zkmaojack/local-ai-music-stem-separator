import sounddevice as sd
from PySide6.QtCore import QSignalBlocker, Qt, QTimer, Signal
from PySide6.QtWidgets import QFrame, QGridLayout, QHBoxLayout, QLabel, QScrollArea, QVBoxLayout, QWidget

from stem_separator.application.instruments import LABELS, STANDARD
from stem_separator.audio.playback import Preview, StemPlayer
from stem_separator.gui.controls import ChannelSlider, Timeline, Waveform, time_text
from stem_separator.gui.hardware import HardwareButton
from stem_separator.gui.theme import technical_font

SEEK_STEPS = 10000


class Results(QWidget):
    audio_error = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self.player: StemPlayer | None = None
        self.waveforms: list[Waveform] = []
        self.mutes: list[HardwareButton] = []
        self.solos: list[HardwareButton] = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        self.display = QScrollArea(objectName="display", widgetResizable=True)
        self.display.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        layout.addWidget(self.display, 1)
        self._build_tracks(STANDARD)

        deck = QFrame(objectName="deck")
        transport = QHBoxLayout(deck)
        transport.setContentsMargins(14, 8, 16, 8)
        transport.setSpacing(13)
        self.play_key = HardwareButton("", glyph="play", primary=True, width=58)
        self.play_key.setFixedSize(58, 55)
        self.play_key.clicked.connect(self.toggle_play)
        self.clock = QLabel("0:00 / 0:00", objectName="time")
        self.clock.setFont(technical_font(16))
        self.clock.setMinimumWidth(125)
        self.scrub = ChannelSlider("播放进度")
        self.scrub.setRange(0, SEEK_STEPS)
        self.scrub.setTracking(False)
        self.scrub.valueChanged.connect(lambda value: self.seek(value / SEEK_STEPS))
        self.volume = ChannelSlider("总音量")
        self.volume.setRange(0, 100)
        self.volume.setValue(80)
        self.volume.setFixedWidth(98)
        self.volume.valueChanged.connect(self._gains)
        transport.addWidget(self.play_key)
        transport.addWidget(self.clock)
        transport.addWidget(self.scrub, 1)
        transport.addWidget(QLabel("音量"))
        transport.addWidget(self.volume)
        layout.addWidget(deck)

        self.timer = QTimer(self, interval=33)
        self.timer.timeout.connect(self._refresh)

    def _build_tracks(self, tracks: tuple[str, ...]) -> None:
        panel = QWidget(objectName="trackPanel")
        grid = QGridLayout(panel)
        self.waveforms.clear()
        self.mutes.clear()
        self.solos.clear()
        grid.setContentsMargins(18, 8, 18, 10)
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(0)
        grid.setColumnMinimumWidth(0, 104)
        grid.setColumnStretch(1, 1)
        self.timeline = Timeline()
        self.timeline.seek.connect(self.seek)
        grid.addWidget(self.timeline, 0, 1)

        for index, track in enumerate(tracks):
            row = index * 2 + 1
            label = LABELS[track]
            name = QLabel(label, objectName="trackName")
            wave = Waveform()
            wave.seek.connect(self.seek)
            mute = HardwareButton("静音", width=58, compact=True)
            solo = HardwareButton("独听", width=58, compact=True)
            for control, description in ((mute, "静音"), (solo, "独听")):
                control.setCheckable(True)
                control.setAccessibleName(f"{label}：{description}")
            mute.toggled.connect(lambda checked, i=index: self._mute(i, checked))
            solo.toggled.connect(lambda checked, i=index: self._solo(i, checked))
            grid.addWidget(name, row, 0)
            grid.addWidget(wave, row, 1)
            grid.addWidget(mute, row, 2, Qt.AlignmentFlag.AlignVCenter)
            grid.addWidget(solo, row, 3, Qt.AlignmentFlag.AlignVCenter)
            grid.setRowStretch(row, 1)
            if index + 1 < len(tracks):
                rule = QFrame(objectName="rule")
                rule.setFixedHeight(1)
                grid.addWidget(rule, row + 1, 0, 1, 4)
            self.waveforms.append(wave)
            self.mutes.append(mute)
            self.solos.append(solo)
        self.display.setWidget(panel)
        self.display.verticalScrollBar().setValue(0)

    def set_preview(self, preview: Preview, tracks: tuple[str, ...] = STANDARD) -> None:
        self.clear()
        self._build_tracks(tracks)
        self.player = StemPlayer(preview)
        self.timeline.duration = preview.duration
        for waveform, values in zip(self.waveforms, preview.waveforms, strict=True):
            waveform.set_samples(values)
        for control in [*self.mutes, *self.solos]:
            control.setChecked(False)
        self._gains()
        self.timeline.update()
        self._refresh()
        self.timer.start()

    def clear(self) -> None:
        self.timer.stop()
        if self.player:
            self.player.close()
            self.player.preview.close()
            self.player = None
        for waveform in self.waveforms:
            waveform.set_samples(())

    def toggle_play(self) -> None:
        if self.player:
            try:
                if self.player.playing:
                    self.player.pause()
                else:
                    self.player.play()
            except sd.PortAudioError as error:
                self._playback_error(error)
            self._refresh()

    def seek(self, fraction: float) -> None:
        if self.player:
            try:
                self.player.seek(round(fraction * self.player.preview.frames))
            except sd.PortAudioError as error:
                self._playback_error(error)
            self._refresh()

    def _playback_error(self, error: sd.PortAudioError) -> None:
        if self.player:
            self.player.close()
        self.audio_error.emit(str(error))

    def _mute(self, index: int, checked: bool) -> None:
        if checked:
            self.solos[index].setChecked(False)
        self._gains()

    def _solo(self, index: int, checked: bool) -> None:
        if checked:
            self.mutes[index].setChecked(False)
            for other, control in enumerate(self.solos):
                if other != index:
                    control.setChecked(False)
        self._gains()

    def _gains(self) -> None:
        if not self.player:
            return
        solo = any(control.isChecked() for control in self.solos)
        audible = [not mute.isChecked() and (not solo or select.isChecked())
                   for mute, select in zip(self.mutes, self.solos, strict=True)]
        self.player.gains = tuple(self.volume.value() / 100 if active else 0.0 for active in audible)
        for waveform, active, select in zip(self.waveforms, audible, self.solos, strict=True):
            waveform.audible = active
            waveform.solo = active and select.isChecked()
            waveform.update()

    def _refresh(self) -> None:
        if not self.player:
            return
        playing = self.player.playing
        glyph = "pause" if playing else "play"
        if self.play_key.glyph != glyph or not self.play_key.accessibleName():
            self.play_key.glyph = glyph
            self.play_key.setAccessibleName("暂停" if playing else "播放")
            self.play_key.setToolTip(self.play_key.accessibleName())
            self.play_key.update()
        frames = self.player.position
        preview = self.player.preview
        fraction = frames / preview.frames
        if not self.scrub.isSliderDown():
            with QSignalBlocker(self.scrub):
                self.scrub.setValue(round(fraction * SEEK_STEPS))
        position = self.scrub.sliderPosition() / SEEK_STEPS if self.scrub.isSliderDown() else fraction
        elapsed = round(preview.duration) if position == 1.0 else position * preview.duration
        self.clock.setText(f"{time_text(elapsed)} / {time_text(round(preview.duration))}")
        if self.timeline.position != position:
            self.timeline.position = position
            self.timeline.update()
            for waveform in self.waveforms:
                waveform.position = position
                waveform.update()
