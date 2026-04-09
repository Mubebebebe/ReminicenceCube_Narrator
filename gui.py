import sys, os
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, 
    QLabel, QPushButton, QFileDialog, QMessageBox, QListWidget, QCheckBox
)
from PySide6.QtCore import Slot, QThread, Qt
from worker import GenerationWorker

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("回想法キューブ ビデオ生成")
        self.setGeometry(100, 100, 850, 650)
        self.parts = []
        self.bgm_path = ""
        self._ui()

    def _ui(self):
        w = QWidget()
        self.setCentralWidget(w)
        l = QVBoxLayout(w)

        # モダンなデザインの適用
        self.setStyleSheet("""
            QMainWindow { background-color: #f0f2f5; }
            QWidget { font-family: 'Segoe UI', 'Meiryo'; font-size: 10pt; }
            QListWidget { background-color: white; border: 1px solid #dcdfe6; border-radius: 8px; padding: 5px; }
            QPushButton { 
                background-color: #0078d4; color: white; border: none; 
                border-radius: 6px; padding: 8px 16px; font-weight: bold; 
            }
            QPushButton:hover { background-color: #106ebe; }
            QPushButton#secondary { background-color: #606266; }
            QPushButton#secondary:hover { background-color: #303133; }
            QCheckBox { spacing: 8px; }
            QLabel#status { background-color: #ffffff; border: 1px solid #dcdfe6; border-radius: 4px; padding: 8px; color: #303133; }
        """)

        l.addWidget(QLabel("1. 画像と回想法データ（JSON）のセット"))
        self.lv = QListWidget()
        l.addWidget(self.lv)
        
        row_list = QHBoxLayout()
        b_img = QPushButton("画像を追加")
        b_img.clicked.connect(self.add_i)
        row_list.addWidget(b_img)

        b_jsn = QPushButton("JSONを紐付け")
        b_jsn.setObjectName("secondary")
        b_jsn.clicked.connect(self.set_j)
        row_list.addWidget(b_jsn)

        b_del = QPushButton("削除")
        b_del.setObjectName("secondary")
        b_del.clicked.connect(self.rem_i)
        row_list.addWidget(b_del)
        l.addLayout(row_list)

        # BGM設定セクション
        l.addSpacing(10)
        l.addWidget(QLabel("2. 音楽（BGM）の設定"))
        bgm_box = QHBoxLayout()
        self.cb_bgm = QCheckBox("BGMを挿入する")
        bgm_box.addWidget(self.cb_bgm)
        
        b_bgm = QPushButton("BGMファイルを選択...")
        b_bgm.setObjectName("secondary")
        b_bgm.clicked.connect(self.sel_bgm)
        bgm_box.addWidget(b_bgm)
        
        self.lbl_bgm = QLabel("未選択")
        self.lbl_bgm.setStyleSheet("color: #909399;")
        bgm_box.addWidget(self.lbl_bgm, 1)
        l.addLayout(bgm_box)

        l.addSpacing(20)
        self.b_gen = QPushButton("動画を生成")
        self.b_gen.setMinimumHeight(50)
        self.b_gen.clicked.connect(self.start)
        l.addWidget(self.b_gen)

        self.st = QLabel("待機中")
        self.st.setObjectName("status")
        l.addWidget(self.st)

    def add_i(self):
        fs, _ = QFileDialog.getOpenFileNames(self, "画像", "", "Images (*.png *.jpg *.jpeg)")
        for f in fs: self.parts.append({"image_path": f, "json_path": ""})
        self.upd()

    def set_j(self):
        idx = self.lv.currentRow()
        if idx < 0: return
        f, _ = QFileDialog.getOpenFileName(self, "JSON選択", "", "JSON (*.json)")
        if f: self.parts[idx]["json_path"] = f
        self.upd()

    def rem_i(self):
        idx = self.lv.currentRow()
        if 0 <= idx < len(self.parts): self.parts.pop(idx)
        self.upd()

    def sel_bgm(self):
        f, _ = QFileDialog.getOpenFileName(self, "BGM選択", "", "Audio (*.mp3 *.wav *.m4a)")
        if f:
            self.bgm_path = f
            self.lbl_bgm.setText(os.path.basename(f))
            self.cb_bgm.setChecked(True)

    def upd(self):
        self.lv.clear()
        for p in self.parts:
            img = os.path.basename(p['image_path'])
            jsn = os.path.basename(p['json_path']) if p['json_path'] else "(JSON未選択)"
            self.lv.addItem(f"画像: {img} / データ: {jsn}")

    def start(self):
        if not self.parts: return
        self.b_gen.setEnabled(False)
        self.th = QThread()
        self.wk = GenerationWorker(self.parts, self.cb_bgm.isChecked(), self.bgm_path)
        self.wk.moveToThread(self.th)
        self.wk.signals.status_update.connect(self.st.setText)
        self.wk.signals.finished.connect(lambda m: self.end(m, True))
        self.wk.signals.error.connect(lambda m: self.end(m, False))
        self.th.started.connect(self.wk.run)
        self.th.start()

    def end(self, m, ok):
        self.b_gen.setEnabled(True)
        if ok: QMessageBox.information(self, "完了", m)
        else: QMessageBox.critical(self, "エラー", m)
        self.th.quit()
        self.th.wait()