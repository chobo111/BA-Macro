import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import unittest
from unittest.mock import patch
from PySide6.QtCore import Qt, QPoint, QPointF, QMimeData
from PySide6.QtWidgets import QApplication, QTableWidgetItem, QPushButton
from widgets import RowsTable


class Drop:
    def __init__(self, table, point):
        self.table, self.point = table, point
        self.accepted = False
        self.mime = QMimeData()
        self.mime.setData(table.MIME, b'row')
    def source(self): return self.table
    def mimeData(self): return self.mime
    def position(self): return QPointF(self.point)
    def setDropAction(self, action): self.action = action
    def accept(self): self.accepted = True
    def ignore(self): self.accepted = False


class RowDragTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt = QApplication.instance() or QApplication([])

    def setUp(self):
        self.table = RowsTable()
        self.table.resize(470, 230)
        self.table.setRowCount(5)
        for row in range(5):
            self.table.setItem(row, 4, QTableWidgetItem(f'row {row}'))
            self.table.setCellWidget(row, 0, QPushButton(f'Key {row}'))
        self.table.show()
        self.qt.processEvents()
        self.moves = []
        self.table.moved.connect(lambda a, b: self.moves.append((a, b)))

    def tearDown(self):
        self.table.finish_preview()
        self.table.close()
        self.table.deleteLater()
        self.qt.processEvents()

    def point(self, row):
        return QPoint(100, self.table.rowViewportPosition(row) + self.table.rowHeight(row)//2)

    def begin(self, row):
        self.assertTrue(self.table.begin_preview(row))
        self.table.drag_anchor = QPoint(100, self.table.rowHeight(row)//2)

    def test_hover_animates_both_directions_without_changing_rows(self):
        self.begin(0)
        table = self.table
        old_y = table.rowViewportPosition(1)
        table.hover_at(self.point(3))
        table.preview.animation.setCurrentTime(80)
        self.assertGreater(table.preview.positions[1], 0)
        self.assertLess(table.preview.positions[1], old_y)
        table.preview.animation.setCurrentTime(160)
        self.assertEqual(table.preview.positions[1], table.rowViewportPosition(0))
        self.assertEqual(table.preview.positions[0], table.rowViewportPosition(3))
        table.hover_at(self.point(1))
        table.preview.animation.setCurrentTime(160)
        self.assertEqual(table.preview.positions[0], table.rowViewportPosition(1))
        self.assertEqual(table.preview.positions[2], table.rowViewportPosition(2))
        self.assertEqual([table.item(i,4).text() for i in range(5)], [f'row {i}' for i in range(5)])
        self.assertEqual(self.moves, [])

    def test_drag_image_has_transparency_and_matches_device_scale(self):
        self.begin(1)
        image = self.table.drag_pixmap()
        self.assertEqual(image.devicePixelRatio(), self.table.preview.images[1].devicePixelRatio())
        alpha = image.toImage().pixelColor(4, 4).alpha()
        self.assertGreater(alpha, 100)
        self.assertLess(alpha, 220)

    def test_drop_commits_once_and_clears_preview(self):
        self.begin(4)
        event = Drop(self.table, self.point(1))
        self.table.dropEvent(event)
        self.assertTrue(event.accepted)
        self.assertEqual(self.moves, [(4,1)])
        self.assertEqual(self.table.drag_origin, -1)
        self.assertTrue(self.table.preview.isHidden())
        self.assertFalse(self.table.scroll_timer.isActive())
        self.table.dropEvent(event)
        self.assertEqual(self.moves, [(4,1)])

    def test_cancelled_native_drag_and_same_slot_do_not_commit(self):
        self.table.selectRow(1)
        self.table.drag_anchor = self.point(1)
        with patch('row_drag.QDrag') as drag:
            drag.return_value.exec.return_value = Qt.DropAction.IgnoreAction
            self.table.startDrag(Qt.DropAction.MoveAction)
        self.assertEqual(self.moves, [])
        self.assertTrue(self.table.preview.isHidden())
        self.begin(2)
        self.table.dropEvent(Drop(self.table, self.point(2)))
        self.assertEqual(self.moves, [])

    def test_edge_scroll_refreshes_rows_and_cancel_restores_view(self):
        self.table.setRowCount(40)
        for row in range(5,40):
            self.table.setItem(row, 4, QTableWidgetItem(str(row)))
        self.qt.processEvents()
        self.begin(0)
        self.table.hover_at(QPoint(100, self.table.viewport().height()-2))
        before = self.table.verticalScrollBar().value()
        self.table.scroll_drag()
        self.assertGreater(self.table.verticalScrollBar().value(), before)
        self.assertGreater(self.table.drag_target, 0)
        self.table.finish_preview()
        self.assertEqual(self.moves, [])
        self.assertTrue(self.table.cellWidget(0,0).isEnabled())

    def test_foreign_drop_and_model_reset_cannot_reorder(self):
        self.begin(1)
        event = Drop(self.table, self.point(3))
        event.table = None
        self.table.dropEvent(event)
        self.assertFalse(event.accepted)
        self.table.setRowCount(0)
        self.assertEqual(self.table.drag_origin, -1)
        self.assertTrue(self.table.preview.isHidden())
        self.assertEqual(self.moves, [])
