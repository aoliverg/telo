import sys
import os
import re
import time
import socket
import subprocess
import signal
import math
import copy
import numpy as np
import yaml

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, 
    QPushButton, QLabel, QTableWidget, QTableWidgetItem, QFileDialog, 
    QHeaderView, QComboBox, QAbstractItemView, QGroupBox,
    QMessageBox, QColorDialog, QCheckBox, QProgressBar, QTextEdit,
    QLineEdit, QSpinBox, QDoubleSpinBox
)
from PyQt6.QtCore import Qt, QTimer, QThread, pyqtSignal
from PyQt6.QtGui import QFont, QTextCursor, QFontMetrics, QIntValidator, QPainter, QPixmap, QColor

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

try:
    from i18n_manager import I18nManager
except ImportError:
    class I18nManager:
        def __init__(self, **kwargs): pass
        def t(self, key, default=None, **kwargs): return default if default is not None else key


def carregar_configuracio_yaml(path=CONFIG_YAML_PATH):
    if os.path.exists(path):
        try:
            with open(path, 'r', encoding='utf-8') as f:
                return yaml.safe_load(f) or {}
        except Exception as e:
            print(f"⚠️ Error llegint {path}: {e}")
    return {}


CONFIG_FULL = carregar_configuracio_yaml()
CONFIG_COMMON = CONFIG_FULL.get('common', {})
CONFIG_AUTO = CONFIG_FULL.get('auto', {})
CONFIG_MANUAL = CONFIG_FULL.get('manual', {})
CONFIG_SINCRO = CONFIG_FULL.get('sincro', {})
STYLES_CFG = CONFIG_COMMON.get('styles', {})
WHISPER_CFG = CONFIG_AUTO.get('whisper_live', {})
WATCHDOG_CFG = CONFIG_AUTO.get('hallucination_watchdog', {})

INTERFACE_CFG = CONFIG_COMMON.get('interface', {})
APP_LANG = INTERFACE_CFG.get('language', 'ca')

DIR_I18N = os.path.join(BASE_DIR, 'i18n')
i18n = I18nManager(modul="sincro", lang=APP_LANG, base_dir=DIR_I18N)
_ = i18n.t

CONFIG_SERVER = CONFIG_COMMON.get('server', {'host': '0.0.0.0', 'port': 5000, 'enabled': True})
if CONFIG_SERVER.get('host') == '00.0.0':
    CONFIG_SERVER['host'] = '0.0.0.0'

WEB_SERVER_ENABLED = CONFIG_SERVER.get('enabled', True)
HDMI_ENABLED = STYLES_CFG.get('hdmi', {}).get('enabled', True)

LANGUAGES_CFG = CONFIG_COMMON.get('languages', {'ca': 'Català', 'es': 'Español', 'fr': 'Français', 'en': 'English'})
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


def parse_time_str_fallback(ts):
    parts = ts.strip().replace(',', '.').split(':')
    if len(parts) == 3:
        h = float(parts[0])
        m = float(parts[1])
        s = float(parts[2])
        return int((h * 3600 + m * 60 + s) * 1000)
    return 0


