import sys
import os
import re
import time
import socket
import subprocess
import signal
import math
import numpy as np
import yaml

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, 
    QPushButton, QLabel, QTableWidget, QTableWidgetItem, QFileDialog, 
    QHeaderView, QComboBox, QAbstractItemView, QGroupBox,
    QMessageBox, QColorDialog, QCheckBox, QProgressBar, QTextEdit,
    QLineEdit
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
    from sincronitzador import Sincronitzador
except ImportError as e:
    print(f"⚠️ Error en importar sincronitzador des de src/: {e}")
    Sincronitzador = None

try:
    from utils import ms_to_time_str, srt_time_to_ms
except ImportError as e:
    print(f"⚠️ Error en importar mòduls compartits des de src/: {e}")
    ms_to_time_str = None
    srt_time_to_ms = None


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
CONFIG_MANUAL = CONFIG_FULL.get('manual', {})
STYLES_CFG = CONFIG_COMMON.get('styles', {})
WHISPER_CFG = CONFIG_AUTO.get('whisper_live', {})

CONFIG_SERVER = CONFIG_COMMON.get('server', {'host': '0.0.0.0', 'port': 5000, 'enabled': True})
if CONFIG_SERVER.get('host') == '00.0.0':
    CONFIG_SERVER['host'] = '0.0.0.0'

WEB_SERVER_ENABLED = CONFIG_SERVER.get('enabled', True)
HDMI_ENABLED = STYLES_CFG.get('hdmi', {}).get('enabled', True)

LANGUAGES_CFG = CONFIG_COMMON.get('languages', {'ca': 'Català', 'es': 'Español', 'fr': 'Français'})
IDIOMES_CONFIG = [{'id': str(k).upper(), 'label': str(v)} for k, v in LANGUAGES_CFG.items()]
IDIOMES_MAP = {item['id']: item['label'] for item in IDIOMES_CONFIG}
IDIOMES_ORDRE = [item['id'] for item in IDIOMES_CONFIG]


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


class ProjectorSincroWindow(QMainWindow):
    def __init__(self, app_title="TELÓ Sincro", config_proj=None):
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
        # 1. Eliminem marcs, vores i barra de títol per a projecció neta
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

    def actualitzar_textos(self, llista_textos_colors):
        self._ultims_textos = llista_textos_colors

        while len(self.labels) < len(llista_textos_colors):
            lbl = QLabel()
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl.setWordWrap(True)
            self.layout_proj.addWidget(lbl, 1)
            self.labels.append(lbl)

        while len(self.labels) > len(llista_textos_colors):
            lbl = self.labels.pop()
            self.layout_proj.removeWidget(lbl)
            lbl.deleteLater()

        num_lignes = len(llista_textos_colors)
        if num_lignes >= 4:
            font_size = max(18, int(self.base_font_size * 0.52))
        elif num_lignes == 3:
            font_size = max(22, int(self.base_font_size * 0.68))
        elif num_lignes == 2:
            font_size = max(26, int(self.base_font_size * 0.85))
        else:
            font_size = self.base_font_size

        font = QFont(self.base_font_family, font_size, QFont.Weight.Bold)
        ample_disponible_px = max(100, self.central_widget.width() - 60)

        for i, (text, color, is_fallback) in enumerate(llista_textos_colors):
            lbl = self.labels[i]
            lbl.setFont(font)
            text_str = str(text)
            if is_fallback:
                text_ajustat = ajustar_text_a_linies_reals_qt(
                    text_str, font, ample_disponible_px, max_lines=self.max_lines
                )
                lbl.setText(f"~ {text_ajustat}")
                lbl.setStyleSheet(f"color: {color}; font-style: italic; background-color: transparent;")
            else:
                text_ajustat = ajustar_text_a_linies_reals_qt(
                    text_str, font, ample_disponible_px, max_lines=self.max_lines
                )
                lbl.setText(text_ajustat)
                lbl.setStyleSheet(f"color: {color}; font-style: normal; background-color: transparent;")

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


