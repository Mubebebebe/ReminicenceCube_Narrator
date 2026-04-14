import sys, os
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, 
    QLabel, QPushButton, QFileDialog, QMessageBox, QListWidget, QCheckBox
)
from PySide6.QtCore import Slot, QThread
from worker import GenerationWorker

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("回想法キューブ ビデオ生成")
        self.setGeometry(100, 100, 850, 650)
        self.input_parts = []
        self.bgm_path = ""
        self._setup_ui()

    def _setup_ui(self):
        w = QWidget()
        self.setCentralWidget(w)
        layout = QVBoxLayout(w)

        self.setStyleSheet("""
            QMainWindow { background-color: #f0f2f5; }
            QWidget { font-family: 'Segoe UI', 'Meiryo'; font-size: 10pt; }
            QListWidget { background-color: white; border: 1px solid #dcdfe6; border-radius: 8px; padding: 5px; }
            QPushButton { background-color: #0078d4; color: white; border-radius: 6px; padding: 10px; font-weight: bold; border: none; }
            QPushButton:hover { background-color: #106ebe; }
            QPushButton#secondary { background-color: #606266; }
            QLabel#status { background-color: #ffffff; border: 1px solid #dcdfe6; padding: 10px; border-radius: 4px; color: #333; }
        """)

        layout.addWidget(QLabel("画像とJSONファイルを追加してください:"))
        self.parts_list = QListWidget()
        layout.addWidget(self.parts_list)
        
        btns = QHBoxLayout()
        add_img_btn = QPushButton("画像を追加...")
        add_img_btn.clicked.connect(self.add_image)
        btns.addWidget(add_img_btn)

        set_json_btn = QPushButton("JSONファイルを選択...")
        set_json_btn.setObjectName("secondary")
        set_json_btn.clicked.connect(self.select_json)
        btns.addWidget(set_json_btn)

        del_btn = QPushButton("削除")
        del_btn.setObjectName("secondary")
        del_btn.clicked.connect(self.remove_part)
        btns.addWidget(del_btn)
        layout.addLayout(btns)

        layout.addSpacing(10)
        layout.addWidget(QLabel("音声・BGM設定:"))
        bgm_row = QHBoxLayout()
        self.bgm_cb = QCheckBox("BGMを挿入する")
        bgm_row.addWidget(self.bgm_cb)
        
        sel_bgm_btn = QPushButton("BGMを選択...")
        sel_bgm_btn.setObjectName("secondary")
        sel_bgm_btn.clicked.connect(self.select_bgm)
        bgm_row.addWidget(sel_bgm_btn)
        
        self.bgm_label = QLabel("未選択")
        self.bgm_label.setStyleSheet("color: #909399;")
        bgm_row.addWidget(self.bgm_label, 1)
        layout.addLayout(bgm_row)

        layout.addSpacing(20)
        self.gen_btn = QPushButton("動画を生成")
        self.gen_btn.setFixedHeight(50)
        self.gen_btn.clicked.connect(self.start_process)
        layout.addWidget(self.gen_btn)

        self.status_label = QLabel("待機中")
        self.status_label.setObjectName("status")
        layout.addWidget(self.status_label)

    def add_image(self):
        fs, _ = QFileDialog.getOpenFileNames(self, "画像を選択", "", "Images (*.jpg *.jpeg *.png)")
        for f in fs: self.input_parts.append({"image_path": f, "json_path": ""})
        self.update_list()

    def select_json(self):
        row = self.parts_list.currentRow()
        if row < 0: return
        f, _ = QFileDialog.getOpenFileName(self, "JSONを選択", "", "JSON (*.json)")
        if f: 
            self.input_parts[row]["json_path"] = f
            self.update_list()

    def remove_part(self):
        row = self.parts_list.currentRow()
        if 0 <= row < len(self.input_parts): self.input_parts.pop(row)
        self.update_list()

    def select_bgm(self):
        f, _ = QFileDialog.getOpenFileName(self, "BGMを選択", "", "Audio (*.mp3 *.wav)")
        if f: 
            self.bgm_path = f
            self.bgm_label.setText(os.path.basename(f))
            self.bgm_cb.setChecked(True)

    def update_list(self):
        self.parts_list.clear()
        for p in self.input_parts:
            self.parts_list.addItem(f"画像: {os.path.basename(p['image_path'])} / データ: {os.path.basename(p['json_path']) if p['json_path'] else '未選択'}")

    def start_process(self):
        if not self.input_parts: return
        self.gen_btn.setEnabled(False)
        self.thread = QThread()
        self.worker = GenerationWorker(self.input_parts, self.bgm_cb.isChecked(), self.bgm_path)
        self.worker.moveToThread(self.thread)
        self.worker.signals.status_update.connect(self.status_label.setText)
        self.worker.signals.finished.connect(self.on_done)
        self.worker.signals.error.connect(self.on_done)
        self.thread.started.connect(self.worker.run)
        self.thread.start()

    def on_done(self, msg):
        self.gen_btn.setEnabled(True)
        QMessageBox.information(self, "完了", msg)
        self.thread.quit()
        self.thread.wait()