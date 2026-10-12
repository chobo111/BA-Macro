"""Cancellation/window changes during lazy setup and native retry waits."""
from dataclasses import replace
import threading
import time
import unittest
from unittest.mock import Mock, patch

from domain import WindowSizeUnavailable
from engine import Command, Engine
from test_engine import FakeWindows
from test_touch_input import Device
from touch_input import TouchDevice, TouchScriptInput, DOWN


class InputPreflightTests(unittest.TestCase):
    def setUp(self):
        self.native = FakeWindows()
        self.entered, self.proceed = threading.Event(), threading.Event()
        self.errors = []
        self.command = Command(1, 0, 0, 'script', 'q', 0, 1,
                               script='click 10,20', expected_size=(1920, 1080))

    def blocked(self):
        self.entered.set()
        if not self.proceed.wait(2):
            raise TimeoutError('test did not release input preparation')

    def wait_idle(self, engine):
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            with engine.condition:
                if not engine.busy and engine.pending is None and not engine.reset_requested:
                    return
            threading.Event().wait(.005)
        self.fail('worker did not finish')

    def change_context(self, engine, change):
        if change == 'stop':
            engine.stop()
        elif change == 'focus':
            self.native.active_value = False
        elif change == 'resize':
            self.native.size = Mock(return_value=(1280, 720))
        elif change == 'minimize':
            self.native.size = Mock(side_effect=WindowSizeUnavailable())
        elif change == 'occlude':
            self.native.point_belongs_to = Mock(return_value=False)

    def during_initialization(self, change):
        device = Device()
        next_device = Device()
        calls = []

        def factory():
            calls.append(True)
            if len(calls) == 1:
                self.blocked()
                return device
            return next_device

        adapter = TouchScriptInput(self.native, factory)
        self.native.create_touch_input = lambda: adapter
        engine = Engine(self.native, lambda key: False, self.errors.append)
        try:
            engine.submit(self.command)
            self.assertTrue(self.entered.wait(1))
            self.assertEqual(device.events, [])
            if change == 'replace':
                engine.submit(replace(self.command, script='click 30,40'))
            else:
                self.change_context(engine, change)
            self.proceed.set()
            self.wait_idle(engine)
            self.assertEqual(self.native.events, [])
            if change == 'unchanged':
                self.assertEqual([p for p, flags in device.events if flags & DOWN], [(10, 20)])
            else:
                self.assertEqual(device.events, [])
                self.assertTrue(device.closed)
            if change == 'replace':
                self.assertEqual([p for p, flags in next_device.events if flags & DOWN], [(30, 40)])
            if change in ('resize', 'occlude'):
                self.assertEqual(len(self.errors), 1)
            else:
                self.assertEqual(self.errors, [])
        finally:
            self.proceed.set()
            engine.shutdown()
            self.assertFalse(engine.worker.is_alive())

    def test_stop_during_initialization_sends_no_touch(self):
        self.during_initialization('stop')

    def test_focus_loss_during_initialization_sends_no_touch(self):
        self.during_initialization('focus')

    def test_resize_during_initialization_sends_no_touch(self):
        self.during_initialization('resize')

    def test_minimize_during_initialization_sends_no_touch(self):
        self.during_initialization('minimize')

    def test_occlusion_during_initialization_sends_no_touch(self):
        self.during_initialization('occlude')

    def test_replacement_during_initialization_only_runs_new_command(self):
        self.during_initialization('replace')

    def test_unchanged_context_still_sends_touch_after_initialization(self):
        self.during_initialization('unchanged')

    def during_retry(self, change):
        device = TouchDevice.__new__(TouchDevice)
        device.handle = 123
        device.promotion_filter = None
        device.destroy = Mock()
        device.inject = Mock(side_effect=[False, True])
        adapter = TouchScriptInput(self.native, lambda: device)
        self.native.create_touch_input = lambda: adapter
        engine = Engine(self.native, lambda key: False, self.errors.append)
        try:
            with patch('touch_input.C.get_last_error', return_value=21), \
                 patch('touch_input.time.sleep', side_effect=lambda _: self.blocked()):
                engine.submit(self.command)
                self.assertTrue(self.entered.wait(1))
                self.change_context(engine, change)
                self.proceed.set()
                self.wait_idle(engine)
            # The first native attempt returned ERROR_NOT_READY; no retry reaches Windows.
            device.inject.assert_called_once()
            device.destroy.assert_called_once_with(123)
            self.assertIsNone(device.handle)
            self.assertFalse(adapter.pressed)
            self.assertEqual(self.native.events, [])
            self.assertEqual(len(self.errors), 1 if change == 'resize' else 0)
        finally:
            self.proceed.set()
            engine.shutdown()
            self.assertFalse(engine.worker.is_alive())

    def test_stop_during_retry_prevents_late_contact(self):
        self.during_retry('stop')

    def test_focus_loss_during_retry_prevents_late_contact(self):
        self.during_retry('focus')

    def test_resize_during_retry_prevents_late_contact(self):
        self.during_retry('resize')

    def test_sleep_only_script_keeps_device_lazy(self):
        factory = Mock(side_effect=AssertionError('unneeded device creation'))
        self.native.create_touch_input = lambda: TouchScriptInput(self.native, factory)
        engine = Engine(self.native, lambda key: False, self.errors.append)
        try:
            engine.submit(replace(self.command, script='sleep 1'))
            self.wait_idle(engine)
            factory.assert_not_called()
            self.assertEqual(self.errors, [])
        finally:
            engine.shutdown()