def ms_to_time_str_fallback(ms):
    s = max(0, int(ms // 1000))
    m = s // 60
    return f"{m:02d}:{s % 60:02d}"


def comprovar_servidor_actiu(host='localhost', port=8000, timeout=0.8):
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
        log_callback(i18n.t('log.wlk.stopped', "🛑 Servidor WLK aturat i port alliberat."))


def assegurar_servidor_wlk(cfg_wlk, log_callback=None):
    host = cfg_wlk.get('host', 'localhost')
    port = cfg_wlk.get('port', 8000)
    auto_start = cfg_wlk.get('auto_start', True)
    cmd = cfg_wlk.get('command', '').strip()

    if comprovar_servidor_actiu(host, port):
        if log_callback:
            log_callback(i18n.t('log.wlk.detected', "ℹ️ Servidor WLK detectat en marxa a {host}:{port}", host=host, port=port))
        return None

    if not auto_start or not cmd:
        if log_callback:
            log_callback(i18n.t('log.wlk.not_active', "⚠️ El servidor WLK no està actiu i no s'ha configurat comanda d'inici."))
        return None

    if log_callback:
        log_callback(i18n.t('log.wlk.starting', "🚀 Iniciant servidor WLK ({host}:{port})...", host=host, port=port))

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
                log_callback(i18n.t('log.wlk.started', "✅ Servidor WLK iniciat correctament."))
            return process

    if log_callback:
        log_callback(i18n.t('log.wlk.no_response', "⚠️ El procés s'ha iniciat però encara no respon al port esperat."))
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
        self.estat_canviat.emit(i18n.t('thread.connecting', "Connectant al servidor WhisperLiveKit..."))

        def callback_resultat(dades, linies=None):
            if self.running and dades:
                if isinstance(dades, str):
                    dades = {"original": dades, "translation": "", "es_consolidat": False}
                self.subtitol_generat.emit(dades)

        try:
            self.transcriptor.iniciar_escolta(callback_resultat=callback_resultat)
            self.estat_canviat.emit(i18n.t('thread.listening', "Escoltant en directe..."))

            while self.running and getattr(self.transcriptor, 'is_listening', True):
                time.sleep(0.1)
        except Exception as e:
            self.estat_canviat.emit(i18n.t('thread.error', "Error en la transcripció: {error}", error=e))

    def stop(self):
        self.running = False
        if self.transcriptor and hasattr(self.transcriptor, 'aturar_escolta'):
            self.transcriptor.aturar_escolta()
        self.estat_canviat.emit(i18n.t('thread.stopped', "Aturat"))


# --- FINESTRA DEL PROJECTOR HDMI (SENSE MARCS + BUFFER HIDPI DE ROTACIÓ) ---
class ProjectorSincroWindow(QMainWindow):
    def __init__(self, app_title="TELÓ Sincro", config_proj=None):
        super().__init__()
        self.app_title = app_title
        self.config_proj = config_proj or {}

        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Window)
        self.base_font_family = self.config_proj.get('font_family', 'Arial')
        self.base_font_size = self.config_proj.get('font_size', 38)
        self.max_lines = self.config_proj.get('max_lines', 3)
        self.bg_color = self.config_proj.get('background_color', '#000000')

        self.set_align_h(self.config_proj.get('align_h', 'Centrat'), redibuixar=False)
        self.set_align_v(self.config_proj.get('align_v', 'Centrat'), redibuixar=False)
        self.rotation_angle = float(self.config_proj.get('rotation', 0.0))

        self.labels = []
        self._ultims_textos = []

        self.init_ui()

    def init_ui(self):
        global i18n
        self.setWindowTitle(i18n.t('projector.title', '{title} - Projector HDMI', title=self.app_title))
        self.setStyleSheet(f"background-color: {self.bg_color};")

        self.content_widget = QWidget(self)
        self.content_widget.setStyleSheet("background-color: transparent;")

        self.layout_proj = QVBoxLayout(self.content_widget)
        self.layout_proj.setContentsMargins(60, 40, 60, 40)
        self.layout_proj.setSpacing(15)
        self.content_widget.hide()

        w = self.config_proj.get('width', 1000)
        h = self.config_proj.get('height', 500)
        self.resize(w, h)

    def set_align_h(self, align_str, redibuixar=True):
        if align_str == "Esquerra":
            self.align_h = Qt.AlignmentFlag.AlignLeft
        elif align_str == "Dreta":
            self.align_h = Qt.AlignmentFlag.AlignRight
        else:
            self.align_h = Qt.AlignmentFlag.AlignHCenter
        if redibuixar and self._ultims_textos:
            self.actualitzar_textos(self._ultims_textos)

    def set_align_v(self, align_str, redibuixar=True):
        if align_str == "A dalt":
            self.align_v_block = "top"
        elif align_str == "A baix":
            self.align_v_block = "bottom"
        else:
            self.align_v_block = "center"
        if redibuixar and self._ultims_textos:
            self.actualitzar_textos(self._ultims_textos)

    def set_rotation(self, angle):
        self.rotation_angle = angle
        self.update()

    def actualitzar_textos(self, llista_textos_colors):
        self._ultims_textos = llista_textos_colors

        while self.layout_proj.count() > 0:
            self.layout_proj.takeAt(0)

        while len(self.labels) < len(llista_textos_colors):
            lbl = QLabel()
            lbl.setWordWrap(True)
            self.labels.append(lbl)

        while len(self.labels) > len(llista_textos_colors):
            lbl = self.labels.pop()
            lbl.deleteLater()

        total_lignes = sum(len(str(text).split('\n')) for text, _, _ in llista_textos_colors)
        if total_lignes >= 6:
            font_size = max(16, int(self.base_font_size * 0.45))
        elif total_lignes >= 4:
            font_size = max(18, int(self.base_font_size * 0.55))
        elif total_lignes == 3:
            font_size = max(22, int(self.base_font_size * 0.68))
        elif total_lignes == 2:
            font_size = max(26, int(self.base_font_size * 0.85))
        else:
            font_size = self.base_font_size

        font = QFont(self.base_font_family, font_size, QFont.Weight.Bold)
        ample_disponible_px = max(100, self.width() - 140)

        if self.align_v_block in ("center", "bottom"):
            self.layout_proj.addStretch(1)

        for i, (text, color, is_fallback) in enumerate(llista_textos_colors):
            lbl = self.labels[i]
            lbl.setFont(font)
            lbl.setAlignment(self.align_h | Qt.AlignmentFlag.AlignVCenter)
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
            self.layout_proj.addWidget(lbl, 0)

        if self.align_v_block in ("center", "top"):
            self.layout_proj.addStretch(1)

        self.update()

    def blackout(self):
        for lbl in self.labels:
            lbl.setText("")
        self.update()

    def set_font_size(self, size):
        self.base_font_size = size
        if self._ultims_textos:
            self.actualitzar_textos(self._ultims_textos)

    def set_font_family(self, family):
        self.base_font_family = family
        if self._ultims_textos:
            self.actualitzar_textos(self._ultims_textos)

    def set_bg_color(self, hex_color):
        self.bg_color = hex_color
        self.setStyleSheet(f"background-color: {hex_color};")
        self.update()

    def paintEvent(self, event):
        w = max(10, self.width())
        h = max(10, self.height())

        dpr = self.devicePixelRatioF() if hasattr(self, 'devicePixelRatioF') else self.devicePixelRatio()
        bw = int(w * dpr)
        bh = int(h * dpr)

        self.content_widget.resize(w, h)

        pixmap = QPixmap(bw, bh)
        pixmap.setDevicePixelRatio(dpr)
        pixmap.fill(Qt.GlobalColor.transparent)
        self.content_widget.render(pixmap)

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

        painter.fillRect(0, 0, w, h, QColor(self.bg_color))

        cx = w / 2.0
        cy = h / 2.0

        painter.translate(cx, cy)
        painter.rotate(self.rotation_angle)
        painter.translate(-cx, -cy)

        painter.drawPixmap(0, 0, pixmap)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._ultims_textos:
            self.actualitzar_textos(self._ultims_textos)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.hide()
        else:
            super().keyPressEvent(event)

    def assignar_a_segona_pantalla(self):
        screens = QApplication.screens()
        if len(screens) > 1:
            segona = screens[1]
            self.setGeometry(segona.geometry())
            if self.windowHandle():
                self.windowHandle().setScreen(segona)
            self.showFullScreen()
        else:
            self.hide()


# --- FINESTRA PRINCIPAL DE CONTROL ---
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
        self.cerca_index_candidat = -1

        self.session_started = False
        self.is_paused = False
        self.is_blackout = False
        self.auto_sincro_active = True
        self.forced_auto_mode = False
        self.is_currently_auto_mode = False

        self.total_elapsed_ms = 0
        self.playhead_ms = 0
        self.time_offset_ms = 0
        self.sub_elapsed_ms = 0
        self.last_sync_verify_ms = 0
        
        matching_cfg = CONFIG_SINCRO.get('matching', {})
        self.n_futurs_prompt = int(matching_cfg.get('context_future_subs', 4))
        self.back_window = int(matching_cfg.get('back_window', 1))

        if Sincronitzador is not None:
            self.sincronitzador = Sincronitzador(
                score_threshold=matching_cfg.get('score_threshold', 52),
                no_offset_threshold=matching_cfg.get('no_offset_threshold', 45),
                offset_penalty=matching_cfg.get('offset_penalty', 1),
                base_window=matching_cfg.get('base_window', 10),
                max_window=matching_cfg.get('max_window', 25),
                back_window=self.back_window,
                desync_threshold=matching_cfg.get('desync_threshold', 2)
            )

        self.default_guide_lang = str(CONFIG_SINCRO.get('default_guide_language', CONFIG_MANUAL.get('default_guide_language', 'CAT'))).upper()
        self.max_desync_grace_ms = int(CONFIG_SINCRO.get('timing', {}).get('grace_limit_sec', 18)) * 1000

        self.translation_cfg = CONFIG_AUTO.get('translation', {'enabled': True, 'target_language': 'ca'})
        self.audio_cfg = CONFIG_AUTO.get('audio', {'samplerate': 16000, 'block_ms': 100, 'device': None})

        self.wlk_subprocess = None
        self.wlk_online = False
        self.transcriptor = None
        self.audio_thread = None
        self.monitor_thread = None

        self.app_title = INTERFACE_CFG.get('title', 'TELÓ')
        self.app_lang = str(APP_LANG).lower().strip()

        colors_cfg = INTERFACE_CFG.get('colors', {})
        self.color_bg = colors_cfg.get('background', '#121212')
        self.color_text = colors_cfg.get('text', '#E0E0E0')
        self.color_primary = colors_cfg.get('primary', '#00E676')
        self.color_secondary = colors_cfg.get('secondary', '#29B6F6')
        self.color_panel_bg = colors_cfg.get('panel_bg', '#1E1E1E')
        self.color_btn_bg = colors_cfg.get('button_bg', '#252525')
        self.color_btn_border = colors_cfg.get('button_border', '#444444')

        self.max_lines_hdmi = STYLES_CFG.get('hdmi', {}).get('max_lines', 3)
        self.projector_cfg = STYLES_CFG.get('hdmi', {})

        self.timer = QTimer(self)
        self.timer.setInterval(100)
        self.timer.timeout.connect(self.update_timers)

        self.timer_wlk_check = QTimer(self)
        self.timer_wlk_check.setInterval(5000)
        self.timer_wlk_check.timeout.connect(self.verificar_servidor_wlk_periodic)

        if self.hdmi_enabled:
            self.projector_window = ProjectorSincroWindow(app_title=self.app_title, config_proj=self.projector_cfg)
        else:
            self.projector_window = None

        self.init_ui()
        self.inicialitzar_llista_idiomes_per_defecte()

        if self.hdmi_enabled and self.projector_window:
            QTimer.singleShot(250, self.detectar_segona_pantalla)

        self.carregar_dispositius_audio()
        self.verificar_servidor_wlk()
        self.timer_wlk_check.start()
        self.inicialitzar_transcriptor()

    def t(self, key, default=None, **kwargs):
        global i18n
        return i18n.t(key, default, **kwargs)

    def log(self, text):
        if hasattr(self, 'log_edit') and self.log_edit is not None:
            self.log_edit.append(str(text))
            self.log_edit.moveCursor(QTextCursor.MoveOperation.End)
        else:
            print(str(text))
    
    def obtenir_prompt_contextual(self):
        """Genera el text pla dels propers N subtítols per alimentar Whisper."""
        if not self.subtitles or self.current_index < 0:
            return ""

        lang_actor = (self.combo_lang_in.currentData() or 'fr').upper() if hasattr(self, 'combo_lang_in') else 'FR'
        lang = lang_actor if (self.subtitles and lang_actor in self.subtitles[0]['texts']) else (self.combo_guia.currentData() or 'UNI' if hasattr(self, 'combo_guia') else 'UNI')

        inici = max(0, self.current_index)
        final = min(inici + getattr(self, 'n_futurs_prompt', 4), len(self.subtitles))

        fragments = []
        for i in range(inici, final):
            txt = self.subtitles[i]['texts'].get(lang, "").strip()
            if txt:
                fragments.append(" ".join(txt.split()))

        return " ".join(fragments)
    
    def inicialitzar_transcriptor(self):
        if TranscriptorWhisperLiveKit is None:
            self.transcriptor = None
            return

        lang_in = self.combo_lang_in.currentData() if hasattr(self, 'combo_lang_in') and self.combo_lang_in.currentData() else CONFIG_SINCRO.get('input_language', CONFIG_AUTO.get('input_language', 'fr'))
        target_lang = self.translation_cfg.get('target_language', 'ca')
        dev = self.combo_audio_device.currentData() if hasattr(self, 'combo_audio_device') else self.audio_cfg.get('device')

        prompt_actual = self.obtenir_prompt_contextual()

        config_trans = {
            "whisper_live": WHISPER_CFG,
            "input_language": lang_in,
            "initial_prompt": prompt_actual,
            "translation": {
                "enabled": True,
                "target_language": target_lang
            },
            "audio": {
                "samplerate": self.audio_cfg.get('samplerate', 16000),
                "block_ms": self.audio_cfg.get('block_ms', 100),
                "device": dev,
            },
            "show_info": False
        }
        try:
            self.transcriptor = TranscriptorWhisperLiveKit(config=config_trans)
            self.transcriptor.inicialitzar_motor()
        except Exception as e:
            self.log(self.t('log.engine.create_error', "⚠️ Alerta creant el motor del transcriptor: {error}", error=e))
            self.transcriptor = None

    def verificar_servidor_wlk(self):
        host = WHISPER_CFG.get('host', 'localhost')
        port = WHISPER_CFG.get('port', 8000)
        self.wlk_online = comprovar_servidor_actiu(host, port)
        if self.wlk_online:
            self.log(self.t('log.wlk.detected', "ℹ️ Servidor WLK detectat en marxa a {host}:{port}", host=host, port=port))
        else:
            try:
                self.wlk_subprocess = assegurar_servidor_wlk(WHISPER_CFG, log_callback=self.log)
                self.wlk_online = comprovar_servidor_actiu(host, port)
            except Exception as e:
                self.log(self.t('log.wlk.manage_error', "⚠️ Error gestionant el servidor WLK: {error}", error=e))

    def verificar_servidor_wlk_periodic(self):
        host = WHISPER_CFG.get('host', 'localhost')
        port = WHISPER_CFG.get('port', 8000)
        actiu_ara = comprovar_servidor_actiu(host, port, timeout=0.8)

        if actiu_ara and not self.wlk_online:
            self.wlk_online = True
            self.log(self.t('log.wlk.active_now', "✅ Servidor WLK detectat i actiu a {host}:{port}!", host=host, port=port))
            if not self.transcriptor:
                self.inicialitzar_transcriptor()
        elif not actiu_ara and self.wlk_online:
            self.wlk_online = False
            self.log(self.t('log.wlk.lost', "⚠️ S'ha perdut la connexió amb el servidor WLK ({host}:{port}).", host=host, port=port))

    def carregar_dispositius_audio(self):
        self.combo_audio_device.blockSignals(True)
        self.combo_audio_device.clear()
        self.combo_audio_device.addItem(self.t('audio.default_device', "Micròfon per defecte del sistema"), None)

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
                self.log(self.t('log.audio.devices_error', "⚠️ Error consultant targetes d'àudio: {error}", error=e))
        else:
            self.combo_audio_device.setEnabled(False)

        dev_cfg = CONFIG_SINCRO.get('audio_device', CONFIG_AUTO.get('audio', {}).get('device'))
        if dev_cfg is not None:
            idx_found = self.combo_audio_device.findData(dev_cfg)
            if idx_found >= 0:
                self.combo_audio_device.setCurrentIndex(idx_found)

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
        self.setWindowTitle(self.t('window.title', '{title} ⚡ - Sincronitzador Intel·ligent', title=self.app_title))
        self.resize(1280, 1000)

        self.setStyleSheet(f"""
            QMainWindow {{ background-color: {self.color_bg}; }}
            QWidget {{ background-color: {self.color_bg}; color: {self.color_text}; font-family: 'Segoe UI', Arial; font-size: 10pt; }}
            
            QGroupBox {{ 
                border: 1px solid {self.color_btn_border}; 
                border-radius: 6px; 
                margin-top: 14px; 
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
                border-radius: 4px; padding: 5px 12px; 
                font-weight: bold; min-height: 20px;
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
                padding: 3px; 
            }}

            QTextEdit {{ background-color: {self.color_panel_bg}; border: 1px solid {self.color_btn_border}; color: {self.color_text}; font-family: monospace; }}
            QLineEdit {{ background-color: {self.color_btn_bg}; border: 1px solid {self.color_btn_border}; color: {self.color_text}; padding: 3px 6px; border-radius: 4px; }}
            QComboBox {{ background-color: {self.color_btn_bg}; border: 1px solid {self.color_btn_border}; padding: 3px 6px; border-radius: 4px; }}
            QSpinBox {{ background-color: {self.color_btn_bg}; border: 1px solid {self.color_btn_border}; color: {self.color_text}; padding: 3px; border-radius: 4px; }}
            QDoubleSpinBox {{ background-color: {self.color_btn_bg}; border: 1px solid {self.color_btn_border}; color: {self.color_text}; padding: 3px; border-radius: 4px; }}
            QCheckBox {{ color: {self.color_text}; font-weight: bold; }}
            
            QProgressBar {{
                border: 1px solid {self.color_btn_border};
                border-radius: 4px;
                background-color: #1a1a1a;
                text-align: center;
                height: 16px;
            }}
            QProgressBar::chunk {{
                background-color: {self.color_primary};
                border-radius: 3px;
            }}
        """)

        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        main_layout.setSpacing(8)
        main_layout.setContentsMargins(12, 10, 12, 10)

        # 1. HEADER
        header_layout = QHBoxLayout()
        self.title_label = QLabel(f"{self.app_title} ⚡")
        self.title_label.setFont(QFont("Arial", 15, QFont.Weight.Bold))
        self.title_label.setStyleSheet(f"color: {self.color_primary};")
        header_layout.addWidget(self.title_label)

        self.btn_open = QPushButton(self.t('btn.open_srt', '📂 Obrir .SRT'))
        self.btn_open.clicked.connect(self.open_srt_file)
        header_layout.addWidget(self.btn_open)

        self.btn_save_cfg = QPushButton(self.t('btn.save_config', '💾 Desar Config'))
        self.btn_save_cfg.setStyleSheet("background-color: #004D40; color: white;")
        self.btn_save_cfg.clicked.connect(self.guardar_configuracio_fitxer)
        header_layout.addWidget(self.btn_save_cfg)

        self.btn_load_cfg = QPushButton(self.t('btn.load_config', '📂 Carregar Config'))
        self.btn_load_cfg.setStyleSheet("background-color: #37474F; color: white;")
        self.btn_load_cfg.clicked.connect(self.carregar_configuracio_fitxer)
        header_layout.addWidget(self.btn_load_cfg)

        if self.hdmi_enabled:
            self.btn_toggle_proj = QPushButton(self.t('btn.toggle_projector', '🖥️ HDMI'))
            self.btn_toggle_proj.clicked.connect(self.toggle_projector_window)
            header_layout.addWidget(self.btn_toggle_proj)

        self.chk_auto_sincro = QCheckBox(self.t('chk.auto_sincro_on', '🔄 Sincro Auto (ON)'))
        self.chk_auto_sincro.setChecked(True)
        self.chk_auto_sincro.toggled.connect(self.on_toggle_auto_sincro)
        self.chk_auto_sincro.setStyleSheet("color: #00E676; font-size: 10pt;")
        header_layout.addWidget(self.chk_auto_sincro)

        header_layout.addSpacing(6)
        self.lbl_grace_limit = QLabel(self.t('lbl.grace_limit', 'Límit Auto:'))
        header_layout.addWidget(self.lbl_grace_limit)

        self.spin_grace = QSpinBox()
        self.spin_grace.setRange(5, 120)
        self.spin_grace.setValue(int(self.max_desync_grace_ms // 1000))
        self.spin_grace.setSuffix(" s")
        self.spin_grace.setToolTip(self.t('tooltip.grace_limit', "Segons sense reconèixer text abans de projectar transcripció directa."))
        self.spin_grace.valueChanged.connect(self.on_grace_time_changed)
        header_layout.addWidget(self.spin_grace)

        self.btn_force_auto = QPushButton(self.t('btn.force_auto_off', '🤖 Forçar Auto (OFF)'))
        self.btn_force_auto.setCheckable(True)
        self.btn_force_auto.setStyleSheet("background-color: #37474F; color: #FFFFFF; font-weight: bold;")
        self.btn_force_auto.toggled.connect(self.on_toggle_force_auto)
        header_layout.addWidget(self.btn_force_auto)

        header_layout.addStretch()

        if self.web_enabled:
            ip = self.emissor.obtenir_ip_local() if hasattr(self.emissor, 'obtenir_ip_local') else "127.0.0.1"
            port = self.emissor.port if hasattr(self.emissor, 'port') else 5000
            url_web = f"http://{ip}:{port}"
            self.ip_label = QLabel(f'{self.t("lbl.web_server", "🌐 Web:")} <a href="{url_web}" style="color: {self.color_primary};">{url_web}</a>')
            self.ip_label.setOpenExternalLinks(True)
        else:
            self.ip_label = QLabel(self.t('lbl.web_server_disabled', '🌐 Web: <span style="color: #7f8c8d;">OFF</span>'))

        self.ip_label.setFont(QFont("Arial", 9, QFont.Weight.Bold))
        header_layout.addWidget(self.ip_label)

        main_layout.addLayout(header_layout)

        # 2. PANELL D'AJUSTOS EN DIRECTE HDMI
        if self.hdmi_enabled:
            main_layout.addWidget(self.crear_panel_ajustos_estil())

        # 3. AUDIO & VÚMETRE
        self.audio_box = QGroupBox(self.t('group.audio', "🎙️ Control d'Entrada d'Àudio i Nivell"))
        audio_layout = QHBoxLayout(self.audio_box)
        audio_layout.setContentsMargins(10, 10, 10, 8)

        self.lbl_audio_dev = QLabel(self.t('lbl.audio_device', "Dispositiu:"))
        audio_layout.addWidget(self.lbl_audio_dev)
        self.combo_audio_device = QComboBox()
        self.combo_audio_device.currentIndexChanged.connect(self.canviar_dispositiu_mic)
        audio_layout.addWidget(self.combo_audio_device, 3)

        audio_layout.addSpacing(10)

        self.chk_activar_vu = QCheckBox(self.t('chk.activate_vu', "Activar Vúmetre"))
        self.chk_activar_vu.setChecked(False)
        self.chk_activar_vu.toggled.connect(self.toggle_vumetre)
        audio_layout.addWidget(self.chk_activar_vu)

        self.vu_meter = QProgressBar()
        self.vu_meter.setRange(0, 100)
        self.vu_meter.setValue(0)
        self.vu_meter.setTextVisible(False)
        self.vu_meter.setFixedHeight(16)
        self.vu_meter.setEnabled(False)
        audio_layout.addWidget(self.vu_meter, 2)

        main_layout.addWidget(self.audio_box)

        # 4. PANELS DE MONITOR DUAL
        display_layout = QHBoxLayout()

        box_live = QVBoxLayout()
        self.lbl_live_title = QLabel(self.t('monitor.live_title', "ENTRADA DE VEU EN DIRECTE (ASR)"))
        self.lbl_live_title.setStyleSheet("color: #FFA726; font-weight: bold; font-size: 9pt;")
        self.display_live_asr = QLabel(self.t('monitor.live_waiting', "[ Esperant veu de l'actor / ponent... ]"))
        self.display_live_asr.setFont(QFont("Arial", 10, QFont.Weight.Bold))
        self.display_live_asr.setWordWrap(True)
        self.display_live_asr.setStyleSheet(f"""
            background-color: {self.color_panel_bg};
            border: 2px solid #FFA726;
            border-radius: 6px;
            padding: 8px;
            min-height: 44px;
        """)
        box_live.addWidget(self.lbl_live_title)
        box_live.addWidget(self.display_live_asr)
        display_layout.addLayout(box_live, 1)

        box_active = QVBoxLayout()
        self.lbl_active_title = QLabel(self.t('monitor.active_official_base', "SUBTÍTOL PROJECTAT (GUIÓ OFICIAL)"))
        self.lbl_active_title.setStyleSheet(f"color: {self.color_primary}; font-weight: bold; font-size: 9pt;")
        self.display_active_sub = QLabel(self.t('monitor.active_waiting', "Carrega un fitxer .SRT per començar"))
        self.display_active_sub.setFont(QFont("Arial", 11, QFont.Weight.Bold))
        self.display_active_sub.setWordWrap(True)
        self.display_active_sub.setStyleSheet(f"""
            background-color: {self.color_panel_bg};
            border: 2px solid {self.color_primary};
            border-radius: 6px;
            padding: 8px;
            min-height: 44px;
        """)
        box_active.addWidget(self.lbl_active_title)
        box_active.addWidget(self.display_active_sub)
        display_layout.addLayout(box_active, 1)

        main_layout.addLayout(display_layout)

        # 5. CONFIGURACIÓ IDIOMES, ASR I SORTIDA HDMI
        nom_grup_sincro = self.t('group.sync_status', "Estat de la Sincronització i Idiomes HDMI") if self.hdmi_enabled else self.t('group.sync_status_no_hdmi', "Estat de la Sincronització i Idiomes")
        self.config_box = QGroupBox(nom_grup_sincro)
        config_layout = QHBoxLayout(self.config_box)
        config_layout.setContentsMargins(10, 10, 10, 8)

        col_esq = QVBoxLayout()
        self.lbl_guide_lang = QLabel(self.t('lbl.guide_lang', "Idioma Guia Operador:"))
        col_esq.addWidget(self.lbl_guide_lang)
        self.combo_guia = QComboBox()
        self.combo_guia.currentIndexChanged.connect(self.on_guia_lang_changed)
        col_esq.addWidget(self.combo_guia)

        col_esq.addSpacing(6)
        self.lbl_lang_in = QLabel(self.t('lbl.lang_in', "Veu de l'actor (ASR):"))
        col_esq.addWidget(self.lbl_lang_in)

        self.combo_lang_in = QComboBox()
        default_lang_in = CONFIG_SINCRO.get('input_language', CONFIG_AUTO.get('input_language', 'fr'))
        idx_in = 0
        for idx, (code, name) in enumerate(LANGUAGES_CFG.items()):
            self.combo_lang_in.addItem(str(name), str(code))
            if str(code).lower() == str(default_lang_in).lower():
                idx_in = idx
        self.combo_lang_in.setCurrentIndex(idx_in)
        self.combo_lang_in.currentIndexChanged.connect(self.canviar_idioma_entrada_veu)
        col_esq.addWidget(self.combo_lang_in)

        self.btn_reset_transcripcio = QPushButton(self.t('btn.reset_asr', "🧹 Netejar Transcripció"))
        self.btn_reset_transcripcio.setStyleSheet("background-color: #37474F; color: #ECEFF1; font-weight: bold; margin-top: 4px;")
        self.btn_reset_transcripcio.clicked.connect(self.reiniciar_transcripcio)
        col_esq.addWidget(self.btn_reset_transcripcio)

        self.lbl_estat_sincro = QLabel(self.t('status.ready', "🟢 Sincronització preparada"))
        self.lbl_estat_sincro.setStyleSheet("color: #00E676; font-weight: bold; font-size: 10pt; margin-top: 6px;")
        col_esq.addWidget(self.lbl_estat_sincro)
        col_esq.addStretch()
        config_layout.addLayout(col_esq, 2)

        config_layout.addSpacing(15)

        if self.hdmi_enabled:
            col_hdmi = QVBoxLayout()
            self.lbl_hdmi_project_langs = QLabel(self.t('lbl.hdmi_project_langs', "Idiomes a Projectar a HDMI:"))
            col_hdmi.addWidget(self.lbl_hdmi_project_langs)

            hdmi_table_layout = QHBoxLayout()
            self.table_hdmi = QTableWidget()
            self.table_hdmi.setColumnCount(3)
            self.table_hdmi.setRowCount(0)
            self.table_hdmi.setHorizontalHeaderLabels([
                self.t('table.hdmi.header.active_lang', 'Actiu i Idioma'),
                self.t('table.hdmi.header.sample', 'Color'),
                self.t('table.hdmi.header.action', 'Acció')
            ])
            self.table_hdmi.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
            self.table_hdmi.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
            self.table_hdmi.setFixedHeight(120)

            hd_h = self.table_hdmi.horizontalHeader()
            hd_h.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
            hd_h.setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
            hd_h.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
            self.table_hdmi.setColumnWidth(1, 55)
            self.table_hdmi.setColumnWidth(2, 95)

            hdmi_table_layout.addWidget(self.table_hdmi, 4)

            btn_order_box = QVBoxLayout()
            self.btn_move_up = QPushButton("⬆️")
            self.btn_move_up.clicked.connect(self.moure_idioma_amunt)
            btn_order_box.addWidget(self.btn_move_up)

            self.btn_move_down = QPushButton("⬇️")
            self.btn_move_down.clicked.connect(self.moure_idioma_avall)
            btn_order_box.addWidget(self.btn_move_down)
            btn_order_box.addStretch()

            hdmi_table_layout.addLayout(btn_order_box, 1)
            col_hdmi.addLayout(hdmi_table_layout)

            config_layout.addLayout(col_hdmi, 4)

        main_layout.addWidget(self.config_box)

        # 6. CONTROLS DE SESSIÓ I TEMPS
        self.timer_box = QGroupBox(self.t('group.timer', "Control Temporal i Sessió"))
        timer_layout = QHBoxLayout(self.timer_box)
        timer_layout.setContentsMargins(10, 8, 10, 8)

        self.btn_start_stop = QPushButton(self.t('btn.start', "▶️ Start"))
        self.btn_start_stop.setStyleSheet("background-color: #1b5e20; color: white;")
        self.btn_start_stop.clicked.connect(self.on_click_start_stop)
        timer_layout.addWidget(self.btn_start_stop)

        self.btn_pause = QPushButton(self.t('btn.pause', "⏸ Pause"))
        self.btn_pause.setEnabled(False)
        self.btn_pause.clicked.connect(self.toggle_pause)
        timer_layout.addWidget(self.btn_pause)

        timer_layout.addSpacing(15)

        self.lbl_title_total = QLabel(self.t('lbl.total_time', "Temps Total:"))
        timer_layout.addWidget(self.lbl_title_total)
        self.lbl_total_time = QLabel("00:00")
        self.lbl_total_time.setFont(QFont("Arial", 11, QFont.Weight.Bold))
        self.lbl_total_time.setStyleSheet(f"color: {self.color_primary}; background-color: #1a1a1a; padding: 3px 6px; border-radius: 4px;")
        timer_layout.addWidget(self.lbl_total_time)

        timer_layout.addSpacing(10)

        self.lbl_title_elapsed = QLabel(self.t('lbl.sub_elapsed', "En Frase:"))
        timer_layout.addWidget(self.lbl_title_elapsed)
        self.lbl_sub_elapsed = QLabel("00:00")
        self.lbl_sub_elapsed.setFont(QFont("Arial", 11, QFont.Weight.Bold))
        self.lbl_sub_elapsed.setStyleSheet(f"color: {self.color_secondary}; background-color: #1a1a1a; padding: 3px 6px; border-radius: 4px;")
        timer_layout.addWidget(self.lbl_sub_elapsed)

        timer_layout.addSpacing(10)

        self.lbl_title_countdown = QLabel(self.t('lbl.sub_countdown', "Següent (teòric):"))
        timer_layout.addWidget(self.lbl_title_countdown)
        self.lbl_sub_countdown = QLabel("--:--")
        self.lbl_sub_countdown.setFont(QFont("Arial", 11, QFont.Weight.Bold))
        self.lbl_sub_countdown.setStyleSheet("color: #ffca28; background-color: #1a1a1a; padding: 3px 6px; border-radius: 4px;")
        timer_layout.addWidget(self.lbl_sub_countdown)

        timer_layout.addStretch()
        main_layout.addWidget(self.timer_box)

        # 7. CONTROLS DE NAVEGACIÓ MANUAL
        ctrl_layout = QHBoxLayout()

        self.btn_prev = QPushButton(self.t('btn.prev', "⏮ Anterior (←)"))
        self.btn_prev.setStyleSheet("padding: 6px;")
        self.btn_prev.clicked.connect(self.prev_sub)
        self.btn_prev.setEnabled(False)
        ctrl_layout.addWidget(self.btn_prev)

        self.btn_blackout = QPushButton(self.t('btn.blackout', "⬛ Blackout (B)"))
        self.btn_blackout.setStyleSheet("background-color: #C62828; color: white; padding: 6px;")
        self.btn_blackout.clicked.connect(self.toggle_blackout)
        ctrl_layout.addWidget(self.btn_blackout)

        self.btn_next = QPushButton(self.t('btn.next', "Següent (Espai / →) ⏭"))
        self.btn_next.setStyleSheet("background-color: #2E7D32; color: white; padding: 6px;")
        self.btn_next.clicked.connect(self.next_sub)
        self.btn_next.setEnabled(False)
        ctrl_layout.addWidget(self.btn_next)

        main_layout.addLayout(ctrl_layout)

        # 8. BARRA DE CERCA AL GUIÓ
        search_layout = QHBoxLayout()
        self.lbl_search_title = QLabel(self.t('lbl.search', "🔎 Cerca al guió:"))
        search_layout.addWidget(self.lbl_search_title)
        
        self.input_search = QLineEdit()
        self.input_search.setPlaceholderText(self.t('search.placeholder', "Escriu per cercar sense projectar..."))
        self.input_search.returnPressed.connect(self.cercar_subtitol_seguent)
        search_layout.addWidget(self.input_search, 4)

        self.btn_search = QPushButton(self.t('btn.search_next', "🔍 Següent"))
        self.btn_search.setStyleSheet("background-color: #0277BD; color: white; padding: 5px 10px;")
        self.btn_search.clicked.connect(self.cercar_subtitol_seguent)
        search_layout.addWidget(self.btn_search)

        self.btn_confirm_search = QPushButton(self.t('btn.search_project', "🚀 Projectar seleccionat"))
        self.btn_confirm_search.setStyleSheet("background-color: #E65100; color: white; padding: 5px 10px;")
        self.btn_confirm_search.clicked.connect(self.confirmar_salt_candidat)
        search_layout.addWidget(self.btn_confirm_search)

        main_layout.addLayout(search_layout)

        # 9. GRAELLA DE SUBTÍTOLS I REGISTRE
        lower_layout = QHBoxLayout()

        self.table = QTableWidget()
        self.table.setColumnCount(3)
        self.table.setRowCount(0)
        self.table.setHorizontalHeaderLabels([
            self.t('table.grid.header.num', '#'),
            self.t('table.grid.header.time', 'Temps'),
            self.t('table.grid.header.text', 'Text del Guió')
        ])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setDefaultSectionSize(28)
        self.table.verticalHeader().setVisible(False)
        self.table.cellClicked.connect(self.on_cell_clicked)

        tbl_h = self.table.horizontalHeader()
        tbl_h.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        tbl_h.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        tbl_h.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)

        lower_layout.addWidget(self.table, 4)

        self.log_box = QGroupBox(self.t('group.log', "Historial i Diagnòstic"))
        log_box_layout = QVBoxLayout(self.log_box)
        log_box_layout.setContentsMargins(6, 10, 6, 6)
        self.log_edit = QTextEdit()
        self.log_edit.setReadOnly(True)
        log_box_layout.addWidget(self.log_edit)
        lower_layout.addWidget(self.log_box, 2)

        main_layout.addLayout(lower_layout, 5)

    def on_grace_time_changed(self, valor_segons):
        self.max_desync_grace_ms = valor_segons * 1000
        self.log(self.t('log.timing.grace_updated', "⏱️ Límit sense verificació actualitzat a {secs}s.", secs=valor_segons))

    def crear_panel_ajustos_estil(self):
        self.group_hdmi_box = QGroupBox(self.t('group.hdmi_styles', "🖥️ Control Global d'Estils del Projector HDMI (En Directe)"))
        layout = QHBoxLayout(self.group_hdmi_box)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(8)

        self.lbl_font = QLabel(self.t('lbl.font', 'Tipografia:'))
        layout.addWidget(self.lbl_font)
        self.combo_font = QComboBox()
        self.combo_font.addItems(["Arial", "Courier New", "Verdana", "Times New Roman", "Trebuchet MS"])
        self.combo_font.setCurrentText(self.projector_cfg.get('font_family', 'Arial'))
        self.combo_font.currentTextChanged.connect(self.actualitzar_font_projector)
        layout.addWidget(self.combo_font)

        self.lbl_size = QLabel(self.t('lbl.size', 'Mida Base:'))
        layout.addWidget(self.lbl_size)
        size_layout = QHBoxLayout()
        size_layout.setSpacing(2)

        self.btn_size_dec = QPushButton("-")
        self.btn_size_dec.setFixedWidth(28)
        self.btn_size_dec.setStyleSheet("padding: 0px; font-weight: bold; font-size: 14pt; color: white;")
        self.btn_size_dec.clicked.connect(self.disminuir_mida_projector)
        size_layout.addWidget(self.btn_size_dec)

        self.input_font_size = QLineEdit()
        self.input_font_size.setFixedWidth(40)
        self.input_font_size.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.input_font_size.setValidator(QIntValidator(10, 200, self))
        mida_inicial = self.projector_cfg.get('font_size', 38)
        self.input_font_size.setText(str(mida_inicial))
        self.input_font_size.textChanged.connect(self.actualitzar_mida_projector)
        size_layout.addWidget(self.input_font_size)

        self.btn_size_inc = QPushButton("+")
        self.btn_size_inc.setFixedWidth(28)
        self.btn_size_inc.setStyleSheet("padding: 0px; font-weight: bold; font-size: 14pt; color: white;")
        self.btn_size_inc.clicked.connect(self.augmentar_mida_projector)
        size_layout.addWidget(self.btn_size_inc)

        layout.addLayout(size_layout)

        self.lbl_rot = QLabel(self.t('lbl.rotation', 'Rotació:'))
        self.lbl_rot.setStyleSheet("color: #FFCA28; font-weight: bold;")
        layout.addWidget(self.lbl_rot)

        self.spin_rotation = QDoubleSpinBox()
        self.spin_rotation.setRange(-15.0, 15.0)
        self.spin_rotation.setSingleStep(0.2)
        self.spin_rotation.setDecimals(1)
        rot_inicial = float(self.projector_cfg.get('rotation', 0.0))
        self.spin_rotation.setValue(rot_inicial)
        self.spin_rotation.setSuffix(" °")
        self.spin_rotation.setFixedWidth(75)
        self.spin_rotation.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.spin_rotation.setStyleSheet("background-color: #2A2A2A; color: #FFCA28; font-weight: bold; border: 1px solid #FFCA28;")
        self.spin_rotation.valueChanged.connect(self.canviar_rotacio_projector)
        layout.addWidget(self.spin_rotation)

        self.lbl_h = QLabel(self.t('lbl.align_h', 'Horitzontal:'))
        layout.addWidget(self.lbl_h)
        self.combo_align_h = QComboBox()
        self.actualitzar_items_align_h()
        self.combo_align_h.currentTextChanged.connect(self.canviar_alineacio_h_projector)
        layout.addWidget(self.combo_align_h)

        self.lbl_v = QLabel(self.t('lbl.align_v', 'Vertical:'))
        layout.addWidget(self.lbl_v)
        self.combo_align_v = QComboBox()
        self.actualitzar_items_align_v()
        self.combo_align_v.currentTextChanged.connect(self.canviar_alineacio_v_projector)
        layout.addWidget(self.combo_align_v)

        self.btn_bg_color = QPushButton(self.t('btn.bg_color', '⬛ Fons HDMI'))
        self.btn_bg_color.clicked.connect(self.canviar_fons_projector)
        layout.addWidget(self.btn_bg_color)

        layout.addStretch()
        return self.group_hdmi_box

    def actualitzar_items_align_h(self):
        curr_data = self.combo_align_h.currentData() if hasattr(self, 'combo_align_h') and self.combo_align_h.count() > 0 else self.projector_cfg.get('align_h', 'Centrat')
        self.combo_align_h.blockSignals(True)
        self.combo_align_h.clear()
        self.combo_align_h.addItem(self.t('align.center', 'Centrat'), 'Centrat')
        self.combo_align_h.addItem(self.t('align.left', 'Esquerra'), 'Esquerra')
        self.combo_align_h.addItem(self.t('align.right', 'Dreta'), 'Dreta')
        idx = self.combo_align_h.findData(curr_data)
        if idx >= 0:
            self.combo_align_h.setCurrentIndex(idx)
        self.combo_align_h.blockSignals(False)

    def actualitzar_items_align_v(self):
        curr_data = self.combo_align_v.currentData() if hasattr(self, 'combo_align_v') and self.combo_align_v.count() > 0 else self.projector_cfg.get('align_v', 'Centrat')
        self.combo_align_v.blockSignals(True)
        self.combo_align_v.clear()
        self.combo_align_v.addItem(self.t('align.center', 'Centrat'), 'Centrat')
        self.combo_align_v.addItem(self.t('align.top', 'A dalt'), 'A dalt')
        self.combo_align_v.addItem(self.t('align.bottom', 'A baix'), 'A baix')
        idx = self.combo_align_v.findData(curr_data)
        if idx >= 0:
            self.combo_align_v.setCurrentIndex(idx)
        self.combo_align_v.blockSignals(False)

    def actualitzar_font_projector(self, font_name):
        if self.projector_window:
            self.projector_window.set_font_family(font_name)
            if not self.is_currently_auto_mode:
                self.actualitzar_pantalles()

    def augmentar_mida_projector(self):
        try:
            val = int(self.input_font_size.text())
        except ValueError:
            val = getattr(self.projector_window, 'base_font_size', 38)
        if val < 200:
            self.input_font_size.setText(str(val + 1))

    def disminuir_mida_projector(self):
        try:
            val = int(self.input_font_size.text())
        except ValueError:
            val = getattr(self.projector_window, 'base_font_size', 38)
        if val > 10:
            self.input_font_size.setText(str(val - 1))

    def actualitzar_mida_projector(self, size_str):
        if not size_str.strip():
            return
        try:
            mida = int(size_str)
            if self.projector_window and 10 <= mida <= 200:
                self.projector_window.set_font_size(mida)
                if not self.is_currently_auto_mode:
                    self.actualitzar_pantalles()
        except ValueError:
            pass

    def canviar_alineacio_h_projector(self, text):
        val_clave = self.combo_align_h.currentData() or text
        if self.projector_window:
            self.projector_window.set_align_h(val_clave)

    def canviar_alineacio_v_projector(self, text):
        val_clave = self.combo_align_v.currentData() or text
        if self.projector_window:
            self.projector_window.set_align_v(val_clave)

    def canviar_rotacio_projector(self, angle):
        if self.projector_window:
            self.projector_window.set_rotation(angle)

    def canviar_fons_projector(self):
        if not self.projector_window:
            return
        color = QColorDialog.getColor()
        if color.isValid():
            self.projector_window.set_bg_color(color.name())

    def reiniciar_transcripcio(self):
        """Buida la pantalla de veu i el buffer del sincronitzador sense tocar el rellotge de l'obra."""
        self.display_live_asr.setText(self.t('monitor.live_waiting', "[ Esperant veu de l'actor / ponent... ]"))
        self.last_sync_verify_ms = self.total_elapsed_ms
        if self.sincronitzador and self.current_index >= 0:
            self.sincronitzador.reiniciar_a_index(self.current_index)
        self.log(self.t('log.asr.reset_manual', "🧹 Transcripció i buffer de concordança reiniciats manualment."))
    
    def _obtenir_regles_watchdog(self):
        """Compila les expressions regulars i frases fantasma definides a config.yaml."""
        if not hasattr(self, '_watchdog_compiled') or self._watchdog_compiled is None:
            cfg = CONFIG_FULL.get('auto', {}).get('hallucination_watchdog', WATCHDOG_CFG)
            enabled = cfg.get('enabled', True)
            cooldown = float(cfg.get('cooldown_sec', 8.0))
            delay_ms = int(cfg.get('reconnect_delay_ms', 150))
            max_rep = int(cfg.get('max_repeated_words', 5))

            raw_patterns = cfg.get('regex_patterns', [r'(?:\.{2,}\s*){2,}', r'\.{5,}', r'^[\.\s]+$'])
            compiled_regex = []
            for pat in raw_patterns:
                try:
                    compiled_regex.append(re.compile(pat, re.IGNORECASE))
                except re.error as e:
                    self.log(self.t('log.watchdog.invalid_regex', "⚠️ Regex invàlida a hallucination_watchdog ('{pattern}'): {error}", pattern=pat, error=e))

            phrases = [re.escape(str(p).strip()) for p in cfg.get('phantom_phrases', []) if str(p).strip()]
            phantom_re = re.compile(rf'^(?:{"|".join(phrases)})[.!\s]*$', re.IGNORECASE) if phrases else None

            repeat_re = re.compile(rf'\b(\w+)\b(?:\s+\1\b){{{max(2, max_rep - 1)},}}', re.IGNORECASE) if max_rep > 1 else None

            self._watchdog_compiled = {
                'enabled': enabled,
                'cooldown': cooldown,
                'delay_ms': delay_ms,
                'regex_list': compiled_regex,
                'phantom_re': phantom_re,
                'repeat_re': repeat_re
            }
        return self._watchdog_compiled

    def _es_alucinacio(self, text):
        """Avalua si el text rebut de Whisper coincideix amb les regles de config.yaml."""
        regles = self._obtenir_regles_watchdog()
        if not regles['enabled']:
            return False

        txt = text.strip()
        if not txt:
            return False

        for rx in regles['regex_list']:
            if rx.pattern == r'^[\.\s]+$' and len(txt) < 3:
                continue
            if rx.search(txt):
                return True

        if regles['phantom_re'] and regles['phantom_re'].match(txt):
            return True

        if regles['repeat_re'] and regles['repeat_re'].search(txt):
            return True

        return False

    def reconectar_transcriptor_en_calent(self, motiu_text=""):
        """Tanca el WebSocket actual de WLK per buidar el buffer i el reobre sense tocar el rellotge."""
        regles = self._obtenir_regles_watchdog()
        ara = time.time()
        if ara - getattr(self, '_ultim_reinici_wlk', 0) < regles['cooldown']:
            return

        if getattr(self, '_reconnecting_asr', False):
            return

        self._ultim_reinici_wlk = ara
        self._reconnecting_asr = True

        mostra_motiu = motiu_text[:40] + ("..." if len(motiu_text) > 40 else "")
        self.log(self.t('log.watchdog.loop_detected', "⚠️ Bucle detectat ('{reason}'). Reiniciant connexió WLK...", reason=mostra_motiu))
        self.reiniciar_transcripcio()

        if self.audio_thread:
            self.audio_thread.stop()
            self.audio_thread.wait(500)
            self.audio_thread = None

        if self.transcriptor:
            self.transcriptor.aturar_escolta()
            self.transcriptor = None

        def _reobrir():
            try:
                if not self.session_started or self.is_paused:
                    return
                lang_in = self.combo_lang_in.currentData() or 'fr'
                config_trans = {
                    "whisper_live": WHISPER_CFG,
                    "input_language": lang_in,
                    "initial_prompt": self.obtenir_prompt_contextual(),
                    "translation": {
                        "enabled": self.translation_cfg.get('enabled', True),
                        "target_language": self.translation_cfg.get('target_language', 'ca')
                    },
                    "audio": {
                        "samplerate": self.audio_cfg.get('samplerate', 16000),
                        "block_ms": self.audio_cfg.get('block_ms', 100),
                        "device": self.combo_audio_device.currentData(),
                    },
                    "show_info": False
                }
                self.transcriptor = TranscriptorWhisperLiveKit(config=config_trans)
                self.audio_thread = AudioThread(self.transcriptor)
                self.audio_thread.subtitol_generat.connect(self.on_nou_subtitol_audio)
                self.audio_thread.start()
                self.log(self.t('log.watchdog.reconnected', "✅ Connexió WLK restablerta amb buffer net."))
            except Exception as e:
                self.log(self.t('log.watchdog.reconnect_error', "⚠️ Error reconnectant WLK: {error}", error=e))
            finally:
                self._reconnecting_asr = False

        QTimer.singleShot(regles['delay_ms'], _reobrir)

    def canviar_idioma_entrada_veu(self):
        """Canvia l'idioma d'escolta de Whisper en calent."""
        nou_idioma = self.combo_lang_in.currentData() or 'fr'
        self.log(self.t('log.asr.changing_lang', "🎙️ Canviant idioma d'entrada ASR a: {lang}...", lang=nou_idioma))

        self.reiniciar_transcripcio()

        if self.session_started and not self.is_paused:
            if self.audio_thread:
                self.audio_thread.stop()
                self.audio_thread.wait(500)
                self.audio_thread = None

            if self.transcriptor:
                self.transcriptor.aturar_escolta()
                self.transcriptor = None

            config_trans = {
                "whisper_live": WHISPER_CFG,
                "input_language": nou_idioma,
                "initial_prompt": self.obtenir_prompt_contextual(),
                "translation": {
                    "enabled": True,
                    "target_language": self.translation_cfg.get('target_language', 'ca')
                },
                "audio": {
                    "samplerate": self.audio_cfg.get('samplerate', 16000),
                    "block_ms": self.audio_cfg.get('block_ms', 100),
                    "device": self.combo_audio_device.currentData(),
                },
                "show_info": False
            }
            try:
                self.transcriptor = TranscriptorWhisperLiveKit(config=config_trans)
                self.audio_thread = AudioThread(self.transcriptor)
                self.audio_thread.subtitol_generat.connect(self.on_nou_subtitol_audio)
                self.audio_thread.start()
                self.log(self.t('log.asr.reconnected_lang', "✅ Reconnectat escoltant en {lang}.", lang=nou_idioma))
            except Exception as e:
                self.log(self.t('log.asr.change_lang_error', "⚠️ Error canviant idioma ASR: {error}", error=e))

    def recollir_estat_actual(self):
        hdmi_actual = {
            'enabled': self.hdmi_enabled,
            'title': "Projector HDMI",
            'font_family': self.combo_font.currentText() if hasattr(self, 'combo_font') else "Arial",
            'font_size': int(self.input_font_size.text()) if hasattr(self, 'input_font_size') and self.input_font_size.text().isdigit() else 38,
            'align_h': self.combo_align_h.currentData() if hasattr(self, 'combo_align_h') else "Centrat",
            'align_v': self.combo_align_v.currentData() if hasattr(self, 'combo_align_v') else "Centrat",
            'rotation': float(self.spin_rotation.value()) if hasattr(self, 'spin_rotation') else 0.0,
            'background_color': getattr(self.projector_window, 'bg_color', "#000000") if self.projector_window else "#000000",
            'active_languages': self.hdmi_langs_data
        }
        sincro_actual = {
            'default_guide_language': self.combo_guia.currentData() or self.default_guide_lang,
            'input_language': self.combo_lang_in.currentData() if hasattr(self, 'combo_lang_in') else 'fr',
            'timing': {
                'grace_limit_sec': self.spin_grace.value(),
                'force_auto_fallback': self.forced_auto_mode
            },
            'audio_device': self.combo_audio_device.currentData(),
            'matching': {
                'context_future_subs': self.n_futurs_prompt,
                'back_window': self.back_window
            }
        }
        
        return {
            'hdmi': hdmi_actual,
            'sincro': sincro_actual,
            'interface_lang': getattr(self, 'app_lang', 'ca')
        }

    def guardar_configuracio_fitxer(self):
        global CONFIG_FULL
        path, _filtre = QFileDialog.getSaveFileName(
            self, 
            self.t('dialog.save_config.title', 'Desar Configuració'), 
            CONFIG_YAML_PATH, 
            self.t('dialog.config_filter', 'Configuració YAML (*.yaml *.yml)')
        )
        if not path:
            return

        if os.path.exists(path):
            dades = carregar_configuracio_yaml(path)
        else:
            dades = copy.deepcopy(CONFIG_FULL)

        for clau_arrel in ('common', 'manual', 'auto', 'sincro'):
            if clau_arrel not in dades:
                dades[clau_arrel] = copy.deepcopy(CONFIG_FULL.get(clau_arrel, {}))

        estat = self.recollir_estat_actual()

        dades.setdefault('common', {}).setdefault('styles', {})['hdmi'] = estat['hdmi']
        dades.setdefault('common', {}).setdefault('interface', {})['language'] = estat['interface_lang']
        
        sincro_dict = dades.setdefault('sincro', {})
        sincro_dict['default_guide_language'] = estat['sincro']['default_guide_language']
        sincro_dict['input_language'] = estat['sincro']['input_language']
        sincro_dict.setdefault('timing', {})['grace_limit_sec'] = estat['sincro']['timing']['grace_limit_sec']
        sincro_dict.setdefault('timing', {})['force_auto_fallback'] = estat['sincro']['timing']['force_auto_fallback']
        sincro_dict.setdefault('matching', {})['context_future_subs'] = estat['sincro']['matching']['context_future_subs']
        sincro_dict['audio_device'] = estat['sincro']['audio_device']

        try:
            with open(path, 'w', encoding='utf-8') as f:
                yaml.safe_dump(dades, f, sort_keys=False, allow_unicode=True, indent=2)
            CONFIG_FULL = dades
            QMessageBox.information(
                self, 
                self.t('dialog.config_saved.title', 'Configuració'), 
                self.t('dialog.config_saved.msg', 'Configuració desada correctament!')
            )
        except Exception as e:
            QMessageBox.critical(
                self, 
                self.t('dialog.error.title', 'Error'), 
                self.t('dialog.save_error.msg', "No s'ha pogut desar la configuració: {error}", error=e)
            )

    def carregar_configuracio_fitxer(self):
        path, _filtre = QFileDialog.getOpenFileName(
            self, 
            self.t('dialog.load_config.title', 'Carregar Configuració'), 
            BASE_DIR, 
            self.t('dialog.config_filter', 'Configuració YAML (*.yaml *.yml)')
        )
        if not path or not os.path.exists(path):
            return

        try:
            cfg = carregar_configuracio_yaml(path)
            self.aplicar_configuracio(cfg)
            QMessageBox.information(
                self, 
                self.t('dialog.config_loaded.title', 'Configuració'), 
                self.t('dialog.config_loaded.msg', 'Configuració carregada i aplicada!')
            )
        except Exception as e:
            QMessageBox.critical(
                self, 
                self.t('dialog.error.title', 'Error'), 
                self.t('dialog.load_error.msg', 'Error en carregar el fitxer: {error}', error=e)
            )

    def aplicar_configuracio(self, cfg):
        global i18n, _, CONFIG_FULL
        CONFIG_FULL = cfg
        self._watchdog_compiled = None
        
        matching_in = cfg.get('sincro', {}).get('matching', {})
        self.back_window = int(matching_in.get('back_window', 1))
        if self.sincronitzador:
            self.sincronitzador.back_window = self.back_window

        # 1. Idioma d'interfície en calent
        nou_idioma = cfg.get('common', {}).get('interface', {}).get('language')
        if nou_idioma and str(nou_idioma).lower().strip() != getattr(self, 'app_lang', None):
            self.app_lang = str(nou_idioma).lower().strip()
            i18n = I18nManager(modul="sincro", lang=self.app_lang, base_dir=DIR_I18N)
            _ = i18n.t
            self.retranslate_ui()

        # 2. Ajustos HDMI
        hdmi = cfg.get('common', {}).get('styles', {}).get('hdmi', {})

        if 'font_family' in hdmi and hasattr(self, 'combo_font'):
            self.combo_font.setCurrentText(hdmi['font_family'])
            if self.projector_window:
                self.projector_window.set_font_family(hdmi['font_family'])

        if 'font_size' in hdmi and hasattr(self, 'input_font_size'):
            self.input_font_size.setText(str(hdmi['font_size']))
            if self.projector_window:
                self.projector_window.set_font_size(int(hdmi['font_size']))

        if 'align_h' in hdmi and hasattr(self, 'combo_align_h'):
            idx = self.combo_align_h.findData(hdmi['align_h'])
            if idx >= 0:
                self.combo_align_h.setCurrentIndex(idx)
            if self.projector_window:
                self.projector_window.set_align_h(hdmi['align_h'])

        if 'align_v' in hdmi and hasattr(self, 'combo_align_v'):
            idx = self.combo_align_v.findData(hdmi['align_v'])
            if idx >= 0:
                self.combo_align_v.setCurrentIndex(idx)
            if self.projector_window:
                self.projector_window.set_align_v(hdmi['align_v'])

        if 'rotation' in hdmi and hasattr(self, 'spin_rotation'):
            self.spin_rotation.setValue(float(hdmi['rotation']))
            if self.projector_window:
                self.projector_window.set_rotation(float(hdmi['rotation']))

        if 'background_color' in hdmi and self.projector_window:
            self.projector_window.set_bg_color(hdmi['background_color'])

        if 'active_languages' in hdmi and hdmi['active_languages']:
            self.hdmi_langs_data = hdmi['active_languages']
            for item in self.hdmi_langs_data:
                self.idiomes_colors_mem[item['lang']] = item['color']
            self.dibuixar_taula_hdmi()

        # 3. Ajustos específics de Sincro
        sincro_cfg = cfg.get('sincro', {})

        self.n_futurs_prompt = int(sincro_cfg.get('matching', {}).get('context_future_subs', 4))

        lang_guia = sincro_cfg.get('default_guide_language') or cfg.get('manual', {}).get('default_guide_language')
        if lang_guia and hasattr(self, 'combo_guia'):
            idx = self.combo_guia.findData(lang_guia.upper())
            if idx >= 0:
                self.combo_guia.setCurrentIndex(idx)

        lang_in = sincro_cfg.get('input_language') or cfg.get('auto', {}).get('input_language')
        if lang_in and hasattr(self, 'combo_lang_in'):
            idx = self.combo_lang_in.findData(lang_in.lower())
            if idx >= 0:
                self.combo_lang_in.setCurrentIndex(idx)

        grace_sec = sincro_cfg.get('timing', {}).get('grace_limit_sec')
        if grace_sec is not None and hasattr(self, 'spin_grace'):
            self.spin_grace.setValue(int(grace_sec))

        audio_dev = sincro_cfg.get('audio_device') or cfg.get('auto', {}).get('audio', {}).get('device')
        if audio_dev is not None and hasattr(self, 'combo_audio_device'):
            idx = self.combo_audio_device.findData(audio_dev)
            if idx >= 0:
                self.combo_audio_device.setCurrentIndex(idx)

        self.actualitzar_pantalles()

    def retranslate_ui(self):
        self.setWindowTitle(self.t('window.title', '{title} ⚡ - Sincronitzador Intel·ligent', title=self.app_title))
        if hasattr(self, 'projector_window') and self.projector_window:
            self.projector_window.setWindowTitle(self.t('projector.title', '{title} - Projector HDMI', title=self.app_title))

        if hasattr(self, 'btn_open'): self.btn_open.setText(self.t('btn.open_srt', '📂 Obrir .SRT'))
        if hasattr(self, 'btn_save_cfg'): self.btn_save_cfg.setText(self.t('btn.save_config', '💾 Desar Config'))
        if hasattr(self, 'btn_load_cfg'): self.btn_load_cfg.setText(self.t('btn.load_config', '📂 Carregar Config'))
        if hasattr(self, 'btn_toggle_proj'): self.btn_toggle_proj.setText(self.t('btn.toggle_projector', '🖥️ HDMI'))

        if hasattr(self, 'chk_auto_sincro'):
            self.chk_auto_sincro.setText(self.t('chk.auto_sincro_on', '🔄 Sincro Auto (ON)') if self.auto_sincro_active else self.t('chk.auto_sincro_off', '🔄 Manual (OFF)'))
        if hasattr(self, 'lbl_grace_limit'): self.lbl_grace_limit.setText(self.t('lbl.grace_limit', 'Límit Auto:'))
        if hasattr(self, 'spin_grace'):
            self.spin_grace.setToolTip(self.t('tooltip.grace_limit', "Segons sense reconèixer text abans de projectar transcripció directa."))
        if hasattr(self, 'btn_force_auto'):
            self.btn_force_auto.setText(self.t('btn.force_auto_on', '🤖 Forçar Auto (ACTIU)') if self.forced_auto_mode else self.t('btn.force_auto_off', '🤖 Forçar Auto (OFF)'))

        if hasattr(self, 'group_hdmi_box'):
            self.group_hdmi_box.setTitle(self.t('group.hdmi_styles', "🖥️ Control Global d'Estils del Projector HDMI (En Directe)"))
        if hasattr(self, 'lbl_font'): self.lbl_font.setText(self.t('lbl.font', 'Tipografia:'))
        if hasattr(self, 'lbl_size'): self.lbl_size.setText(self.t('lbl.size', 'Mida Base:'))
        if hasattr(self, 'lbl_rot'): self.lbl_rot.setText(self.t('lbl.rotation', 'Rotació:'))
        if hasattr(self, 'lbl_h'): self.lbl_h.setText(self.t('lbl.align_h', 'Horitzontal:'))
        if hasattr(self, 'lbl_v'): self.lbl_v.setText(self.t('lbl.align_v', 'Vertical:'))
        if hasattr(self, 'btn_bg_color'): self.btn_bg_color.setText(self.t('btn.bg_color', '⬛ Fons HDMI'))

        if hasattr(self, 'combo_align_h'): self.actualitzar_items_align_h()
        if hasattr(self, 'combo_align_v'): self.actualitzar_items_align_v()

        if hasattr(self, 'audio_box'): self.audio_box.setTitle(self.t('group.audio', "🎙️ Control d'Entrada d'Àudio i Nivell"))
        if hasattr(self, 'lbl_audio_dev'): self.lbl_audio_dev.setText(self.t('lbl.audio_device', "Dispositiu:"))
        if hasattr(self, 'chk_activar_vu'): self.chk_activar_vu.setText(self.t('chk.activate_vu', "Activar Vúmetre"))

        if hasattr(self, 'lbl_live_title'): self.lbl_live_title.setText(self.t('monitor.live_title', "ENTRADA DE VEU EN DIRECTE (ASR)"))
        if hasattr(self, 'lbl_active_title'):
            if self.is_currently_auto_mode:
                self.lbl_active_title.setText(self.t('monitor.active_auto', "SUBTÍTOL PROJECTAT (MODE AUTO - ASR + TRADUCCIÓ DIRECTA)"))
            elif self.current_index >= 0:
                self.lbl_active_title.setText(self.t('monitor.active_official', "SUBTÍTOL PROJECTAT (GUIÓ OFICIAL: #{idx})", idx=self.current_index + 1))
            else:
                self.lbl_active_title.setText(self.t('monitor.active_official_base', "SUBTÍTOL PROJECTAT (GUIÓ OFICIAL)"))

        if hasattr(self, 'config_box'):
            nom_grup_sincro = self.t('group.sync_status', "Estat de la Sincronització i Idiomes HDMI") if self.hdmi_enabled else self.t('group.sync_status_no_hdmi', "Estat de la Sincronització i Idiomes")
            self.config_box.setTitle(nom_grup_sincro)
        if hasattr(self, 'lbl_guide_lang'): self.lbl_guide_lang.setText(self.t('lbl.guide_lang', "Idioma Guia Operador:"))
        if hasattr(self, 'lbl_lang_in'): self.lbl_lang_in.setText(self.t('lbl.lang_in', "Veu de l'actor (ASR):"))
        if hasattr(self, 'btn_reset_transcripcio'): self.btn_reset_transcripcio.setText(self.t('btn.reset_asr', "🧹 Netejar Transcripció"))
        if hasattr(self, 'lbl_hdmi_project_langs'): self.lbl_hdmi_project_langs.setText(self.t('lbl.hdmi_project_langs', "Idiomes a Projectar a HDMI:"))

        if hasattr(self, 'table_hdmi'):
            self.table_hdmi.setHorizontalHeaderLabels([
                self.t('table.hdmi.header.active_lang', 'Actiu i Idioma'),
                self.t('table.hdmi.header.sample', 'Color'),
                self.t('table.hdmi.header.action', 'Acció')
            ])

        if hasattr(self, 'timer_box'): self.timer_box.setTitle(self.t('group.timer', "Control Temporal i Sessió"))
        if hasattr(self, 'btn_start_stop'):
            self.btn_start_stop.setText(self.t('btn.stop', "⏹️ Stop") if self.session_started else self.t('btn.start', "▶️ Start"))
        if hasattr(self, 'btn_pause'):
            self.btn_pause.setText(self.t('btn.resume', "▶️ Reprendre") if self.is_paused else self.t('btn.pause', "⏸ Pause"))
        if hasattr(self, 'btn_prev'): self.btn_prev.setText(self.t('btn.prev', "⏮ Anterior (←)"))
        if hasattr(self, 'btn_blackout'): self.btn_blackout.setText(self.t('btn.blackout', "⬛ Blackout (B)"))
        if hasattr(self, 'btn_next'): self.btn_next.setText(self.t('btn.next', "Següent (Espai / →) ⏭"))

        if hasattr(self, 'lbl_title_total'): self.lbl_title_total.setText(self.t('lbl.total_time', "Temps Total:"))
        if hasattr(self, 'lbl_title_elapsed'): self.lbl_title_elapsed.setText(self.t('lbl.sub_elapsed', "En Frase:"))
        if hasattr(self, 'lbl_title_countdown'): self.lbl_title_countdown.setText(self.t('lbl.sub_countdown', "Següent (teòric):"))

        if hasattr(self, 'lbl_search_title'): self.lbl_search_title.setText(self.t('lbl.search', "🔎 Cerca al guió:"))
        if hasattr(self, 'input_search'): self.input_search.setPlaceholderText(self.t('search.placeholder', "Escriu per cercar sense projectar..."))
        if hasattr(self, 'btn_search'): self.btn_search.setText(self.t('btn.search_next', "🔍 Següent"))
        if hasattr(self, 'btn_confirm_search'): self.btn_confirm_search.setText(self.t('btn.search_project', "🚀 Projectar seleccionat"))

        if hasattr(self, 'table'):
            self.table.setHorizontalHeaderLabels([
                self.t('table.grid.header.num', '#'),
                self.t('table.grid.header.time', 'Temps'),
                self.t('table.grid.header.text', 'Text del Guió')
            ])

        if hasattr(self, 'log_box'): self.log_box.setTitle(self.t('group.log', "Historial i Diagnòstic"))

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
            self.table_hdmi.setRowHeight(row, 28)

            cb = QCheckBox(f"[{data['lang']}]  {data['label']}")
            cb.setChecked(data['enabled'])
            cb.setStyleSheet("font-weight: bold; margin-left: 6px; color: #FFFFFF;")
            cb.toggled.connect(lambda val, r=row: self.on_checkbox_toggled(r, val))
            self.table_hdmi.setCellWidget(row, 0, cb)

            sample = QLabel()
            sample.setFixedSize(18, 18)
            sample.setStyleSheet(f"background-color: {data['color']}; border: 1px solid white; border-radius: 3px;")
            sample_container = QWidget()
            s_layout = QHBoxLayout(sample_container)
            s_layout.setContentsMargins(0, 0, 0, 0)
            s_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
            s_layout.addWidget(sample)
            self.table_hdmi.setCellWidget(row, 1, sample_container)

            btn_col = QPushButton(self.t('btn.color', "🎨"))
            btn_col.setCursor(Qt.CursorShape.PointingHandCursor)
            btn_col.setStyleSheet("""
                QPushButton {
                    background-color: #383838;
                    color: #FFFFFF;
                    border: 1px solid #777777;
                    border-radius: 3px;
                    padding: 2px 6px;
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
            self.chk_auto_sincro.setText(self.t('chk.auto_sincro_on', "🔄 Sincro Auto (ON)"))
            self.chk_auto_sincro.setStyleSheet("color: #00E676; font-size: 10pt;")
        else:
            self.chk_auto_sincro.setText(self.t('chk.auto_sincro_off', "🔄 Manual (OFF)"))
            self.chk_auto_sincro.setStyleSheet("color: #FFA726; font-size: 10pt;")

    def on_toggle_force_auto(self, checked):
        self.forced_auto_mode = checked
        if checked:
            self.btn_force_auto.setText(self.t('btn.force_auto_on', "🤖 Forçar Auto (ACTIU)"))
            self.btn_force_auto.setStyleSheet("background-color: #E65100; color: #FFFFFF; font-weight: bold;")
            self.lbl_estat_sincro.setText(self.t('status.forced_auto', "🤖 Mode Auto Forçat per l'operador (ASR + Traducció)"))
            self.lbl_estat_sincro.setStyleSheet("color: #FFA726; font-weight: bold;")
            self.log(self.t('log.mode.forced_auto_on', "ℹ️ L'operador ha activat manualment el Mode Auto."))
        else:
            self.btn_force_auto.setText(self.t('btn.force_auto_off', "🤖 Forçar Auto (OFF)"))
            self.btn_force_auto.setStyleSheet("background-color: #37474F; color: #FFFFFF; font-weight: bold;")
            self.is_currently_auto_mode = False
            self.lbl_estat_sincro.setText(self.t('status.rehooking', "🟢 Reenganxant Sincronització..."))
            self.lbl_estat_sincro.setStyleSheet("color: #00E676; font-weight: bold;")
            self.log(self.t('log.mode.forced_auto_off', "ℹ️ Desactivat forçat de Mode Auto. Tornant a sincronitzar guió."))
            if self.current_index >= 0:
                self.set_active_sub(self.current_index, recalibrar_temps=True)

    def open_srt_file(self):
        file_path, _filtre = QFileDialog.getOpenFileName(
            self, 
            self.t('dialog.open_srt.title', "Obrir SRT"), 
            "", 
            self.t('dialog.open_srt.filter', "Subtítols SRT (*.srt)")
        )
        if file_path:
            self.parse_srt(file_path)
            self.configurar_canals_i_ui()
            self.render_grid()
            if self.subtitles:
                self.set_active_sub(0, recalibrar_temps=False)
                self.btn_next.setEnabled(True)
                self.btn_prev.setEnabled(True)

    def parse_srt(self, path):
        with open(path, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read()

        content = content.replace('\r\n', '\n').replace('\r', '\n')
        blocks = re.split(r'\n\s*\n', content.strip())
        self.subtitles = []
        detected_tag_set = set()

        for block in blocks:
            lines = [l.strip() for l in block.split('\n') if l.strip()]
            if len(lines) >= 2:
                sub_id = str(lines[0])
                time_str = str(lines[1])
                raw_text = "\n".join(lines[2:])

                times = time_str.split(' --> ')
                if len(times) > 0:
                    start_ms = srt_time_to_ms(times[0]) if srt_time_to_ms else parse_time_str_fallback(times[0])
                else:
                    start_ms = 0

                if len(times) > 1:
                    end_ms = srt_time_to_ms(times[1]) if srt_time_to_ms else parse_time_str_fallback(times[1])
                else:
                    end_ms = 0

                matches = re.findall(r'\[([A-Z]{2,4})\]\s*(.*?)(?=\n?\[[A-Z]{2,4}\]|$)', raw_text, re.DOTALL)

                texts_by_lang = {}
                if matches:
                    for lang, text in matches:
                        clean_lines = [l.strip() for l in text.strip().split('\n') if l.strip()]
                        texts_by_lang[str(lang)] = "\n".join(clean_lines)
                        detected_tag_set.add(str(lang))
                else:
                    clean_lines = [l.strip() for l in raw_text.strip().split('\n') if l.strip()]
                    texts_by_lang['UNI'] = "\n".join(clean_lines)
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
        if not self.subtitles:
            return

        lang_guia = self.combo_guia.currentData() or 'UNI'

        self.table.setUpdatesEnabled(False)
        self.table.blockSignals(True)

        for row, sub in enumerate(self.subtitles):
            item = self.table.item(row, 2)
            text_guia = sub['texts'].get(lang_guia, "")
            if item:
                item.setText(text_guia)

        self.table.blockSignals(False)
        self.table.setUpdatesEnabled(True)

        if 0 <= self.current_index < len(self.subtitles):
            text_actiu = self.subtitles[self.current_index]['texts'].get(lang_guia, "")
            self.display_active_sub.setText(f"[{lang_guia}] {text_actiu}")

    def render_grid(self):
        lang_guia = self.combo_guia.currentData() or (self.detected_langs[0] if self.detected_langs else 'UNI')
        
        self.table.setUpdatesEnabled(False)
        self.table.blockSignals(True)

        self.table.setRowCount(len(self.subtitles))
        for row, sub in enumerate(self.subtitles):
            self.table.setRowHeight(row, 28)
            item_id = QTableWidgetItem(str(sub['id']))
            item_id.setTextAlignment(Qt.AlignmentFlag.AlignCenter)

            item_time = QTableWidgetItem(str(sub['time']))
            item_time.setTextAlignment(Qt.AlignmentFlag.AlignCenter)

            text_guia = str(sub['texts'].get(lang_guia, ""))
            item_text = QTableWidgetItem(text_guia)

            self.table.setItem(row, 0, item_id)
            self.table.setItem(row, 1, item_time)
            self.table.setItem(row, 2, item_text)

        self.table.blockSignals(False)
        self.table.setUpdatesEnabled(True)

    def on_cell_clicked(self, row, column):
        self.set_active_sub(row, recalibrar_temps=True)

    def set_active_sub(self, index, recalibrar_temps=False):
        if 0 <= index < len(self.subtitles):
            self.is_blackout = False
            self.is_currently_auto_mode = False
            self.current_index = index
            self.table.selectRow(self.current_index)
            
            item = self.table.item(self.current_index, 0)
            if item:
                self.table.setCurrentItem(item)
                self.table.scrollToItem(item, QAbstractItemView.ScrollHint.PositionAtCenter)
                
            self.sub_elapsed_ms = 0

            if recalibrar_temps:
                sub_target = self.subtitles[index]['start_ms']
                self.time_offset_ms = sub_target - self.total_elapsed_ms
                self.playhead_ms = sub_target
                self.last_sync_verify_ms = self.total_elapsed_ms
                fn_t = ms_to_time_str if ms_to_time_str else ms_to_time_str_fallback
                self.log(self.t('log.nav.positioned', "📍 Posicionat a subtítol #{idx} ({time}). Compte enrere reiniciat.", idx=index + 1, time=fn_t(sub_target)))

            if self.sincronitzador:
                self.sincronitzador.reiniciar_a_index(self.current_index)
            self.actualitzar_pantalles()

    def confirmar_salt_candidat(self):
        fila_seleccionada = self.table.currentRow()
        if 0 <= fila_seleccionada < len(self.subtitles):
            self.set_active_sub(fila_seleccionada, recalibrar_temps=True)
            self.cerca_index_candidat = -1

    def cercar_subtitol_seguent(self):
        query = self.input_search.text().strip().lower()
        if not query or not self.subtitles:
            return

        total = len(self.subtitles)
        punt_inici = self.cerca_index_candidat if self.cerca_index_candidat >= 0 else self.current_index
        start_search_idx = (punt_inici + 1) if (0 <= punt_inici < total - 1) else 0

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
            self.cerca_index_candidat = found_idx
            item = self.table.item(found_idx, 0)
            if item:
                self.table.setCurrentItem(item)
                self.table.scrollToItem(item, QAbstractItemView.ScrollHint.PositionAtCenter)
        else:
            self.cerca_index_candidat = -1
            QMessageBox.information(
                self, 
                self.t('dialog.search_not_found.title', "Cerca"), 
                self.t('dialog.search_not_found.msg', "No s'ha trobat cap subtítol amb el text: '{query}'", query=query)
            )

    def actualitzar_pantalles(self):
        if self.is_blackout or not (0 <= self.current_index < len(self.subtitles)):
            return

        sub = self.subtitles[self.current_index]
        lang_guia = self.combo_guia.currentData() or 'UNI'
        text_guia = str(sub['texts'].get(lang_guia, ""))

        self.lbl_active_title.setText(self.t('monitor.active_official', "SUBTÍTOL PROJECTAT (GUIÓ OFICIAL: #{idx})", idx=self.current_index + 1))
        self.lbl_active_title.setStyleSheet(f"color: {self.color_primary}; font-weight: bold; font-size: 9pt;")
        self.display_active_sub.setText(f"[{lang_guia}] {text_guia}")
        self.display_active_sub.setStyleSheet(f"""
            background-color: {self.color_panel_bg};
            border: 2px solid {self.color_primary};
            border-radius: 6px;
            padding: 8px;
            min-height: 44px;
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

        self.lbl_active_title.setText(self.t('monitor.active_auto', "SUBTÍTOL PROJECTAT (MODE AUTO - ASR + TRADUCCIÓ DIRECTA)"))
        self.lbl_active_title.setStyleSheet("color: #FFA726; font-weight: bold; font-size: 9pt;")
        
        lbl_veu = self.t('monitor.auto_voice', "Veu:")
        lbl_trad = self.t('monitor.auto_translation', "Traducció:")
        mostra_auto = f"{lbl_veu} {text_orig}"
        if text_trans:
            mostra_auto += f"\n{lbl_trad} {text_trans}"
            
        self.display_active_sub.setText(mostra_auto)
        self.display_active_sub.setStyleSheet(f"""
            background-color: {self.color_panel_bg};
            border: 2px dashed #FFA726;
            border-radius: 6px;
            padding: 8px;
            min-height: 44px;
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
                lang_in = str(self.combo_lang_in.currentData() or 'fr').lower()
                lang_out = str(self.translation_cfg.get('target_language', 'ca')).lower()
                self.emissor.emetre(lang_in, w_orig)
                if w_trans and lang_out != lang_in:
                    self.emissor.emetre(lang_out, w_trans)

    def on_nou_subtitol_audio(self, dades):
        if not self.session_started or self.is_paused or self.is_blackout:
            return

        if getattr(self, '_reconnecting_asr', False):
            return

        if isinstance(dades, str):
            text_orig = dades
            text_trans = ""
        else:
            text_orig = str(dades.get("original", "")).strip()
            text_trans = str(dades.get("translation", "")).strip()

        if not text_orig:
            return

        # --- WATCHDOG ANTI-AL·LUCINACIONS ---
        if self._es_alucinacio(text_orig):
            self.reconectar_transcriptor_en_calent(motiu_text=text_orig)
            return

        # 1. Mostrem EXCLUSIVAMENT la transcripció retallada a les línies màximes fixes de l'HDMI
        ample_disponible = max(100, self.display_live_asr.width() - 25)
        text_retallat = ajustar_text_a_linies_reals_qt(
            text_orig,
            self.display_live_asr.font(),
            ample_disponible,
            max_lines=self.max_lines_hdmi
        )
        self.display_live_asr.setText(text_retallat)

        # 2. Si no hi ha guió carregat o està forçat el mode auto
        if self.forced_auto_mode or not self.subtitles or not self.auto_sincro_active or self.sincronitzador is None:
            self.aplicar_mode_auto(text_orig, text_trans)
            return

        # 3. Concordança intel·ligent amb el sincronitzador
        lang_actor = (self.combo_lang_in.currentData() or 'fr').upper()
        lang_comparacio = lang_actor if (self.subtitles and lang_actor in self.subtitles[0]['texts']) else (self.combo_guia.currentData() or 'UNI')

        status, payload, nou_idx, score = self.sincronitzador.avaluar(
            text_asr=text_orig,
            current_index=self.current_index,
            subtitles=self.subtitles,
            lang_guia=lang_comparacio,
            current_playhead_ms=self.playhead_ms
        )

        fn_time = ms_to_time_str if ms_to_time_str else ms_to_time_str_fallback
        timestamp = fn_time(self.total_elapsed_ms)

        if status == "MATCH":
            self.last_sync_verify_ms = self.total_elapsed_ms
            sub_target_time = payload['start_ms']
            nou_offset = sub_target_time - self.total_elapsed_ms
            
            self.time_offset_ms = int(0.7 * self.time_offset_ms + 0.3 * nou_offset)
            self.playhead_ms = self.total_elapsed_ms + self.time_offset_ms

            if self.is_currently_auto_mode:
                self.log(self.t('log.sync.rehooked', "[{timestamp}] 🔄 Reenganxat al guió a #{idx}", timestamp=timestamp, idx=nou_idx + 1))
            self.is_currently_auto_mode = False

            offset_s = f"{self.time_offset_ms/1000:+.1f}"
            if nou_idx != self.current_index:
                self.set_active_sub(nou_idx, recalibrar_temps=False)
                self.lbl_estat_sincro.setText(self.t('status.synced', "🟢 Sincronitzat (Línia {line} | Score: {score}% | Offset: {offset}s)", line=nou_idx + 1, score=score, offset=offset_s))
                self.lbl_estat_sincro.setStyleSheet("color: #00E676; font-weight: bold;")
                preview_txt = payload['texts'].get(lang_comparacio, '')[:45]
                self.log(self.t('log.sync.match', "[{timestamp}] 🎯 MATCH #{idx} (Score: {score}%): {preview}...", timestamp=timestamp, idx=nou_idx + 1, score=score, preview=preview_txt))
            else:
                self.lbl_estat_sincro.setText(self.t('status.calibrated', "🟢 Calibrat (Score: {score}% | Offset: {offset}s)", score=score, offset=offset_s))
                self.lbl_estat_sincro.setStyleSheet("color: #00E676; font-weight: bold;")
        else:
            temps_desync = self.total_elapsed_ms - self.last_sync_verify_ms
            if temps_desync > self.max_desync_grace_ms:
                secs_sense = int(temps_desync / 1000)
                self.lbl_estat_sincro.setText(self.t('status.auto_active', "🤖 Mode Auto Actiu (Sense verificar fa {secs}s)", secs=secs_sense))
                self.lbl_estat_sincro.setStyleSheet("color: #FFA726; font-weight: bold;")
                self.aplicar_mode_auto(text_orig, text_trans)

    def on_click_start_stop(self):
        """Atura o engega immediatament sense diàlegs de confirmació."""
        if not self.session_started:
            self.start_session()
        else:
            self.stop_session()

    def start_session(self):
        host = WHISPER_CFG.get('host', 'localhost')
        port = WHISPER_CFG.get('port', 8000)
        if not comprovar_servidor_actiu(host, port):
            self.log(self.t('log.wlk.retry', "⚠️ El servidor WLK no respon. Intentant reactivar-lo..."))
            self.verificar_servidor_wlk()

        self.session_started = True
        self.is_paused = False
        self.is_blackout = False
        self.is_currently_auto_mode = False
        self.total_elapsed_ms = 0
        self.time_offset_ms = 0
        self.last_sync_verify_ms = 0

        if self.subtitles and self.current_index < 0:
            self.set_active_sub(0, recalibrar_temps=False)

        self.timer.start()

        if self.transcriptor:
            sel_dev = self.combo_audio_device.currentData()
            self.transcriptor.device = sel_dev
            try:
                self.transcriptor.inicialitzar_motor()
            except Exception as e:
                self.log(self.t('log.engine.audio_error', "⚠️ Error validant dispositiu d'àudio: {error}", error=e))

            self.audio_thread = AudioThread(self.transcriptor)
            self.audio_thread.subtitol_generat.connect(self.on_nou_subtitol_audio)
            self.audio_thread.start()

        if self.chk_activar_vu.isChecked():
            self.toggle_vumetre(True)

        self.btn_start_stop.setText(self.t('btn.stop', "⏹️ Stop"))
        self.btn_start_stop.setStyleSheet("background-color: #C62828; color: white;")
        self.btn_pause.setEnabled(True)

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
        self.playhead_ms = 0
        self.time_offset_ms = 0
        self.sub_elapsed_ms = 0
        self.last_sync_verify_ms = 0

        self.lbl_total_time.setText("00:00")
        self.lbl_sub_elapsed.setText("00:00")
        self.lbl_sub_countdown.setText("--:--")

        self.btn_start_stop.setText(self.t('btn.start', "▶️ Start"))
        self.btn_start_stop.setStyleSheet("background-color: #1b5e20; color: white;")
        self.btn_pause.setEnabled(False)

    def toggle_pause(self):
        if not self.session_started:
            return
        if self.is_paused:
            self.is_paused = False
            self.timer.start()
            self.btn_pause.setText(self.t('btn.pause', "⏸ Pause"))
            self.btn_pause.setStyleSheet(f"background-color: {self.color_btn_bg}; color: white;")
        else:
            self.is_paused = True
            self.timer.stop()
            self.btn_pause.setText(self.t('btn.resume', "▶️ Reprendre"))
            self.btn_pause.setStyleSheet("background-color: #1b5e20; color: white;")

    def toggle_blackout(self):
        if not self.is_blackout:
            self.is_blackout = True
            self.display_active_sub.setText(self.t('preview.blackout', "[ BLACKOUT ]"))
            if self.hdmi_enabled and self.projector_window:
                self.projector_window.blackout()
            if self.web_enabled:
                for lang in self.detected_langs:
                    self.emissor.emetre(str(lang).lower(), "")
        else:
            self.is_blackout = False
            if self.current_index >= 0:
                self.actualitzar_pantalles()

    def update_timers(self):
        if not self.session_started or self.is_paused:
            return

        self.total_elapsed_ms += 100
        self.sub_elapsed_ms += 100
        self.playhead_ms = max(0, self.total_elapsed_ms + self.time_offset_ms)

        fn_time = ms_to_time_str if ms_to_time_str else ms_to_time_str_fallback
        self.lbl_total_time.setText(fn_time(self.total_elapsed_ms))
        self.lbl_sub_elapsed.setText(fn_time(self.sub_elapsed_ms))

        # CÀLCUL RELATIU RESPECTE AL SUBTÍTOL ACTUAL
        if 0 <= self.current_index < len(self.subtitles) - 1:
            sub_actual = self.subtitles[self.current_index]
            sub_seguent = self.subtitles[self.current_index + 1]

            durada_teorica = max(1000, sub_seguent['start_ms'] - sub_actual['start_ms'])
            temps_restant = durada_teorica - self.sub_elapsed_ms

            self.lbl_sub_countdown.setText(fn_time(temps_restant))
            self.lbl_sub_countdown.setStyleSheet("color: #ff5252; background-color: #1a1a1a;" if temps_restant < 0 else "color: #ffca28; background-color: #1a1a1a;")

            # SALT PER TEMPS OBLIGATORI SI ES CONSUMEIX LA FRASE
            if self.auto_sincro_active and not self.forced_auto_mode:
                if self.sub_elapsed_ms >= durada_teorica:
                    self.set_active_sub(self.current_index + 1, recalibrar_temps=True)
                    self.log(self.t('log.sync.auto_time_jump', "⏱️ Salt per temps automàtic a #{idx}", idx=self.current_index + 1))
        else:
            self.lbl_sub_countdown.setText(self.t('lbl.time_end', "FI"))

        # Diagnòstic visual si l'actor porta molta estona sense ser validat
        if self.auto_sincro_active and not self.forced_auto_mode and self.subtitles:
            temps_sense_verif = self.total_elapsed_ms - self.last_sync_verify_ms
            if temps_sense_verif > self.max_desync_grace_ms:
                if not self.is_currently_auto_mode:
                    secs_g = int(self.max_desync_grace_ms / 1000)
                    self.lbl_estat_sincro.setText(self.t('status.auto_active_gt', "🤖 Mode Auto Actiu (Sense verificar > {secs}s)", secs=secs_g))
                    self.lbl_estat_sincro.setStyleSheet("color: #FFA726; font-weight: bold;")

    def next_sub(self):
        if self.current_index < len(self.subtitles) - 1:
            self.set_active_sub(self.current_index + 1, recalibrar_temps=True)

    def prev_sub(self):
        if self.current_index > 0:
            self.set_active_sub(self.current_index - 1, recalibrar_temps=True)

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
        if hasattr(self, 'timer_wlk_check') and self.timer_wlk_check.isActive():
            self.timer_wlk_check.stop()

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
