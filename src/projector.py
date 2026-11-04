from PyQt6.QtWidgets import QMainWindow, QWidget, QVBoxLayout, QLabel, QApplication
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont

class WindowProjector(QMainWindow):
    def __init__(self, app_title="TELÓ", config_proj=None):
        super().__init__()
        self.config_proj = config_proj or {}
        self.app_title = app_title
        
        self.labels = []
        self.init_ui()

    def init_ui(self):
        self.setWindowTitle(f"{self.app_title} - Projector")
        self.setStyleSheet("background-color: black;")
        
        self.central_widget = QWidget()
        self.setCentralWidget(self.central_widget)
        self.layout_proj = QVBoxLayout(self.central_widget)
        self.layout_proj.setContentsMargins(20, 20, 20, 20)
        self.layout_proj.setSpacing(10)

        # Mida i paràmetres inicials de config.yaml
        w = self.config_proj.get('width', 1000)
        h = self.config_proj.get('height', 400)
        self.resize(w, h)

    def actualitzar_text(self, *args):
        """
        Accepta:
          - actualitzar_text([text1, text2, text3, text4])  -> Llista dinàmica
          - actualitzar_text(text_sup, text_inf)            -> Compatibilitat anterior
        """
        if len(args) == 1 and isinstance(args[0], (list, tuple)):
            textos = [t for t in args[0] if t is not None]
        else:
            textos = [t for t in args if t]

        # Creem o eliminem QLabels dinàmicament segons quants idiomes hi ha
        while len(self.labels) < len(textos):
            lbl = QLabel()
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl.setWordWrap(True)
            self.layout_proj.addWidget(lbl)
            self.labels.append(lbl)

        while len(self.labels) > len(textos):
            lbl = self.labels.pop()
            self.layout_proj.removeWidget(lbl)
            lbl.deleteLater()

        # Ajustem la mida de la font si hi ha molts idiomes per evitar desbordaments
        font_base = self.config_proj.get('font_size', 38)
        font_family = self.config_proj.get('font_family', 'Arial')
        
        if len(textos) >= 4:
            font_size = max(18, int(font_base * 0.55))
        elif len(textos) == 3:
            font_size = max(22, int(font_base * 0.70))
        else:
            font_size = font_base

        colors_ordre = ["#00E676", "#29B6F6", "#FFCA28", "#FF80AB", "#FFFFFF"]

        for i, text in enumerate(textos):
            color = colors_ordre[i % len(colors_ordre)]
            self.labels[i].setText(text)
            self.labels[i].setFont(QFont(font_family, font_size, QFont.Weight.Bold))
            self.labels[i].setStyleSheet(f"color: {color}; background-color: transparent;")

    def blackout(self):
        for lbl in self.labels:
            lbl.setText("")

    def assignar_a_segona_pantalla(self):
        screens = QApplication.screens()
        if len(screens) > 1:
            segona = screens[1]
            self.windowHandle().setScreen(segona)
            self.setGeometry(segona.geometry())
            self.showFullScreen()
