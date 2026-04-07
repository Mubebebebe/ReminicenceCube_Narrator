import sys
import os
import logging
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QTextEdit, QFileDialog, QMessageBox,
    QListWidget, QListWidgetItem, QDialog, QDialogButtonBox
)
from PySide6.QtCore import Slot, QThread, Qt
from worker import GenerationWorker

logger = logging.getLogger(__name__)

class JSONInputDialog(QDialog):
    """回想法の記録（JSON）を入力するためのダイアログ"""
    def __init__(self, parent=None, current_json=""):
        super().__init__(parent)
        self.setWindowTitle("回想法記録（JSON）の編集")
        self.setMinimumWidth(600)
        self.setMinimumHeight(500)

        layout = QVBoxLayout(self)

        info_label = QLabel("回想法の記録をJSON形式で入力してください。")
        info_label.setStyleSheet("font-weight: bold; color: #555;")
        layout.addWidget(info_label)

        self.text_edit = QTextEdit()
        self.text_edit.setPlaceholderText('[{"face": "Front", "transcript": "...", "location": {"x":0, "y":0, "w":1, "h":1}}, ...]')
        self.text_edit.setPlainText(current_json)
        layout.addWidget(self.text_edit)
        
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def get_text(self):
        return self.text_edit.toPlainText().strip()

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("回想法キューブ ビデオ生成")
        self.setGeometry(100, 100, 800, 700)
        self.thread = None
        self.worker = None
        self.input_parts = []
        
        self._setup_ui()
        logger.info("MainWindowの初期化完了。")

    def _setup_ui(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)

        # スタイルシートの適用
        self.setStyleSheet("""
            QWidget { font-size: 11pt; background-color: #f5f5f5; }
            QListWidget { background-color: white; border: 1px solid #ddd; border-radius: 5px; padding: 5px; }
            QPushButton { background-color: #0078D4; color: white; padding: 10px; border: none; border-radius: 5px; font-weight: bold; }
            QPushButton:hover { background-color: #005A9E; }
            QPushButton#secondary { background-color: #666; }
            QLabel#statusLabel { color: #333; font-weight: bold; padding: 5px; background: #e0e0e0; border-radius: 3px; }
        """)

        # 1. 画像リストセクション
        main_layout.addWidget(QLabel("動画に使用する画像と回想データ:"))
        
        self.parts_list_widget = QListWidget()
        self.parts_list_widget.itemDoubleClicked.connect(self.edit_json_for_selected_item)
        main_layout.addWidget(self.parts_list_widget)

        list_buttons_layout = QHBoxLayout()
        add_image_button = QPushButton("画像を追加...")
        add_image_button.clicked.connect(self.add_image_part)
        list_buttons_layout.addWidget(add_image_button)

        remove_image_button = QPushButton("削除")
        remove_image_button.setObjectName("secondary")
        remove_image_button.clicked.connect(self.remove_selected_image_part)
        list_buttons_layout.addWidget(remove_image_button)
        
        edit_json_button = QPushButton("JSONデータを編集...")
        edit_json_button.setObjectName("secondary")
        edit_json_button.clicked.connect(self.edit_json_for_selected_item)
        list_buttons_layout.addWidget(edit_json_button)

        main_layout.addLayout(list_buttons_layout)
        
        # 2. 生成ボタンとステータス
        self.generate_button = QPushButton("感動的な動画を生成開始")
        self.generate_button.setFixedHeight(50)
        self.generate_button.clicked.connect(self.start_generation)
        main_layout.addWidget(self.generate_button)

        main_layout.addWidget(QLabel("ステータス:"))
        self.status_label = QLabel("準備完了")
        self.status_label.setObjectName("statusLabel")
        main_layout.addWidget(self.status_label)

    def update_list_display(self):
        self.parts_list_widget.clear()
        for idx, part in enumerate(self.input_parts):
            filename = os.path.basename(part['image_path'])
            item = QListWidgetItem(f"{idx+1}: {filename}")
            self.parts_list_widget.addItem(item)

    @Slot()
    def add_image_part(self):
        filenames, _ = QFileDialog.getOpenFileNames(self, "画像を選択", "", "Image files (*.png *.jpg *.jpeg)")
        if filenames:
            for f in filenames:
                # デフォルトの空JSONをセット
                self.input_parts.append({"image_path": f, "conversation_text": "[]"})
            self.update_list_display()
            self.status_label.setText(f"{len(filenames)}件追加しました。ダブルクリックでJSONを編集してください。")

    @Slot()
    def remove_selected_image_part(self):
        row = self.parts_list_widget.currentRow()
        if 0 <= row < len(self.input_parts):
            self.input_parts.pop(row)
            self.update_list_display()

    @Slot()
    def edit_json_for_selected_item(self):
        row = self.parts_list_widget.currentRow()
        if row < 0: return
        
        current_data = self.input_parts[row]
        dialog = JSONInputDialog(self, current_data["conversation_text"])
        if dialog.exec():
            new_json = dialog.get_text()
            # 簡易バリデーション
            try:
                import json
                json.loads(new_json)
                self.input_parts[row]["conversation_text"] = new_json
                self.status_label.setText("JSONデータを更新しました。")
            except:
                QMessageBox.warning(self, "エラー", "JSONの形式が正しくありません。")

    @Slot()
    def start_generation(self):
        if not self.input_parts:
            QMessageBox.warning(self, "エラー", "画像が追加されていません。")
            return

        self.generate_button.setEnabled(False)
        self.status_label.setText("ビデオ生成パイプラインを開始中...")

        # ワーカースレッドの構築
        self.thread = QThread()
        self.worker = GenerationWorker(self.input_parts)
        self.worker.moveToThread(self.thread)

        # シグナルの接続
        self.worker.signals.status_update.connect(self.status_label.setText)
        self.worker.signals.finished.connect(self.on_finished)
        self.worker.signals.error.connect(self.on_error)
        self.thread.started.connect(self.worker.run)
        
        self.thread.start()

    def on_finished(self, msg):
        self.generate_button.setEnabled(True)
        self.status_label.setText("完了")
        QMessageBox.information(self, "成功", msg)
        self.cleanup_thread()

    def on_error(self, err):
        self.generate_button.setEnabled(True)
        self.status_label.setText("エラーが発生しました")
        QMessageBox.critical(self, "エラー", f"生成中にエラーが発生しました:\n{err}")
        self.cleanup_thread()

    def cleanup_thread(self):
        if self.thread:
            self.thread.quit()
            self.thread.wait()
            self.thread = None