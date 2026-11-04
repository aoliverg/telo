import sys
import os
import time
import socket
import subprocess
import math
import numpy as np
import yaml

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, 
    QPushButton, QLabel, QTextEdit, QComboBox, QGroupBox, QMessageBox, 
    QCheckBox, QColorDialog, QProgressBar
)
from PyQt6.QtCore import Qt, QTimer, QThread, pyqtSignal
from PyQt6.QtGui import QFont, QTextCursor, QFontMetrics

# Importació de sounddevice per al control de micròfons
try:
    import sounddevice as sd
except ImportError:
    sd = None
    print("⚠️ Advertència: 'sounddevice' no està instal·lat. El vúmetre no estarà disponible.")

# Directoris del projecte
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SRC_DIR = os.path.join(BASE_DIR, 'src')
CONFIG_YAML_PATH = os.path.join(BASE_DIR, 'config.yaml')

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
    from utils import ms_to_time_str
except ImportError as e:
    print(f"⚠️ Error en importar mòduls compartits des de src/: {e}")
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
WHISPER_CFG = CONFIG_AUTO.get('whisper_live', {})

CONFIG_SERVER = CONFIG_COMMON.get('server', {'host': '0.0.0.0', 'port': 5000})
CONFIG_SERVER['channels'] = [
    {'id': 'auto_original', 'label': 'Transcripció (Original)'},
    {'id': 'auto_translation', 'label': 'Traducció en Directe'}
]


# --- CÀLCUL EXACTE EN PÍXELS PER AL LÍMIT DE LÍNIES VISUALS EN QT ---
def ajustar_text_a_linies_reals_qt(text, font, ample_disponible_px, max_lines):
    """
    Calcula exactament on trenca visualment el text en píxels reals
    segons la font, mida i amplada física del contenidor,
    i retorna estrictament les darreres 'max_lines'.
    """
    if not text or max_lines <= 0:
        return text

    metrics = QFontMetrics(font)
    paragrafs = text.split('\n')
    linies_totals = []

    for par in paragrafs:
        par = par.strip()
        if not par:
            continue
        
        paraules = par.split()
        linia_actual = ""
        
        for paraula in paraules:
            prova = f"{linia_actual} {paraula}".strip()
            if metrics.horizontalAdvance(prova) <= ample_disponible_px:
                linia_actual = prova
            else:
                if linia_actual:
                    linies_totals.append(linia_actual)
                linia_actual = paraula
                
        if linia_actual:
            linies_totals.append(linia_actual)

    if len(linies_totals) > max_lines:
        linies_totals = linies_totals[-max_lines:]

    return '\n'.join(linies_totals)


# --- UTILITATS DE GESTIÓ DEL SERVIDOR WLK ---
def comprovar_servidor_actiu(host='localhost', port=8000, timeout=1.0):
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except (socket.timeout, ConnectionRefusedError, OSError):
        return False


def assegurar_servidor_wlk(cfg_wlk, log_callback=None):
    host = cfg_wlk.get('host', 'localhost')
    port = cfg_wlk.get('port', 8000)
    auto_start = cfg_wlk.get('auto_start', True)
    cmd = cfg_wlk.get('command', '').strip()

    if comprovar_servidor_actiu(host, port):
        if log_callback:
            log_callback(f"ℹ️ Servidor WLK detectat en marxa a {host}:{port}")
        return None

    if not auto_start or not cmd:
        if log_callback:
            log_callback("⚠️ El servidor WLK no està actiu i no s'ha configurat comanda d'inici.")
        return None

    if log_callback:
        log_callback(f"🚀 Iniciant servidor WLK ({host}:{port})...")

    process = subprocess.Popen(
        cmd,
        shell=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        preexec_fn=os.setsid if os.name != 'nt' else None
    )

    max_retries = 30
    for _ in range(max_retries):
        time.sleep(1.0)
        if comprovar_servidor_actiu(host, port):
            if log_callback:
                log_callback("✅ Servidor WLK iniciat correctament.")
            return process

    if log_callback:
        log_callback("⚠️ El procés s'ha iniciat però encara no respon al port esperat.")
    return process


