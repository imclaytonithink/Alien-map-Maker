"""Building blocks that keep side panels readable at any width.

- ``WrapButton`` / ``WrapCheckBox``: the label wraps onto more lines instead
  of being cut off when the panel is narrow.
- ``FlowLayout``: a row of controls that continues on the next line when it
  runs out of room (like words in a paragraph); a row that fits looks like a
  ``QHBoxLayout``.
- ``FitFormLayout``: a form that puts its labels above the fields when labels
  and fields don't fit side by side.
"""
from __future__ import annotations

from PyQt6.QtCore import QEvent, QObject, QRect, QSize, Qt
from PyQt6.QtWidgets import (QAbstractSpinBox, QCheckBox, QComboBox, QFormLayout, QLabel,
                             QLayout, QPushButton, QSizePolicy, QStyle, QStyleOptionButton,
                             QWidget)

_GROW = QSizePolicy.PolicyFlag.GrowFlag.value


class FitFormLayout(QFormLayout):
    """A form whose labels move above the fields when the form is too narrow
    for labels and fields side by side (see ``_FormFit``), and whose fields use
    the whole width on every platform style.

    It also sidesteps a QFormLayout caching problem: the form keeps one table
    of row heights for wrapping rows but trusts it both for the last width it
    measured and for its own preferred width. Each parent layout pass asks the
    widest box for its height at exactly its preferred width, which refills the
    table, and a relayout at the real width then reuses rows measured for the
    preferred width - wrapped labels get one-line heights and are cut off.
    Reporting a preferred width one pixel wider keeps every question on the
    entry that matches the table."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.setRowWrapPolicy(QFormLayout.RowWrapPolicy.DontWrapRows)
        if parent is not None:
            _FormFit(self)

    def sizeHint(self) -> QSize:
        hint = super().sizeHint()
        return QSize(hint.width() + 1, hint.height())

    def minimumSize(self) -> QSize:
        """As narrow as the form gets with labels above the fields, so parents
        can make it that narrow (it then switches) instead of stopping at the
        width of labels and fields side by side."""
        base = super().minimumSize()
        widest = 0
        for row in range(self.rowCount()):
            for role in (QFormLayout.ItemRole.LabelRole, QFormLayout.ItemRole.FieldRole,
                         QFormLayout.ItemRole.SpanningRole):
                item = self.itemAt(row, role)
                if item is None or item.isEmpty():
                    continue
                widest = max(widest, item.minimumSize().width()
                             if role == QFormLayout.ItemRole.LabelRole else _field_need(item))
        margins = self.contentsMargins()
        return QSize(min(base.width(), widest + margins.left() + margins.right()),
                     base.height())


def _field_need(item) -> int:
    """Width a form field needs beside its label. Combo boxes and spin boxes
    cut their text off below their size hint; everything else here can shrink
    to its minimum (wrapping labels, flow rows, sliders, line edits...)."""
    widget = item.widget()
    if isinstance(widget, (QComboBox, QAbstractSpinBox)):
        return max(item.sizeHint().width(), item.minimumSize().width())
    return item.minimumSize().width()


class _FormFit(QObject):
    """Switches one form between labels beside fields and labels above them.
    Qt's own WrapLongRows judges each row by its own label, then places the
    field after the widest label, which squeezes fields; this decides for the
    whole form, from sizes that don't depend on the current arrangement, so
    it never flips back and forth."""

    _EVENTS = (QEvent.Type.Resize, QEvent.Type.LayoutRequest, QEvent.Type.Show,
               QEvent.Type.FontChange, QEvent.Type.StyleChange)

    def __init__(self, form: QFormLayout):
        box = form.parentWidget()
        super().__init__(box)
        self._form = form
        box.installEventFilter(self)

    def eventFilter(self, watched, event):
        if event.type() in self._EVENTS:
            self.fit(watched)
        return False

    def fit(self, box: QWidget):
        form = self._form
        margins = form.contentsMargins()
        room = box.contentsRect().width() - margins.left() - margins.right()
        labels = fields = 0
        for row in range(form.rowCount()):
            label = form.itemAt(row, QFormLayout.ItemRole.LabelRole)
            field = form.itemAt(row, QFormLayout.ItemRole.FieldRole)
            if label is None or field is None or label.isEmpty() or field.isEmpty():
                continue
            labels = max(labels, label.sizeHint().width())
            fields = max(fields, _field_need(field))
        spacing = max(0, form.horizontalSpacing())
        policy = (QFormLayout.RowWrapPolicy.WrapAllRows
                  if labels and room > 0 and labels + spacing + fields > room
                  else QFormLayout.RowWrapPolicy.DontWrapRows)
        if form.rowWrapPolicy() != policy:
            form.setRowWrapPolicy(policy)


class _WrapText:
    """Mixin for QPushButton / QCheckBox: word-wraps the label when the
    widget is narrower than the label on one line. ``text()`` returns the
    label as written; Qt paints a copy with line breaks."""

    _contents = QStyle.ContentsType.CT_PushButton

    def _init_wrap(self, text: str):
        self._full_text = text or ""
        policy = self.sizePolicy()
        policy.setHorizontalPolicy(QSizePolicy.Policy.Preferred)
        policy.setHeightForWidth(True)
        self.setSizePolicy(policy)
        super().setText(self._full_text)

    # -- the label as written ------------------------------------------------
    def text(self) -> str:
        return self._full_text

    def setText(self, text: str):
        self._full_text = text or ""
        self._apply_wrap(self.width())
        self.updateGeometry()

    # -- sizes -----------------------------------------------------------------
    def _box(self, text: str) -> QSize:
        """Size the style gives this widget for a (possibly multi-line) label."""
        option = QStyleOptionButton()
        self.initStyleOption(option)
        content = self.fontMetrics().size(Qt.TextFlag.TextShowMnemonic, text or "X")
        if not option.icon.isNull():
            content = QSize(content.width() + option.iconSize.width() + 4,
                            max(content.height(), option.iconSize.height()))
        return self.style().sizeFromContents(self._contents, option, content, self)

    def _wrapped(self, width: int) -> str:
        text = self._full_text
        if width <= 0 or not text or "\n" in text:
            return text
        metrics = self.fontMetrics()
        room = width - (self._box("X").width() - metrics.horizontalAdvance("X"))
        if metrics.horizontalAdvance(text) <= room:
            return text
        lines, line = [], ""
        for word in text.split(" "):
            trial = f"{line} {word}" if line else word
            if line and metrics.horizontalAdvance(trial) > room:
                lines.append(line)
                line = word
            else:
                line = trial
        lines.append(line)
        return "\n".join(lines)

    def _apply_wrap(self, width: int):
        shown = self._wrapped(width)
        if shown != super().text():
            super().setText(shown)

    def sizeHint(self) -> QSize:
        return QSize(self._box(self._full_text).width(),
                     self._box(super().text()).height())

    def minimumSizeHint(self) -> QSize:
        metrics = self.fontMetrics()
        longest = max(self._full_text.split() or [""], key=metrics.horizontalAdvance)
        return QSize(self._box(longest).width(), self._box(super().text()).height())

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        return self._box(self._wrapped(width)).height()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._apply_wrap(event.size().width())


class WrapButton(_WrapText, QPushButton):
    def __init__(self, text: str = "", parent=None):
        super().__init__(parent)
        self._init_wrap(text)


class WrapCheckBox(_WrapText, QCheckBox):
    _contents = QStyle.ContentsType.CT_CheckBox

    def __init__(self, text: str = "", parent=None):
        super().__init__(parent)
        self._init_wrap(text)


ZERO_WIDTH_SPACE = "\u200b"
_BREAK_AFTER = "_-/\\("


def breakable(text: str, run: int = 14) -> str:
    """Let a word-wrapped QLabel break long names (asset file names, paths) at
    underscores, dashes and slashes, and inside very long words, instead of
    running past the edge of a narrow panel. Only adds zero-width spaces."""
    out, since = [], 0
    for char in text or "":
        out.append(char)
        if char.isspace():
            since = 0
        elif char in _BREAK_AFTER:
            out.append(ZERO_WIDTH_SPACE)
            since = 0
        else:
            since += 1
            if since >= run:
                out.append(ZERO_WIDTH_SPACE)
                since = 0
    return "".join(out)


def fixed_label(text: str) -> QLabel:
    """A small label (like the × between two sizes) that never stretches."""
    label = QLabel(text)
    label.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)
    return label


class FlowLayout(QLayout):
    """Controls in a row that continues on the next line when it runs out of
    room. Spare room on a line goes to the controls that can grow, so a row
    that fits looks like a ``QHBoxLayout``. ``add(widget, stick=True)`` keeps
    a control on the same line as the one before it (``30 × 30``)."""

    def __init__(self, parent=None, spacing: int = 6):
        super().__init__(parent)
        self._items = []
        self._stick: set[int] = set()       # ids of items glued to the previous one
        self._gap = spacing
        self.setContentsMargins(0, 0, 0, 0)

    # -- QLayout plumbing --------------------------------------------------------
    def add(self, widget: QWidget, stick: bool = False) -> QWidget:
        self.addWidget(widget)
        if stick and self._items:
            self._stick.add(id(self._items[-1]))
        return widget

    def addItem(self, item):
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int):
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index: int):
        if 0 <= index < len(self._items):
            item = self._items.pop(index)
            self._stick.discard(id(item))
            return item
        return None

    def expandingDirections(self):
        return Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        return self._arrange(QRect(0, 0, width, 0), apply=False)

    def setGeometry(self, rect: QRect):
        super().setGeometry(rect)
        self._arrange(rect, apply=True)

    def _visible(self):
        return [item for item in self._items if not item.isEmpty()]

    def sizeHint(self) -> QSize:
        items = self._visible()
        width = sum(item.sizeHint().width() for item in items) + \
            self._gap * max(0, len(items) - 1)
        height = max((item.sizeHint().height() for item in items), default=0)
        margins = self.contentsMargins()
        return QSize(width + margins.left() + margins.right(),
                     height + margins.top() + margins.bottom())

    def minimumSize(self) -> QSize:
        width = height = 0
        for unit in self._units():
            width = max(width, sum(item.minimumSize().width() for item in unit)
                        + self._gap * (len(unit) - 1))
            height = max([height] + [item.minimumSize().height() for item in unit])
        margins = self.contentsMargins()
        return QSize(width + margins.left() + margins.right(),
                     height + margins.top() + margins.bottom())

    # -- arranging ---------------------------------------------------------------
    def _units(self):
        units = []
        for item in self._visible():
            if units and id(item) in self._stick:
                units[-1].append(item)
            else:
                units.append([item])
        return units

    @staticmethod
    def _can_grow(item) -> bool:
        widget = item.widget()
        if widget is None:
            return bool(item.expandingDirections() & Qt.Orientation.Horizontal)
        return bool(widget.sizePolicy().horizontalPolicy().value & _GROW)

    @staticmethod
    def _height(item, width: int) -> int:
        if item.hasHeightForWidth():
            height = item.heightForWidth(width)
        else:
            height = item.sizeHint().height()
        return max(height, item.minimumSize().height())

    def _arrange(self, rect: QRect, apply: bool) -> int:
        margins = self.contentsMargins()
        area = rect.adjusted(margins.left(), margins.top(),
                             -margins.right(), -margins.bottom())
        room = max(1, area.width())
        lines, line, used = [], [], 0
        for unit in self._units():
            sizes = [[item, max(min(item.sizeHint().width(), room),
                                min(item.minimumSize().width(), room))] for item in unit]
            width = sum(size for _item, size in sizes) + self._gap * (len(sizes) - 1)
            if line and used + self._gap + width > room:
                lines.append(line)
                line, used = [], 0
            used += (self._gap if line else 0) + width
            line.extend(sizes)
        if line:
            lines.append(line)

        y = area.y()
        for index, entries in enumerate(lines):
            spare = room - sum(size for _item, size in entries) - self._gap * (len(entries) - 1)
            growers = [entry for entry in entries if self._can_grow(entry[0])
                       and entry[0].maximumSize().width() > entry[1]]
            while spare > 0 and growers:
                share, extra = divmod(spare, len(growers))
                still = []
                for position, entry in enumerate(growers):
                    want = share + (1 if position < extra else 0)
                    room_left = entry[0].maximumSize().width() - entry[1]
                    add = min(want, room_left)
                    entry[1] += add
                    spare -= add
                    if room_left > add:
                        still.append(entry)
                growers = still             # capped controls drop out; the rest share again
            height = max(self._height(item, size) for item, size in entries)
            if apply:
                x = area.x()
                for item, size in entries:
                    item_height = min(self._height(item, size), height)
                    item.setGeometry(QRect(x, y + (height - item_height) // 2,
                                           size, item_height))
                    x += size + self._gap
            y += height
            if index < len(lines) - 1:
                y += self._gap
        return y - rect.y() + margins.bottom()
