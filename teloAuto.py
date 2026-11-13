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
    QPushButton, QLabel, QTextEdit, QComboBox, QGroupBox, QMessageBox, 
    QCheckBox, QColorDialog, QProgressBar, QTableWidget, QHeaderView,
    QAbstractItemView, QLineEdit, QDoubleSpinBox, QFileDialog
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
    from utils import ms_to_time_str
except ImportError as e:
    print(f"⚠️ Error en importar mòduls compartits des de src/: {e}")
    ms_to_time_str = None

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
STYLES_CFG = CONFIG_COMMON.get('styles', {})
WHISPER_CFG = CONFIG_AUTO.get('whisper_live', {})
WATCHDOG_CFG = CONFIG_AUTO.get('hallucination_watchdog', {})

INTERFACE_CFG = CONFIG_COMMON.get('interface', {})
APP_LANG = INTERFACE_CFG.get('language', 'ca')

DIR_I18N = os.path.join(BASE_DIR, 'i18n')
i18n = I18nManager(modul="auto", lang=APP_LANG, base_dir=DIR_I18N)
_ = i18n.t

CONFIG_SERVER = CONFIG_COMMON.get('server', {'host': '0.0.0.0', 'port': 5000, 'enabled': True})
if CONFIG_SERVER.get('host') == '00.0.0':
    CONFIG_SERVER['host'] = '0.0.0.0'

