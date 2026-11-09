import sys
import os
import time
import socket
import subprocess
import signal
import math
import numpy as np
import yaml

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, 
    QPushButton, QLabel, QTextEdit, QComboBox, QGroupBox, QMessageBox, 
    QCheckBox, QColorDialog, QProgressBar, QTableWidget, QHeaderView,
    QAbstractItemView
)
from PyQt6.QtCore import Qt, QTimer, QThread, pyqtSignal
from PyQt6.QtGui import QFont, QTextCursor, QFontMetrics

try:
    import sounddevice as sd
except ImportError:
    sd = None
    print("⚠️ Advertència: 'sounddevice' no està instal·lat.")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SRC_DIR = os.path.join(BASE_DIR, 'src')
CONFIG_YAML_PATH = os.path.join(BASE_DIR, 'config.yaml')

if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

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
                return yaml.safe_load(f) or {}
        except Exception as e:
            print(f"⚠️ Error llegint {CONFIG_YAML_PATH}: {e}")
    return {}


CONFIG_FULL = carregar_configuracio_yaml()
CONFIG_COMMON = CONFIG_FULL.get('common', {})
CONFIG_AUTO = CONFIG_FULL.get('auto', {})
STYLES_CFG = CONFIG_COMMON.get('styles', {})
WHISPER_CFG = CONFIG_AUTO.get('whisper_live', {})

CONFIG_SERVER = CONFIG_COMMON.get('server', {'host': '0.0.0.0', 'port': 5000, 'enabled': True})
if CONFIG_SERVER.get('host') == '00.0.0':
    CONFIG_SERVER['host'] = '0.0.0.0'

CONFIG_SERVER['channels'] = [
    {'id': 'auto_original', 'label': 'Transcripció (Original)'},
    {'id': 'auto_translation', 'label': 'Traducció en Directe'}
]

WEB_SERVER_ENABLED = CONFIG_SERVER.get('enabled', True)
HDMI_ENABLED = STYLES_CFG.get('hdmi', {}).get('enabled', True)


def ajustar_text_a_linies_reals_qt(text, font, ample_disponible_px, max_lines):
    if not text or max_lines <= 0:
        return text

    metrics = QFontMetrics(font)
    paragrafs = str(text).split('\n')
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


def comprovar_servidor_actiu(host='localhost', port=8000, timeout=1.0):
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except (socket.timeout, ConnectionRefusedError, OSError):
        return False


