import sounddevice as sd
from PySide6.QtCore import QEvent, QPoint, QPointF, QSignalBlocker, Qt, QTimer, QVariantAnimation, Signal
from PySide6.QtWidgets import (
    QCheckBox, QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton, QScrollArea, QVBoxLayout, QWidget,
    QStyle, QWidgetAction,
)

from stem_separator.application.instruments import LABELS, STANDARD
from stem_separator.audio.playback import Preview, StemPlayer
from stem_separator.gui.controls import (
    ChannelSlider, Timeline, TimelineHover, TrackName, Waveform, plot_fraction, time_text,
)
from stem_separator.gui.hardware import HardwareButton
from stem_separator.gui.surfaces import DeviceMenu, HardwarePanel
from stem_separator.gui.theme import SPACE, STEM_TONES, TRACK_KEY_SIZE, WAVEFORM, technical_font

SEEK_STEPS = 1_000_000
STEM_GROUPS = {
    "vocal": ("vocals", "lead-vocal", "back-vocal"),
    "keys": ("piano", "digital-piano", "organ", "harpsichord", "accordion"),
    "guitar": ("acoustic-guitar", "electric-guitar", "ukulele", "banjo", "mandolin"),
    "drums": ("percussion", "kick", "snare", "hh", "toms", "cymbals", "timpani"),
    "synth": ("riser", "impact", "whoosh"),
    "strings": ("violin", "viola", "cello", "double-bass", "harp"),
    "winds": ("woodwind", "brass", "flute", "clarinet", "oboe", "bassoon", "french-horn", "trumpet", "trombone"),
}
STEM_COLORS = STEM_TONES | {stem: STEM_TONES[family] for family, stems in STEM_GROUPS.items() for stem in stems}