class TeloSincroPyQt(QMainWindow):
    PALETA_PER_DEFECTE = ["#00E676", "#29B6F6", "#FFCA28", "#FF80AB", "#FFFFFF", "#E040FB"]

    def __init__(self, emissor):
        super().__init__()
        self.emissor = emissor
        self.web_enabled = WEB_SERVER_ENABLED and (self.emissor is not None)
        self.hdmi_enabled = HDMI_ENABLED

        self.subtitles = []
        self.current_index = -1
        self.detected_langs = []
        self.idiomes_colors_mem = {}
        self.hdmi_langs_data = []

        self.session_started = False
        self.is_paused = False
        self.is_blackout = False
        self.auto_sincro_active = True
        self.forced_auto_mode = False
        self.is_currently_auto_mode = False

        self.total_elapsed_ms = 0
        self.sub_elapsed_ms = 0

        self.wlk_subprocess = None
        self.transcriptor = None
        self.audio_thread = None
        self.monitor_thread = None

        if Sincronitzador is not None:
            self.sincronitzador = Sincronitzador(
                score_threshold=72,
                no_offset_threshold=58,
                offset_penalty=2,
                base_window=4,
                max_window=15,
                desync_threshold=2
            )
        else:
            self.sincronitzador = None

        interface_cfg = CONFIG_COMMON.get('interface', {})
        self.app_title = interface_cfg.get('title', 'teloSincro')

        colors_cfg = interface_cfg.get('colors', {})
        self.color_bg = colors_cfg.get('background', '#121212')
        self.color_text = colors_cfg.get('text', '#E0E0E0')
        self.color_primary = colors_cfg.get('primary', '#00E676')
        self.color_secondary = colors_cfg.get('secondary', '#29B6F6')
        self.color_panel_bg = colors_cfg.get('panel_bg', '#1E1E1E')
        self.color_btn_bg = colors_cfg.get('button_bg', '#252525')
        self.color_btn_border = colors_cfg.get('button_border', '#444444')

        self.default_guide_lang = str(CONFIG_MANUAL.get('default_guide_language', 'CAT')).upper()
        self.max_lines_hdmi = STYLES_CFG.get('hdmi', {}).get('max_lines', 3)
        self.projector_cfg = STYLES_CFG.get('hdmi', {})

        self.timer = QTimer(self)
        self.timer.setInterval(100)
        self.timer.timeout.connect(self.update_timers)

        if self.hdmi_enabled:
            self.projector_window = ProjectorSincroWindow(app_title=self.app_title, config_proj=self.projector_cfg)
        else:
            self.projector_window = None

        # 1. UI construïda primer (assegura existència de self.log_edit)
        self.init_ui()
        self.inicialitzar_llista_idiomes_per_defecte()

        if self.hdmi_enabled and self.projector_window:
            QTimer.singleShot(250, self.detectar_segona_pantalla)

        # 2. Inicialització de subsistemes d'àudio i connexió
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

        lang_in = CONFIG_AUTO.get('input_language', 'fr')
        target_lang = CONFIG_AUTO.get('translation', {}).get('target_language', 'ca')
        dev = self.combo_audio_device.currentData() if hasattr(self, 'combo_audio_device') else None

        config_trans = {
            "whisper_live": WHISPER_CFG,
            "input_language": lang_in,
            "translation": {
                "enabled": True,
                "target_language": target_lang
            },
            "audio": {
                "samplerate": CONFIG_AUTO.get('audio', {}).get('samplerate', 16000),
                "block_ms": CONFIG_AUTO.get('audio', {}).get('block_ms', 100),
                "device": dev,
            },
            "show_info": False
        }
        try:
            self.transcriptor = TranscriptorWhisperLiveKit(config=config_trans)
            self.transcriptor.inicialitzar_motor()
        except Exception as e:
            self.log(f"⚠️ Alerta creant el motor del transcriptor: {e}")
            self.transcriptor = None

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
        self.setWindowTitle(f"{self.app_title} ⚡ - Sincronitzador Intel·ligent")
        self.resize(1260, 990)

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
            QLineEdit {{ background-color: {self.color_btn_bg}; border: 1px solid {self.color_btn_border}; color: {self.color_text}; padding: 4px 8px; border-radius: 4px; }}
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
        title_label = QLabel(f"{self.app_title} ⚡")
        title_label.setFont(QFont("Arial", 16, QFont.Weight.Bold))
        title_label.setStyleSheet(f"color: {self.color_primary};")
        header_layout.addWidget(title_label)

        btn_open = QPushButton("📂 Obrir arxiu .SRT")
        btn_open.clicked.connect(self.open_srt_file)
        header_layout.addWidget(btn_open)

        if self.hdmi_enabled:
            btn_toggle_proj = QPushButton("🖥️ Projector HDMI")
            btn_toggle_proj.clicked.connect(self.toggle_projector_window)
            header_layout.addWidget(btn_toggle_proj)

        self.chk_auto_sincro = QCheckBox("🔄 Mode Sincro Automàtic")
        self.chk_auto_sincro.setChecked(True)
        self.chk_auto_sincro.toggled.connect(self.on_toggle_auto_sincro)
        self.chk_auto_sincro.setStyleSheet("color: #00E676; font-size: 11pt;")
        header_layout.addWidget(self.chk_auto_sincro)

        self.btn_force_auto = QPushButton("🤖 Forçar Mode Auto (OFF)")
        self.btn_force_auto.setCheckable(True)
        self.btn_force_auto.setStyleSheet("background-color: #37474F; color: #FFFFFF; font-weight: bold;")
        self.btn_force_auto.toggled.connect(self.on_toggle_force_auto)
        header_layout.addWidget(self.btn_force_auto)

        header_layout.addStretch()

        if self.web_enabled:
            ip = self.emissor.obtenir_ip_local() if hasattr(self.emissor, 'obtenir_ip_local') else "127.0.0.1"
            port = self.emissor.port if hasattr(self.emissor, 'port') else 5000
            url_web = f"http://{ip}:{port}"
            self.ip_label = QLabel(f'🌐 Web Server: <a href="{url_web}" style="color: {self.color_primary};">{url_web}</a>')
            self.ip_label.setOpenExternalLinks(True)
        else:
            self.ip_label = QLabel('🌐 Web Server: <span style="color: #7f8c8d;">Desactivat</span>')

        self.ip_label.setFont(QFont("Arial", 10, QFont.Weight.Bold))
        header_layout.addWidget(self.ip_label)

        main_layout.addLayout(header_layout)

        # 2. PANELL D'AJUSTOS EN DIRECTE HDMI (Com a teloAuto)
        if self.hdmi_enabled:
            main_layout.addWidget(self.crear_panel_ajustos_estil())

        # 3. AUDIO & VÚMETRE
        audio_box = QGroupBox("🎙️ Control d'Entrada d'Àudio i Nivell")
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

        # 4. PANELS DE MONITOR DUAL
        display_layout = QHBoxLayout()

        box_live = QVBoxLayout()
        lbl_live_title = QLabel("ENTRADA DE VEU EN DIRECTE (ASR) I TRADUCCIÓ")
        lbl_live_title.setStyleSheet("color: #FFA726; font-weight: bold;")
        self.display_live_asr = QLabel("[ Esperant veu de l'actor / ponent... ]")
        self.display_live_asr.setFont(QFont("Arial", 11, QFont.Weight.Bold))
        self.display_live_asr.setWordWrap(True)
        self.display_live_asr.setStyleSheet(f"""
            background-color: {self.color_panel_bg};
            border: 2px solid #FFA726;
            border-radius: 8px;
            padding: 10px;
            min-height: 55px;
        """)
        box_live.addWidget(lbl_live_title)
        box_live.addWidget(self.display_live_asr)
        display_layout.addLayout(box_live, 1)

        box_active = QVBoxLayout()
        self.lbl_active_title = QLabel("SUBTÍTOL PROJECTAT (GUIÓ OFICIAL)")
        self.lbl_active_title.setStyleSheet(f"color: {self.color_primary}; font-weight: bold;")
        self.display_active_sub = QLabel("Carrega un fitxer .SRT per començar")
        self.display_active_sub.setFont(QFont("Arial", 12, QFont.Weight.Bold))
        self.display_active_sub.setWordWrap(True)
        self.display_active_sub.setStyleSheet(f"""
            background-color: {self.color_panel_bg};
            border: 2px solid {self.color_primary};
            border-radius: 8px;
            padding: 10px;
            min-height: 55px;
        """)
        box_active.addWidget(self.lbl_active_title)
        box_active.addWidget(self.display_active_sub)
        display_layout.addLayout(box_active, 1)

        main_layout.addLayout(display_layout)

        # 5. CONFIGURACIÓ IDIOMES I SORTIDA HDMI
        config_box = QGroupBox("Estat de la Sincronització i Configuració d'Idiomes" + (" HDMI" if self.hdmi_enabled else ""))
        config_layout = QHBoxLayout(config_box)
        config_layout.setContentsMargins(12, 18, 12, 12)

        col_esq = QVBoxLayout()
        col_esq.addWidget(QLabel("Idioma Guia (comparació amb veu):"))
        self.combo_guia = QComboBox()
        self.combo_guia.currentIndexChanged.connect(self.on_guia_lang_changed)
        col_esq.addWidget(self.combo_guia)

        self.lbl_estat_sincro = QLabel("🟢 Sincronització preparada")
        self.lbl_estat_sincro.setStyleSheet("color: #00E676; font-weight: bold; font-size: 11pt; margin-top: 5px;")
        col_esq.addWidget(self.lbl_estat_sincro)
        col_esq.addStretch()
        config_layout.addLayout(col_esq, 2)

        config_layout.addSpacing(20)

        # Taula Idiomes HDMI
        if self.hdmi_enabled:
            col_hdmi = QVBoxLayout()
            col_hdmi.addWidget(QLabel("Idiomes a Projectar a HDMI:"))

            hdmi_table_layout = QHBoxLayout()
            self.table_hdmi = QTableWidget()
            self.table_hdmi.setColumnCount(3)
            self.table_hdmi.setRowCount(0)
            self.table_hdmi.setHorizontalHeaderLabels(["Actiu i Idioma", "Mostra", "Acció"])
            self.table_hdmi.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
            self.table_hdmi.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
            self.table_hdmi.setMinimumHeight(130)

            hd_h = self.table_hdmi.horizontalHeader()
            hd_h.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
            hd_h.setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
            hd_h.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
            self.table_hdmi.setColumnWidth(1, 60)
            self.table_hdmi.setColumnWidth(2, 110)

            hdmi_table_layout.addWidget(self.table_hdmi, 4)

            btn_order_box = QVBoxLayout()
            self.btn_move_up = QPushButton("⬆️ Pujar")
            self.btn_move_up.clicked.connect(self.moure_idioma_amunt)
            btn_order_box.addWidget(self.btn_move_up)

            self.btn_move_down = QPushButton("⬇️ Baixar")
            self.btn_move_down.clicked.connect(self.moure_idioma_avall)
            btn_order_box.addWidget(self.btn_move_down)
            btn_order_box.addStretch()

            hdmi_table_layout.addLayout(btn_order_box, 1)
            col_hdmi.addLayout(hdmi_table_layout)

            config_layout.addLayout(col_hdmi, 4)

        main_layout.addWidget(config_box)

        # 6. CONTROLS DE SESSIÓ I TEMPS
        timer_box = QGroupBox("Control Temporal i Sessió")
        timer_layout = QHBoxLayout(timer_box)

        self.btn_start_stop = QPushButton("▶️ Start")
        self.btn_start_stop.setStyleSheet("background-color: #1b5e20; color: white;")
        self.btn_start_stop.clicked.connect(self.on_click_start_stop)
        timer_layout.addWidget(self.btn_start_stop)

        self.btn_pause = QPushButton("⏸ Pause")
        self.btn_pause.setEnabled(False)
        self.btn_pause.clicked.connect(self.toggle_pause)
        timer_layout.addWidget(self.btn_pause)

        timer_layout.addSpacing(20)

        timer_layout.addWidget(QLabel("Temps Total:"))
        self.lbl_total_time = QLabel("00:00")
        self.lbl_total_time.setFont(QFont("Arial", 12, QFont.Weight.Bold))
        self.lbl_total_time.setStyleSheet(f"color: {self.color_primary}; background-color: #1a1a1a; padding: 4px 8px; border-radius: 4px;")
        timer_layout.addWidget(self.lbl_total_time)

        timer_layout.addSpacing(15)

        timer_layout.addWidget(QLabel("En Frase:"))
        self.lbl_sub_elapsed = QLabel("00:00")
        self.lbl_sub_elapsed.setFont(QFont("Arial", 12, QFont.Weight.Bold))
        self.lbl_sub_elapsed.setStyleSheet(f"color: {self.color_secondary}; background-color: #1a1a1a; padding: 4px 8px; border-radius: 4px;")
        timer_layout.addWidget(self.lbl_sub_elapsed)

        timer_layout.addSpacing(15)

        timer_layout.addWidget(QLabel("Següent (teòric):"))
        self.lbl_sub_countdown = QLabel("--:--")
        self.lbl_sub_countdown.setFont(QFont("Arial", 12, QFont.Weight.Bold))
        self.lbl_sub_countdown.setStyleSheet("color: #ffca28; background-color: #1a1a1a; padding: 4px 8px; border-radius: 4px;")
        timer_layout.addWidget(self.lbl_sub_countdown)

        timer_layout.addStretch()
        main_layout.addWidget(timer_box)

        # 7. CONTROLS DE NAVEGACIÓ MANUAL DE RECOLZAMENT
        ctrl_layout = QHBoxLayout()

        self.btn_prev = QPushButton("⏮ Anterior (←)")
        self.btn_prev.setStyleSheet("padding: 8px;")
        self.btn_prev.clicked.connect(self.prev_sub)
        self.btn_prev.setEnabled(False)
        ctrl_layout.addWidget(self.btn_prev)

        self.btn_blackout = QPushButton("⬛ Blackout (B)")
        self.btn_blackout.setStyleSheet("background-color: #C62828; color: white; padding: 8px;")
        self.btn_blackout.clicked.connect(self.toggle_blackout)
        ctrl_layout.addWidget(self.btn_blackout)

        self.btn_next = QPushButton("Següent (Espai / →) ⏭")
        self.btn_next.setStyleSheet("background-color: #2E7D32; color: white; padding: 8px;")
        self.btn_next.clicked.connect(self.next_sub)
        self.btn_next.setEnabled(False)
        ctrl_layout.addWidget(self.btn_next)

        main_layout.addLayout(ctrl_layout)

        # 8. BARRA DE CERCA RÀPIDA AL GUIÓ
        search_layout = QHBoxLayout()
        search_layout.addWidget(QLabel("🔎 Cerca ràpida al guió:"))
        self.input_search = QLineEdit()
        self.input_search.setPlaceholderText("Escriu el text a cercar a continuació del subtítol actual...")
        self.input_search.returnPressed.connect(self.cercar_subtitol_seguent)
        search_layout.addWidget(self.input_search, 4)

        self.btn_search = QPushButton("🔍 Següent")
        self.btn_search.setStyleSheet("background-color: #0277BD; color: white;")
        self.btn_search.clicked.connect(self.cercar_subtitol_seguent)
        search_layout.addWidget(self.btn_search, 1)

        main_layout.addLayout(search_layout)

        # 9. GRAELLA DE SUBTÍTOLS I HISTORIAL LOG
        lower_layout = QHBoxLayout()

        self.table = QTableWidget()
        self.table.setColumnCount(3)
        self.table.setRowCount(0)
        self.table.setHorizontalHeaderLabels(["#", "Temps", "Text del Guió"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.cellClicked.connect(self.on_cell_clicked)

        tbl_h = self.table.horizontalHeader()
        tbl_h.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        tbl_h.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        tbl_h.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)

        lower_layout.addWidget(self.table, 3)

        log_box = QGroupBox("Historial i Diagnòstic")
        log_box_layout = QVBoxLayout(log_box)
        self.log_edit = QTextEdit()
        self.log_edit.setReadOnly(True)
        log_box_layout.addWidget(self.log_edit)
        lower_layout.addWidget(log_box, 2)

        main_layout.addLayout(lower_layout)

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
            if self.is_currently_auto_mode:
                pass
            else:
                self.actualitzar_pantalles()

    def actualitzar_mida_projector(self, size_str):
        if self.projector_window:
            self.projector_window.set_font_size(int(size_str))
            if self.is_currently_auto_mode:
                pass
            else:
                self.actualitzar_pantalles()

    def canviar_fons_projector(self):
        if not self.projector_window:
            return
        color = QColorDialog.getColor()
        if color.isValid():
            self.projector_window.set_bg_color(color.name())

    def inicialitzar_llista_idiomes_per_defecte(self):
        self.detected_langs = [item['id'] for item in IDIOMES_CONFIG]
        self.hdmi_langs_data = []
        for i, item in enumerate(IDIOMES_CONFIG):
            color = self.PALETA_PER_DEFECTE[i % len(self.PALETA_PER_DEFECTE)]
            self.hdmi_langs_data.append({
                'lang': item['id'],
                'label': item['label'],
                'color': color,
                'enabled': True
            })
            self.idiomes_colors_mem[item['id']] = color

        self.combo_guia.blockSignals(True)
        self.combo_guia.clear()
        for item in IDIOMES_CONFIG:
            self.combo_guia.addItem(str(item['label']), str(item['id']))
        self.combo_guia.blockSignals(False)

        if self.hdmi_enabled:
            self.dibuixar_taula_hdmi()

    def dibuixar_taula_hdmi(self):
        if not self.hdmi_enabled or not hasattr(self, 'table_hdmi'):
            return

        self.table_hdmi.blockSignals(True)
        self.table_hdmi.setRowCount(len(self.hdmi_langs_data))

        for row, data in enumerate(self.hdmi_langs_data):
            self.table_hdmi.setRowHeight(row, 36)

            cb = QCheckBox(f"[{data['lang']}]  {data['label']}")
            cb.setChecked(data['enabled'])
            cb.setStyleSheet("font-weight: bold; margin-left: 8px; color: #FFFFFF;")
            cb.toggled.connect(lambda val, r=row: self.on_checkbox_toggled(r, val))
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
            btn_col.clicked.connect(lambda _, r=row: self.triar_color_idioma(r))
            self.table_hdmi.setCellWidget(row, 2, btn_col)

        self.table_hdmi.blockSignals(False)

    def on_checkbox_toggled(self, row, val):
        if 0 <= row < len(self.hdmi_langs_data):
            self.hdmi_langs_data[row]['enabled'] = val
            if not self.is_currently_auto_mode:
                self.actualitzar_pantalles()

    def triar_color_idioma(self, row):
        if not (0 <= row < len(self.hdmi_langs_data)):
            return
        color = QColorDialog.getColor()
        if color.isValid():
            self.hdmi_langs_data[row]['color'] = color.name()
            self.idiomes_colors_mem[self.hdmi_langs_data[row]['lang']] = color.name()
            self.dibuixar_taula_hdmi()
            if not self.is_currently_auto_mode:
                self.actualitzar_pantalles()

    def moure_idioma_amunt(self):
        if not self.hdmi_enabled or not hasattr(self, 'table_hdmi'):
            return
        curr_row = self.table_hdmi.currentRow()
        if curr_row > 0:
            self.hdmi_langs_data[curr_row], self.hdmi_langs_data[curr_row - 1] = (
                self.hdmi_langs_data[curr_row - 1], self.hdmi_langs_data[curr_row]
            )
            self.dibuixar_taula_hdmi()
            self.table_hdmi.selectRow(curr_row - 1)
            if not self.is_currently_auto_mode:
                self.actualitzar_pantalles()

    def moure_idioma_avall(self):
        if not self.hdmi_enabled or not hasattr(self, 'table_hdmi'):
            return
        curr_row = self.table_hdmi.currentRow()
        if 0 <= curr_row < len(self.hdmi_langs_data) - 1:
            self.hdmi_langs_data[curr_row], self.hdmi_langs_data[curr_row + 1] = (
                self.hdmi_langs_data[curr_row + 1], self.hdmi_langs_data[curr_row]
            )
            self.dibuixar_taula_hdmi()
            self.table_hdmi.selectRow(curr_row + 1)
            if not self.is_currently_auto_mode:
                self.actualitzar_pantalles()

    def on_toggle_auto_sincro(self, enabled):
        self.auto_sincro_active = enabled
        if enabled:
            self.chk_auto_sincro.setText("🔄 Mode Sincro Automàtic (ON)")
            self.chk_auto_sincro.setStyleSheet("color: #00E676; font-size: 11pt;")
        else:
            self.chk_auto_sincro.setText("🔄 Mode Manual (Sincro OFF)")
            self.chk_auto_sincro.setStyleSheet("color: #FFA726; font-size: 11pt;")

    def on_toggle_force_auto(self, checked):
        self.forced_auto_mode = checked
        if checked:
            self.btn_force_auto.setText("🤖 Forçar Mode Auto (ACTIU)")
            self.btn_force_auto.setStyleSheet("background-color: #E65100; color: #FFFFFF; font-weight: bold;")
            self.lbl_estat_sincro.setText("🤖 Mode Auto Forçat per l'operador (ASR + Traducció)")
            self.lbl_estat_sincro.setStyleSheet("color: #FFA726; font-weight: bold;")
            self.log("ℹ️ L'operador ha activat manualment el Mode Auto.")
        else:
            self.btn_force_auto.setText("🤖 Forçar Mode Auto (OFF)")
            self.btn_force_auto.setStyleSheet("background-color: #37474F; color: #FFFFFF; font-weight: bold;")
            self.is_currently_auto_mode = False
            self.lbl_estat_sincro.setText("🟢 Reenganxant Sincronització...")
            self.lbl_estat_sincro.setStyleSheet("color: #00E676; font-weight: bold;")
            self.log("ℹ️ Desactivat forçat de Mode Auto. Tornant a sincronitzar guió.")
            if self.current_index >= 0:
                self.set_active_sub(self.current_index)

    def open_srt_file(self):
        file_path, _ = QFileDialog.getOpenFileName(self, "Obrir SRT", "", "Subtítols SRT (*.srt)")
        if file_path:
            self.parse_srt(file_path)
            self.configurar_canals_i_ui()
            self.render_grid()
            if self.subtitles:
                self.set_active_sub(0)
                self.btn_next.setEnabled(True)
                self.btn_prev.setEnabled(True)

    def parse_srt(self, path):
        with open(path, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read()

        blocks = content.strip().replace('\r', '').split('\n\n')
        self.subtitles = []
        detected_tag_set = set()

        for block in blocks:
            lines = block.split('\n')
            if len(lines) >= 3:
                sub_id = str(lines[0])
                time_str = str(lines[1])
                raw_text = "\n".join(lines[2:])

                times = time_str.split(' --> ')
                start_ms = srt_time_to_ms(times[0]) if len(times) > 0 and srt_time_to_ms else 0
                end_ms = srt_time_to_ms(times[1]) if len(times) > 1 and srt_time_to_ms else 0

                matches = re.findall(r'\[([A-Z]{3})\]\s*(.*?)(?=\[[A-Z]{3}\]|$)', raw_text, re.DOTALL)

                texts_by_lang = {}
                if matches:
                    for lang, text in matches:
                        clean_t = str(text).strip()
                        texts_by_lang[str(lang)] = clean_t
                        detected_tag_set.add(str(lang))
                else:
                    clean_t = str(raw_text).strip()
                    texts_by_lang['UNI'] = clean_t
                    detected_tag_set.add('UNI')

                self.subtitles.append({
                    "id": sub_id,
                    "time": time_str,
                    "start_ms": start_ms,
                    "end_ms": end_ms,
                    "texts": texts_by_lang
                })

        self.detected_langs = [lang for lang in IDIOMES_ORDRE if lang in detected_tag_set]
        for lang in detected_tag_set:
            if lang not in self.detected_langs:
                self.detected_langs.append(lang)

    def configurar_canals_i_ui(self):
        if self.web_enabled and hasattr(self.emissor, 'actualitzar_canals'):
            nous_canals = []
            for lang in self.detected_langs:
                label = IDIOMES_MAP.get(lang, lang)
                nous_canals.append({'id': str(lang).lower(), 'label': str(label)})
            nous_canals.append({'id': 'auto_original', 'label': 'Transcripció en Directe (Auto)'})
            nous_canals.append({'id': 'auto_translation', 'label': 'Traducció en Directe (Auto)'})
            self.emissor.actualitzar_canals(nous_canals)

        self.combo_guia.blockSignals(True)
        self.combo_guia.clear()
        index_def = 0
        for i, lang in enumerate(self.detected_langs):
            self.combo_guia.addItem(str(IDIOMES_MAP.get(lang, lang)), str(lang))
            if lang == self.default_guide_lang:
                index_def = i

        if self.combo_guia.count() > 0:
            self.combo_guia.setCurrentIndex(index_def)
        self.combo_guia.blockSignals(False)

        existents = {item['lang']: item for item in self.hdmi_langs_data}
        nous_hdmi = []
        for i, lang in enumerate(self.detected_langs):
            label = str(IDIOMES_MAP.get(lang, lang))
            if lang in existents:
                nous_hdmi.append(existents[lang])
            else:
                color = self.idiomes_colors_mem.get(lang, self.PALETA_PER_DEFECTE[i % len(self.PALETA_PER_DEFECTE)])
                self.idiomes_colors_mem[lang] = color
                nous_hdmi.append({
                    'lang': str(lang),
                    'label': label,
                    'color': color,
                    'enabled': True
                })

        self.hdmi_langs_data = nous_hdmi
        self.dibuixar_taula_hdmi()

    def on_guia_lang_changed(self):
        self.render_grid()
        if self.current_index >= 0:
            self.set_active_sub(self.current_index)

    def render_grid(self):
        lang_guia = self.combo_guia.currentData() or (self.detected_langs[0] if self.detected_langs else 'UNI')
        self.table.setRowCount(len(self.subtitles))
        for row, sub in enumerate(self.subtitles):
            item_id = QTableWidgetItem(str(sub['id']))
            item_id.setTextAlignment(Qt.AlignmentFlag.AlignCenter)

            item_time = QTableWidgetItem(str(sub['time']))
            item_time.setTextAlignment(Qt.AlignmentFlag.AlignCenter)

            text_guia = str(sub['texts'].get(lang_guia, ""))
            item_text = QTableWidgetItem(text_guia)

            self.table.setItem(row, 0, item_id)
            self.table.setItem(row, 1, item_time)
            self.table.setItem(row, 2, item_text)

    def on_cell_clicked(self, row, column):
        self.set_active_sub(row)

    def set_active_sub(self, index):
        if 0 <= index < len(self.subtitles):
            self.is_blackout = False
            self.is_currently_auto_mode = False
            self.current_index = index
            self.table.selectRow(self.current_index)
            self.table.scrollToItem(self.table.item(self.current_index, 0), QAbstractItemView.ScrollHint.PositionAtCenter)
            self.sub_elapsed_ms = 0
            
            if self.sincronitzador:
                self.sincronitzador.reiniciar_a_index(self.current_index)
            self.actualitzar_pantalles()

    def cercar_subtitol_seguent(self):
        query = self.input_search.text().strip().lower()
        if not query or not self.subtitles:
            return

        total = len(self.subtitles)
        start_search_idx = (self.current_index + 1) if (0 <= self.current_index < total - 1) else 0

        found_idx = -1
        for offset in range(total):
            idx = (start_search_idx + offset) % total
            sub = self.subtitles[idx]
            
            trobat = False
            for lang_text in sub['texts'].values():
                if query in str(lang_text).lower():
                    trobat = True
                    break
            
            if trobat:
                found_idx = idx
                break

        if found_idx != -1:
            self.set_active_sub(found_idx)
            self.lbl_estat_sincro.setText(f"🔎 Trobat #{found_idx + 1}")
            self.lbl_estat_sincro.setStyleSheet("color: #29B6F6; font-weight: bold;")
        else:
            QMessageBox.information(self, "Cerca", f"No s'ha trobat cap subtítol amb el text: '{query}'")

    def actualitzar_pantalles(self):
        if self.is_blackout or not (0 <= self.current_index < len(self.subtitles)):
            return

        sub = self.subtitles[self.current_index]
        lang_guia = self.combo_guia.currentData() or 'UNI'
        text_guia = str(sub['texts'].get(lang_guia, ""))

        self.lbl_active_title.setText(f"SUBTÍTOL PROJECTAT (GUIÓ OFICIAL: #{self.current_index + 1})")
        self.lbl_active_title.setStyleSheet(f"color: {self.color_primary}; font-weight: bold;")
        self.display_active_sub.setText(f"[{lang_guia}] {text_guia}")
        self.display_active_sub.setStyleSheet(f"""
            background-color: {self.color_panel_bg};
            border: 2px solid {self.color_primary};
            border-radius: 8px;
            padding: 10px;
            min-height: 55px;
        """)

        if self.hdmi_enabled and self.projector_window:
            llista_hdmi = []
            for item in self.hdmi_langs_data:
                if item['enabled']:
                    lang = item['lang']
                    color = item['color']
                    text = sub['texts'].get(lang, "")
                    if text:
                        llista_hdmi.append((str(text), color, False))
            self.projector_window.actualitzar_textos(llista_hdmi)

        if self.web_enabled:
            for lang in self.detected_langs:
                t = str(sub['texts'].get(lang, ""))
                self.emissor.emetre(str(lang).lower(), t)

    def aplicar_mode_auto(self, text_orig, text_trans):
        self.is_currently_auto_mode = True

        self.lbl_active_title.setText("SUBTÍTOL PROJECTAT (MODE AUTO - ASR + TRADUCCIÓ DIRECTA)")
        self.lbl_active_title.setStyleSheet("color: #FFA726; font-weight: bold;")
        
        mostra_auto = f"Veu: {text_orig}"
        if text_trans:
            mostra_auto += f"\nTraducció: {text_trans}"
            
        self.display_active_sub.setText(mostra_auto)
        self.display_active_sub.setStyleSheet(f"""
            background-color: {self.color_panel_bg};
            border: 2px dashed #FFA726;
            border-radius: 8px;
            padding: 10px;
            min-height: 55px;
        """)

        if self.hdmi_enabled and self.projector_window:
            color_orig = self.color_primary
            color_trans = self.color_secondary
            if len(self.hdmi_langs_data) >= 1:
                color_orig = self.hdmi_langs_data[0]['color']
            if len(self.hdmi_langs_data) >= 2:
                color_trans = self.hdmi_langs_data[1]['color']

            llista_auto = [(str(text_orig), color_orig, True)]
            if text_trans:
                llista_auto.append((str(text_trans), color_trans, True))
            self.projector_window.actualitzar_textos(llista_auto)

        if self.web_enabled:
            def web_rec(t, max_p=25):
                if not t:
                    return ""
                p = str(t).strip().split()
                return ("... " + " ".join(p[-max_p:])) if len(p) > max_p else str(t)

            w_orig = web_rec(text_orig)
            w_trans = web_rec(text_trans)

            if self.detected_langs:
                canal_orig = str(self.detected_langs[0]).lower()
                self.emissor.emetre(canal_orig, w_orig)
                if len(self.detected_langs) > 1 and w_trans:
                    canal_trans = str(self.detected_langs[1]).lower()
                    self.emissor.emetre(canal_trans, w_trans)
            else:
                self.emissor.emetre("auto_original", w_orig)
                if w_trans:
                    self.emissor.emetre("auto_translation", w_trans)

    def on_nou_subtitol_audio(self, dades):
        if not self.session_started or self.is_paused or self.is_blackout:
            return

        if isinstance(dades, str):
            text_orig = dades
            text_trans = ""
        else:
            text_orig = str(dades.get("original", "")).strip()
            text_trans = str(dades.get("translation", "")).strip()

        if not text_orig and not text_trans:
            return

        txt_monitor = f"Veu: {text_orig}"
        if text_trans:
            txt_monitor += f"\nTraducció: {text_trans}"
        self.display_live_asr.setText(txt_monitor)

        if self.forced_auto_mode or not self.subtitles or not self.auto_sincro_active or self.sincronitzador is None:
            self.aplicar_mode_auto(text_orig, text_trans)
            return

        lang_guia = self.combo_guia.currentData() or 'UNI'
        status, payload, nou_idx, score = self.sincronitzador.avaluar(
            text_orig, self.current_index, self.subtitles, lang_guia
        )

        timestamp = ms_to_time_str(self.total_elapsed_ms) if ms_to_time_str else "00:00"

        if status == "MATCH":
            if self.is_currently_auto_mode:
                self.log(f"[{timestamp}] 🔄 Reenganxat al guió a #{nou_idx + 1}")
            self.is_currently_auto_mode = False

            if nou_idx != self.current_index:
                self.set_active_sub(nou_idx)
                self.lbl_estat_sincro.setText(f"🟢 Sincronitzat (Línia {nou_idx + 1} | Score: {score}%)")
                self.lbl_estat_sincro.setStyleSheet("color: #00E676; font-weight: bold;")
                self.log(f"[{timestamp}] 🎯 MATCH #{nou_idx + 1} (Score: {score}%): {payload['texts'].get(lang_guia, '')[:45]}...")
        else:
            self.lbl_estat_sincro.setText(f"🤖 Mode Auto Actiu (Buscant guió | Finestra: {self.sincronitzador.current_window})")
            self.lbl_estat_sincro.setStyleSheet("color: #FFA726; font-weight: bold;")
            self.aplicar_mode_auto(text_orig, text_trans)

    def on_click_start_stop(self):
        if not self.session_started:
            self.start_session()
        else:
            self.confirm_and_stop_session()

    def start_session(self):
        host = WHISPER_CFG.get('host', 'localhost')
        port = WHISPER_CFG.get('port', 8000)
        if not comprovar_servidor_actiu(host, port):
            self.log("⚠️ El servidor WLK no respon. Intentant reactivar-lo...")
            self.verificar_servidor_wlk()

        self.session_started = True
        self.is_paused = False
        self.is_blackout = False
        self.is_currently_auto_mode = False

        if self.subtitles and self.current_index < 0:
            self.set_active_sub(0)

        self.timer.start()

        if self.transcriptor:
            sel_dev = self.combo_audio_device.currentData()
            self.transcriptor.device = sel_dev
            try:
                self.transcriptor.inicialitzar_motor()
            except Exception as e:
                self.log(f"⚠️ Error validant dispositiu d'àudio: {e}")

            self.audio_thread = AudioThread(self.transcriptor)
            self.audio_thread.subtitol_generat.connect(self.on_nou_subtitol_audio)
            self.audio_thread.start()

        if self.chk_activar_vu.isChecked():
            self.toggle_vumetre(True)

        self.btn_start_stop.setText("⏹️ Stop")
        self.btn_start_stop.setStyleSheet("background-color: #C62828; color: white;")
        self.btn_pause.setEnabled(True)

    def confirm_and_stop_session(self):
        if not self.is_paused:
            self.timer.stop()

        reply = QMessageBox.question(
            self,
            "Confirmar Aturada",
            "Vols aturar la sessió i reiniciar els comptadors?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )
        if reply == QMessageBox.StandardButton.Yes:
            self.stop_session()
        else:
            if not self.is_paused:
                self.timer.start()

    def stop_session(self):
        self.session_started = False
        self.is_paused = False
        self.is_currently_auto_mode = False
        self.timer.stop()

        if self.monitor_thread:
            self.monitor_thread.stop()
            self.monitor_thread = None
            self.vu_meter.setValue(0)

        if self.audio_thread:
            self.audio_thread.stop()
            self.audio_thread = None

        self.total_elapsed_ms = 0
        self.sub_elapsed_ms = 0
        self.lbl_total_time.setText("00:00")
        self.lbl_sub_elapsed.setText("00:00")
        self.lbl_sub_countdown.setText("--:--")

        self.btn_start_stop.setText("▶️ Start")
        self.btn_start_stop.setStyleSheet("background-color: #1b5e20; color: white;")
        self.btn_pause.setEnabled(False)

    def toggle_pause(self):
        if not self.session_started:
            return
        if self.is_paused:
            self.is_paused = False
            self.timer.start()
            self.btn_pause.setText("⏸ Pause")
            self.btn_pause.setStyleSheet(f"background-color: {self.color_btn_bg}; color: white;")
        else:
            self.is_paused = True
            self.timer.stop()
            self.btn_pause.setText("▶️ Reprendre")
            self.btn_pause.setStyleSheet("background-color: #1b5e20; color: white;")

    def toggle_blackout(self):
        if not self.is_blackout:
            self.is_blackout = True
            self.display_active_sub.setText("[ BLACKOUT ]")
            if self.hdmi_enabled and self.projector_window:
                self.projector_window.blackout()
            if self.web_enabled:
                for lang in self.detected_langs:
                    self.emissor.emetre(str(lang).lower(), "")
                self.emissor.emetre("auto_original", "")
                self.emissor.emetre("auto_translation", "")
        else:
            self.is_blackout = False
            if self.current_index >= 0:
                self.actualitzar_pantalles()

    def update_timers(self):
        if not self.session_started or self.is_paused:
            return
        self.total_elapsed_ms += 100
        self.sub_elapsed_ms += 100

        if ms_to_time_str:
            self.lbl_total_time.setText(ms_to_time_str(self.total_elapsed_ms))
            self.lbl_sub_elapsed.setText(ms_to_time_str(self.sub_elapsed_ms))

        if 0 <= self.current_index < len(self.subtitles) - 1 and ms_to_time_str:
            c_start = self.subtitles[self.current_index]['start_ms']
            n_start = self.subtitles[self.current_index + 1]['start_ms']
            diff = (n_start - c_start) - self.sub_elapsed_ms
            self.lbl_sub_countdown.setText(ms_to_time_str(diff))
            self.lbl_sub_countdown.setStyleSheet("color: #ff5252; background-color: #1a1a1a;" if diff < 0 else "color: #ffca28; background-color: #1a1a1a;")
        else:
            self.lbl_sub_countdown.setText("FI")

    def next_sub(self):
        if self.current_index < len(self.subtitles) - 1:
            self.set_active_sub(self.current_index + 1)

    def prev_sub(self):
        if self.current_index > 0:
            self.set_active_sub(self.current_index - 1)

    def keyPressEvent(self, event):
        if not self.subtitles:
            return

        if event.key() in (Qt.Key.Key_Space, Qt.Key.Key_Right):
            self.next_sub()
        elif event.key() == Qt.Key.Key_Left:
            self.prev_sub()
        elif event.key() == Qt.Key.Key_B:
            self.toggle_blackout()
        else:
            super().keyPressEvent(event)

    def toggle_projector_window(self):
        if not self.projector_window:
            return
        if self.projector_window.isVisible():
            self.projector_window.hide()
        else:
            self.projector_window.show()

    def detectar_segona_pantalla(self):
        if self.hdmi_enabled and self.projector_window:
            self.projector_window.assignar_a_segona_pantalla()

    def closeEvent(self, event):
        self.stop_session()
        if self.monitor_thread:
            self.monitor_thread.stop()

        stop_wlk = WHISPER_CFG.get('stop_on_exit', True)
        if stop_wlk:
            port = WHISPER_CFG.get('port', 8000)
            aturar_servidor_wlk_complet(self.wlk_subprocess, port=port)

        try:
            if hasattr(self, 'projector_window') and self.projector_window:
                self.projector_window.close()
        except Exception:
            pass
        event.accept()


if __name__ == "__main__":
    app = QApplication(sys.argv)

    emissor = None
    if WEB_SERVER_ENABLED and EmissorWeb is not None:
        try:
            canals_web = [{'id': str(item['id']).lower(), 'label': str(item['label'])} for item in IDIOMES_CONFIG]
            canals_web.append({'id': 'auto_original', 'label': 'Transcripció en Directe (Auto)'})
            canals_web.append({'id': 'auto_translation', 'label': 'Traducció en Directe (Auto)'})
            
            CONFIG_SERVER['channels'] = canals_web
            emissor = EmissorWeb(
                config_server=CONFIG_SERVER, 
                mode="SINCRO",
                config_styles=STYLES_CFG
            )
            emissor.iniciar()
        except Exception as e:
            print(f"⚠️ Alerta en iniciar el servidor web: {e}")

    window = TeloSincroPyQt(emissor)
    window.show()

    sys.exit(app.exec())
