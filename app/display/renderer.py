from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from app.models import PlaybackState, RenderState, UIMode


class EInkRenderer:
    def __init__(self, width: int = 600, height: int = 448) -> None:
        self.width = width
        self.height = height
        self.scale = min(width / 800, height / 480)
        self.font_regular = self._font(self._scaled(30))
        self.font_small = self._font(self._scaled(22))
        self.font_medium = self._font(self._scaled(38))
        self.font_large = self._font(self._scaled(118))
        self.font_alarm = self._font(self._scaled(130))
        self.font_title = self._font(self._scaled(54))

    def render_to_file(self, state: RenderState, path: Path) -> None:
        image = self.render(state)
        path.parent.mkdir(parents=True, exist_ok=True)
        image.save(path)

    def render(self, state: RenderState) -> Image.Image:
        image = Image.new("1", (self.width, self.height), 1)
        draw = ImageDraw.Draw(image)
        draw.rectangle((0, 0, self.width - 1, self.height - 1), outline=0)

        if state.alarm_runtime.phase == "WAKE_STAGE":
            self._render_gentle_wake(draw, state)
        elif state.alarm_runtime.active:
            self._render_alarm_active(draw, state)
        elif state.mode == UIMode.AMBIENT:
            self._render_ambient(draw, state)
        elif state.mode == UIMode.SLEEP_SCREEN:
            self._render_sleep_screen(draw, state)
        elif state.mode == UIMode.MENU:
            self._render_menu(draw, state)
        elif state.mode == UIMode.SOURCE_DETAIL:
            self._render_detail(draw, state)
        elif state.mode == UIMode.SLEEP_TIMER:
            self._render_sleep_timer(draw, state)
        elif state.mode == UIMode.ALARM:
            self._render_alarm_settings(draw, state)
        elif state.mode == UIMode.BLUETOOTH_PAIRING:
            self._render_bluetooth_pairing(draw, state)
        elif state.mode == UIMode.OUTPUT_SELECT:
            self._render_output(draw, state)
        else:
            self._render_home(draw, state)

        return image

    def _render_home(self, draw: ImageDraw.ImageDraw, state: RenderState) -> None:
        if state.source_complete:
            self._render_home_clock(draw, state, compact=True)
            draw.line((self._x(40), self._y(146), self._x(760), self._y(146)), fill=0, width=1)
            source = state.current_source_label or state.detail_title or "Playlist"
            total = state.queue_length or state.completed_count
            self._center(draw, source, self._y(174), self.font_title)
            self._center(draw, "Completed", self._y(252), self.font_regular)
            self._center(
                draw,
                f"{state.completed_count} / {total} listened",
                self._y(298),
                self.font_small,
            )
        elif state.playback.is_audio_active:
            self._render_home_clock(draw, state, compact=True)
            draw.line((self._x(40), self._y(146), self._x(760), self._y(146)), fill=0, width=1)
            source = state.current_source_label or "Audio"
            self._center(draw, source, self._y(174), self.font_title)
            self._center(draw, state.playback.title, self._y(244), self.font_medium)
            self._center(draw, self._playback_context(state), self._y(306), self.font_small)
        else:
            self._render_home_clock(draw, state, compact=False)
            sleep_context = self._sleep_context(state)
            if sleep_context:
                self._center(draw, sleep_context, self._y(176), self.font_regular)
            if state.bluetooth.last_message:
                self._center(draw, state.bluetooth.last_message, self._y(214), self.font_small)
            self._center(draw, "Hold knob for menu", self._y(286), self.font_small)

        self._bottom_bar(draw, state)

    def _render_ambient(self, draw: ImageDraw.ImageDraw, state: RenderState) -> None:
        time_text = state.now.strftime("%-I:%M")
        period = state.now.strftime("%p")
        day_text = state.now.strftime("%A")
        date_text = state.now.strftime("%B %-d")
        self._center(draw, time_text, self._y(54), self.font_large)
        draw.text((self._x(618), self._y(106)), period, font=self.font_medium, fill=0)
        self._center(draw, day_text, self._y(230), self.font_medium)
        self._center(draw, date_text, self._y(286), self.font_regular)
        if state.ambient_show_playback_glyph and state.playback.state == PlaybackState.PLAYING:
            self._center(draw, "▶", self.height - self._scaled(64), self.font_small)

    def _render_sleep_screen(self, draw: ImageDraw.ImageDraw, state: RenderState) -> None:
        time_text = state.now.strftime("%-I:%M")
        period = state.now.strftime("%p")
        self._center(draw, time_text, self._y(78), self.font_large)
        draw.text((self._x(618), self._y(130)), period, font=self.font_medium, fill=0)
        sleep_context = self._sleep_context(state)
        if sleep_context:
            self._center(draw, sleep_context, self._y(262), self.font_regular)

    def _render_home_clock(
        self,
        draw: ImageDraw.ImageDraw,
        state: RenderState,
        compact: bool,
    ) -> None:
        time_text = state.now.strftime("%-I:%M")
        period = state.now.strftime("%p")
        if compact:
            self._center(draw, time_text, self._y(16), self.font_medium)
            draw.text((self._x(522), self._y(26)), period, font=self.font_regular, fill=0)
            sleep_context = self._sleep_context(state)
            if sleep_context:
                self._center(draw, sleep_context, self._y(88), self.font_small)
            if state.bluetooth.last_message:
                self._center(draw, state.bluetooth.last_message, self._y(112), self.font_small)
            return

        self._center(draw, time_text, self._y(24), self.font_large)
        draw.text((self._x(620), self._y(76)), period, font=self.font_medium, fill=0)

    def _render_menu(self, draw: ImageDraw.ImageDraw, state: RenderState) -> None:
        self._left_fit(
            draw,
            state.menu_title,
            self._x(40),
            self._y(26),
            self.width - self._x(80),
            self.font_title,
        )
        draw.line(
            (self._x(40), self._y(92), self.width - self._x(40), self._y(92)),
            fill=0,
            width=max(1, self._scaled(2)),
        )
        visible = self._visible_menu_items(state)
        y = self._y(112)
        row_height = self._scaled(46)
        for absolute_index, item in visible:
            marker = ">" if absolute_index == state.selected_index else " "
            self._left_fit(
                draw,
                f"{marker} {item.label}",
                self._x(54),
                y,
                self.width - self._x(108),
                self.font_medium,
            )
            y += row_height
        self._bottom_hint(draw, "Turn move   Knob select   Hold home")

    def _render_detail(self, draw: ImageDraw.ImageDraw, state: RenderState) -> None:
        self._center(draw, state.detail_title or "Source", self._y(90), self.font_title)
        self._center(draw, state.detail_subtitle or "Playing", self._y(172), self.font_medium)
        self._center(draw, state.playback.title, self._y(258), self.font_regular)
        self._center(draw, "Knob pauses   Hold home", self._y(328), self.font_small)
        self._bottom_bar(draw, state)

    def _render_sleep_timer(self, draw: ImageDraw.ImageDraw, state: RenderState) -> None:
        self._center(draw, "Sleep Timer", self._y(76), self.font_title)
        self._center(draw, state.sleep_timer_label, self._y(168), self.font_large)
        self._center(draw, "Knob cycles", self._y(332), self.font_regular)
        self._bottom_hint(draw, "Hold knob home")

    def _render_alarm_settings(self, draw: ImageDraw.ImageDraw, state: RenderState) -> None:
        self._center(draw, "Alarm", self._y(58), self.font_title)
        self._center(draw, "On" if state.alarm.enabled else "Off", self._y(128), self.font_large)
        self._center(draw, state.alarm.label(), self._y(286), self.font_medium)
        self._center(
            draw,
            state.alarm_source_label or state.alarm.source_id,
            self._y(338),
            self.font_regular,
        )
        self._bottom_hint(draw, "Turn adjust   Knob toggle")

    def _render_gentle_wake(self, draw: ImageDraw.ImageDraw, state: RenderState) -> None:
        time_text = state.now.strftime("%-I:%M")
        self._center(draw, "Good Morning", self._y(44), self.font_title)
        self._center(draw, time_text, self._y(112), self.font_large)
        stage = state.alarm_runtime.wake_stage
        total = state.alarm_runtime.wake_stages or state.alarm.wake_stages
        label = "Gentle Wake" if stage <= 1 else "Morning Brief"
        self._center(draw, label, self._y(260), self.font_medium)
        self._center(
            draw,
            f"Stage {stage} of {total} · Volume {state.alarm_runtime.fade_volume}%",
            self._y(316),
            self.font_small,
        )
        self._bottom_hint(draw, "Knob stop   Source snooze   Hold dismiss")

    def _render_output(self, draw: ImageDraw.ImageDraw, state: RenderState) -> None:
        self._center(draw, "Audio Output", self._y(58), self.font_title)
        self._center(draw, state.output_label, self._y(148), self.font_large)
        preferred = state.bluetooth.preferred_device_name if state.bluetooth.preferred_device_mac else "Not paired"
        self._center(draw, f"Preferred: {preferred}", self._y(286), self.font_regular)
        message = state.bluetooth.last_message or "BossDAC / Headphones"
        self._center(draw, message, self._y(340), self.font_regular)
        self._bottom_hint(draw, "Hold knob home")

    def _render_bluetooth_pairing(self, draw: ImageDraw.ImageDraw, state: RenderState) -> None:
        self._left_fit(
            draw,
            "Bluetooth Pairing",
            self._x(40),
            self._y(26),
            self.width - self._x(80),
            self.font_title,
        )
        draw.line((self._x(40), self._y(92), self.width - self._x(40), self._y(92)), fill=0)
        if not state.menu_items:
            self._center(draw, "Scanning...", self._y(158), self.font_medium)
            self._center(draw, state.bluetooth.last_message or "No devices yet", self._y(226), self.font_regular)
        else:
            y = self._y(120)
            row_height = self._scaled(50)
            for index, item in self._visible_menu_items(state):
                marker = ">" if index == state.selected_index else " "
                self._left_fit(
                    draw,
                    f"{marker} {item.label}",
                    self._x(54),
                    y,
                    self.width - self._x(108),
                    self.font_medium,
                )
                y += row_height
            selected = state.menu_items[state.selected_index]
            message = state.bluetooth.last_message or f"Knob pairs {selected.label}"
            self._center(draw, message, self._y(326), self.font_small)
            if not message.startswith("Pairing:"):
                self._center(draw, f"Knob pairs {selected.label}", self._y(352), self.font_small)
        self._bottom_hint(draw, "Turn select   Knob pair")

    def _render_alarm_active(self, draw: ImageDraw.ImageDraw, state: RenderState) -> None:
        banner_height = self._y(150)
        draw.rectangle((0, 0, self.width, banner_height), fill=0)
        self._center(draw, "ALARM", self._y(8), self.font_alarm, fill=1)
        self._center(
            draw,
            state.alarm_source_label or "Alarm Source",
            self._y(180),
            self.font_title,
        )
        fade_volume = state.alarm_runtime.fade_volume or state.playback.volume
        self._center(
            draw,
            f"Fade volume {fade_volume}% of {state.alarm.target_volume}%",
            self._y(260),
            self.font_medium,
        )
        fade = "Fade in" if state.alarm_runtime.fading else "Playing"
        self._center(draw, fade, self._y(318), self.font_regular)
        self._bottom_hint(draw, "Knob stop   Source snooze   Hold dismiss")

    def _bottom_bar(self, draw: ImageDraw.ImageDraw, state: RenderState) -> None:
        top_y = self.height - self._scaled(58)
        center_y = self.height - self._scaled(28)
        margin = self._scaled(34)
        draw.line((margin, top_y, self.width - margin, top_y), fill=0, width=1)

        slots = self._status_bar_slots()
        values = [
            ("playback", ""),
            ("volume", str(state.playback.volume)),
            ("sleep", self._sleep_indicator(state)),
            ("output", self._output_indicator(state)),
        ]
        for index, (kind, value) in enumerate(values):
            x = slots[index]
            if index:
                divider_x = round((slots[index - 1] + slots[index]) / 2)
                draw.line(
                    (
                        divider_x,
                        top_y + self._scaled(13),
                        divider_x,
                        self.height - self._scaled(14),
                    ),
                    fill=0,
                )
            self._draw_status_item(draw, kind, value, x, center_y, state)

    def _status_bar_slots(self) -> list[int]:
        margin = self._scaled(48)
        usable = self.width - (margin * 2)
        return [round(margin + usable * fraction) for fraction in (0.08, 0.36, 0.65, 0.91)]

    def _draw_status_item(
        self,
        draw: ImageDraw.ImageDraw,
        kind: str,
        value: str,
        x: int,
        center_y: int,
        state: RenderState,
    ) -> None:
        icon_size = self._scaled(20)
        icon_left = x - self._scaled(28)
        if kind == "playback":
            self._draw_playback_icon(draw, state.playback.state, x, center_y)
            return
        if kind == "volume":
            self._draw_speaker_icon(draw, icon_left, center_y, icon_size)
        elif kind == "sleep":
            self._draw_sleep_icon(draw, icon_left, center_y, icon_size)
        elif kind == "output":
            self._draw_output_icon(draw, icon_left, center_y, icon_size, state)
            if value == "BT":
                return
        self._left_fit(
            draw,
            value,
            icon_left + self._scaled(28),
            center_y - self._scaled(16),
            self._scaled(86),
            self.font_regular,
        )

    def _draw_playback_icon(
        self,
        draw: ImageDraw.ImageDraw,
        state: PlaybackState,
        center_x: int,
        center_y: int,
    ) -> None:
        size = self._scaled(20)
        if state == PlaybackState.PLAYING:
            draw.polygon(
                [
                    (center_x - size // 3, center_y - size // 2),
                    (center_x - size // 3, center_y + size // 2),
                    (center_x + size // 2, center_y),
                ],
                fill=0,
            )
        elif state == PlaybackState.PAUSED:
            bar_width = max(2, self._scaled(5))
            gap = self._scaled(7)
            for left in (center_x - gap, center_x + self._scaled(2)):
                draw.rectangle(
                    (
                        left,
                        center_y - size // 2,
                        left + bar_width,
                        center_y + size // 2,
                    ),
                    fill=0,
                )
        else:
            radius = self._scaled(8)
            draw.ellipse(
                (
                    center_x - radius,
                    center_y - radius,
                    center_x + radius,
                    center_y + radius,
                ),
                outline=0,
                width=max(1, self._scaled(2)),
            )

    def _draw_speaker_icon(
        self,
        draw: ImageDraw.ImageDraw,
        left: int,
        center_y: int,
        size: int,
    ) -> None:
        top = center_y - size // 2
        draw.rectangle((left, top + size // 3, left + size // 4, top + size * 2 // 3), fill=0)
        draw.polygon(
            [
                (left + size // 4, top + size // 3),
                (left + size * 3 // 5, top + size // 8),
                (left + size * 3 // 5, top + size * 7 // 8),
                (left + size // 4, top + size * 2 // 3),
            ],
            fill=0,
        )
        draw.arc(
            (
                left + size // 2,
                top + size // 5,
                left + size,
                top + size * 4 // 5,
            ),
            start=-45,
            end=45,
            fill=0,
            width=max(1, self._scaled(2)),
        )

    def _draw_sleep_icon(
        self,
        draw: ImageDraw.ImageDraw,
        left: int,
        center_y: int,
        size: int,
    ) -> None:
        top = center_y - size // 2
        draw.ellipse(
            (left, top, left + size, top + size),
            outline=0,
            width=max(1, self._scaled(2)),
        )
        draw.pieslice((left, top, left + size, top + size), start=90, end=270, fill=0)

    def _draw_output_icon(
        self,
        draw: ImageDraw.ImageDraw,
        left: int,
        center_y: int,
        size: int,
        state: RenderState,
    ) -> None:
        if state.bluetooth.active_sink == "bluetooth" or state.bluetooth.connected:
            self._center_in_box(
                draw,
                "BT",
                left - self._scaled(3),
                left + size + self._scaled(14),
                center_y - self._scaled(14),
                self.font_small,
            )
            return
        draw.line(
            (left, center_y, left + size, center_y),
            fill=0,
            width=max(1, self._scaled(2)),
        )
        draw.arc(
            (
                left + size // 4,
                center_y - size // 2,
                left + size * 3 // 4,
                center_y + size // 2,
            ),
            start=90,
            end=270,
            fill=0,
            width=max(1, self._scaled(2)),
        )
        draw.arc(
            (
                left + size // 2,
                center_y - size // 2,
                left + size,
                center_y + size // 2,
            ),
            start=-90,
            end=90,
            fill=0,
            width=max(1, self._scaled(2)),
        )

    def _output_indicator(self, state: RenderState) -> str:
        sink = state.bluetooth.active_sink.lower()
        if state.bluetooth.connected or sink == "bluetooth":
            return "BT"
        if sink in {"dac", "headphones", "hp"}:
            return "HP"
        if sink in {"speaker", "internal"}:
            return "SP"
        return sink.upper()[:3] if sink else "HP"

    def _sleep_indicator(self, state: RenderState) -> str:
        value = state.sleep_timer_label.replace("Sleep ", "").strip()
        return "Off" if value.lower() == "off" else value

    def _sleep_context(self, state: RenderState) -> str:
        value = self._sleep_indicator(state)
        if value.lower() == "off":
            return ""
        return f"Sleep in {value}"

    def _playback_context(self, state: RenderState) -> str:
        parts: list[str] = []
        if state.track_index is not None and state.queue_length:
            parts.append(f"Track {state.track_index + 1} of {state.queue_length}")
        if state.playback.subtitle:
            parts.append(state.playback.subtitle)
        if state.progress_label:
            parts.append(state.progress_label)
        unique_parts: list[str] = []
        for part in parts:
            if part and part not in unique_parts:
                unique_parts.append(part)
        return " · ".join(unique_parts)

    def _bottom_hint(self, draw: ImageDraw.ImageDraw, text: str) -> None:
        line_y = self.height - self._scaled(70)
        text_y = self.height - self._scaled(48)
        margin = self._scaled(28)
        draw.line((margin, line_y, self.width - margin, line_y), fill=0, width=1)
        self._center(draw, text, text_y, self.font_small)

    def _visible_menu_items(self, state: RenderState) -> list[tuple[int, object]]:
        window = 6 if self.height >= 420 else 5
        start = max(0, min(state.selected_index - 2, max(0, len(state.menu_items) - window)))
        end = min(len(state.menu_items), start + window)
        return list(enumerate(state.menu_items[start:end], start=start))

    def _center(
        self,
        draw: ImageDraw.ImageDraw,
        text: str,
        y: int,
        font: ImageFont.ImageFont,
        fill: int = 0,
    ) -> None:
        text = self._fit_text(draw, text, font, self.width - self._scaled(72))
        bbox = draw.textbbox((0, 0), text, font=font)
        x = (self.width - (bbox[2] - bbox[0])) // 2
        draw.text((x, y), text, font=font, fill=fill)

    def _right(
        self, draw: ImageDraw.ImageDraw, text: str, x: int, y: int, font: ImageFont.ImageFont
    ) -> None:
        text = self._fit_text(draw, text, font, self._scaled(300))
        bbox = draw.textbbox((0, 0), text, font=font)
        draw.text((x - (bbox[2] - bbox[0]), y), text, font=font, fill=0)

    def _left_fit(
        self,
        draw: ImageDraw.ImageDraw,
        text: str,
        x: int,
        y: int,
        width: int,
        font: ImageFont.ImageFont,
        fill: int = 0,
    ) -> None:
        draw.text((x, y), self._fit_text(draw, text, font, width), font=font, fill=fill)

    def _center_in_box(
        self,
        draw: ImageDraw.ImageDraw,
        text: str,
        left: int,
        right: int,
        y: int,
        font: ImageFont.ImageFont,
    ) -> None:
        text = self._fit_text(draw, text, font, right - left - self._scaled(12))
        bbox = draw.textbbox((0, 0), text, font=font)
        x = left + ((right - left) - (bbox[2] - bbox[0])) // 2
        draw.text((x, y), text, font=font, fill=0)

    def _fit_text(self, draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, width: int) -> str:
        if draw.textlength(text, font=font) <= width:
            return text
        ellipsis = "..."
        trimmed = text
        while trimmed and draw.textlength(trimmed + ellipsis, font=font) > width:
            trimmed = trimmed[:-1]
        return (trimmed + ellipsis) if trimmed else ellipsis

    def _scaled(self, value: int) -> int:
        return max(1, round(value * self.scale))

    def _x(self, value: int) -> int:
        return round(value * self.width / 800)

    def _y(self, value: int) -> int:
        return round(value * self.height / 480)

    def _font(self, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
        candidates = [
            "/System/Library/Fonts/Supplemental/Arial.ttf",
            "/Library/Fonts/Arial.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        ]
        for candidate in candidates:
            path = Path(candidate)
            if path.exists():
                return ImageFont.truetype(str(path), size=size)
        return ImageFont.load_default()
