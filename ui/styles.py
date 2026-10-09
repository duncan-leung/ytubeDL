"""Stylesheets and theme definitions for ytubeDL."""

MODERN_STYLE = """
QMainWindow {
    background-color: #f7f8fa;
    color: #1e293b;
}

QWidget {
    font-family: "Helvetica Neue", Arial, sans-serif;
    font-size: 13px;
    color: #1e293b;
}

QGroupBox {
    font-weight: 600;
    border: 1px solid #e2e8f0;
    border-radius: 8px;
    margin-top: 12px;
    padding-top: 14px;
    background-color: #ffffff;
}

QGroupBox::title {
    subcontrol-origin: margin;
    left: 14px;
    padding: 0 6px;
    color: #334155;
}

QLineEdit {
    border: 1px solid #cbd5e1;
    border-radius: 6px;
    padding: 7px 10px;
    background-color: #ffffff;
    selection-background-color: #3b82f6;
}

QLineEdit:focus {
    border: 1.5px solid #3b82f6;
}

QPushButton {
    background-color: #ffffff;
    border: 1px solid #cbd5e1;
    border-radius: 6px;
    padding: 7px 14px;
    font-weight: 500;
    color: #1e293b;
}

QPushButton:hover {
    background-color: #f1f5f9;
    border-color: #94a3b8;
}

QPushButton:pressed {
    background-color: #e2e8f0;
}

QPushButton#btn_run {
    background-color: #10b981;
    border: 1px solid #059669;
    color: #ffffff;
    font-weight: 600;
}

QPushButton#btn_run:hover {
    background-color: #059669;
}

QPushButton#btn_stop {
    background-color: #ef4444;
    border: 1px solid #dc2626;
    color: #ffffff;
    font-weight: 600;
}

QPushButton#btn_stop:hover {
    background-color: #dc2626;
}

QPushButton#btn_add {
    background-color: #3b82f6;
    border: 1px solid #2563eb;
    color: #ffffff;
    font-weight: 600;
}

QPushButton#btn_add:hover {
    background-color: #2563eb;
}

QTableView {
    background-color: #ffffff;
    border: 1px solid #e2e8f0;
    border-radius: 6px;
    gridline-color: #f1f5f9;
    selection-background-color: #e0f2fe;
    selection-color: #0f172a;
    alternate-background-color: #fafbfc;
}

QHeaderView::section {
    background-color: #f8fafc;
    color: #475569;
    padding: 6px 8px;
    border: none;
    border-bottom: 1px solid #cbd5e1;
    font-weight: 600;
}

QProgressBar {
    border: 1px solid #cbd5e1;
    border-radius: 6px;
    text-align: center;
    background-color: #f1f5f9;
    height: 16px;
    font-size: 11px;
}

QProgressBar::chunk {
    background-color: #3b82f6;
    border-radius: 5px;
}

QPlainTextEdit#log_viewer {
    background-color: #0f172a;
    color: #38bdf8;
    font-family: Menlo, Monaco, Consolas, "Courier New", monospace;
    font-size: 11px;
    border: 1px solid #334155;
    border-radius: 6px;
    padding: 6px;
}

QStatusBar {
    background-color: #f8fafc;
    border-top: 1px solid #e2e8f0;
    color: #64748b;
}
"""
