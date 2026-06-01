from __future__ import annotations

import unittest

from PIL import Image

from app.display.waveshare_display import (
    MODE_FULL,
    MODE_PARTIAL,
    WaveshareDisplay,
    display_model_spec,
)


class FakeEpd:
    width = 600
    height = 448

    def __init__(self, calls: list[str] | None = None) -> None:
        self.calls: list[str] = calls if calls is not None else []

    def init(self) -> None:
        self.calls.append("init")

    def init_Part(self) -> None:
        self.calls.append("init_Part")

    def Clear(self) -> None:
        self.calls.append("Clear")

    def getbuffer(self, image: Image.Image) -> str:
        self.calls.append(f"getbuffer:{image.size[0]}x{image.size[1]}")
        return "buffer"

    def display(self, buffer: str) -> None:
        self.calls.append(f"display:{buffer}")

    def display_Partial(self, buffer: str) -> None:
        self.calls.append(f"display_Partial:{buffer}")

    def sleep(self) -> None:
        self.calls.append("sleep")


class FakeDriver:
    EPD_WIDTH = 600
    EPD_HEIGHT = 448

    def __init__(self) -> None:
        self.calls: list[str] = []

    def EPD(self) -> FakeEpd:
        self.calls.append("EPD")
        return FakeEpd(self.calls)


class Fake4In2Epd(FakeEpd):
    width = 400
    height = 300


class Fake4In2LowercasePartialEpd:
    width = 400
    height = 300

    def __init__(self, calls: list[str] | None = None) -> None:
        self.calls: list[str] = calls if calls is not None else []

    def init(self) -> None:
        self.calls.append("init")

    def init_Part(self) -> None:
        self.calls.append("init_Part")

    def Clear(self) -> None:
        self.calls.append("Clear")

    def getbuffer(self, image: Image.Image) -> str:
        self.calls.append(f"getbuffer:{image.size[0]}x{image.size[1]}")
        return "buffer"

    def display(self, buffer: str) -> None:
        self.calls.append(f"display:{buffer}")

    def display_part(self, buffer: str) -> None:
        self.calls.append(f"display_part:{buffer}")

    def sleep(self) -> None:
        self.calls.append("sleep")


class FakeTwoBufferPartialEpd(Fake4In2LowercasePartialEpd):
    def display_part(self, old_buffer: str, new_buffer: str) -> None:
        self.calls.append(f"display_part:{old_buffer}->{new_buffer}")


class Fake4In2Driver:
    EPD_WIDTH = 400
    EPD_HEIGHT = 300

    def __init__(self) -> None:
        self.calls: list[str] = []

    def EPD(self) -> Fake4In2Epd:
        self.calls.append("EPD")
        return Fake4In2Epd(self.calls)


class FakeLowercasePartialDriver:
    EPD_WIDTH = 400
    EPD_HEIGHT = 300

    def __init__(self) -> None:
        self.calls: list[str] = []

    def EPD(self) -> Fake4In2LowercasePartialEpd:
        self.calls.append("EPD")
        return Fake4In2LowercasePartialEpd(self.calls)


class FakeTwoBufferPartialDriver:
    EPD_WIDTH = 400
    EPD_HEIGHT = 300

    def __init__(self) -> None:
        self.calls: list[str] = []

    def EPD(self) -> FakeTwoBufferPartialEpd:
        self.calls.append("EPD")
        return FakeTwoBufferPartialEpd(self.calls)