# --- FIL DE MONITORATGE DEL NIVELL DEL MICRÒFON (VÚMETRE) ---
class AudioLevelMonitorThread(QThread):
    level_signal = pyqtSignal(int)

    def __init__(self, device_index=None):
        super().__init__()
        self.device_index = device_index
        self.running = False

    def set_device(self, device_index):
        self.device_index = device_index

    def run(self):
        if sd is None:
            return

        self.running = True

        def audio_callback(indata, frames, time_info, status):
            if not self.running:
                return
            rms = np.sqrt(np.mean(indata**2))
            if rms > 0:
                db = 20 * math.log10(rms)
                percent = int(max(0, min(100, (db + 50) * 2)))
            else:
                percent = 0
            self.level_signal.emit(percent)

        try:
            with sd.InputStream(
                device=self.device_index,
                channels=1,
                samplerate=16000,
                blocksize=1024,
                callback=audio_callback
            ):
                while self.running:
                    time.sleep(0.05)
        except Exception as e:
            print(f"⚠️ Error monitoritzant micròfon: {e}")

    def stop(self):
        self.running = False
        self.wait(500)


# --- FINESTRA DEL PROJECTOR HDMI DINÀMICA PER A TELOAUTO ---
class ProjectorAutoWindow(QMainWindow):
    def __init__(self, app_title="TELÓ", config_proj=None):
        super().__init__()
        self.app_title = app_title
        self.config_proj = config_proj or {}
        
        self.base_font_family = self.config_proj.get('font_family', 'Arial')
        self.base_font_size = self.config_proj.get('font_size', 38)
        self.max_lines = self.config_proj.get('max_lines', 3)
        self.bg_color = self.config_proj.get('background_color', '#000000')
        self.labels = []
        self._ultims_textos = []

        self.init_ui()

    def init_ui(self):
        self.setWindowTitle(f"{self.app_title} - Projector HDMI")
        self.setStyleSheet(f"background-color: {self.bg_color};")
        
        self.central_widget = QWidget()
        self.setCentralWidget(self.central_widget)
        self.layout_proj = QVBoxLayout(self.central_widget)
        self.layout_proj.setContentsMargins(30, 20, 30, 20)
        self.layout_proj.setSpacing(15)

        w = self.config_proj.get('width', 1000)
        h = self.config_proj.get('height', 500)
        self.resize(w, h)

    def actualitzar_textos(self, textos_amb_color):
        self._ultims_textos = textos_amb_color

        while len(self.labels) < len(textos_amb_color):
            lbl = QLabel()
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl.setWordWrap(True)
            self.layout_proj.addWidget(lbl, 1)
            self.labels.append(lbl)

        while len(self.labels) > len(textos_amb_color):
            lbl = self.labels.pop()
            self.layout_proj.removeWidget(lbl)
            lbl.deleteLater()

        num_lignes = len(textos_amb_color)
        if num_lignes >= 3:
            font_size = max(22, int(self.base_font_size * 0.68))
        elif num_lignes == 2:
            font_size = max(26, int(self.base_font_size * 0.85))
        else:
            font_size = self.base_font_size

        font = QFont(self.base_font_family, font_size, QFont.Weight.Bold)
        ample_disponible_px = max(100, self.central_widget.width() - 60)

        for i, (text, color) in enumerate(textos_amb_color):
            lbl = self.labels[i]
            lbl.setFont(font)
            
            text_ajustat = ajustar_text_a_linies_reals_qt(
                text, 
                font, 
                ample_disponible_px, 
                max_lines=self.max_lines
            )
            
            lbl.setText(text_ajustat)
            lbl.setStyleSheet(f"color: {color}; background-color: transparent;")

    def blackout(self):
        for lbl in self.labels:
            lbl.setText("")

    def set_font_size(self, size):
        self.base_font_size = size

    def set_font_family(self, family):
        self.base_font_family = family

    def set_bg_color(self, hex_color):
        self.bg_color = hex_color
        self.setStyleSheet(f"background-color: {hex_color};")

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._ultims_textos:
            self.actualitzar_textos(self._ultims_textos)

    def assignar_a_segona_pantalla(self):
        screens = QApplication.screens()
        if len(screens) > 1:
            segona = screens[1]
            if self.windowHandle():
                self.windowHandle().setScreen(segona)
            self.setGeometry(segona.geometry())
            self.showFullScreen()


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
        self.wlk_subprocess = None
        self.transcriptor = None
        self.audio_thread = None
        self.monitor_thread = None
        
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

        self.color_proj_orig = self.color_primary
        self.color_proj_trans = self.color_secondary

        self.raw_text_orig = ""
        self.raw_text_trans = ""

        self.max_lines_hdmi = STYLES_CFG.get('hdmi', {}).get('max_lines', 3)

        self.projector_cfg = STYLES_CFG.get('hdmi', {})
        self.languages_cfg = CONFIG_COMMON.get('languages', {'ca': 'Català', 'es': 'Español'})
        self.translation_cfg = CONFIG_AUTO.get('translation', {})

        self.total_elapsed_ms = 0
        self.timer = QTimer(self)
        self.timer.setInterval(100)
        self.timer.timeout.connect(self.update_timers)

        self.projector_window = ProjectorAutoWindow(app_title=self.app_title, config_proj=self.projector_cfg)

        self.init_ui()
        self.detectar_segona_pantalla()

        self.carregar_dispositius_audio()
        self.iniciar_monitoratge_audio()
        self.verificar_servidor_wlk()

        if TranscriptorWhisperLiveKit is not None:
            self.transcriptor = TranscriptorWhisperLiveKit(config_path=CONFIG_YAML_PATH)
            try:
                self.transcriptor.inicialitzar_motor()
            except Exception as e:
                self.log(f"⚠️ Alerta en inicialitzar el motor del transcriptor: {e}")
        else:
            self.transcriptor = None

        if self.transcriptor:
            self.transcriptor.translation_enabled = self.chk_translation.isChecked()

    def log(self, text):
        if hasattr(self, 'log_edit'):
            self.log_edit.append(text)
            self.log_edit.moveCursor(QTextCursor.MoveOperation.End)
        else:
            print(text)

    def verificar_servidor_wlk(self):
        try:
            self.wlk_subprocess = assegurar_servidor_wlk(WHISPER_CFG, log_callback=self.log)
        except Exception as e:
            self.log(f"⚠️ Error gestionant el servidor WLK: {e}")

    def carregar_dispositius_audio(self):
        self.combo_audio_device.clear()
        self.combo_audio_device.addItem("Micròfon per defecte del sistema", None)

        if sd is None:
            self.combo_audio_device.setEnabled(False)
            return

        try:
            devices = sd.query_devices()
            for idx, dev in enumerate(devices):
                if dev.get('max_input_channels', 0) > 0:
                    nom = dev.get('name', f'Dispositiu {idx}')
                    host_api = sd.query_hostapis(dev.get('hostapi', 0)).get('name', '')
                    etiqueta = f"{nom} ({host_api})" if host_api else nom
                    self.combo_audio_device.addItem(etiqueta, idx)
        except Exception as e:
            self.log(f"⚠️ Error consultant targetes d'àudio: {e}")

    def iniciar_monitoratge_audio(self):
        if sd is None:
            return
        selected_dev = self.combo_audio_device.currentData()
        self.monitor_thread = AudioLevelMonitorThread(device_index=selected_dev)
        self.monitor_thread.level_signal.connect(self.actualitzar_vumetre)
        self.monitor_thread.start()

    def canviar_dispositiu_mic(self):
        nou_dev = self.combo_audio_device.currentData()
        if self.monitor_thread:
            self.monitor_thread.stop()
            self.monitor_thread.set_device(nou_dev)
            self.monitor_thread.start()

        if self.transcriptor and hasattr(self.transcriptor, 'device'):
            self.transcriptor.device = nou_dev

    def actualitzar_vumetre(self, valor):
        self.vu_meter.setValue(valor)
        if valor > 85:
            self.vu_meter.setStyleSheet("""
                QProgressBar { border: 1px solid #444; border-radius: 4px; background-color: #1a1a1a; text-align: center; }
                QProgressBar::chunk { background-color: #ff3d00; border-radius: 3px; }
            """)
        elif valor > 60:
            self.vu_meter.setStyleSheet("""
                QProgressBar { border: 1px solid #444; border-radius: 4px; background-color: #1a1a1a; text-align: center; }
                QProgressBar::chunk { background-color: #ffca28; border-radius: 3px; }
            """)
        else:
            self.vu_meter.setStyleSheet(f"""
                QProgressBar {{ border: 1px solid #444; border-radius: 4px; background-color: #1a1a1a; text-align: center; }}
                QProgressBar::chunk {{ background-color: {self.color_primary}; border-radius: 3px; }}
            """)

    def init_ui(self):
        self.setWindowTitle(f"{self.app_title} 🤖")
        self.resize(1180, 980)
        
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
                font-weight: bold; min-height: 22px;
            }}
            QPushButton:hover {{ background-color: {self.color_btn_border}; }}

            QTextEdit {{ background-color: {self.color_panel_bg}; border: 1px solid {self.color_btn_border}; color: {self.color_text}; font-family: monospace; }}
            QComboBox {{ background-color: {self.color_btn_bg}; border: 1px solid {self.color_btn_border}; padding: 4px 8px; border-radius: 4px; }}
            QCheckBox {{ color: {self.color_text}; font-weight: bold; }}
            
            QProgressBar {{
                border: 1px solid {self.color_btn_border};
                border-radius: 4px;
                background-color: #1a1a1a;
                text-align: center;
                height: 18px;
            }}
            QProgressBar::chunk {{
                background-color: {self.color_primary};
                border-radius: 3px;
            }}
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
        main_layout.addWidget(self.crear_panel_ajustos_estil())

        # 3. PANELL D'ENTRADA D'ÀUDIO I VÚMETRE
        audio_box = QGroupBox("🎙️ Control d'Entrada d'Àudio i Nivell de Micròfon")
        audio_layout = QHBoxLayout(audio_box)
        audio_layout.setContentsMargins(12, 18, 12, 12)

        audio_layout.addWidget(QLabel("Dispositiu d'entrada:"))
        self.combo_audio_device = QComboBox()
        self.combo_audio_device.currentIndexChanged.connect(self.canviar_dispositiu_mic)
        audio_layout.addWidget(self.combo_audio_device, 3)

        audio_layout.addSpacing(20)

        audio_layout.addWidget(QLabel("Senyal / Vúmetre:"))
        self.vu_meter = QProgressBar()
        self.vu_meter.setRange(0, 100)
        self.vu_meter.setValue(0)
        self.vu_meter.setTextVisible(False)
        self.vu_meter.setFixedHeight(18)
        audio_layout.addWidget(self.vu_meter, 2)

        main_layout.addWidget(audio_box)

        # 4. CONFIGURACIÓ ASR I TRADUCCIÓ
        config_box = QGroupBox("Configuració ASR i Traducció")
        config_layout = QHBoxLayout(config_box)

        config_layout.addWidget(QLabel("Idioma Entrada (ASR):"))
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

        # 5. PREVISUALITZACIÓ DUAL / INDIVIDUAL
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

        # 6. CONTROLS DE SESSIÓ I TEMPS
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

        # 7. HISTORIAL LOG
        log_box = QGroupBox("Historial de Transcripció i Traducció")
        log_layout = QVBoxLayout(log_box)
        self.log_edit = QTextEdit()
        self.log_edit.setReadOnly(True)
        log_layout.addWidget(self.log_edit)
        main_layout.addWidget(log_box)

    def crear_panel_ajustos_estil(self):
        group_box = QGroupBox("🖥️ Control Global d'Estils del Projector HDMI (En Directe)")
        layout = QHBoxLayout(group_box)

        layout.addWidget(QLabel("Tipografia:"))
        combo_font = QComboBox()
        combo_font.addItems(["Arial", "Courier New", "Verdana", "Times New Roman", "Trebuchet MS"])
        combo_font.setCurrentText(self.projector_cfg.get('font_family', 'Arial'))
        combo_font.currentTextChanged.connect(self.actualitzar_font_projector)
        layout.addWidget(combo_font)

        layout.addSpacing(10)

        layout.addWidget(QLabel("Mida Base:"))
        combo_size = QComboBox()
        combo_size.addItems(["24", "28", "32", "38", "44", "50", "60", "72"])
        combo_size.setCurrentText(str(self.projector_cfg.get('font_size', 38)))
        combo_size.currentTextChanged.connect(self.actualitzar_mida_projector)
        layout.addWidget(combo_size)

        layout.addSpacing(15)

        self.btn_col_orig = QPushButton("🟢 Color Original")
        self.btn_col_orig.clicked.connect(self.triar_color_original)
        layout.addWidget(self.btn_col_orig)

        self.btn_col_trans = QPushButton("🔵 Color Traducció")
        self.btn_col_trans.clicked.connect(self.triar_color_traduccio)
        layout.addWidget(self.btn_col_trans)

        layout.addSpacing(10)

        btn_bg_color = QPushButton("⬛ Fons HDMI")
        btn_bg_color.clicked.connect(self.canviar_fons_projector)
        layout.addWidget(btn_bg_color)

        layout.addStretch()
        return group_box

    def actualitzar_font_projector(self, font_name):
        self.projector_window.set_font_family(font_name)
        self.refrescar_projector()

    def actualitzar_mida_projector(self, size_str):
        self.projector_window.set_font_size(int(size_str))
        self.refrescar_projector()

    def canviar_fons_projector(self):
        color = QColorDialog.getColor()
        if color.isValid():
            self.projector_window.set_bg_color(color.name())

    def triar_color_original(self):
        color = QColorDialog.getColor()
        if color.isValid():
            self.color_proj_orig = color.name()
            self.refrescar_projector()

    def triar_color_traduccio(self):
        color = QColorDialog.getColor()
        if color.isValid():
            self.color_proj_trans = color.name()
            self.refrescar_projector()

    def refrescar_projector(self):
        if self.is_blackout:
            return
        llista_hdmi = []
        if self.raw_text_orig:
            llista_hdmi.append((self.raw_text_orig, self.color_proj_orig))
        if self.chk_translation.isChecked() and self.raw_text_trans:
            llista_hdmi.append((self.raw_text_trans, self.color_proj_trans))
            
        self.projector_window.actualitzar_textos(llista_hdmi)

    def on_toggle_translation(self, enabled):
        self.combo_lang_target.setEnabled(enabled)
        self.box_trans_widget.setVisible(enabled)
        
        if getattr(self, 'transcriptor', None) is not None:
            self.transcriptor.translation_enabled = enabled

        self.refrescar_projector()

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
        host = WHISPER_CFG.get('host', 'localhost')
        port = WHISPER_CFG.get('port', 8000)
        if not comprovar_servidor_actiu(host, port):
            self.log("⚠️ El servidor WLK no respon. Intentant reactivar-lo...")
            self.verificar_servidor_wlk()

        if not self.transcriptor:
            QMessageBox.critical(self, "Error", "El transcriptor no s'ha inicialitzat.")
            return

        self.is_blackout = False
        self.transcriptor.translation_enabled = self.chk_translation.isChecked()
        self.transcriptor.target_language = self.combo_lang_target.currentData()

        sel_dev = self.combo_audio_device.currentData()
        if hasattr(self.transcriptor, 'device'):
            self.transcriptor.device = sel_dev

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
            self.refrescar_projector()

    def update_timers(self):
        if not self.session_started or self.is_paused:
            return
        self.total_elapsed_ms += 100
        self.lbl_total_time.setText(ms_to_time_str(self.total_elapsed_ms))

    def on_nou_subtitol(self, dades):
        if self.is_paused or self.is_blackout or not dades:
            return

        if isinstance(dades, str):
            text_orig = dades
            text_trans = ""
        else:
            text_orig = dades.get("original", "")
            text_trans = dades.get("translation", "") if self.chk_translation.isChecked() else ""

        self.raw_text_orig = text_orig
        self.raw_text_trans = text_trans

        # 1. Al monitor de control es limita amb l'amplada de la caixa de l'operador
        ample_op_orig = max(100, self.display_orig.width() - 25)
        text_disp_orig = ajustar_text_a_linies_reals_qt(
            text_orig, 
            self.display_orig.font(), 
            ample_op_orig, 
            max_lines=self.max_lines_hdmi
        )
        self.display_orig.setText(text_disp_orig)

        if text_trans and self.chk_translation.isChecked():
            ample_op_trans = max(100, self.display_trans.width() - 25)
            text_disp_trans = ajustar_text_a_linies_reals_qt(
                text_trans, 
                self.display_trans.font(), 
                ample_op_trans, 
                max_lines=self.max_lines_hdmi
            )
            self.display_trans.setText(text_disp_trans)

        # 2. El projector HDMI calcula les seves pròpies línies en funció dels seus píxels reals
        self.refrescar_projector()

        # 3. A la web s'envia el text complet perquè el client (mòbil/ulleres) apliqui el seu CSS clamp
        if self.emissor:
            if text_orig:
                self.emissor.emetre("auto_original", text_orig)
            if text_trans and self.chk_translation.isChecked():
                self.emissor.emetre("auto_translation", text_trans)

        # 4. Historial
        timestamp = ms_to_time_str(self.total_elapsed_ms)
        if text_orig:
            self.log_edit.append(f"[{timestamp}] 🟢 {text_disp_orig}")
        if text_trans and self.chk_translation.isChecked():
            self.log_edit.append(f"[{timestamp}] 🔵 {text_disp_trans}")
            
        self.log_edit.moveCursor(QTextCursor.MoveOperation.End)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_B:
            self.toggle_blackout()
        else:
            super().keyPressEvent(event)

    def closeEvent(self, event):
        self.stop_session()

        if self.monitor_thread:
            self.monitor_thread.stop()

        # Comprova si a config.yaml està configurat per aturar-se (per defecte True)
        stop_wlk = WHISPER_CFG.get('stop_on_exit', True)
        if self.wlk_subprocess and stop_wlk:
            try:
                # Mata tot el grup de processos creats per la comanda (inclòs el servidor wlk fill)
                os.killpg(os.getpgid(self.wlk_subprocess.pid), 15)  # 15 = SIGTERM
            except ProcessLookupError:
                pass
            except Exception as e:
                print(f"⚠️ Error aturant el servidor WLK: {e}")

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
