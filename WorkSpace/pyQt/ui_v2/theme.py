"""Shared visual language for the separate POCO v2 interface."""
from pathlib import Path
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFont
from PyQt5.QtWidgets import QLabel, QPushButton, QFrame, QVBoxLayout

BLUE = "#3182F6"
STYLE = """
QWidget { font-family: 'Noto Sans CJK KR', 'Noto Sans', sans-serif;
    font-size: 13px; color: #333D4B; }
QMainWindow, QDialog, QWidget#shell { background: #F2F4F6; }
QFrame[role="card"] { background: white; border: 1px solid #E8EBEF; border-radius: 18px; }
QFrame[role="soft"] { background: #F9FAFB; border: none; border-radius: 12px; }
QLabel { background: transparent; border: none; padding: 0; }
QLabel[role="title"] { font-size: 23px; font-weight: 700; color: #191F28; }
QLabel[role="heading"] { font-size: 19px; font-weight: 700; color: #191F28; }
QLabel[role="section"] { font-size: 15px; font-weight: 700; color: #333D4B; }
QLabel[role="muted"] { color: #6B7684; font-size: 12px; }
QLabel[role="eyebrow"] { color: #3182F6; font-size: 11px; font-weight: 700; }
QLabel[role="badge"] { color: #3182F6; background: #E8F3FF; border-radius: 9px;
    padding: 4px 9px; font-size: 11px; font-weight: 600; }
QLabel[role="notice"] { color: #6B7684; background: #E8EBEF; border-radius: 10px;
    padding: 8px 12px; font-size: 12px; }
QPushButton { background: #E8EDF3; color: #4E5968; border: none;
    border-radius: 11px; padding: 8px 12px; font-weight: 600; min-height: 24px; }
QPushButton:hover { background: #DDE4EC; }
QPushButton:pressed { background: #CAD5E2; }
QPushButton:focus { border: 2px solid #90BFFF; padding: 6px 10px; }
QPushButton[role="primary"] { background: #3182F6; color: white; }
QPushButton[role="primary"]:hover { background: #1B64DA; }
QPushButton[role="primary"]:pressed { background: #1555BC; }
QPushButton[role="subtle"] { background: transparent; color: #6B7684; }
QPushButton[role="subtle"]:hover { background: #E8EBEF; }
QPushButton[role="danger"] { background: #FFF0F0; color: #D64141; }
QPushButton[role="danger"]:hover { background: #FFE0E0; }
QPushButton:disabled, QPushButton[role="primary"]:disabled {
    color: #9DA7B4; background: #EDF0F3; }
QPushButton[role="step"] { border-radius: 9px; background: #F2F4F6; color: #8B95A1;
    padding: 5px 8px; font-size: 12px; }
QPushButton[role="step"]:checked { background: #E8F3FF; color: #1B64DA; }
QPushButton[role="step"]:disabled { color: #B0B8C1; background: #F2F4F6; }
QPushButton[role="profile"] { text-align: left; background: white; border: 1px solid #E5E8EB; }
QPushButton[role="profile"]:checked { border: 2px solid #3182F6; background: #F2F8FF; color: #1B64DA; }
QPushButton#exitButton { font-size: 24px; font-weight: 400; background: #E8EBEF; padding: 0; }
QPushButton#exitButton:hover { background: #FFE0E0; color: #D64141; }
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {
    background: white; border: 1px solid #DDE3EA; border-radius: 9px;
    padding: 5px 8px; min-height: 26px; selection-background-color: #3182F6; }
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus { border: 1px solid #3182F6; }
QSpinBox::up-button, QDoubleSpinBox::up-button, QSpinBox::down-button, QDoubleSpinBox::down-button {
    width: 25px; }
QCheckBox { spacing: 9px; }
QCheckBox::indicator { width: 23px; height: 23px; border: 1px solid #CBD3DE; border-radius: 7px; background: white; }
QCheckBox::indicator:checked { background: #3182F6; border-color: #3182F6; image: url(__CHECK_SVG__); }
QProgressBar { border: none; border-radius: 7px; background: #E8EDF3;
    color: #4E5968; text-align: center; min-height: 14px; }
QProgressBar::chunk { background: #3182F6; border-radius: 7px; }
QSlider::groove:horizontal { height: 6px; background: #E8EDF3; border-radius: 3px; }
QSlider::sub-page:horizontal { background: #3182F6; border-radius: 3px; }
QSlider::handle:horizontal { background: white; border: 3px solid #3182F6;
    width: 18px; height: 18px; margin: -9px 0; border-radius: 12px; }
QScrollArea { background: transparent; border: none; }
QScrollBar:vertical { background: transparent; width: 7px; margin: 0; }
QScrollBar::handle:vertical { background: #CBD3DE; border-radius: 3px; min-height: 20px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QToolTip { color: #FFFFFF; background: #333D4B; border: none; padding: 7px; }
QMessageBox { background: white; }
"""
STYLE = STYLE.replace("__CHECK_SVG__", (Path(__file__).parent / "assets" / "check.svg").as_posix())