class WaveshareDisplayModeTest(unittest.TestCase):
    def make_display(self, *, disable_partial: bool = False) -> tuple[WaveshareDisplay, FakeEpd]:
        epd = FakeEpd()
        display = WaveshareDisplay(
            partial_update_enabled=True,
            disable_partial=disable_partial,
            full_clear_interval=0,
        )
        display._epd = epd
        display._initialized = True
        display._display_mode = MODE_FULL
        display.width = epd.width
        display.height = epd.height
        return display, epd

    def test_partial_update_switches_to_partial_lut_and_uses_display_partial(self) -> None:
        display, epd = self.make_display()

        display.partial_update(Image.new("1", (600, 448), 1), reason="menu_navigation")

        self.assertEqual(display._display_mode, MODE_PARTIAL)
        self.assertEqual(epd.calls, ["init_Part", "getbuffer:600x448", "display_Partial:buffer"])

    def test_clean_full_update_switches_from_partial_to_full_before_clear_and_display(self) -> None:
        display, epd = self.make_display()
        display._display_mode = MODE_PARTIAL

        display.full_update(
            Image.new("1", (600, 448), 1),
            reason="major_layout_transition",
            clean_refresh=True,
        )

        self.assertEqual(display._display_mode, MODE_FULL)
        self.assertEqual(epd.calls, ["init", "Clear", "getbuffer:600x448", "display:buffer"])

    def test_any_full_update_from_partial_mode_forces_clean_clear(self) -> None:
        display, epd = self.make_display()
        display._display_mode = MODE_PARTIAL

        display.full_update(
            Image.new("1", (600, 448), 1),
            reason="source_change",
            clean_refresh=False,
        )

        self.assertEqual(display._display_mode, MODE_FULL)
        self.assertEqual(epd.calls, ["init", "Clear", "getbuffer:600x448", "display:buffer"])

    def test_disable_partial_never_calls_partial_driver_methods(self) -> None:
        display, epd = self.make_display(disable_partial=True)

        display.partial_update(Image.new("1", (600, 448), 1), reason="clock_refresh")

        self.assertEqual(display._display_mode, MODE_FULL)
        self.assertEqual(epd.calls, ["getbuffer:600x448", "display:buffer"])

    def test_full_update_wakes_panel_if_previous_one_shot_slept(self) -> None:
        display, epd = self.make_display(disable_partial=True)
        display._sleeping = True
        display._display_mode = MODE_FULL

        display.full_update(Image.new("1", (600, 448), 1), reason="clock_refresh")

        self.assertFalse(display._sleeping)
        self.assertEqual(epd.calls, ["init", "Clear", "getbuffer:600x448", "display:buffer"])

    def test_one_shot_render_path_matches_manual_push_lifecycle(self) -> None:
        driver = FakeDriver()
        display = WaveshareDisplay(full_clear_interval=0)
        display._epd_module = driver
        path = self._write_preview_image()

        self.assertTrue(
            display.one_shot_render_path(
                str(path),
                reason="major_layout_transition",
                displayed_hash="abc123",
            )
        )

        self.assertEqual(
            driver.calls,
            ["EPD", "init", "getbuffer:600x448", "display:buffer", "sleep"],
        )
        self.assertTrue(display._sleeping)
        self.assertEqual(display._display_mode, MODE_FULL)

    def test_4in2_model_uses_400x300_driver_dimensions(self) -> None:
        driver = Fake4In2Driver()
        display = WaveshareDisplay(display_model="waveshare_4in2_v2", full_clear_interval=0)
        display._epd_module = driver
        path = self._write_preview_image(size=(600, 448))

        self.assertEqual(display.driver_name, "epd4in2_V2")
        self.assertEqual(display_model_spec("waveshare_4in2_v2").width, 400)
        self.assertTrue(
            display.one_shot_render_path(
                str(path),
                reason="major_layout_transition",
                displayed_hash="abc123",
            )
        )

        self.assertEqual(
            driver.calls,
            ["EPD", "init", "getbuffer:400x300", "display:buffer", "sleep"],
        )
        self.assertEqual(display.width, 400)
        self.assertEqual(display.height, 300)

    def test_default_display_model_is_4in2(self) -> None:
        spec = display_model_spec(None)

        self.assertEqual(spec.model, "waveshare_4in2_v2")
        self.assertEqual(spec.width, 400)
        self.assertEqual(spec.height, 300)

    def test_4in2_lowercase_partial_api_is_detected_and_used(self) -> None:
        driver = FakeLowercasePartialDriver()
        display = WaveshareDisplay(display_model="waveshare_4in2_v2", full_clear_interval=0)
        display._epd_module = driver

        self.assertTrue(display._ensure_initialized())
        display.partial_update(Image.new("1", (400, 300), 1), reason="play_pause")

        self.assertEqual(display._display_mode, MODE_PARTIAL)
        self.assertIn("init_Part", driver.calls)
        self.assertIn("display_part:buffer", driver.calls)
        self.assertEqual(display._partial_update_count, 1)
        self.assertEqual(display._full_update_count, 0)
        self.assertEqual(display._partial_api.display_method_name, "display_part")

    def test_4in2_partial_after_one_shot_uses_full_fallback(self) -> None:
        epd = Fake4In2Epd()
        display = WaveshareDisplay(display_model="waveshare_4in2_v2", full_clear_interval=0)
        display._epd = epd
        display._initialized = True
        display._display_mode = MODE_FULL
        display._previous_update_was_one_shot = True
        display.width = epd.width
        display.height = epd.height

        display.partial_update(Image.new("1", (400, 300), 1), reason="volume_change")

        self.assertEqual(display._display_mode, MODE_FULL)
        self.assertIn("display:buffer", epd.calls)
        self.assertNotIn("display_Partial:buffer", epd.calls)
        self.assertEqual(display._partial_update_count, 0)
        self.assertEqual(display._full_update_count, 1)

    def test_two_buffer_partial_api_receives_previous_full_buffer(self) -> None:
        driver = FakeTwoBufferPartialDriver()
        display = WaveshareDisplay(display_model="waveshare_4in2_v2", full_clear_interval=0)
        display._epd_module = driver

        self.assertTrue(display._ensure_initialized())
        display.full_update(Image.new("1", (400, 300), 1), reason="startup")
        display.partial_update(Image.new("1", (400, 300), 1), reason="volume_change")

        self.assertIn("display:buffer", driver.calls)
        self.assertIn("display_part:buffer->buffer", driver.calls)
        self.assertEqual(display._partial_update_count, 1)

    def _write_preview_image(self, size=(600, 448)):
        import tempfile
        from pathlib import Path

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / "latest_screen.png"
        Image.new("1", size, 1).save(path)
        return path


if __name__ == "__main__":
    unittest.main()
