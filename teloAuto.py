import sys
import os
import time
import yaml
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, 
    QPushButton, QLabel, QTextEdit, QComboBox, QGroupBox, QMessageBox, QCheckBox, QColorDialog, QTabWidget
)
from PyQt6.QtCore import Qt, QTimer, QThread, pyqtSignal
from PyQt6.QtGui import QFont, QTextCursor

# Directoris del projecte
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SRC_DIR = os.path.join(BASE_DIR, 'src')
CONFIG_YAML_PATH = os.path.join(BASE_DIR, 'configTelo.yaml')

if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

# Importació de mòduls des de src/
try:
    from emissor import EmissorWeb
except ImportError as e:
    print(f"⚠️ Alerta en importar emissor des de src/: {e}")
    EmissorWeb = None

try:
    from TranscriptorWhisperLiveKit import TranscriptorWhisperLiveKit
except ImportError as e:
    print(f"⚠️ Error en importar TranscriptorWhisperLiveKit des de src/: {e}")
    TranscriptorWhisperLiveKit = None

try:
    from projector import WindowProjector
    from utils import ms_to_time_str
except ImportError as e:
    print(f"⚠️ Error en importar mòduls compartits des de src/: {e}")
    WindowProjector = None
    ms_to_time_str = None


def carregar_configuracio_yaml():
    if os.path.exists(CONFIG_YAML_PATH):
        try:
            with open(CONFIG_YAML_PATH, 'r', encoding='utf-8') as f:
                return yaml.safe_load(f)
        except Exception as e:
            print(f"⚠️ Error llegint {CONFIG_YAML_PATH}: {e}")
    return {}


# Càrrega de Seccions
CONFIG_FULL = carregar_configuracio_yaml()
CONFIG_COMMON = CONFIG_FULL.get('common', {})
CONFIG_AUTO = CONFIG_FULL.get('auto', {})
STYLES_CFG = CONFIG_COMMON.get('styles', {})

CONFIG_SERVER = CONFIG_COMMON.get('server', {'host': '0.0.0.0', 'port': 5000})
CONFIG_SERVER['channels'] = [
    {'id': 'auto_original', 'label': 'Transcripció (Original)'},
    {'id': 'auto_translation', 'label': 'Traducció en Directe'}
]


# --- FIL ASÍNCRON D'ÀUDIO I TRANSCRIPCIÓ ---
class AudioThread(QThread):
    subtitol_generat = pyqtSignal(dict)
    estat_canviat = pyqtSignal(str)

    def __init__(self, transcriptor):
        super().__init__()
        self.transcriptor = transcriptor
        self.running = False

    def run(self):
        self.running = True
        self.estat_canviat.emit("Connectant al servidor WhisperLiveKit...")
        
        def callback_resultat(dades, linies=None):
            if self.running and dades:
                if isinstance(dades, str):
                    dades = {"original": dades, "translation": ""}
                self.subtitol_generat.emit(dades)

        try:
            self.transcriptor.iniciar_escolta(callback_resultat=callback_resultat)
            self.estat_canviat.emit("Escoltant en directe...")
            
            while self.running and getattr(self.transcriptor, 'is_listening', True):
                time.sleep(0.1)
        except Exception as e:
            self.estat_canviat.emit(f"Error en la transcripció: {e}")

    def stop(self):
        self.running = False
        if self.transcriptor and hasattr(self.transcriptor, 'aturar_escolta'):
            self.transcriptor.aturar_escolta()
        self.estat_canviat.emit("Aturat")