def role(widget, value):
    widget.setProperty("role", value)
    # Explicit local rules also override designer object-ID selectors after widgets
    # are moved out of the v1 tree (Qt retains some of those inherited selectors).
    text_styles = {
        "title": "font-size:23px;font-weight:700;color:#191F28;",
        "heading": "font-size:20px;font-weight:700;color:#191F28;",
        "section": "font-size:15px;font-weight:700;color:#333D4B;",
        "muted": "font-size:12px;color:#6B7684;font-weight:400;",
        "eyebrow": "font-size:11px;color:#3182F6;font-weight:700;",
        "badge": "font-size:11px;font-weight:600;color:#3182F6;background:#E8F3FF;border-radius:9px;padding:4px 9px;",
        "notice": "font-size:12px;color:#6B7684;background:#E8EBEF;border-radius:10px;padding:8px 12px;",
    }
    if isinstance(widget, QLabel):
        widget.setStyleSheet("QLabel{background:transparent;border:none;padding:0;" + text_styles.get(value, "") + "}")
    elif isinstance(widget, QPushButton):
        colors = {"primary": ("#3182F6", "white", "#1B64DA"),
                  "danger": ("#FFF0F0", "#D64141", "#FFE0E0"),
                  "subtle": ("transparent", "#6B7684", "#E8EBEF"),
                  "step": ("#F2F4F6", "#8B95A1", "#E8EDF3"),
                  "profile": ("white", "#333D4B", "#F2F8FF")}
        bg, fg, hover = colors.get(value, ("#E8EDF3", "#4E5968", "#DDE4EC"))
        height = int(widget.property("controlHeight") or 40)
        inner_height = max(0, height - 16)
        css = ("QPushButton{background:%s;color:%s;border:none;border-radius:11px;padding:8px 12px;min-height:%dpx;max-height:%dpx;font-size:13px;font-weight:600;}"
               "QPushButton:hover{background:%s;}QPushButton:focus{border:2px solid #90BFFF;padding:6px 10px;}"
               "QPushButton:disabled{background:#EDF0F3;color:#9DA7B4;}" % (bg, fg, inner_height, inner_height, hover))
        if value == "step":
            css += "QPushButton{font-size:12px;}QPushButton:checked{background:#E8F3FF;color:#1B64DA;}"
        if value == "profile":
            css += "QPushButton{text-align:left;border:1px solid #E5E8EB;}QPushButton:checked{border:2px solid #3182F6;background:#F2F8FF;color:#1B64DA;}"
        widget.setStyleSheet(css)
    elif isinstance(widget, QFrame):
        widget.setStyleSheet('QFrame[role="card"]{background:white;border:1px solid #E8EBEF;border-radius:18px;}' if value == "card" else
                            'QFrame[role="soft"]{background:#F9FAFB;border:none;border-radius:12px;}')
    widget.style().unpolish(widget)
    widget.style().polish(widget)
    return widget


def label(text="", kind="", wrap=False):
    item = QLabel(text)
    item.setWordWrap(wrap)
    item.setTextFormat(Qt.PlainText)
    role(item, kind)
    return item


def button(text, callback=None, kind="", height=40):
    item = QPushButton(text)
    item.setProperty("controlHeight", height)
    item.setMinimumHeight(height)
    item.setCursor(Qt.PointingHandCursor)
    item.setAutoDefault(False)
    role(item, kind)
    if callback:
        item.clicked.connect(callback)
    return item


def card(padding=16):
    item = QFrame()
    role(item, "card")
    layout = QVBoxLayout(item)
    layout.setContentsMargins(padding, padding, padding, padding)
    layout.setSpacing(9)
    return item, layout


def close_button(callback):
    item = button("×", callback, height=38)
    item.setObjectName("exitButton")
    item.setStyleSheet("QPushButton{font-size:24px;font-weight:400;background:#E8EBEF;color:#4E5968;border:none;border-radius:11px;padding:0;}QPushButton:hover{background:#FFE0E0;color:#D64141;}")
    item.setFixedSize(38, 38)
    item.setToolTip("창 닫기")
    item.setAccessibleName("창 닫기")
    return item


def apply_theme(app):
    app.setFont(QFont("Noto Sans CJK KR", 10))
    app.setStyleSheet(STYLE)
