import unittest
from unittest.mock import Mock
from session import ExecutionSession


class SessionTests(unittest.TestCase):
    def test_nested_modal_and_exception_restore_original_state(self):
        state = ExecutionSession(Mock())
        stop, close, restore = Mock(), Mock(), Mock()
        with state.editing(True, stop, close, restore):
            self.assertTrue(state.modal)
            with self.assertRaises(ValueError):
                with state.editing(False, stop):
                    raise ValueError('nested dialog failed')
            self.assertTrue(state.modal)
        self.assertFalse(state.modal)
        stop.assert_called_once()
        close.assert_called_once()
        restore.assert_called_once()

    def test_stop_and_capture_policy_during_editing(self):
        native = Mock()
        native.foreground_owned_by.return_value = True
        state = ExecutionSession(native)
        state.target = 0
        state.armed = True
        with state.editing(False, Mock()):
            self.assertTrue(state.eligible('pause', False))
            self.assertFalse(state.eligible('capture_selected', False))
            self.assertFalse(state.eligible('move', False))
            native.foreground_owned_by.return_value = False
            self.assertTrue(state.eligible('move', False))
        self.assertTrue(state.armed)