# --- INTERFÍCIE PRINCIPAL PYQT6 ---
class TeloAutoPyQt(QMainWindow):
    def __init__(self, emissor):
        super().__init__()
        self.emissor = emissor
        self.session_started = False
        self.is_paused = False
        self.is_blackout = False
        
        interface_cfg = CONFIG_COMMON.get('interface', {})
        self.app_title = interface_cfg.get('title', 'TeloAuto')
        
        colors_cfg = interface_cfg.get('colors', {})
        self.color_bg = colors_cfg.get('background', '#121212')
        self.color_text = colors_cfg.get('text', '#E0E0E0')
        self.color_primary = colors_cfg.get('primary', '#00E676')
        self.color_secondary = colors_cfg.get('secondary', '#29B6F6')
        self.color_panel_bg = colors_cfg.get('panel_bg', '#1E1E1E')
        self.color_btn_bg = colors_cfg.get('button_bg', '#252525')
        self.color_btn_border = colors_cfg.get('button_border', '#444444')

        self.projector_cfg = STYLES_CFG.get('hdmi', {})
        self.projector_cfg['display_channel'] = CONFIG_AUTO.get('projector_display_channel', 'both')
        self.languages_cfg = CONFIG_COMMON.get('languages', {'ca': 'Català', 'es': 'Español'})
        self.translation_cfg = CONFIG_AUTO.get('translation', {})

        self.total_elapsed_ms = 0
        self.sub_elapsed_ms = 0

        self.timer = QTimer(self)
        self.timer.setInterval(100)
        self.timer.timeout.connect(self.update_timers)

        if TranscriptorWhisperLiveKit is not None:
            self.transcriptor = TranscriptorWhisperLiveKit(config_path=CONFIG_YAML_PATH)
            try:
                self.transcriptor.inicialitzar_motor()
            except Exception as e:
                print(f"⚠️ Alerta en inicialitzar el motor del transcriptor: {e}")
        else:
            self.transcriptor = None

        self.audio_thread = None
        self.projector_window = WindowProjector(app_title=self.app_title, config_proj=self.projector_cfg)

        self.init_ui()
        self.detectar_segona_pantalla()

    def init_ui(self):
        self.setWindowTitle(f"{self.app_title}")
        self.resize(1150, 960)
        
        self.setStyleSheet(f"""
            QMainWindow {{ background-color: {self.color_bg}; }}
            QWidget {{ background-color: {self.color_bg}; color: {self.color_text}; font-family: 'Segoe UI', Arial; font-size: 10pt; }}
            
            QGroupBox {{ 
                border: 1px solid {self.color_btn_border}; 
                border-radius: 6px; 
                margin-top: 16px; 
                font-weight: bold;
            }}
            QGroupBox::title {{ 
                subcontrol-origin: margin; 
                subcontrol-position: top left;
                left: 12px; top: 0px; padding: 0 5px; 
                color: {self.color_primary}; 
                background-color: {self.color_bg};
            }}

            QPushButton {{ 
                background-color: {self.color_btn_bg}; 
                border: 1px solid {self.color_btn_border}; 
                border-radius: 4px; padding: 6px 14px; 
                font-weight: bold; min-height: 20px;
            }}
            QPushButton:hover {{ background-color: {self.color_btn_border}; }}

            QTextEdit {{ background-color: {self.color_panel_bg}; border: 1px solid {self.color_btn_border}; color: {self.color_text}; font-family: monospace; }}
            QComboBox {{ background-color: {self.color_btn_bg}; border: 1px solid {self.color_btn_border}; padding: 4px 8px; border-radius: 4px; }}
            QCheckBox {{ color: {self.color_text}; font-weight: bold; }}
            QTabWidget::pane {{ border: 1px solid {self.color_btn_border}; border-radius: 4px; }}
            QTabBar::tab {{ background: {self.color_btn_bg}; padding: 6px 16px; margin-right: 2px; border-top-left-radius: 4px; border-top-right-radius: 4px; }}
            QTabBar::tab:selected {{ background: {self.color_primary}; color: black; font-weight: bold; }}
        """)

        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        main_layout.setSpacing(10)
        main_layout.setContentsMargins(15, 15, 15, 15)

        # 1. HEADER
        header_layout = QHBoxLayout()
        title_label = QLabel(f"{self.app_title} 🤖")
        title_label.setFont(QFont("Arial", 16, QFont.Weight.Bold))
        title_label.setStyleSheet(f"color: {self.color_primary};")
        header_layout.addWidget(title_label)

        btn_toggle_proj = QPushButton("🖥️ Projector HDMI")
        btn_toggle_proj.clicked.connect(self.toggle_projector_window)
        header_layout.addWidget(btn_toggle_proj)

        header_layout.addStretch()

        ip = self.emissor.obtenir_ip_local() if self.emissor else "127.0.0.1"
        port = self.emissor.port if self.emissor else 5000
        url_auto = f"http://{ip}:{port}"

        self.ip_label = QLabel(f'🌐 Web Server: <a href="{url_auto}" style="color: {self.color_primary};">{url_auto}</a>')
        self.ip_label.setFont(QFont("Arial", 10, QFont.Weight.Bold))
        self.ip_label.setOpenExternalLinks(True)
        header_layout.addWidget(self.ip_label)

        main_layout.addLayout(header_layout)

        # 2. PANELL D'AJUSTOS EN DIRECTE HDMI
        main_layout.addWidget(self.crear_panel_ajustos_estil_separats())

        # 3. CONFIGURACIÓ ASR I TRADUCCIÓ
        config_box = QGroupBox("Configuració ASR i Traducció")
        config_layout = QHBoxLayout(config_box)

        config_layout.addWidget(QLabel("Entrada (ASR):"))
        self.combo_lang_in = QComboBox()
        for code, name in self.languages_cfg.items():
            self.combo_lang_in.addItem(name, code)
        config_layout.addWidget(self.combo_lang_in)

        config_layout.addSpacing(20)

        is_trans_enabled = self.translation_cfg.get('enabled', True)
        self.chk_translation = QCheckBox("Activar Traducció")
        self.chk_translation.setChecked(is_trans_enabled)
        self.chk_translation.toggled.connect(self.on_toggle_translation)
        config_layout.addWidget(self.chk_translation)

        config_layout.addSpacing(10)

        self.combo_lang_target = QComboBox()
        target_default = self.translation_cfg.get('target_language', 'es')
        for idx, (code, name) in enumerate(self.languages_cfg.items()):
            self.combo_lang_target.addItem(name, code)
            if code == target_default:
                self.combo_lang_target.setCurrentIndex(idx)
        config_layout.addWidget(self.combo_lang_target)

        config_layout.addStretch()
        main_layout.addWidget(config_box)

        # 4. PREVISUALITZACIÓ DUAL / INDIVIDUAL
        self.display_layout = QHBoxLayout()

        self.box_orig_widget = QWidget()
        box_orig = QVBoxLayout(self.box_orig_widget)
        box_orig.setContentsMargins(0, 0, 0, 0)
        lbl_title_orig = QLabel("TRANSCRIPCIÓ (ORIGINAL)")
        lbl_title_orig.setStyleSheet(f"color: {self.color_primary}; font-weight: bold;")
        self.display_orig = QLabel("Esperant àudio...")
        self.display_orig.setFont(QFont("Arial", 13, QFont.Weight.Bold))
        self.display_orig.setWordWrap(True)
        self.display_orig.setStyleSheet(f"background-color: {self.color_panel_bg}; border: 2px solid {self.color_primary}; border-radius: 8px; padding: 10px; min-height: 60px;")
        box_orig.addWidget(lbl_title_orig)
        box_orig.addWidget(self.display_orig)

        self.box_trans_widget = QWidget()
        box_trans = QVBoxLayout(self.box_trans_widget)
        box_trans.setContentsMargins(0, 0, 0, 0)
        lbl_title_trans = QLabel("TRADUCCIÓ (TARGET)")
        lbl_title_trans.setStyleSheet(f"color: {self.color_secondary}; font-weight: bold;")
        self.display_trans = QLabel("Esperant traducció...")
        self.display_trans.setFont(QFont("Arial", 13, QFont.Weight.Bold))
        self.display_trans.setWordWrap(True)
        self.display_trans.setStyleSheet(f"background-color: {self.color_panel_bg}; border: 2px solid {self.color_secondary}; border-radius: 8px; padding: 10px; min-height: 60px;")
        box_trans.addWidget(lbl_title_trans)
        box_trans.addWidget(self.display_trans)

        self.display_layout.addWidget(self.box_orig_widget)
        self.display_layout.addWidget(self.box_trans_widget)
        main_layout.addLayout(self.display_layout)

        self.on_toggle_translation(is_trans_enabled)

        # 5. CONTROLS DE SESSIÓ I TEMPS
        timer_box = QGroupBox("Control Temporal")
        timer_layout = QHBoxLayout(timer_box)

        self.btn_start_stop = QPushButton("▶️ Start")
        self.btn_start_stop.setStyleSheet("background-color: #1b5e20; color: white;")
        self.btn_start_stop.clicked.connect(self.on_click_start_stop)
        timer_layout.addWidget(self.btn_start_stop)

        self.btn_pause = QPushButton("⏸️ Pause")
        self.btn_pause.setEnabled(False)
        self.btn_pause.clicked.connect(self.toggle_pause)
        timer_layout.addWidget(self.btn_pause)

        self.btn_blackout = QPushButton("⬛ Blackout (B)")
        self.btn_blackout.setStyleSheet("background-color: #C62828; color: white;")
        self.btn_blackout.clicked.connect(self.toggle_blackout)
        timer_layout.addWidget(self.btn_blackout)

        timer_layout.addSpacing(20)

        timer_layout.addWidget(QLabel("Temps Total:"))
        self.lbl_total_time = QLabel("00:00")
        self.lbl_total_time.setFont(QFont("Arial", 12, QFont.Weight.Bold))
        self.lbl_total_time.setStyleSheet(f"color: {self.color_primary}; background-color: #1a1a1a; padding: 4px 8px; border-radius: 4px;")
        timer_layout.addWidget(self.lbl_total_time)

        timer_layout.addStretch()
        main_layout.addWidget(timer_box)

        # 6. HISTORIAL LOG
        log_box = QGroupBox("Historial de Transcripció i Traducció")
        log_layout = QVBoxLayout(log_box)
        self.log_edit = QTextEdit()
        self.log_edit.setReadOnly(True)
        log_layout.addWidget(self.log_edit)
        main_layout.addWidget(log_box)

    def crear_panel_ajustos_estil_separats(self):
        group_box = QGroupBox("🖥️ Control d'Estils del Projector HDMI (En Directe)")
        group_layout = QVBoxLayout(group_box)

        tabs = QTabWidget()

        for target_key, target_label in [('orig', '🟢 Original (Text Superior)'), ('trans', '🔵 Traducció (Text Inferior)')]:
            tab = QWidget()
            layout = QHBoxLayout(tab)

            layout.addWidget(QLabel("Font:"))
            combo_font = QComboBox()
            combo_font.addItems(["Arial", "Courier New", "Verdana", "Times New Roman", "Trebuchet MS"])
            combo_font.currentTextChanged.connect(lambda val, tk=target_key: self.actualitzar_estil_hdmi(tk, 'font_family', val))
            layout.addWidget(combo_font)

            layout.addSpacing(10)

            layout.addWidget(QLabel("Mida:"))
            combo_size = QComboBox()
            combo_size.addItems(["24", "28", "32", "38", "44", "50", "60", "72"])
            combo_size.setCurrentText("38")
            combo_size.currentTextChanged.connect(lambda val, tk=target_key: self.actualitzar_estil_hdmi(tk, 'font_size', val))
            layout.addWidget(combo_size)

            layout.addSpacing(10)

            btn_text_color = QPushButton("🎨 Color Text")
            btn_text_color.clicked.connect(lambda _, tk=target_key: self.seleccionar_color_hdmi(tk, 'color_text'))
            layout.addWidget(btn_text_color)

            layout.addStretch()
            tabs.addTab(tab, target_label)

        # Pestanya per al Fons General
        tab_bg = QWidget()
        layout_bg = QHBoxLayout(tab_bg)
        btn_bg_color = QPushButton("⬛ Color de Fons General HDMI")
        btn_bg_color.clicked.connect(lambda: self.seleccionar_color_hdmi('both', 'color_fons'))
        layout_bg.addWidget(btn_bg_color)
        layout_bg.addStretch()
        tabs.addTab(tab_bg, "🎨 Fons General")

        group_layout.addWidget(tabs)
        return group_box

    def seleccionar_color_hdmi(self, target_key, color_type):
        color = QColorDialog.getColor()
        if color.isValid():
            self.actualitzar_estil_hdmi(target_key, color_type, color.name())

    def actualitzar_estil_hdmi(self, target_key, prop_name, value):
        if hasattr(self, 'projector_window') and self.projector_window:
            kwargs = {
                'target': target_key,
                'font_family': value if prop_name == 'font_family' else None,
                'font_size': value if prop_name == 'font_size' else None,
                'font_color': value if prop_name == 'color_text' else None,
                'bg_color': value if prop_name == 'color_fons' else None,
            }
            self.projector_window.actualitzar_estils_hdmi(**kwargs)

    def on_toggle_translation(self, enabled):
        self.combo_lang_target.setEnabled(enabled)
        self.box_trans_widget.setVisible(enabled)
        
        if self.transcriptor:
            self.transcriptor.translation_enabled = enabled

        if not enabled:
            self.projector_window.actualitzar_mode_visualitzacio('original')
        else:
            channel_yaml = self.projector_cfg.get('display_channel', 'both')
            self.projector_window.actualitzar_mode_visualitzacio(channel_yaml)

    def toggle_projector_window(self):
        if self.projector_window.isVisible():
            self.projector_window.hide()
        else:
            self.projector_window.show()

    def on_click_start_stop(self):
        if not self.session_started:
            self.start_session()
        else:
            self.stop_session()

    def start_session(self):
        if not self.transcriptor:
            QMessageBox.critical(self, "Error", "El transcriptor no s'ha inicialitzat.")
            return

        self.is_blackout = False
        self.transcriptor.translation_enabled = self.chk_translation.isChecked()
        self.transcriptor.target_language = self.combo_lang_target.currentData()

        self.session_started = True
        self.is_paused = False
        self.timer.start()

        self.audio_thread = AudioThread(self.transcriptor)
        self.audio_thread.subtitol_generat.connect(self.on_nou_subtitol)
        self.audio_thread.start()

        self.btn_start_stop.setText("⏹️ Stop")
        self.btn_start_stop.setStyleSheet("background-color: #C62828; color: white;")
        self.btn_pause.setEnabled(True)

        self.display_orig.setText("[ Escoltant... ]")
        if self.chk_translation.isChecked():
            self.display_trans.setText("[ Generant traducció... ]")

    def stop_session(self):
        self.session_started = False
        self.is_paused = False
        self.is_blackout = False
        self.timer.stop()
        if self.audio_thread:
            self.audio_thread.stop()
            self.audio_thread = None

        self.btn_start_stop.setText("▶️ Start")
        self.btn_start_stop.setStyleSheet("background-color: #1b5e20; color: white;")
        self.btn_pause.setEnabled(False)

    def toggle_pause(self):
        self.is_paused = not self.is_paused

    def toggle_blackout(self):
        if not self.is_blackout:
            self.is_blackout = True
            self.display_orig.setText("[ BLACKOUT ]")
            self.display_trans.setText("[ BLACKOUT ]")
            
            if hasattr(self, 'projector_window') and self.projector_window:
                self.projector_window.blackout()

            if self.emissor:
                self.emissor.emetre("auto_original", "")
                self.emissor.emetre("auto_translation", "")
        else:
            self.is_blackout = False
            self.display_orig.setText("[ Escoltant... ]")
            if self.chk_translation.isChecked():
                self.display_trans.setText("[ Generant traducció... ]")

    def update_timers(self):
        if not self.session_started or self.is_paused:
            return
        self.total_elapsed_ms += 100
        self.lbl_total_time.setText(ms_to_time_str(self.total_elapsed_ms))

    def on_nou_subtitol(self, dades):
        if self.is_paused or self.is_blackout or not dades:
            return

        self.sub_elapsed_ms = 0

        if isinstance(dades, str):
            text_orig = dades
            text_trans = ""
        else:
            text_orig = dades.get("original", "")
            text_trans = dades.get("translation", "") if self.chk_translation.isChecked() else ""

        if text_orig:
            self.display_orig.setText(text_orig)
        if text_trans and self.chk_translation.isChecked():
            self.display_trans.setText(text_trans)

        self.projector_window.actualitzar_text(text_orig, text_trans)

        if self.emissor:
            if text_orig:
                self.emissor.emetre("auto_original", text_orig)
            if text_trans and self.chk_translation.isChecked():
                self.emissor.emetre("auto_translation", text_trans)

        timestamp = ms_to_time_str(self.total_elapsed_ms)
        if text_orig:
            self.log_edit.append(f"[{timestamp}] 🟢 {text_orig}")
        if text_trans and self.chk_translation.isChecked():
            self.log_edit.append(f"[{timestamp}] 🔵 {text_trans}")
            
        self.log_edit.moveCursor(QTextCursor.MoveOperation.End)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_B:
            self.toggle_blackout()
        else:
            super().keyPressEvent(event)

    def closeEvent(self, event):
        self.stop_session()
        try:
            if hasattr(self, 'projector_window') and self.projector_window:
                self.projector_window.close()
        except Exception as e:
            print(f"Error tancant el projector: {e}")
        event.accept()

    def detectar_segona_pantalla(self):
        self.projector_window.assignar_a_segona_pantalla()


if __name__ == "__main__":
    app = QApplication(sys.argv)

    emissor = None
    if EmissorWeb is not None:
        try:
            emissor = EmissorWeb(CONFIG_SERVER, mode="AUTOMÀTIC")
            emissor.iniciar()
        except Exception as e:
            print(f"⚠️ Alerta en iniciar el servidor web: {e}")

    window = TeloAutoPyQt(emissor)
    window.show()

    sys.exit(app.exec())
