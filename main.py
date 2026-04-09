import sys, logging
from PySide6.QtWidgets import QApplication
from gui import MainWindow
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())