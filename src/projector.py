from PyQt6.QtWidgets import QWidget, QVBoxLayout, QTextEdit, QFrame, QApplication
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont, QTextCursor

class WindowProjector(QWidget):
    """
    Finestra del Projector HDMI compartida per teloAuto i teloManual,
    amb suport per a estils independents entre Original i Traducció.
    """
    def __init__(self, app_title="TELÓ", config_proj=None):
        super().__init__()
        cfg = config_proj or {}
        
        self.font_family_orig = cfg.get('font_family', 'Arial').split(',')[0].strip()
        self.font_size_orig = int(cfg.get('font_size', 38))
        self.font_color_orig = cfg.get('font_color', cfg.get('color_text', '#00E676'))
        
        self.font_family_trans = cfg.get('font_family', 'Arial').split(',')[0].strip()
        self.font_size_trans = int(cfg.get('font_size', 38))
        self.font_color_trans = '#29B6F6'

        self.bg_color = cfg.get('background_color', cfg.get('color_fons', '#000000'))
        self.max_lines = cfg.get('max_lines', 3)
        self.alignment_str = str(cfg.get('alignment', 'center')).lower()
        self.display_channel = str(cfg.get('display_channel', 'both')).lower()
        
        width = cfg.get('width', 1000)
        height = cfg.get('height', 400)

        self.setWindowTitle(f"{app_title} - Projector HDMI")
        self.resize(width, height)
        self.setStyleSheet(f"background-color: {self.bg_color};")
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(10)

        # 1. Subpantalla Superior (Original)
        self.text_orig = QTextEdit()
        self.configurar_text_edit(self.text_orig, self.font_family_orig, self.font_size_orig, self.font_color_orig)

        # 2. Línia divisòria central
        self.divider = QFrame()
        self.divider.setFrameShape(QFrame.Shape.HLine)
        self.divider.setFrameShadow(QFrame.Shadow.Sunken)
        self.divider.setStyleSheet("background-color: #555555; border: none; min-height: 2px; max-height: 2px;")

        # 3. Subpantalla Inferior (Traducció)
        self.text_trans = QTextEdit()
        self.configurar_text_edit(self.text_trans, self.font_family_trans, self.font_size_trans, self.font_color_trans)

        layout.addWidget(self.text_orig, stretch=1)
        layout.addWidget(self.divider)
        layout.addWidget(self.text_trans, stretch=1)

        self.actualitzar_mode_visualitzacio(self.display_channel)

    def configurar_text_edit(self, widget, family, size, color):
        widget.setReadOnly(True)
        widget.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        widget.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        font = QFont(family, int(size), QFont.Weight.Bold)
        widget.setFont(font)
        widget.setStyleSheet(f"""
            QTextEdit {{
                background-color: {self.bg_color};
                color: {color};
                border: none;
            }}
        """)

    def actualitzar_estils_hdmi(self, target='both', font_family=None, font_size=None, font_color=None, bg_color=None):
        """Aplica canvis d'estil específics per a Original, Traducció o Fons General."""
        if bg_color:
            self.bg_color = bg_color
            self.setStyleSheet(f"background-color: {self.bg_color};")

        if target in ['orig', 'both']:
            if font_family: self.font_family_orig = font_family
            if font_size: self.font_size_orig = int(font_size)
            if font_color: self.font_color_orig = font_color
            self.configurar_text_edit(self.text_orig, self.font_family_orig, self.font_size_orig, self.font_color_orig)

        if target in ['trans', 'both']:
            if font_family: self.font_family_trans = font_family
            if font_size: self.font_size_trans = int(font_size)
            if font_color: self.font_color_trans = font_color
            self.configurar_text_edit(self.text_trans, self.font_family_trans, self.font_size_trans, self.font_color_trans)

    def _obtenir_alineacio(self):
        if self.alignment_str == 'right':
            return Qt.AlignmentFlag.AlignRight
        elif self.alignment_str == 'left':
            return Qt.AlignmentFlag.AlignLeft
        return Qt.AlignmentFlag.AlignCenter

    def actualitzar_mode_visualitzacio(self, channel):
        self.display_channel = channel.lower()
        if self.display_channel == 'both':
            self.text_orig.setVisible(True)
            self.divider.setVisible(True)
            self.text_trans.setVisible(True)
        elif self.display_channel == 'original':
            self.text_orig.setVisible(True)
            self.divider.setVisible(False)
            self.text_trans.setVisible(False)
        elif self.display_channel == 'translation':
            self.text_orig.setVisible(False)
            self.divider.setVisible(False)
            self.text_trans.setVisible(True)

    def _format_text_lines(self, text):
        if not text:
            return ""
        linies = text.strip().split("\n")
        if len(linies) > self.max_lines:
            linies = linies[-self.max_lines:]
        return "\n".join(linies)

    def actualitzar_text(self, text_orig, text_trans=""):
        if not text_orig.strip() and not text_trans.strip():
            self.blackout()
            return

        if self.display_channel in ['both', 'original']:
            fmt_orig = self._format_text_lines(text_orig)
            self.text_orig.setPlainText(fmt_orig)
            self.text_orig.selectAll()
            self.text_orig.setAlignment(self._obtenir_alineacio())
            cursor = self.text_orig.textCursor()
            cursor.movePosition(QTextCursor.MoveOperation.End)
            self.text_orig.setTextCursor(cursor)

        if self.display_channel in ['both', 'translation']:
            fmt_trans = self._format_text_lines(text_trans)
            self.text_trans.setPlainText(fmt_trans)
            self.text_trans.selectAll()
            self.text_trans.setAlignment(self._obtenir_alineacio())
            cursor = self.text_trans.textCursor()
            cursor.movePosition(QTextCursor.MoveOperation.End)
            self.text_trans.setTextCursor(cursor)

    def blackout(self):
        self.text_orig.clear()
        self.text_trans.clear()

    def assignar_a_segona_pantalla(self):
        try:
            screens = QApplication.screens()
            if len(screens) > 1:
                second_screen = screens[1]
                self.showNormal()
                screen_geometry = second_screen.geometry()
                self.move(screen_geometry.x(), screen_geometry.y())
                if self.windowHandle():
                    self.windowHandle().setScreen(second_screen)
                self.showFullScreen()
            else:
                self.hide()
        except Exception as e:
            print(f"Error assignant segona pantalla: {e}")

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.showNormal()
        else:
            super().keyPressEvent(event)
