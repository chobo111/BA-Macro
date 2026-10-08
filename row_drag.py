"""Animated row previews; the model changes only after an accepted drop."""
from PySide6.QtCore import Qt, Signal, QPoint, QRect, QMimeData, QTimer, QVariantAnimation, QEasingCurve
from PySide6.QtGui import QDrag, QPainter, QPixmap, QColor, QPen, QRegion
from PySide6.QtWidgets import QTableWidget, QWidget, QAbstractItemView, QStyle, QStyleOptionViewItem


class RowPreview(QWidget):
    def __init__(self, table):
        super().__init__(table.viewport())
        self.table = table
        self.images = {}
        self.positions = {}
        self.starts = {}
        self.ends = {}
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.animation = QVariantAnimation(self)
        self.animation.setDuration(160)
        self.animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.animation.valueChanged.connect(self.advance)
        self.hide()

    def advance(self, progress):
        self.positions = {row: self.starts.get(row, y) + (y-self.starts.get(row, y))*progress
                          for row, y in self.ends.items()}
        self.update()

    def layout_rows(self, animate=True):
        table = self.table
        order = list(range(table.rowCount()))
        order.insert(table.drag_target, order.pop(table.drag_origin))
        y, ends = table.rowViewportPosition(0), {}
        for row in order:
            ends[row] = y
            y += table.rowHeight(row)
        self.animation.stop()
        self.starts, self.ends = dict(self.positions), ends
        if animate:
            self.animation.setStartValue(0.0)
            self.animation.setEndValue(1.0)
            self.animation.start()
        else:
            self.positions = dict(ends)
            self.update()

    def render_row(self, row):
        """Render complete cells independently of the viewport's clipping region."""
        table = self.table
        ratio, height = table.devicePixelRatioF(), table.rowHeight(row)
        pixmap = QPixmap(round(self.width()*ratio), round(height*ratio))
        pixmap.setDevicePixelRatio(ratio)
        pixmap.fill(table.palette().base().color())
        painter = QPainter(pixmap)
        row_y, covered = table.rowViewportPosition(row), set()
        for column in range(table.columnCount()):
            if column in covered or table.isColumnHidden(column):
                continue
            covered.update(range(column+1, column+table.columnSpan(row, column)))
            index = table.model().index(row, column)
            rect = table.visualRect(index).translated(0, -row_y)
            option = QStyleOptionViewItem()
            table.initViewItemOption(option)
            option.rect, option.index = rect, index
            if table.alternatingRowColors() and row % 2:
                option.features |= QStyleOptionViewItem.ViewItemFeature.Alternate
            if table.selectionModel().isSelected(index):
                option.state |= QStyle.StateFlag.State_Selected
            if table.currentIndex() == index and table.hasFocus():
                option.state |= QStyle.StateFlag.State_HasFocus
            if not index.flags() & Qt.ItemFlag.ItemIsEnabled:
                option.state &= ~QStyle.StateFlag.State_Enabled
            table.itemDelegateForIndex(index).paint(painter, option, index)
            widget = table.cellWidget(row, column)
            if widget is not None:
                painter.save()
                painter.setClipRect(rect)
                widget.render(painter, widget.pos()-QPoint(0, row_y), QRegion(),
                              QWidget.RenderFlag.DrawWindowBackground | QWidget.RenderFlag.DrawChildren)
                painter.restore()
        painter.end()
        return pixmap

    def capture(self):
        self.hide()
        table = self.table
        self.setGeometry(table.viewport().rect())
        # Moving one row shifts its neighbours by at most that row's height.
        # Include this margin so rows just outside the viewport can animate in.
        margin = table.rowHeight(table.drag_origin)
        visible = self.rect().adjusted(0, -margin, 0, margin)
        self.images.clear()
        for row in range(table.rowCount()):
            area = QRect(0, table.rowViewportPosition(row), self.width(), table.rowHeight(row))
            if not table.isRowHidden(row) and area.intersects(visible):
                self.images[row] = self.render_row(row)
        self.show()
        self.raise_()

    def paintEvent(self, event):
        table = self.table
        painter = QPainter(self)
        painter.fillRect(self.rect(), table.palette().base())
        for row, image in self.images.items():
            if row != table.drag_origin:
                painter.drawPixmap(0, round(self.positions.get(row, table.rowViewportPosition(row))), image)
        if table.drag_origin in self.positions:
            y = round(self.positions[table.drag_origin])
            gap = QRect(2, y+2, self.width()-4, table.rowHeight(table.drag_origin)-4)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            accent = table.palette().link().color()
            painter.setPen(QPen(accent, 1, Qt.PenStyle.DashLine))
            fill = QColor(accent)
            fill.setAlpha(24)
            painter.setBrush(fill)
            painter.drawRoundedRect(gap, 5, 5)