CONFIG_SERVER['channels'] = [
    {'id': 'auto_original', 'label': i18n.t('channel.original', 'Transcripció (Original)')},
    {'id': 'auto_translation', 'label': i18n.t('channel.translation', 'Traducció en Directe')}
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


# --- FINESTRA DEL PROJECTOR HDMI (SENSE MARCS + BUFFER HIDPI DE ROTACIÓ) ---
class ProjectorAutoWindow(QMainWindow):
    def __init__(self, app_title="TELÓ", config_proj=None):
        super().__init__()
        self.app_title = app_title
        self.config_proj = config_proj or {}
        
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Window)
        
        self.base_font_family = self.config_proj.get('font_family', 'Arial')
        self.base_font_size = self.config_proj.get('font_size', 38)
        self.max_lines = self.config_proj.get('max_lines', 3)
        self.bg_color = self.config_proj.get('background_color', '#000000')
        
        align_h_cfg = self.config_proj.get('align_h', 'Centrat')
        self.set_align_h(align_h_cfg, redibuixar=False)

        align_v_cfg = self.config_proj.get('align_v', 'Centrat')
        self.set_align_v(align_v_cfg, redibuixar=False)

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

    def actualitzar_textos(self, textos_amb_color):
        self._ultims_textos = textos_amb_color

        while self.layout_proj.count() > 0:
            self.layout_proj.takeAt(0)

        while len(self.labels) < len(textos_amb_color):
            lbl = QLabel()
            lbl.setWordWrap(True)
            self.labels.append(lbl)

        while len(self.labels) > len(textos_amb_color):
            lbl = self.labels.pop()
            lbl.deleteLater()

        num_lignes = len(textos_amb_color)
        if num_lignes >= 3:
            font_size = max(20, int(self.base_font_size * 0.65))
        elif num_lignes == 2:
            font_size = max(24, int(self.base_font_size * 0.82))
        else:
            font_size = self.base_font_size

        font = QFont(self.base_font_family, font_size, QFont.Weight.Bold)
        ample_disponible_px = max(100, self.width() - 140)

        if self.align_v_block in ("center", "bottom"):
            self.layout_proj.addStretch(1)

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
            lbl.setAlignment(self.align_h | Qt.AlignmentFlag.AlignVCenter)
            lbl.setStyleSheet(f"color: {color}; background-color: transparent;")
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
            self.show()


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
        
        def callback_resultat(dades):
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
        
        self.wlk_online = False
        self.timer_wlk_check = QTimer(self)
        self.timer_wlk_check.setInterval(5000)
        self.timer_wlk_check.timeout.connect(self.verificar_servidor_wlk_periodic)
        self.timer_wlk_check.start()
        
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

        self.hdmi_items_data = [
            {'id': 'orig', 'label': self.t('channel.original', 'Transcripció (Original)'), 'color': self.color_primary, 'enabled': True},
            {'id': 'trans', 'label': self.t('channel.translation', 'Traducció en Directe'), 'color': self.color_secondary, 'enabled': True}
        ]

        self.raw_text_orig = ""
        self.raw_text_trans = ""
        
        self.ultim_log_orig = ""
        self.ultim_log_trans = ""

        self.max_lines_hdmi = STYLES_CFG.get('hdmi', {}).get('max_lines', 3)
        self.projector_cfg = STYLES_CFG.get('hdmi', {})
        self.languages_cfg = CONFIG_COMMON.get('languages', {'ca': 'Català', 'es': 'Español', 'fr': 'Français', 'en': 'English'})
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

        self.init_ui()
        if self.hdmi_enabled:
            self.dibuixar_taula_hdmi()
            self.detectar_segona_pantalla()

        self.carregar_dispositius_audio()
        self.verificar_servidor_wlk()
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

    # --- WATCHDOG ANTI-AL·LUCINACIONS ---
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

    def reiniciar_transcripcio(self):
        """Buida els textos en pantalla i els acumuladors sense aturar el rellotge."""
        self.raw_text_orig = ""
        self.raw_text_trans = ""
        self.ultim_log_orig = ""
        self.ultim_log_trans = ""
        self.display_orig.setText(self.t('preview.waiting_audio', "Esperant àudio..."))
        if self.chk_translation.isChecked():
            self.display_trans.setText(self.t('preview.waiting_trans', "Esperant traducció..."))
        if self.hdmi_enabled and self.projector_window and not self.is_blackout:
            self.projector_window.blackout()
        if self.web_enabled:
            self.emissor.emetre("auto_original", "")
            self.emissor.emetre("auto_translation", "")
        self.log(self.t('log.asr.reset_manual', "🧹 Transcripció i buffer reiniciats manualment."))

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
                lang_in = self.combo_lang_in.currentData() or CONFIG_AUTO.get('input_language', 'fr')
                lang_target = self.combo_lang_target.currentData() or self.translation_cfg.get('target_language', 'ca')
                trans_on = self.chk_translation.isChecked()

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
                    "show_info": False
                }
                self.transcriptor = TranscriptorWhisperLiveKit(config=config_trans)
                self.audio_thread = AudioThread(self.transcriptor)
                self.audio_thread.subtitol_generat.connect(self.on_nou_subtitol)
                self.audio_thread.start()
                self.log(self.t('log.watchdog.reconnected', "✅ Connexió WLK restablerta amb buffer net."))
            except Exception as e:
                self.log(self.t('log.watchdog.reconnect_error', "⚠️ Error reconnectant WLK: {error}", error=e))
            finally:
                self._reconnecting_asr = False

        QTimer.singleShot(regles['delay_ms'], _reobrir)

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
            self.log(self.t('log.engine.create_error', "⚠️ Alerta en inicialitzar el transcriptor: {error}", error=e))

    def verificar_servidor_wlk(self):
        """Comprovació i arrencada inicial en obrir l'aplicació."""
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
        """Es crida cada 5 segons automàticament."""
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

        dev_cfg = self.audio_cfg.get('device')
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
        self.setWindowTitle(self.t('window.title', '{title} 🤖 - Control Automàtic (ASR)', title=self.app_title))
        self.resize(1220, 980)
        
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
            QDoubleSpinBox {{ background-color: {self.color_btn_bg}; border: 1px solid {self.color_btn_border}; color: {self.color_text}; padding: 3px; border-radius: 4px; }}
            QLineEdit {{ background-color: {self.color_btn_bg}; border: 1px solid {self.color_btn_border}; color: {self.color_text}; padding: 4px; border-radius: 4px; }}
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
        self.title_label = QLabel(f"{self.app_title} 🤖")
        self.title_label.setFont(QFont("Arial", 16, QFont.Weight.Bold))
        self.title_label.setStyleSheet(f"color: {self.color_primary};")
        header_layout.addWidget(self.title_label)

        self.btn_save_cfg = QPushButton(self.t('btn.save_config', '💾 Desar Config'))
        self.btn_save_cfg.setStyleSheet("background-color: #004D40; color: white;")
        self.btn_save_cfg.clicked.connect(self.guardar_configuracio_fitxer)
        header_layout.addWidget(self.btn_save_cfg)

        self.btn_load_cfg = QPushButton(self.t('btn.load_config', '📂 Carregar Config'))
        self.btn_load_cfg.setStyleSheet("background-color: #37474F; color: white;")
        self.btn_load_cfg.clicked.connect(self.carregar_configuracio_fitxer)
        header_layout.addWidget(self.btn_load_cfg)

        if self.hdmi_enabled:
            self.btn_toggle_proj = QPushButton(self.t('btn.toggle_projector', '🖥️ Projector HDMI'))
            self.btn_toggle_proj.clicked.connect(self.toggle_projector_window)
            header_layout.addWidget(self.btn_toggle_proj)

        header_layout.addStretch()

        if self.web_enabled:
            ip = self.emissor.obtenir_ip_local() if hasattr(self.emissor, 'obtenir_ip_local') else "127.0.0.1"
            port = self.emissor.port if hasattr(self.emissor, 'port') else 5000
            url_auto = f"http://{ip}:{port}"
            self.ip_label = QLabel(f"{self.t('lbl.web_server', '🌐 Web Server:')} <a href=\"{url_auto}\" style=\"color: {self.color_primary};\">{url_auto}</a>")
            self.ip_label.setOpenExternalLinks(True)
        else:
            self.ip_label = QLabel(self.t('lbl.web_server_disabled', '🌐 Web Server: <span style="color: #7f8c8d;">Desactivat</span>'))

        self.ip_label.setFont(QFont("Arial", 10, QFont.Weight.Bold))
        header_layout.addWidget(self.ip_label)

        main_layout.addLayout(header_layout)

        # 2. PANELL D'AJUSTOS EN DIRECTE HDMI
        if self.hdmi_enabled:
            main_layout.addWidget(self.crear_panel_ajustos_estil())

        # 3. PANELL D'ENTRADA D'ÀUDIO I VÚMETRE
        self.audio_box = QGroupBox(self.t('group.audio', "🎙️ Control d'Entrada d'Àudio"))
        audio_layout = QHBoxLayout(self.audio_box)
        audio_layout.setContentsMargins(12, 18, 12, 12)

        self.lbl_audio_dev = QLabel(self.t('lbl.audio_device', "Dispositiu:"))
        audio_layout.addWidget(self.lbl_audio_dev)
        self.combo_audio_device = QComboBox()
        self.combo_audio_device.currentIndexChanged.connect(self.canviar_dispositiu_mic)
        audio_layout.addWidget(self.combo_audio_device, 3)

        audio_layout.addSpacing(15)

        self.chk_activar_vu = QCheckBox(self.t('chk.activate_vu', "Activar Vúmetre"))
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

        main_layout.addWidget(self.audio_box)

        # 4. CONFIGURACIÓ D'IDIOMES I HDMI
        nom_grup_asr = self.t('group.asr_config', "Configuració ASR, Traducció i Control de Sortida HDMI") if self.hdmi_enabled else self.t('group.asr_config_no_hdmi', "Configuració ASR i Traducció")
        self.config_box = QGroupBox(nom_grup_asr)
        config_layout = QHBoxLayout(self.config_box)
        config_layout.setContentsMargins(12, 18, 12, 12)

        col_asr = QVBoxLayout()
        self.lbl_lang_in = QLabel(self.t('lbl.lang_in', "Idioma Entrada (ASR):"))
        col_asr.addWidget(self.lbl_lang_in)
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
        self.chk_translation = QCheckBox(self.t('chk.translation', "Activar Traducció"))
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

        self.btn_aplicar_idiomes = QPushButton(self.t('btn.apply_langs', "⚡ Aplicar Idiomes"))
        self.btn_aplicar_idiomes.setStyleSheet("background-color: #0277BD; color: white;")
        self.btn_aplicar_idiomes.clicked.connect(self.aplicar_idiomes_manual)
        col_asr.addWidget(self.btn_aplicar_idiomes)

        self.btn_reset_transcripcio = QPushButton(self.t('btn.reset_asr', "🧹 Netejar Transcripció"))
        self.btn_reset_transcripcio.setStyleSheet("background-color: #37474F; color: #ECEFF1; font-weight: bold;")
        self.btn_reset_transcripcio.clicked.connect(self.reiniciar_transcripcio)
        col_asr.addWidget(self.btn_reset_transcripcio)

        col_asr.addStretch()
        config_layout.addLayout(col_asr, 2)
        config_layout.addSpacing(20)

        if self.hdmi_enabled:
            col_hdmi = QVBoxLayout()
            self.lbl_hdmi_output = QLabel(self.t('lbl.hdmi_output', "Sortida Projector HDMI:"))
            col_hdmi.addWidget(self.lbl_hdmi_output)

            hdmi_table_layout = QHBoxLayout()
            self.table_hdmi = QTableWidget()
            self.table_hdmi.setColumnCount(3)
            self.table_hdmi.setHorizontalHeaderLabels([
                self.t('table.hdmi.header.channel', 'Actiu i Canal'),
                self.t('table.hdmi.header.sample', 'Mostra'),
                self.t('table.hdmi.header.action', 'Acció')
            ])
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
            self.btn_move_up = QPushButton(self.t('btn.move_up', "⬆️ Pujar"))
            self.btn_move_up.clicked.connect(self.moure_canal_amunt)
            btn_order_box.addWidget(self.btn_move_up)

            self.btn_move_down = QPushButton(self.t('btn.move_down', "⬇️ Baixar"))
            self.btn_move_down.clicked.connect(self.moure_canal_avall)
            btn_order_box.addWidget(self.btn_move_down)
            btn_order_box.addStretch()

            hdmi_table_layout.addLayout(btn_order_box, 1)
            col_hdmi.addLayout(hdmi_table_layout)

            config_layout.addLayout(col_hdmi, 4)

        main_layout.addWidget(self.config_box)

        # 5. PREVISUALITZACIÓ
        self.display_layout = QHBoxLayout()

        self.box_orig_widget = QWidget()
        box_orig = QVBoxLayout(self.box_orig_widget)
        box_orig.setContentsMargins(0, 0, 0, 0)
        self.lbl_title_orig = QLabel(self.t('preview.title_orig', "TRANSCRIPCIÓ (ORIGINAL)"))
        self.lbl_title_orig.setStyleSheet(f"color: {self.color_primary}; font-weight: bold;")
        self.display_orig = QLabel(self.t('preview.waiting_audio', "Esperant àudio..."))
        self.display_orig.setFont(QFont("Arial", 13, QFont.Weight.Bold))
        self.display_orig.setWordWrap(True)
        self.display_orig.setStyleSheet(f"background-color: {self.color_panel_bg}; border: 2px solid {self.color_primary}; border-radius: 8px; padding: 10px; min-height: 60px;")
        box_orig.addWidget(self.lbl_title_orig)
        box_orig.addWidget(self.display_orig)

        self.box_trans_widget = QWidget()
        box_trans = QVBoxLayout(self.box_trans_widget)
        box_trans.setContentsMargins(0, 0, 0, 0)
        self.lbl_title_trans = QLabel(self.t('preview.title_trans', "TRADUCCIÓ (TARGET)"))
        self.lbl_title_trans.setStyleSheet(f"color: {self.color_secondary}; font-weight: bold;")
        self.display_trans = QLabel(self.t('preview.waiting_trans', "Esperant traducció..."))
        self.display_trans.setFont(QFont("Arial", 13, QFont.Weight.Bold))
        self.display_trans.setWordWrap(True)
        self.display_trans.setStyleSheet(f"background-color: {self.color_panel_bg}; border: 2px solid {self.color_secondary}; border-radius: 8px; padding: 10px; min-height: 60px;")
        box_trans.addWidget(self.lbl_title_trans)
        box_trans.addWidget(self.display_trans)

        self.display_layout.addWidget(self.box_orig_widget)
        self.display_layout.addWidget(self.box_trans_widget)
        main_layout.addLayout(self.display_layout)

        # 6. CONTROLS DE SESSIÓ
        self.timer_box = QGroupBox(self.t('group.session', "Control Temporal"))
        timer_layout = QHBoxLayout(self.timer_box)

        self.btn_start_stop = QPushButton(self.t('btn.start', "▶️ Start"))
        self.btn_start_stop.setStyleSheet("background-color: #1b5e20; color: white;")
        self.btn_start_stop.clicked.connect(self.on_click_start_stop)
        timer_layout.addWidget(self.btn_start_stop)

        self.btn_pause = QPushButton(self.t('btn.pause', "⏸️ Pause"))
        self.btn_pause.setEnabled(False)
        self.btn_pause.clicked.connect(self.toggle_pause)
        timer_layout.addWidget(self.btn_pause)

        self.btn_blackout = QPushButton(self.t('btn.blackout', "⬛ Blackout (B)"))
        self.btn_blackout.setStyleSheet("background-color: #C62828; color: white;")
        self.btn_blackout.clicked.connect(self.toggle_blackout)
        timer_layout.addWidget(self.btn_blackout)

        timer_layout.addSpacing(20)

        self.lbl_title_total = QLabel(self.t('lbl.total_time', "Temps Total:"))
        timer_layout.addWidget(self.lbl_title_total)
        self.lbl_total_time = QLabel("00:00")
        self.lbl_total_time.setFont(QFont("Arial", 12, QFont.Weight.Bold))
        self.lbl_total_time.setStyleSheet(f"color: {self.color_primary}; background-color: #1a1a1a; padding: 4px 8px; border-radius: 4px;")
        timer_layout.addWidget(self.lbl_total_time)

        timer_layout.addStretch()
        main_layout.addWidget(self.timer_box)

        # 7. LOG
        self.log_box = QGroupBox(self.t('group.log', "Historial de Transcripció i Traducció"))
        log_layout = QVBoxLayout(self.log_box)
        self.log_edit = QTextEdit()
        self.log_edit.setReadOnly(True)
        log_layout.addWidget(self.log_edit)
        main_layout.addWidget(self.log_box)

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

            btn_col = QPushButton(self.t('btn.color', "🎨 Color"))
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
        self.group_hdmi_box = QGroupBox(self.t('group.hdmi_styles', "🖥️ Control Global d'Estils del Projector HDMI (En Directe)"))
        layout = QHBoxLayout(self.group_hdmi_box)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(8)

        self.lbl_font = QLabel(self.t('lbl.font', 'Font:'))
        layout.addWidget(self.lbl_font)
        self.combo_font = QComboBox()
        self.combo_font.addItems(["Arial", "Courier New", "Verdana", "Times New Roman", "Trebuchet MS"])
        self.combo_font.setCurrentText(self.projector_cfg.get('font_family', 'Arial'))
        self.combo_font.currentTextChanged.connect(self.actualitzar_font_projector)
        layout.addWidget(self.combo_font)

        self.lbl_size = QLabel(self.t('lbl.size', 'Mida:'))
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

        self.lbl_h = QLabel(self.t('lbl.align_h', 'H:'))
        layout.addWidget(self.lbl_h)
        self.combo_align_h = QComboBox()
        self.actualitzar_items_align_h()
        self.combo_align_h.currentTextChanged.connect(self.canviar_alineacio_h_projector)
        layout.addWidget(self.combo_align_h)

        self.lbl_v = QLabel(self.t('lbl.align_v', 'V:'))
        layout.addWidget(self.lbl_v)
        self.combo_align_v = QComboBox()
        self.actualitzar_items_align_v()
        self.combo_align_v.currentTextChanged.connect(self.canviar_alineacio_v_projector)
        layout.addWidget(self.combo_align_v)

        self.btn_bg_color = QPushButton(self.t('btn.bg_color', '⬛ Fons'))
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
            self.refrescar_projector()

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
                self.refrescar_projector()
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
            'active_channels': self.hdmi_items_data
        }
        auto_actual = {
            'input_language': self.combo_lang_in.currentData() or 'fr',
            'translation': {
                'enabled': self.chk_translation.isChecked(),
                'target_language': self.combo_lang_target.currentData() or 'ca'
            },
            'audio': {
                'device': self.combo_audio_device.currentData()
            }
        }
        return {
            'hdmi': hdmi_actual,
            'auto': auto_actual,
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
        
        auto_dict = dades.setdefault('auto', {})
        auto_dict['input_language'] = estat['auto']['input_language']
        auto_dict.setdefault('translation', {})['enabled'] = estat['auto']['translation']['enabled']
        auto_dict.setdefault('translation', {})['target_language'] = estat['auto']['translation']['target_language']
        auto_dict.setdefault('audio', {})['device'] = estat['auto']['audio']['device']

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
        
        # 1. Idioma d'interfície en calent
        nou_idioma = cfg.get('common', {}).get('interface', {}).get('language')
        if nou_idioma and str(nou_idioma).lower().strip() != getattr(self, 'app_lang', None):
            self.app_lang = str(nou_idioma).lower().strip()
            i18n = I18nManager(modul="auto", lang=self.app_lang, base_dir=DIR_I18N)
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

        if 'active_channels' in hdmi and hdmi['active_channels']:
            self.hdmi_items_data = hdmi['active_channels']
            self.dibuixar_taula_hdmi()

        # 3. Ajustos ASR i Traducció
        auto_cfg = cfg.get('auto', {})
        if 'input_language' in auto_cfg and hasattr(self, 'combo_lang_in'):
            idx = self.combo_lang_in.findData(auto_cfg['input_language'])
            if idx >= 0:
                self.combo_lang_in.setCurrentIndex(idx)

        trans_cfg = auto_cfg.get('translation', {})
        if 'enabled' in trans_cfg and hasattr(self, 'chk_translation'):
            self.chk_translation.setChecked(bool(trans_cfg['enabled']))

        if 'target_language' in trans_cfg and hasattr(self, 'combo_lang_target'):
            idx = self.combo_lang_target.findData(trans_cfg['target_language'])
            if idx >= 0:
                self.combo_lang_target.setCurrentIndex(idx)

        audio_cfg = auto_cfg.get('audio', {})
        if 'device' in audio_cfg and hasattr(self, 'combo_audio_device'):
            idx = self.combo_audio_device.findData(audio_cfg['device'])
            if idx >= 0:
                self.combo_audio_device.setCurrentIndex(idx)

        self.refrescar_projector()

    def retranslate_ui(self):
        self.setWindowTitle(self.t('window.title', '{title} 🤖 - Control Automàtic (ASR)', title=self.app_title))
        if hasattr(self, 'projector_window') and self.projector_window:
            self.projector_window.setWindowTitle(self.t('projector.title', '{title} - Projector HDMI', title=self.app_title))

        if hasattr(self, 'btn_save_cfg'): self.btn_save_cfg.setText(self.t('btn.save_config', '💾 Desar Config'))
        if hasattr(self, 'btn_load_cfg'): self.btn_load_cfg.setText(self.t('btn.load_config', '📂 Carregar Config'))
        if hasattr(self, 'btn_toggle_proj'): self.btn_toggle_proj.setText(self.t('btn.toggle_projector', '🖥️ Projector HDMI'))

        if hasattr(self, 'group_hdmi_box'):
            self.group_hdmi_box.setTitle(self.t('group.hdmi_styles', "🖥️ Control Global d'Estils del Projector HDMI (En Directe)"))
        if hasattr(self, 'lbl_font'): self.lbl_font.setText(self.t('lbl.font', 'Font:'))
        if hasattr(self, 'lbl_size'): self.lbl_size.setText(self.t('lbl.size', 'Mida:'))
        if hasattr(self, 'lbl_rot'): self.lbl_rot.setText(self.t('lbl.rotation', 'Rotació:'))
        if hasattr(self, 'lbl_h'): self.lbl_h.setText(self.t('lbl.align_h', 'H:'))
        if hasattr(self, 'lbl_v'): self.lbl_v.setText(self.t('lbl.align_v', 'V:'))
        if hasattr(self, 'btn_bg_color'): self.btn_bg_color.setText(self.t('btn.bg_color', '⬛ Fons'))

        if hasattr(self, 'combo_align_h'): self.actualitzar_items_align_h()
        if hasattr(self, 'combo_align_v'): self.actualitzar_items_align_v()

        if hasattr(self, 'audio_box'): self.audio_box.setTitle(self.t('group.audio', "🎙️ Control d'Entrada d'Àudio"))
        if hasattr(self, 'lbl_audio_dev'): self.lbl_audio_dev.setText(self.t('lbl.audio_device', "Dispositiu:"))
        if hasattr(self, 'chk_activar_vu'): self.chk_activar_vu.setText(self.t('chk.activate_vu', "Activar Vúmetre"))

        if hasattr(self, 'config_box'):
            nom_grup_asr = self.t('group.asr_config', "Configuració ASR, Traducció i Control de Sortida HDMI") if self.hdmi_enabled else self.t('group.asr_config_no_hdmi', "Configuració ASR i Traducció")
            self.config_box.setTitle(nom_grup_asr)
        if hasattr(self, 'lbl_lang_in'): self.lbl_lang_in.setText(self.t('lbl.lang_in', "Idioma Entrada (ASR):"))
        if hasattr(self, 'chk_translation'): self.chk_translation.setText(self.t('chk.translation', "Activar Traducció"))
        if hasattr(self, 'btn_aplicar_idiomes'): self.btn_aplicar_idiomes.setText(self.t('btn.apply_langs', "⚡ Aplicar Idiomes"))
        if hasattr(self, 'btn_reset_transcripcio'): self.btn_reset_transcripcio.setText(self.t('btn.reset_asr', "🧹 Netejar Transcripció"))
        if hasattr(self, 'lbl_hdmi_output'): self.lbl_hdmi_output.setText(self.t('lbl.hdmi_output', "Sortida Projector HDMI:"))

        if hasattr(self, 'table_hdmi'):
            self.table_hdmi.setHorizontalHeaderLabels([
                self.t('table.hdmi.header.channel', 'Actiu i Canal'),
                self.t('table.hdmi.header.sample', 'Mostra'),
                self.t('table.hdmi.header.action', 'Acció')
            ])

        if hasattr(self, 'lbl_title_orig'): self.lbl_title_orig.setText(self.t('preview.title_orig', "TRANSCRIPCIÓ (ORIGINAL)"))
        if hasattr(self, 'lbl_title_trans'): self.lbl_title_trans.setText(self.t('preview.title_trans', "TRADUCCIÓ (TARGET)"))

        if hasattr(self, 'timer_box'): self.timer_box.setTitle(self.t('group.session', "Control Temporal"))
        if hasattr(self, 'btn_start_stop'):
            self.btn_start_stop.setText(self.t('btn.stop', "⏹️ Stop") if self.session_started else self.t('btn.start', "▶️ Start"))
        if hasattr(self, 'btn_pause'):
            self.btn_pause.setText(self.t('btn.resume', "▶️ Reprendre") if self.is_paused else self.t('btn.pause', "⏸️ Pause"))
        if hasattr(self, 'btn_blackout'): self.btn_blackout.setText(self.t('btn.blackout', "⬛ Blackout (B)"))
        if hasattr(self, 'lbl_title_total'): self.lbl_title_total.setText(self.t('lbl.total_time', "Temps Total:"))

        if hasattr(self, 'log_box'): self.log_box.setTitle(self.t('group.log', "Historial de Transcripció i Traducció"))

    def aplicar_idiomes_manual(self):
        lang_in = self.combo_lang_in.currentData() or CONFIG_AUTO.get('input_language', 'fr')
        lang_target = self.combo_lang_target.currentData() or self.translation_cfg.get('target_language', 'ca')
        trans_on = self.chk_translation.isChecked()

        self.log(self.t('log.asr.applying_langs', "⚙️ Sol·licitant canvi: Entrada={lang_in}, Traducció={lang_target} (Activa: {trans_on})", lang_in=lang_in, lang_target=lang_target, trans_on=trans_on))

        self.raw_text_orig = ""
        self.raw_text_trans = ""
        self.ultim_log_orig = ""
        self.ultim_log_trans = ""
        self.display_orig.setText(self.t('preview.connecting_asr', "[ Connectant ASR a {lang}... ]", lang=lang_in))
        if trans_on:
            self.display_trans.setText(self.t('preview.connecting_trans', "[ Connectant Traducció a {lang}... ]", lang=lang_target))

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
            self.log(self.t('log.asr.reconnected_pair', "✅ Reconnectat a {lang_in} -> {lang_target}.", lang_in=lang_in, lang_target=lang_target))
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
            self.log(self.t('log.wlk.retry', "⚠️ El servidor WLK no respon. Intentant reactivar-lo..."))
            self.verificar_servidor_wlk()

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
            QMessageBox.critical(
                self, 
                self.t('dialog.audio_error.title', "Error d'Àudio"), 
                self.t('dialog.audio_error.msg', "No s'ha pogut iniciar l'àudio: {error}", error=e)
            )
            return

        self.is_blackout = False
        self.session_started = True
        self.is_paused = False
        self.total_elapsed_ms = 0
        self.timer.start()

        self.audio_thread = AudioThread(self.transcriptor)
        self.audio_thread.subtitol_generat.connect(self.on_nou_subtitol)
        self.audio_thread.start()

        if self.chk_activar_vu.isChecked():
            self.toggle_vumetre(True)

        self.btn_start_stop.setText(self.t('btn.stop', "⏹️ Stop"))
        self.btn_start_stop.setStyleSheet("background-color: #C62828; color: white;")
        self.btn_pause.setEnabled(True)

        self.display_orig.setText(self.t('preview.listening', "[ Escoltant en {lang}... ]", lang=lang_in))
        if trans_on:
            self.display_trans.setText(self.t('preview.translating', "[ Traduint a {lang}... ]", lang=lang_target))
        self.log(self.t('log.session.started', "🚀 Sessió iniciada: Entrada={lang_in}, Traducció={lang_target} (Activa: {trans_on})", lang_in=lang_in, lang_target=lang_target, trans_on=trans_on))

    def stop_session(self):
        self.session_started = False
        self.is_paused = False
        self.is_blackout = False
        self.timer.stop()

        if self.monitor_thread:
            self.monitor_thread.stop()
            self.monitor_thread = None
            self.vu_meter.setValue(0)

        if self.audio_thread:
            self.audio_thread.stop()
            self.audio_thread = None

        self.lbl_total_time.setText("00:00")
        self.btn_start_stop.setText(self.t('btn.start', "▶️ Start"))
        self.btn_start_stop.setStyleSheet("background-color: #1b5e20; color: white;")
        self.btn_pause.setEnabled(False)

    def toggle_pause(self):
        self.is_paused = not self.is_paused
        self.btn_pause.setText(self.t('btn.resume', "▶️ Reprendre") if self.is_paused else self.t('btn.pause', "⏸️ Pause"))

    def toggle_blackout(self):
        if not self.is_blackout:
            self.is_blackout = True
            self.display_orig.setText(self.t('preview.blackout', "[ BLACKOUT ]"))
            self.display_trans.setText(self.t('preview.blackout', "[ BLACKOUT ]"))
            
            if self.hdmi_enabled and self.projector_window:
                self.projector_window.blackout()

            if self.web_enabled:
                self.emissor.emetre("auto_original", "")
                self.emissor.emetre("auto_translation", "")
        else:
            self.is_blackout = False
            self.display_orig.setText(self.t('preview.listening_generic', "[ Escoltant... ]"))
            if self.chk_translation.isChecked():
                self.display_trans.setText(self.t('preview.translating_generic', "[ Generant traducció... ]"))
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

        if getattr(self, '_reconnecting_asr', False):
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

        # --- WATCHDOG ANTI-AL·LUCINACIONS ---
        if text_orig and self._es_alucinacio(text_orig):
            self.reconectar_transcriptor_en_calent(motiu_text=text_orig)
            return

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
