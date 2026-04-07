import sys
import os
import logging
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QFileDialog, QMessageBox,
    QListWidget, QListWidgetItem
)
from PySide6.QtCore import Slot, QThread, Qt
from worker import GenerationWorker

logger = logging.getLogger(__name__)

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("回想法キューブ ビデオ生成")
        self.setGeometry(100, 100, 800, 600)
        self.thread = None
        self.worker = None
        self.input_parts = []
        
        self._setup_ui()

    def _setup_ui(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)

        self.setStyleSheet("""
            QWidget { font-size: 11pt; background-color: #f5f5f5; }
            QListWidget { background-color: white; border: 1px solid #ddd; border-radius: 5px; }
            QPushButton { background-color: #0078D4; color: white; padding: 10px; border: none; border-radius: 5px; font-weight: bold; }
            QPushButton:hover { background-color: #005A9E; }
            QPushButton#secondary { background-color: #666; }
            QLabel#statusLabel { color: #333; font-weight: bold; background: #e0e0e0; padding: 5px; border-radius: 3px; }
        """)

        main_layout.addWidget(QLabel("画像と対応するJSONファイルを選択してください:"))
        
        self.parts_list_widget = QListWidget()
        main_layout.addWidget(self.parts_list_widget)

        btn_layout = QHBoxLayout()
        add_image_btn = QPushButton("画像を追加...")
        add_image_btn.clicked.connect(self.add_image_part)
        btn_layout.addWidget(add_image_btn)

        set_json_btn = QPushButton("JSONファイルを選択...")
        set_json_btn.setObjectName("secondary")
        set_json_btn.clicked.connect(self.select_json_file)
        btn_layout.addWidget(set_json_btn)

        remove_btn = QPushButton("削除")
        remove_btn.setObjectName("secondary")
        remove_btn.clicked.connect(self.remove_selected)
        btn_layout.addWidget(remove_btn)
        main_layout.addLayout(btn_layout)
        
        # ボタンの文言を「動画を生成」に変更
        self.generate_button = QPushButton("動画を生成")
        self.generate_button.setFixedHeight(50)
        self.generate_button.clicked.connect(self.start_generation)
        main_layout.addWidget(self.generate_button)

        main_layout.addWidget(QLabel("ステータス:"))
        self.status_label = QLabel("準備完了")
        self.status_label.setObjectName("statusLabel")
        main_layout.addWidget(self.status_label)

    def update_list(self):
        self.parts_list_widget.clear()
        for p in self.input_parts:
            img = os.path.basename(p['image_path'])
            jsn = os.path.basename(p['json_path']) if p['json_path'] else "JSON未選択"
            self.parts_list_widget.addItem(f"画像: {img} / データ: {jsn}")

    @Slot()
    def add_image_part(self):
        files, _ = QFileDialog.getOpenFileNames(self, "画像を選択", "", "Images (*.png *.jpg *.jpeg)")
        if files:
            for f in files:
                self.input_parts.append({"image_path": f, "json_path": ""})
            self.update_list()

    @Slot()
    def select_json_file(self):
        row = self.parts_list_widget.currentRow()
        if row < 0:
            QMessageBox.warning(self, "注意", "JSONを紐付ける画像をリストから選択してください。")
            return
        
        file, _ = QFileDialog.getOpenFileName(self, "JSONファイルを選択", "", "JSON files (*.json)")
        if file:
            self.input_parts[row]["json_path"] = file
            self.update_list()

    @Slot()
    def remove_selected(self):
        row = self.parts_list_widget.currentRow()
        if 0 <= row < len(self.input_parts):
            self.input_parts.pop(row)
            self.update_list()

    @Slot()
    def start_generation(self):
        if not self.input_parts: return
        self.generate_button.setEnabled(False)
        self.status_label.setText("処理中...")

        self.thread = QThread()
        self.worker = GenerationWorker(self.input_parts)
        self.worker.moveToThread(self.thread)
        self.worker.signals.status_update.connect(self.status_label.setText)
        self.worker.signals.finished.connect(self.on_success)
        self.worker.signals.error.connect(self.on_fail)
        self.thread.started.connect(self.worker.run)
        self.thread.start()

    def on_success(self, msg):
        self.generate_button.setEnabled(True)
        QMessageBox.information(self, "完了", msg)
        self.cleanup()

    def on_fail(self, msg):
        self.generate_button.setEnabled(True)
        QMessageBox.critical(self, "エラー", msg)
        self.cleanup()

    def cleanup(self):
        if self.thread:
            self.thread.quit()
            self.thread.wait()