class AnimatedRowsTable(QTableWidget):
    moved = Signal(int, int)
    MIME = 'application/x-blue-archive-studio-row'

    def __init__(self, rows, columns):
        super().__init__(rows, columns)
        self.drag_origin = self.drag_target = -1
        self.drag_position = None
        self.drag_anchor = QPoint()
        self.preview = RowPreview(self)
        self.scroll_timer = QTimer(self)
        self.scroll_timer.setInterval(60)
        self.scroll_timer.timeout.connect(self.scroll_drag)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.setDropIndicatorShown(False)
        self.model().modelAboutToBeReset.connect(self.finish_preview)
        self.model().rowsAboutToBeRemoved.connect(self.finish_preview)
        self.model().rowsAboutToBeInserted.connect(self.finish_preview)

    def mousePressEvent(self, event):
        self.drag_anchor = event.position().toPoint()
        super().mousePressEvent(event)

    def begin_preview(self, origin):
        self.finish_preview()
        if not 0 <= origin < self.rowCount():
            return False
        self.drag_origin = self.drag_target = origin
        self.preview.positions = {row: self.rowViewportPosition(row) for row in range(self.rowCount())}
        self.preview.capture()
        if origin not in self.preview.images:
            self.finish_preview()
            return False
        self.preview.layout_rows(False)
        return True

    def drag_pixmap(self):
        source = self.preview.images[self.drag_origin]
        pixmap = QPixmap(source.size())
        pixmap.setDevicePixelRatio(source.devicePixelRatio())
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setOpacity(.68)
        painter.drawPixmap(0, 0, source)
        painter.end()
        return pixmap

    def startDrag(self, supported_actions):
        origin = self.currentRow()
        if not self.begin_preview(origin):
            return
        drag = QDrag(self)
        try:
            mime = QMimeData()
            mime.setData(self.MIME, b'row')
            drag.setMimeData(mime)
            drag.setPixmap(self.drag_pixmap())
            self.drag_anchor = QPoint(max(0, min(self.drag_anchor.x(), self.viewport().width()-1)),
                                      max(0, min(self.drag_anchor.y()-self.rowViewportPosition(origin),
                                                 self.rowHeight(origin)-1)))
            drag.setHotSpot(self.drag_anchor)
            drag.exec(Qt.DropAction.MoveAction)
        finally:
            self.finish_preview()
            drag.deleteLater()

    def accepts_drag(self, event):
        return (event.source() is self and event.mimeData().hasFormat(self.MIME)
                and 0 <= self.drag_origin < self.rowCount())

    def dragEnterEvent(self, event):
        if self.accepts_drag(event):
            self.preview.show()
            event.setDropAction(Qt.DropAction.MoveAction)
            event.accept()
        else:
            event.ignore()

    def hover_at(self, position):
        self.drag_position = position
        y = position.y() - self.drag_anchor.y() + self.rowHeight(self.drag_origin)//2
        target = self.rowAt(y)
        if target < 0:
            target = 0 if y < 0 else self.rowCount()-1
        if target != self.drag_target:
            self.drag_target = target
            self.preview.layout_rows()

    def dragMoveEvent(self, event):
        if not self.accepts_drag(event):
            event.ignore()
            return
        self.hover_at(event.position().toPoint())
        if not self.scroll_timer.isActive():
            self.scroll_timer.start()
        event.setDropAction(Qt.DropAction.MoveAction)
        event.accept()

    def dragLeaveEvent(self, event):
        self.scroll_timer.stop()
        self.drag_position = None
        self.preview.hide()
        event.accept()

    def scroll_drag(self):
        if self.drag_origin < 0 or self.drag_position is None:
            return
        y, height = self.drag_position.y(), self.viewport().height()
        margin = min(28, height//4)
        direction = -1 if y < margin else 1 if y >= height-margin else 0
        if direction:
            bar = self.verticalScrollBar()
            bar.setValue(bar.value() + direction*bar.singleStep())
            self.hover_at(self.drag_position)

    def scrollContentsBy(self, dx, dy):
        super().scrollContentsBy(dx, dy)
        if hasattr(self, 'preview') and self.drag_origin >= 0:
            self.preview.capture()
            self.preview.layout_rows(False)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, 'preview') and self.drag_origin >= 0:
            self.preview.capture()
            self.preview.layout_rows(False)

    def dropEvent(self, event):
        if not self.accepts_drag(event):
            event.ignore()
            return
        self.hover_at(event.position().toPoint())
        origin, target = self.drag_origin, self.drag_target
        self.finish_preview()
        event.setDropAction(Qt.DropAction.MoveAction)
        event.accept()
        if origin != target:
            self.moved.emit(origin, target)

    def finish_preview(self, *_):
        self.scroll_timer.stop()
        self.preview.animation.stop()
        self.preview.hide()
        self.preview.images.clear()
        self.preview.positions.clear()
        self.drag_origin = self.drag_target = -1
        self.drag_position = None
        self.viewport().update()