def aturar_servidor_wlk_complet(process=None, port=8000, log_callback=None):
    if process:
        try:
            pgid = os.getpgid(process.pid)
            os.killpg(pgid, signal.SIGTERM)
            time.sleep(0.3)
            os.killpg(pgid, signal.SIGKILL)
        except (ProcessLookupError, OSError):
            pass

    if os.name != 'nt':
        try:
            subprocess.run(f"fuser -k -9 {port}/tcp", shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass
        try:
            subprocess.run("pkill -9 -f 'wlk --pcm-input'", shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass

    if log_callback:
        log_callback("🛑 Servidor WLK aturat i port alliberat.")


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

    max_retries = 40
    for _ in range(max_retries):
        time.sleep(1.0)
        if comprovar_servidor_actiu(host, port):
            if log_callback:
                log_callback("✅ Servidor WLK iniciat correctament.")
            return process

    if log_callback:
        log_callback("⚠️ El procés s'ha iniciat però encara no respon al port esperat.")
    return process


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
                    time.sleep(0.08)
        except Exception as e:
            print(f"⚠️ Monitor àudio: {e}")

    def stop(self):
        self.running = False
        self.wait(500)


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
        # 1. Eliminem marcs, vores i barra de títol de la finestra
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.FramelessWindowHint)

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
                str(text), 
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
        
        def callback_resultat(dades):
            if self.running and dades:
                if isinstance(dades, str):
                    dades = {"original": dades, "translation": "", "es_consolidat": False}
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


class TeloAutoPyQt(QMainWindow):
    def __init__(self, emissor):
        super().__init__()
        self.emissor = emissor
        self.web_enabled = WEB_SERVER_ENABLED and (self.emissor is not None)
        self.hdmi_enabled = HDMI_ENABLED

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

        self.hdmi_items_data = [
            {'id': 'orig', 'label': 'Transcripció (Original)', 'color': self.color_primary, 'enabled': True},
            {'id': 'trans', 'label': 'Traducció en Directe', 'color': self.color_secondary, 'enabled': True}
        ]

        self.raw_text_orig = ""
        self.raw_text_trans = ""
        
        self.ultim_log_orig = ""
        self.ultim_log_trans = ""

        self.max_lines_hdmi = STYLES_CFG.get('hdmi', {}).get('max_lines', 3)
        self.projector_cfg = STYLES_CFG.get('hdmi', {})
        self.languages_cfg = CONFIG_COMMON.get('languages', {'ca': 'Català', 'es': 'Español', 'fr': 'Français'})
        self.translation_cfg = CONFIG_AUTO.get('translation', {})
        self.audio_cfg = CONFIG_AUTO.get('audio', {})

        self.total_elapsed_ms = 0
        self.timer = QTimer(self)
        self.timer.setInterval(100)
        self.timer.timeout.connect(self.update_timers)

        if self.hdmi_enabled:
            self.projector_window = ProjectorAutoWindow(app_title=self.app_title, config_proj=self.projector_cfg)
        else:
            self.projector_window = None

        # 1. Primer construïm la UI completament (assegura que self.log_edit existeixi)
        self.init_ui()
        if self.hdmi_enabled:
            self.dibuixar_taula_hdmi()
            self.detectar_segona_pantalla()

        # 2. Després inicialitzem els subsistemes
        self.carregar_dispositius_audio()
        self.verificar_servidor_wlk()
        self.inicialitzar_transcriptor()

    def log(self, text):
        """Escriu al panell inferior de registre i a la consola."""
        if hasattr(self, 'log_edit') and self.log_edit is not None:
            self.log_edit.append(str(text))
            self.log_edit.moveCursor(QTextCursor.MoveOperation.End)
        else:
            print(str(text))

    def inicialitzar_transcriptor(self):
        if TranscriptorWhisperLiveKit is None:
            self.transcriptor = None
            return

        lang_in = (
            self.combo_lang_in.currentData()
            if hasattr(self, 'combo_lang_in') and self.combo_lang_in.currentData()
            else CONFIG_AUTO.get('input_language', 'fr')
        )
        lang_target = (
            self.combo_lang_target.currentData()
            if hasattr(self, 'combo_lang_target') and self.combo_lang_target.currentData()
            else self.translation_cfg.get('target_language', 'ca')
        )
        trans_on = (
            self.chk_translation.isChecked()
            if hasattr(self, 'chk_translation')
            else self.translation_cfg.get('enabled', True)
        )
        dev = (
            self.combo_audio_device.currentData()
            if hasattr(self, 'combo_audio_device')
            else self.audio_cfg.get('device', None)
        )

        config_transcriptor = {
            "whisper_live": WHISPER_CFG,
            "input_language": lang_in,
            "translation": {
                "enabled": trans_on,
                "target_language": lang_target,
            },
            "audio": {
                "samplerate": self.audio_cfg.get('samplerate', 16000),
                "block_ms": self.audio_cfg.get('block_ms', 100),
                "device": dev,
            },
            "show_info": True,
        }

        try:
            self.transcriptor = TranscriptorWhisperLiveKit(config=config_transcriptor)
            self.transcriptor.inicialitzar_motor()
        except Exception as e:
            self.log(f"⚠️ Alerta en inicialitzar el transcriptor: {e}")

    def verificar_servidor_wlk(self):
        try:
            self.wlk_subprocess = assegurar_servidor_wlk(WHISPER_CFG, log_callback=self.log)
        except Exception as e:
            self.log(f"⚠️ Error gestionant el servidor WLK: {e}")

    def carregar_dispositius_audio(self):
        self.combo_audio_device.blockSignals(True)
        self.combo_audio_device.clear()
        self.combo_audio_device.addItem("Micròfon per defecte del sistema", None)

        if sd is not None:
            try:
                devices = sd.query_devices()
                apis = sd.query_hostapis()
                for idx, dev in enumerate(devices):
                    if dev.get('max_input_channels', 0) > 0:
                        nom = str(dev.get('name', f'Dispositiu {idx}'))
                        host_idx = dev.get('hostapi', 0)
                        host_api = str(apis[host_idx].get('name', '')) if host_idx < len(apis) else ''
                        etiqueta = f"{nom} ({host_api})" if host_api else nom
                        self.combo_audio_device.addItem(etiqueta, idx)
            except Exception as e:
                self.log(f"⚠️ Error consultant targetes d'àudio: {e}")
        else:
            self.combo_audio_device.setEnabled(False)

        self.combo_audio_device.blockSignals(False)

    def toggle_vumetre(self, estat):
        if estat:
            if sd is None:
                return
            selected_dev = self.combo_audio_device.currentData()
            self.monitor_thread = AudioLevelMonitorThread(device_index=selected_dev)
            self.monitor_thread.level_signal.connect(self.actualitzar_vumetre)
            self.monitor_thread.start()
            self.vu_meter.setEnabled(True)
        else:
            if self.monitor_thread:
                self.monitor_thread.stop()
                self.monitor_thread = None
            self.vu_meter.setValue(0)
            self.vu_meter.setEnabled(False)

    def canviar_dispositiu_mic(self, index=None):
        nou_dev = self.combo_audio_device.currentData()
        if hasattr(self, 'chk_activar_vu') and self.chk_activar_vu.isChecked() and self.monitor_thread:
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
        self.setWindowTitle(f"{self.app_title} 🤖 - Optimitzat")
        self.resize(1200, 980)
        
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

            QTableWidget {{ 
                background-color: {self.color_panel_bg}; 
                gridline-color: #2A2A2A; 
                border: 1px solid {self.color_btn_border}; 
                selection-background-color: #2E7D32; 
            }}
            QHeaderView::section {{ 
                background-color: {self.color_btn_bg}; 
                color: {self.color_primary}; 
                font-weight: bold; 
                border: 1px solid {self.color_btn_border}; 
                padding: 4px; 
            }}

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

        if self.hdmi_enabled:
            btn_toggle_proj = QPushButton("🖥️ Projector HDMI")
            btn_toggle_proj.clicked.connect(self.toggle_projector_window)
            header_layout.addWidget(btn_toggle_proj)

        header_layout.addStretch()

        if self.web_enabled:
            ip = self.emissor.obtenir_ip_local() if hasattr(self.emissor, 'obtenir_ip_local') else "127.0.0.1"
            port = self.emissor.port if hasattr(self.emissor, 'port') else 5000
            url_auto = f"http://{ip}:{port}"
            self.ip_label = QLabel(f'🌐 Web Server: <a href="{url_auto}" style="color: {self.color_primary};">{url_auto}</a>')
            self.ip_label.setOpenExternalLinks(True)
        else:
            self.ip_label = QLabel('🌐 Web Server: <span style="color: #7f8c8d;">Desactivat</span>')

        self.ip_label.setFont(QFont("Arial", 10, QFont.Weight.Bold))
        header_layout.addWidget(self.ip_label)

        main_layout.addLayout(header_layout)

        # 2. PANELL D'AJUSTOS EN DIRECTE HDMI
        if self.hdmi_enabled:
            main_layout.addWidget(self.crear_panel_ajustos_estil())

        # 3. PANELL D'ENTRADA D'ÀUDIO I VÚMETRE
        audio_box = QGroupBox("🎙️ Control d'Entrada d'Àudio")
        audio_layout = QHBoxLayout(audio_box)
        audio_layout.setContentsMargins(12, 18, 12, 12)

        audio_layout.addWidget(QLabel("Dispositiu:"))
        self.combo_audio_device = QComboBox()
        self.combo_audio_device.currentIndexChanged.connect(self.canviar_dispositiu_mic)
        audio_layout.addWidget(self.combo_audio_device, 3)

        audio_layout.addSpacing(15)

        self.chk_activar_vu = QCheckBox("Activar Vúmetre")
        self.chk_activar_vu.setChecked(False)
        self.chk_activar_vu.toggled.connect(self.toggle_vumetre)
        audio_layout.addWidget(self.chk_activar_vu)

        self.vu_meter = QProgressBar()
        self.vu_meter.setRange(0, 100)
        self.vu_meter.setValue(0)
        self.vu_meter.setTextVisible(False)
        self.vu_meter.setFixedHeight(18)
        self.vu_meter.setEnabled(False)
        audio_layout.addWidget(self.vu_meter, 2)

        main_layout.addWidget(audio_box)

        # 4. CONFIGURACIÓ D'IDIOMES I HDMI
        config_box = QGroupBox("Configuració ASR, Traducció i Control de Sortida" + (" HDMI" if self.hdmi_enabled else ""))
        config_layout = QHBoxLayout(config_box)
        config_layout.setContentsMargins(12, 18, 12, 12)

        col_asr = QVBoxLayout()
        col_asr.addWidget(QLabel("Idioma Entrada (ASR):"))
        self.combo_lang_in = QComboBox()
        default_lang_in = CONFIG_AUTO.get('input_language', 'fr')
        idx_in_selected = 0
        for idx, (code, name) in enumerate(self.languages_cfg.items()):
            self.combo_lang_in.addItem(str(name), str(code))
            if str(code).lower() == str(default_lang_in).lower():
                idx_in_selected = idx
        self.combo_lang_in.setCurrentIndex(idx_in_selected)
        col_asr.addWidget(self.combo_lang_in)

        col_asr.addSpacing(10)

        is_trans_enabled = self.translation_cfg.get('enabled', True)
        self.chk_translation = QCheckBox("Activar Traducció")
        self.chk_translation.setChecked(is_trans_enabled)
        self.chk_translation.toggled.connect(self.on_toggle_translation_chk)
        col_asr.addWidget(self.chk_translation)

        self.combo_lang_target = QComboBox()
        target_default = self.translation_cfg.get('target_language', 'ca')
        idx_target_selected = 0
        for idx, (code, name) in enumerate(self.languages_cfg.items()):
            self.combo_lang_target.addItem(str(name), str(code))
            if str(code).lower() == str(target_default).lower():
                idx_target_selected = idx
        self.combo_lang_target.setCurrentIndex(idx_target_selected)
        col_asr.addWidget(self.combo_lang_target)

        self.btn_aplicar_idiomes = QPushButton("⚡ Aplicar Idiomes")
        self.btn_aplicar_idiomes.setStyleSheet("background-color: #0277BD; color: white;")
        self.btn_aplicar_idiomes.clicked.connect(self.aplicar_idiomes_manual)
        col_asr.addWidget(self.btn_aplicar_idiomes)

        col_asr.addStretch()
        config_layout.addLayout(col_asr, 2)
        config_layout.addSpacing(20)

        if self.hdmi_enabled:
            col_hdmi = QVBoxLayout()
            col_hdmi.addWidget(QLabel("Sortida Projector HDMI:"))

            hdmi_table_layout = QHBoxLayout()
            self.table_hdmi = QTableWidget()
            self.table_hdmi.setColumnCount(3)
            self.table_hdmi.setHorizontalHeaderLabels(["Actiu i Canal", "Mostra", "Acció"])
            self.table_hdmi.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
            self.table_hdmi.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
            self.table_hdmi.setMinimumHeight(110)

            hd_header = self.table_hdmi.horizontalHeader()
            hd_header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
            hd_header.setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
            hd_header.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
            self.table_hdmi.setColumnWidth(1, 60)
            self.table_hdmi.setColumnWidth(2, 110)

            hdmi_table_layout.addWidget(self.table_hdmi, 4)

            btn_order_box = QVBoxLayout()
            self.btn_move_up = QPushButton("⬆️ Pujar")
            self.btn_move_up.clicked.connect(self.moure_canal_amunt)
            btn_order_box.addWidget(self.btn_move_up)

            self.btn_move_down = QPushButton("⬇️ Baixar")
            self.btn_move_down.clicked.connect(self.moure_canal_avall)
            btn_order_box.addWidget(self.btn_move_down)
            btn_order_box.addStretch()

            hdmi_table_layout.addLayout(btn_order_box, 1)
            col_hdmi.addLayout(hdmi_table_layout)

            config_layout.addLayout(col_hdmi, 4)

        main_layout.addWidget(config_box)

        # 5. PREVISUALITZACIÓ
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

        # 6. CONTROLS DE SESSIÓ
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

        # 7. LOG
        log_box = QGroupBox("Historial de Transcripció i Traducció")
        log_layout = QVBoxLayout(log_box)
        self.log_edit = QTextEdit()
        self.log_edit.setReadOnly(True)
        log_layout.addWidget(self.log_edit)
        main_layout.addWidget(log_box)

    def dibuixar_taula_hdmi(self):
        if not self.hdmi_enabled or not hasattr(self, 'table_hdmi'):
            return

        self.table_hdmi.blockSignals(True)
        self.table_hdmi.setRowCount(len(self.hdmi_items_data))

        for row, data in enumerate(self.hdmi_items_data):
            self.table_hdmi.setRowHeight(row, 36)

            cb = QCheckBox(data['label'])
            cb.setChecked(data['enabled'])
            cb.setStyleSheet("font-weight: bold; margin-left: 8px; color: #FFFFFF;")
            cb.toggled.connect(lambda val, r=row: self.on_checkbox_hdmi_toggled(r, val))
            self.table_hdmi.setCellWidget(row, 0, cb)

            sample = QLabel()
            sample.setFixedSize(22, 22)
            sample.setStyleSheet(f"background-color: {data['color']}; border: 2px solid white; border-radius: 4px;")
            
            sample_container = QWidget()
            s_layout = QHBoxLayout(sample_container)
            s_layout.setContentsMargins(0, 0, 0, 0)
            s_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
            s_layout.addWidget(sample)
            self.table_hdmi.setCellWidget(row, 1, sample_container)

            btn_col = QPushButton("🎨 Color")
            btn_col.setCursor(Qt.CursorShape.PointingHandCursor)
            btn_col.setStyleSheet("""
                QPushButton {
                    background-color: #383838;
                    color: #FFFFFF;
                    border: 1px solid #777777;
                    border-radius: 4px;
                    font-weight: bold;
                    padding: 3px 10px;
                }
                QPushButton:hover {
                    background-color: #505050;
                    border: 1px solid #00E676;
                }
            """)
            btn_col.clicked.connect(lambda _, r=row: self.triar_color_canal(r))
            self.table_hdmi.setCellWidget(row, 2, btn_col)

        self.table_hdmi.blockSignals(False)

    def on_checkbox_hdmi_toggled(self, row, val):
        if 0 <= row < len(self.hdmi_items_data):
            self.hdmi_items_data[row]['enabled'] = val
            self.refrescar_projector()

    def triar_color_canal(self, row):
        if not (0 <= row < len(self.hdmi_items_data)):
            return
        color = QColorDialog.getColor()
        if color.isValid():
            self.hdmi_items_data[row]['color'] = color.name()
            self.dibuixar_taula_hdmi()
            self.refrescar_projector()

    def moure_canal_amunt(self):
        if not self.hdmi_enabled or not hasattr(self, 'table_hdmi'):
            return
        curr_row = self.table_hdmi.currentRow()
        if curr_row > 0:
            self.hdmi_items_data[curr_row], self.hdmi_items_data[curr_row - 1] = (
                self.hdmi_items_data[curr_row - 1], self.hdmi_items_data[curr_row]
            )
            self.dibuixar_taula_hdmi()
            self.table_hdmi.selectRow(curr_row - 1)
            self.refrescar_projector()

    def moure_canal_avall(self):
        if not self.hdmi_enabled or not hasattr(self, 'table_hdmi'):
            return
        curr_row = self.table_hdmi.currentRow()
        if 0 <= curr_row < len(self.hdmi_items_data) - 1:
            self.hdmi_items_data[curr_row], self.hdmi_items_data[curr_row + 1] = (
                self.hdmi_items_data[curr_row + 1], self.hdmi_items_data[curr_row]
            )
            self.dibuixar_taula_hdmi()
            self.table_hdmi.selectRow(curr_row + 1)
            self.refrescar_projector()

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

        btn_bg_color = QPushButton("⬛ Fons HDMI")
        btn_bg_color.clicked.connect(self.canviar_fons_projector)
        layout.addWidget(btn_bg_color)

        layout.addStretch()
        return group_box

    def actualitzar_font_projector(self, font_name):
        if self.projector_window:
            self.projector_window.set_font_family(font_name)
            self.refrescar_projector()

    def actualitzar_mida_projector(self, size_str):
        if self.projector_window:
            self.projector_window.set_font_size(int(size_str))
            self.refrescar_projector()

    def canviar_fons_projector(self):
        if not self.projector_window:
            return
        color = QColorDialog.getColor()
        if color.isValid():
            self.projector_window.set_bg_color(color.name())

    def refrescar_projector(self):
        if not self.hdmi_enabled or not self.projector_window or self.is_blackout:
            return
        
        textos_map = {
            'orig': self.raw_text_orig,
            'trans': self.raw_text_trans if self.chk_translation.isChecked() else ""
        }

        llista_hdmi = []
        for item in self.hdmi_items_data:
            if item['enabled']:
                text_item = textos_map.get(item['id'], "")
                if text_item:
                    llista_hdmi.append((str(text_item), item['color']))
            
        self.projector_window.actualitzar_textos(llista_hdmi)

    def on_toggle_translation_chk(self, enabled):
        self.combo_lang_target.setEnabled(enabled)
        self.box_trans_widget.setVisible(enabled)
        if self.transcriptor:
            self.transcriptor.translation_enabled = enabled
        self.refrescar_projector()

    def toggle_projector_window(self):
        if not self.projector_window:
            return
        if self.projector_window.isVisible():
            self.projector_window.hide()
        else:
            self.projector_window.show()

    def aplicar_idiomes_manual(self):
        lang_in = self.combo_lang_in.currentData() or CONFIG_AUTO.get('input_language', 'fr')
        lang_target = self.combo_lang_target.currentData() or self.translation_cfg.get('target_language', 'ca')
        trans_on = self.chk_translation.isChecked()

        self.log(f"⚙️ Sol·licitant canvi: Entrada={lang_in}, Traducció={lang_target} (Activa: {trans_on})")

        self.raw_text_orig = ""
        self.raw_text_trans = ""
        self.ultim_log_orig = ""
        self.ultim_log_trans = ""
        self.display_orig.setText(f"[ Connectant ASR a {lang_in}... ]")
        if trans_on:
            self.display_trans.setText(f"[ Connectant Traducció a {lang_target}... ]")

        if self.session_started and not self.is_paused:
            if self.audio_thread:
                self.audio_thread.stop()
                self.audio_thread.wait(600)
                self.audio_thread = None

            if self.transcriptor:
                self.transcriptor.aturar_escolta()
                self.transcriptor = None

            config_trans = {
                "whisper_live": WHISPER_CFG,
                "input_language": lang_in,
                "translation": {
                    "enabled": trans_on,
                    "target_language": lang_target,
                },
                "audio": {
                    "samplerate": self.audio_cfg.get('samplerate', 16000),
                    "block_ms": self.audio_cfg.get('block_ms', 100),
                    "device": self.combo_audio_device.currentData(),
                },
                "show_info": True
            }

            self.transcriptor = TranscriptorWhisperLiveKit(config=config_trans)

            self.audio_thread = AudioThread(self.transcriptor)
            self.audio_thread.subtitol_generat.connect(self.on_nou_subtitol)
            self.audio_thread.start()
            self.log(f"✅ Reconnectat a {lang_in} -> {lang_target}.")
        else:
            if self.transcriptor:
                self.transcriptor.language = lang_in
                self.transcriptor.target_language = lang_target
                self.transcriptor.translation_enabled = trans_on

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

        # Llegim exactament els valors visuals seleccionats a la interfície
        lang_in = self.combo_lang_in.currentData() or CONFIG_AUTO.get('input_language', 'fr')
        lang_target = self.combo_lang_target.currentData() or self.translation_cfg.get('target_language', 'ca')
        trans_on = self.chk_translation.isChecked()
        audio_dev = self.combo_audio_device.currentData()

        config_trans = {
            "whisper_live": WHISPER_CFG,
            "input_language": lang_in,
            "translation": {
                "enabled": trans_on,
                "target_language": lang_target,
            },
            "audio": {
                "samplerate": self.audio_cfg.get('samplerate', 16000),
                "block_ms": self.audio_cfg.get('block_ms', 100),
                "device": audio_dev,
            },
            "show_info": True
        }

        self.transcriptor = TranscriptorWhisperLiveKit(config=config_trans)

        try:
            self.transcriptor.inicialitzar_motor()
        except Exception as e:
            QMessageBox.critical(self, "Error d'Àudio", f"No s'ha pogut iniciar l'àudio: {e}")
            return

        self.is_blackout = False
        self.session_started = True
        self.is_paused = False
        self.timer.start()

        self.audio_thread = AudioThread(self.transcriptor)
        self.audio_thread.subtitol_generat.connect(self.on_nou_subtitol)
        self.audio_thread.start()

        self.btn_start_stop.setText("⏹️ Stop")
        self.btn_start_stop.setStyleSheet("background-color: #C62828; color: white;")
        self.btn_pause.setEnabled(True)

        self.display_orig.setText(f"[ Escoltant en {lang_in}... ]")
        if trans_on:
            self.display_trans.setText(f"[ Traduint a {lang_target}... ]")
        self.log(f"🚀 Sessió iniciada: Entrada={lang_in}, Traducció={lang_target} (Activa: {trans_on})")

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
            
            if self.hdmi_enabled and self.projector_window:
                self.projector_window.blackout()

            if self.web_enabled:
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
        if ms_to_time_str:
            self.lbl_total_time.setText(ms_to_time_str(self.total_elapsed_ms))

    def on_nou_subtitol(self, dades):
        if self.is_paused or self.is_blackout or not dades:
            return

        if isinstance(dades, str):
            text_orig = dades
            text_trans = ""
            definitiu_orig = ""
            definitiu_trans = ""
            es_consolidat = False
        else:
            text_orig = str(dades.get("original", "")).strip()
            text_trans = str(dades.get("translation", "")).strip() if self.chk_translation.isChecked() else ""
            definitiu_orig = str(dades.get("original_definitiu", "")).strip()
            definitiu_trans = str(dades.get("translation_definitiu", "")).strip() if self.chk_translation.isChecked() else ""
            es_consolidat = dades.get("es_consolidat", False)

        self.raw_text_orig = text_orig
        self.raw_text_trans = text_trans

        ample_op_orig = max(100, self.display_orig.width() - 25)
        text_disp_orig = ajustar_text_a_linies_reals_qt(
            text_orig, 
            self.display_orig.font(), 
            ample_op_orig, 
            max_lines=self.max_lines_hdmi
        )
        self.display_orig.setText(text_disp_orig)

        if self.chk_translation.isChecked():
            ample_op_trans = max(100, self.display_trans.width() - 25)
            text_disp_trans = ajustar_text_a_linies_reals_qt(
                text_trans, 
                self.display_trans.font(), 
                ample_op_trans, 
                max_lines=self.max_lines_hdmi
            )
            self.display_trans.setText(text_disp_trans)

        if self.hdmi_enabled:
            self.refrescar_projector()

        if self.web_enabled:
            def obtenir_text_recent(text, max_paraules=25):
                if not text:
                    return ""
                paraules = str(text).strip().split()
                if len(paraules) > max_paraules:
                    return "... " + " ".join(paraules[-max_paraules:])
                return str(text)

            web_orig = obtenir_text_recent(text_orig, max_paraules=25)
            web_trans = obtenir_text_recent(text_trans, max_paraules=25)

            if web_orig:
                self.emissor.emetre("auto_original", web_orig)
            if web_trans and self.chk_translation.isChecked():
                self.emissor.emetre("auto_translation", web_trans)

        if es_consolidat and definitiu_orig and definitiu_orig != self.ultim_log_orig:
            nou_tros_orig = definitiu_orig[len(self.ultim_log_orig):].strip()
            nou_tros_trans = definitiu_trans[len(self.ultim_log_trans):].strip() if self.chk_translation.isChecked() else ""

            if nou_tros_orig:
                timestamp = ms_to_time_str(self.total_elapsed_ms) if ms_to_time_str else "00:00"
                self.log_edit.append(f"[{timestamp}] 🟢 {nou_tros_orig}")
                if nou_tros_trans:
                    self.log_edit.append(f"[{timestamp}] 🔵 {nou_tros_trans}")
                self.log_edit.moveCursor(QTextCursor.MoveOperation.End)

                self.ultim_log_orig = definitiu_orig
                self.ultim_log_trans = definitiu_trans

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_B:
            self.toggle_blackout()
        else:
            super().keyPressEvent(event)

    def closeEvent(self, event):
        self.stop_session()

        if self.monitor_thread:
            self.monitor_thread.stop()

        stop_wlk = WHISPER_CFG.get('stop_on_exit', True)
        if stop_wlk:
            port = WHISPER_CFG.get('port', 8000)
            aturar_servidor_wlk_complet(self.wlk_subprocess, port=port)

        try:
            if self.hdmi_enabled and hasattr(self, 'projector_window') and self.projector_window:
                self.projector_window.close()
        except Exception as e:
            print(f"Error tancant el projector: {e}")
        event.accept()

    def detectar_segona_pantalla(self):
        if self.hdmi_enabled and self.projector_window:
            self.projector_window.assignar_a_segona_pantalla()


if __name__ == "__main__":
    app = QApplication(sys.argv)

    emissor = None
    if WEB_SERVER_ENABLED and EmissorWeb is not None:
        try:
            emissor = EmissorWeb(
                config_server=CONFIG_SERVER,
                mode="AUTOMÀTIC",
                config_styles=STYLES_CFG
            )
            emissor.iniciar()
        except Exception as e:
            print(f"⚠️ Alerta en iniciar el servidor web: {e}")

    window = TeloAutoPyQt(emissor)
    window.show()

    sys.exit(app.exec())