class Results(QWidget):
    audio_error = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self.player: StemPlayer | None = None
        self.waveforms: list[Waveform] = []
        self.mutes: list[HardwareButton] = []
        self.solos: list[HardwareButton] = []
        self.rows: dict[str, tuple[int, tuple[QWidget, ...]]] = {}
        self.active_solo: str | None = None
        self.user_muted: set[str] = set()
        self._row_dim: tuple[float, ...] = ()
        self._dim_from: tuple[float, ...] = ()
        self._dim_to: tuple[float, ...] = ()
        self._dim_motion = QVariantAnimation(self, duration=155, startValue=0., endValue=1.)
        self._dim_motion.valueChanged.connect(self._paint_mix)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(3 * SPACE)

        self.display = QScrollArea(objectName="display", widgetResizable=True)
        self.display.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.hover = TimelineHover(self.display.viewport())
        self._hover_pointer: QPointF | None = None
        self.display.viewport().installEventFilter(self)
        self.display.verticalScrollBar().valueChanged.connect(self._reposition_hover)
        bezel = HardwarePanel(recessed=True)
        screen_layout = QVBoxLayout(bezel)
        screen_layout.setContentsMargins(7, 7, 7, 7)
        screen_layout.setSpacing(0)
        screen_layout.addWidget(self.display)
        layout.addWidget(bezel, 1)
        self.deck = HardwarePanel(objectName="deck")
        transport = QHBoxLayout(self.deck)
        transport.setContentsMargins(3 * SPACE, 2 * SPACE, 4 * SPACE, 2 * SPACE)
        transport.setSpacing(2 * SPACE)
        self.play_key = HardwareButton("", glyph="play", primary=True, width=60)
        self.play_key.setFixedSize(60, 56)
        self.play_key.clicked.connect(self.toggle_play)
        self.clock = QLabel("0:00 / 0:00", objectName="time")
        self.clock.setFont(technical_font(16))
        self.clock.setFixedWidth(self.clock.fontMetrics().horizontalAdvance("00:00 / 00:00") + SPACE)
        self.scrub = ChannelSlider("播放进度")
        self.scrub.setParent(self.deck)
        self.scrub.setRange(0, SEEK_STEPS)
        self.scrub.setTracking(False)
        self.scrub.setSingleStep(SEEK_STEPS // 100)
        self.scrub.setPageStep(SEEK_STEPS // 10)
        self.scrub.valueChanged.connect(lambda value: self.seek(value / SEEK_STEPS))
        self.scrub.sliderMoved.connect(lambda _: self._refresh())
        self.volume = ChannelSlider("总音量")
        self.volume.setRange(0, 100)
        self.volume.setValue(80)
        self.volume.setFixedWidth(82)
        self.volume.valueChanged.connect(self._gains)
        transport.addWidget(self.play_key)
        transport.addWidget(self.clock)
        transport.addStretch(1)
        transport.addWidget(QLabel("音量"))
        transport.addWidget(self.volume)
        layout.addWidget(self.deck)
        self._build_tracks(STANDARD)

        QWidget.setTabOrder(self.play_key, self.scrub)
        QWidget.setTabOrder(self.scrub, self.volume)

        self.timer = QTimer(self, interval=33)
        self.timer.timeout.connect(self._refresh)

    def _align_scrubber(self) -> None:
        # Both controls live in the same logical coordinate system, including after the
        # scroll area's viewport shrinks to accommodate a vertical scrollbar.
        left = self.timeline.mapTo(self, QPoint()).x() - self.deck.x()
        self.scrub.setGeometry(left, (self.deck.height() - 36) // 2, self.timeline.width(), 36)
        self._reposition_hover()

    def eventFilter(self, watched, event) -> bool:
        if watched is self.hover.parentWidget() and event.type() == QEvent.Type.Resize:
            self.hover.setGeometry(watched.rect())
            self._reposition_hover()
        elif watched is self.hover.parentWidget() and event.type() == QEvent.Type.Hide:
            self._hide_hover()
        return super().eventFilter(watched, event)

    def _reposition_hover(self) -> None:
        if self._hover_pointer is not None:
            self._move_hover(self._hover_pointer)

    def _move_hover(self, point: QPointF) -> None:
        if self.player is None:
            return
        viewport = self.display.viewport()
        local = viewport.mapFromGlobal(point)
        if not viewport.rect().contains(local.toPoint()):
            self._hide_hover()
            return
        x = self.timeline.mapFromGlobal(point).x()
        self._hover_pointer = point
        self.hover.show_position(plot_fraction(x, self.timeline.width()), self.timeline)

    def _hide_hover(self) -> None:
        self._hover_pointer = None
        self.hover.clear()

    def _connect_plot(self, plot: Timeline | Waveform) -> None:
        plot.seek.connect(self.seek)
        plot.hovered.connect(self._move_hover)
        plot.hover_left.connect(self._hide_hover)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._align_scrubber()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._align_scrubber()

    def _build_tracks(self, tracks: tuple[str, ...]) -> None:
        panel = QWidget(objectName="trackPanel")
        grid = QGridLayout(panel)
        self.waveforms.clear()
        self.mutes.clear()
        self.solos.clear()
        self.rows.clear()
        grid.setContentsMargins(4 * SPACE, 2 * SPACE, 4 * SPACE, 2 * SPACE)
        grid.setHorizontalSpacing(3 * SPACE)
        grid.setVerticalSpacing(0)
        grid.setColumnMinimumWidth(0, self.play_key.width() + self.clock.width())
        grid.setColumnStretch(1, 1)
        self.timeline = Timeline()
        self._connect_plot(self.timeline)
        self.timeline.geometry_changed.connect(self._align_scrubber)
        grid.addWidget(self.timeline, 0, 1)

        for index, track in enumerate(tracks):
            row = index * 2 + 1
            label = LABELS[track]
            name = TrackName(label)
            wave = Waveform(STEM_COLORS.get(track, WAVEFORM))
            self._connect_plot(wave)
            wave.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            wave.customContextMenuRequested.connect(
                lambda point, i=index, track_id=track, widget=wave:
                self._track_menu(widget, widget.mapToGlobal(point), i, track_id)
            )
            mute = HardwareButton("", glyph="speaker", compact=True)
            solo = HardwareButton("", glyph="headphones", compact=True)
            for control in (mute, solo):
                control.setFixedSize(TRACK_KEY_SIZE, TRACK_KEY_SIZE)
                control.setCheckable(True)
            self._describe_track_key(mute, label, "静音")
            self._describe_track_key(solo, label, "独听")
            mute.clicked.connect(lambda checked, track_id=track: self._mute(track_id, checked))
            solo.clicked.connect(lambda checked, track_id=track: self._solo(track_id, checked))
            grid.addWidget(name, row, 0)
            grid.addWidget(wave, row, 1)
            grid.addWidget(mute, row, 2, Qt.AlignmentFlag.AlignVCenter)
            grid.addWidget(solo, row, 3, Qt.AlignmentFlag.AlignVCenter)
            grid.setRowStretch(row, 1)
            widgets = (name, wave, mute, solo)
            if index + 1 < len(tracks):
                rule = QFrame(objectName="rule")
                rule.setFixedHeight(1)
                grid.addWidget(rule, row + 1, 0, 1, 4)
                widgets += (rule,)
            self.rows[track] = (row, widgets)
            self.waveforms.append(wave)
            self.mutes.append(mute)
            self.solos.append(solo)
        self.display.setWidget(panel)
        self.hover.setGeometry(self.display.viewport().rect())
        self.hover.raise_()
        self.display.verticalScrollBar().setValue(0)

    def set_track_visible(self, track: str, visible: bool) -> None:
        # Keep the active exit button reachable when the soloed row is hidden.
        if not visible and self.active_solo == track:
            self._solo(track, False)
        self._hide_hover()
        row, widgets = self.rows[track]
        for widget in widgets:
            widget.setVisible(visible)
        self.display.widget().layout().setRowStretch(row, int(visible))

    def _track_menu(self, anchor, point, index: int, track: str) -> None:
        if self.player is None or self.active_solo not in (None, track):
            return
        menu = DeviceMenu(anchor)
        menu.setAccessibleName(LABELS[track])
        for button, title in ((self.mutes[index], "静音"), (self.solos[index], "独听")):
            action = menu.addAction(title)
            action.setCheckable(True)
            action.setChecked(button.isChecked())
            action.setEnabled(button.isEnabled())
            action.triggered.connect(lambda _, key=button: key.click())
        menu.addSeparator()
        menu.addAction("隐藏此轨", lambda: self.set_track_visible(track, False))
        menu.addAction("显示全部音轨", self._show_all_tracks)
        menu.run_at(point)

    def _show_all_tracks(self) -> None:
        for track in self.rows:
            self.set_track_visible(track, True)

    def visibility_menu(self, anchor: QWidget) -> DeviceMenu:
        menu = DeviceMenu(anchor)
        menu.setAccessibleName("显示或隐藏音轨")
        body = QWidget(objectName="menuPanel")
        layout = QVBoxLayout(body)
        layout.setContentsMargins(2 * SPACE, SPACE, 2 * SPACE, 2 * SPACE)
        layout.setSpacing(2 * SPACE)
        scroll = QScrollArea(widgetResizable=True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setFixedSize(190, min(280, 34 * len(self.rows) + 8))
        options = QWidget()
        rows = QVBoxLayout(options)
        rows.setContentsMargins(0, 0, 4, 0)
        rows.setSpacing(0)
        checks = []
        for name, (_, widgets) in self.rows.items():
            check = QCheckBox(LABELS[name], checked=not widgets[0].isHidden())
            check.toggled.connect(lambda visible, track=name: self.set_track_visible(track, visible))
            rows.addWidget(check)
            checks.append(check)
        rows.addStretch()
        scroll.setWidget(options)
        layout.addWidget(scroll)
        toolbar = QHBoxLayout()
        for label, visible in (("显示全部", True), ("隐藏全部", False)):
            button = QPushButton(label, objectName="utility")
            button.clicked.connect(lambda _checked, value=visible: self._check_tracks(checks, value))
            toolbar.addWidget(button)
        layout.addLayout(toolbar)
        action = QWidgetAction(menu)
        action.setDefaultWidget(body)
        menu.addAction(action)
        return menu

    @staticmethod
    def _check_tracks(checks: list[QCheckBox], visible: bool) -> None:
        for check in checks:
            check.setChecked(visible)

    def set_preview(self, preview: Preview, tracks: tuple[str, ...] = STANDARD) -> None:
        self.clear()
        self.player = StemPlayer(preview)
        end = time_text(round(preview.duration))
        self.clock.setFixedWidth(self.clock.fontMetrics().horizontalAdvance(f"{end} / {end}") + SPACE)
        self._build_tracks(tracks)
        self.timeline.duration = preview.duration
        for waveform, values in zip(self.waveforms, preview.waveforms, strict=True):
            waveform.set_samples(values)
        self._row_dim = (0.,) * len(tracks)
        self._apply_mix()
        self.timeline.update()
        self._refresh()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if hasattr(self, "timer"):
            self._refresh()

    def hideEvent(self, event) -> None:
        self.timer.stop()
        super().hideEvent(event)

    def clear(self) -> None:
        self.timer.stop()
        self._dim_motion.stop()
        self.active_solo = None
        self.user_muted.clear()
        self._hide_hover()
        if self.player:
            self.player.close()
            self.player.preview.close()
            self.player = None
        for waveform in self.waveforms:
            waveform.set_samples(None)

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

    def _mute(self, track: str, checked: bool) -> None:
        if self.active_solo in (None, track):
            if checked:
                self.user_muted.add(track)
            else:
                self.user_muted.discard(track)
            self._apply_mix()

    def _solo(self, track: str, checked: bool) -> None:
        if self.active_solo not in (None, track):
            return
        self.active_solo = track if checked else None
        self._apply_mix()

    @staticmethod
    def _describe_track_key(button: HardwareButton, label: str, action: str) -> None:
        description = f"{label} · {action}"
        button.setToolTip(description)
        button.setAccessibleName(description)

    def _gains(self) -> None:
        if self.player:
            master = self.volume.value() / 100
            self.player.gains = tuple(master if self._audible(track) else 0. for track in self.rows)

    def _audible(self, track: str) -> bool:
        return track == self.active_solo if self.active_solo is not None else track not in self.user_muted

    def _apply_mix(self) -> None:
        self._gains()
        for track, (_, widgets) in self.rows.items():
            _, _, mute, solo, *_ = widgets
            active = track == self.active_solo
            enabled = self.active_solo is None or active
            mute.set_monitor_muted(not active if self.active_solo is not None else None)
            mute.setChecked(track in self.user_muted)
            solo.setChecked(active)
            mute.setEnabled(enabled)
            solo.setEnabled(enabled)
            label = LABELS[track]
            if enabled:
                action = "取消静音" if track in self.user_muted else "静音"
                if active:
                    action += "（退出独听后生效）"
                solo_action = "退出独听" if active else "独听"
            else:
                action, solo_action = "静音不可用（其他音轨正在独听）", "独听不可用"
            self._describe_track_key(mute, label, action)
            self._describe_track_key(solo, label, solo_action)
        target = tuple(float(self.active_solo not in (None, track)) for track in self.rows)
        self._dim_motion.stop()
        self._dim_from, self._dim_to = self._row_dim, target
        if self._dim_from == target or not self.style().styleHint(QStyle.StyleHint.SH_Widget_Animate):
            self._paint_mix(1.)
        else:
            self._paint_mix(0.)
            self._dim_motion.start()

    def _paint_mix(self, fraction: float) -> None:
        self._row_dim = tuple(start + (end - start) * fraction for start, end in
                              zip(self._dim_from, self._dim_to, strict=True))
        for (track, (_, widgets)), dim in zip(self.rows.items(), self._row_dim, strict=True):
            label, waveform, *_ = widgets
            solo = track == self.active_solo
            label.set_mix_state(solo, dim)
            waveform.set_mix_state(solo or track not in self.user_muted, solo, dim)

    def _refresh(self) -> None:
        if not self.player:
            return
        playing = self.player.playing
        if playing and self.isVisible():
            if not self.timer.isActive():
                self.timer.start()
        else:
            self.timer.stop()
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
        position = self.scrub.fraction
        display_fraction = position if self.scrub.isSliderDown() else fraction
        elapsed = round(preview.duration) if display_fraction == 1.0 else display_fraction * preview.duration
        self.clock.setText(f"{time_text(elapsed)} / {time_text(round(preview.duration))}")
        self.timeline.set_position(position)
        for waveform in self.waveforms:
            waveform.set_position(position)
