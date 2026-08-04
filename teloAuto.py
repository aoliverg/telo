import sys
import os
import time
import numpy as np
import yaml
import speech_recognition as sr

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, 
    QPushButton, QLabel, QTextEdit, QComboBox, QGroupBox, QMessageBox,
    QDialog, QProgressBar
)
from PyQt6.QtCore import Qt, QTimer, QThread, pyqtSignal
from PyQt6.QtGui import QFont, QTextCursor

# Directoris del projecte
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SRC_DIR = os.path.join(BASE_DIR, 'src')
CONFIG_YAML_PATH = os.path.join(BASE_DIR, 'configTeloAuto.yaml')

if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

# Importació de mòduls
try:
    from emissor import EmissorWeb
    from transcriptorWhisper import TranscriptorWhisper
except ImportError as e:
    print(f"⚠️ Alerta en importar mòduls des de src/: {e}")

CONFIG_SERVER = {
    'host': '0.0.0.0',
    'port': 5000,
    'channels': [{'id': 'auto', 'label': 'Transcripció en Directe'}]
}

def carregar_configuracio_yaml():
    """Carrega la configuració del YAML o la crea per defecte si no existeix"""
    config_defecte = {
        'whisper': {
            'engine_type': 'ctranslate2',
            'model': './whisper-large-v3-ct2',
            'language': 'ca',
            'device': 'cuda',
            'energy_threshold': 800,
            'input_device_index': None
        },
        'languages': {
            'ca': 'Català',
            'es': 'Español',
            'fr': 'Français',
            'en': 'English',
            'it': 'Italiano',
            'de': 'Deutsch'
        }
    }
    
    if os.path.exists(CONFIG_YAML_PATH):
        try:
            with open(CONFIG_YAML_PATH, 'r', encoding='utf-8') as f:
                config = yaml.safe_load(f)
                print(f"[*] Configuració carregada des de {CONFIG_YAML_PATH}")
                return config
        except Exception as e:
            print(f"⚠️ Error llegint {CONFIG_YAML_PATH}: {e}. Utilitzant valors per defecte.")
            return config_defecte
    else:
        try:
            with open(CONFIG_YAML_PATH, 'w', encoding='utf-8') as f:
                yaml.dump(config_defecte, f, default_flow_style=False, allow_unicode=True)
                print(f"[*] Creat nou fitxer de configuració: {CONFIG_YAML_PATH}")
        except Exception as e:
            print(f"⚠️ No s'ha pogut crear {CONFIG_YAML_PATH}: {e}")
        return config_defecte


def ms_to_time_str(ms):
    seconds = int(ms // 1000)
    minutes = int(seconds // 60)
    hours = int(minutes // 60)
    sec_rem = seconds % 60
    min_rem = minutes % 60
    
    if hours > 0:
        return f"{hours:02d}:{min_rem:02d}:{sec_rem:02d}"
    else:
        return f"{min_rem:02d}:{sec_rem:02d}"


def obtenir_microfons_filtrats():
    dispositius_valids = []
    vistos = set()
    
    try:
        mics = sr.Microphone.list_microphone_names()
        for idx, name in enumerate(mics):
            nom_net = name.strip()
            ignorar = ['sysdefault', 'dmix', 'surround', 'iec958', 'spdif', 'front', 'rear', 'center_lfe']
            if any(p in nom_net.lower() for p in ignorar):
                continue
            if nom_net not in vistos:
                vistos.add(nom_net)
                dispositius_valids.append((idx, nom_net))
    except Exception as e:
        print(f"Error llistant micròfons: {e}")
        
    return dispositius_valids


# --- FIL PER MESURAR EL NIVELL DE SENYAL D'ÀUDIO EN CONTINU ---
class AudioLevelThread(QThread):
    level_signal = pyqtSignal(int)

    def __init__(self, device_index=None):
        super().__init__()
        self.device_index = device_index
        self.running = False

    def set_device(self, device_index):
        was_running = self.running
        if self.running:
            self.stop()
        self.device_index = device_index
        if was_running:
            self.start()

    def run(self):
        self.running = True
        recognizer = sr.Recognizer()
        
        try:
            microphone = sr.Microphone(sample_rate=16000, device_index=self.device_index)
            with microphone as source:
                while self.running:
                    audio = recognizer.record(source, duration=0.1)
                    raw_data = audio.get_raw_data(convert_rate=16000, convert_width=2)
                    
                    if raw_data:
                        samples = np.frombuffer(raw_data, dtype=np.int16).astype(np.float32)
                        rms = np.sqrt(np.mean(samples**2))
                        level = int(min(100, (rms / 2500.0) * 100))
                        self.level_signal.emit(level)
        except Exception:
            self.level_signal.emit(0)

    def stop(self):
        self.running = False
        self.wait()


# --- DIÀLEG SELECTOR DE MICRÒFON ---
class MicSelectorDialog(QDialog):
    def __init__(self, current_device_index=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("🎙️ Selecció de Micròfon d'Entrada")
        self.resize(450, 160)
        self.selected_index = current_device_index

        self.setStyleSheet("""
            QDialog { background-color: #1A1A1A; color: #E0E0E0; }
            QLabel { font-size: 10pt; font-weight: bold; color: #00E676; }
            QComboBox { background-color: #252525; color: white; border: 1px solid #444; padding: 6px; border-radius: 4px; }
            QPushButton { background-color: #2E7D32; color: white; border: none; padding: 8px 16px; font-weight: bold; border-radius: 4px; }
            QPushButton:hover { background-color: #388E3C; }
        """)

        layout = QVBoxLayout(self)
        layout.setSpacing(15)

        layout.addWidget(QLabel("Selecciona la font d'àudio activa:"))

        self.combo_mics = QComboBox()
        mics_filtrats = obtenir_microfons_filtrats()
        
        if mics_filtrats:
            for idx, name in mics_filtrats:
                self.combo_mics.addItem(f"[{idx}] {name}", idx)
        else:
            self.combo_mics.addItem("Default (Micròfon del Sistema)", None)

        if self.selected_index is not None:
            idx_found = self.combo_mics.findData(self.selected_index)
            if idx_found >= 0:
                self.combo_mics.setCurrentIndex(idx_found)

        self.combo_mics.currentIndexChanged.connect(self.on_mic_changed)
        layout.addWidget(self.combo_mics)

        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        btn_ok = QPushButton("Acceptar")
        btn_ok.clicked.connect(self.accept)
        btn_layout.addWidget(btn_ok)

        layout.addLayout(btn_layout)

    def on_mic_changed(self):
        self.selected_index = self.combo_mics.currentData()


# --- FIL PER A LA CAPTURA I TRANSCRIPCIÓ WHISPER ---
class AudioThread(QThread):
    subtitol_generat = pyqtSignal(str)
    estat_canviat = pyqtSignal(str)

    def __init__(self, transcriptor, idioma='ca'):
        super().__init__()
        self.transcriptor = transcriptor
        self.idioma = idioma
        self.running = False
        self.stop_listen_fn = None

    def set_idioma(self, idioma):
        self.idioma = idioma

    def run(self):
        self.running = True
        self.estat_canviat.emit(f"Escoltant en [{self.idioma.upper()}]...")
        
        def audio_callback(recognizer, audio):
            if not self.running:
                return
            try:
                raw_data = audio.get_raw_data(convert_rate=16000, convert_width=2)
                transcripcio, _ = self.transcriptor.processar_amb_alternatives(raw_data, idioma=self.idioma)
                clean_text = transcripcio.strip()
                if clean_text:
                    self.subtitol_generat.emit(clean_text)
            except Exception as e:
                print(f"Error processant àudio: {e}")

        self.stop_listen_fn = self.transcriptor.escoltar_continu(audio_callback)
        
        while self.running:
            time.sleep(0.1)

    def stop(self):
        self.running = False
        if self.stop_listen_fn:
            try:
                self.stop_listen_fn(wait_for_stop=False)
            except Exception:
                pass
        self.estat_canviat.emit("Aturat")


# --- FINESTRA PROJECTOR HDMI ---
class WindowProjector(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("TELÓ AUTO - Projector HDMI")
        self.setStyleSheet("background-color: black;")
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(40, 40, 40, 40)

        self.lbl_text = QLabel("")
        self.lbl_text.setFont(QFont("Arial", 38, QFont.Weight.Bold))
        self.lbl_text.setStyleSheet("color: white;")
        self.lbl_text.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_text.setWordWrap(True)

        layout.addWidget(self.lbl_text)

    def actualitzar_text(self, text):
        self.lbl_text.setText(text)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.showNormal()
        else:
            super().keyPressEvent(event)


# --- FINESTRA PRINCIPAL CONTROL AUTOMÀTIC ---
class TeloAutoPyQt(QMainWindow):
    def __init__(self, emissor):
        super().__init__()
        self.emissor = emissor
        self.session_started = False
        self.is_paused = False
        
        # 1. Carregar configuració del YAML
        self.config_yaml = carregar_configuracio_yaml()
        whisper_cfg = self.config_yaml.get('whisper', {})
        self.languages_cfg = self.config_yaml.get('languages', {'ca': 'Català'})
        
        self.current_mic_index = whisper_cfg.get('input_device_index', None)
        model_path = whisper_cfg.get('model', './whisper-large-v3-ct2')

        if not os.path.isabs(model_path):
            model_path = os.path.abspath(os.path.join(BASE_DIR, model_path))

        self.total_elapsed_ms = 0
        self.sub_elapsed_ms = 0

        self.timer = QTimer(self)
        self.timer.setInterval(100)
        self.timer.timeout.connect(self.update_timers)

        # 2. Inicialitzar TranscriptorWhisper
        print(f"[*] Carregant model Whisper des de: {model_path}")
        self.transcriptor = TranscriptorWhisper(model=model_path, config=self.config_yaml)
        self.audio_thread = None

        self.projector_window = WindowProjector()

        self.init_ui()
        self.detectar_segona_pantalla()

        # 3. Seleccionar idioma per defecte del YAML
        lang_default = whisper_cfg.get('language', 'ca')
        idx_lang = self.combo_lang.findData(lang_default)
        if idx_lang >= 0:
            self.combo_lang.setCurrentIndex(idx_lang)

        # 4. Iniciar Vúmetre
        self.vu_thread = AudioLevelThread(device_index=self.current_mic_index)
        self.vu_thread.level_signal.connect(self.actualitzar_vumetre_principal)
        self.vu_thread.start()

    def init_ui(self):
        self.setWindowTitle("TELÓ 🤖 - Control Automàtic amb Whisper IA")
        self.resize(1150, 820)
        
        self.setStyleSheet("""
            QMainWindow { background-color: #121212; }
            QWidget { background-color: #121212; color: #E0E0E0; font-family: 'Segoe UI', Arial; font-size: 10pt; }
            
            QGroupBox { 
                border: 1px solid #333333; 
                border-radius: 6px; 
                margin-top: 16px; 
                font-weight: bold;
                font-size: 10pt;
            }
            QGroupBox::title { 
                subcontrol-origin: margin; 
                subcontrol-position: top left;
                left: 12px; 
                top: 0px;
                padding: 0 5px; 
                color: #00E676; 
                background-color: #121212;
            }

            QPushButton { 
                background-color: #252525; 
                border: 1px solid #444; 
                border-radius: 4px; 
                padding: 6px 14px; 
                font-weight: bold; 
                min-height: 20px;
            }
            QPushButton:hover { background-color: #333333; }
            QPushButton:disabled { background-color: #1a1a1a; color: #555555; border: 1px solid #222; }

            QTextEdit { background-color: #1E1E1E; border: 1px solid #333; color: #E0E0E0; font-family: monospace; }
            QComboBox { background-color: #252525; border: 1px solid #444; padding: 4px 8px; border-radius: 4px; }
            
            QProgressBar { border: 1px solid #333; border-radius: 3px; background-color: #1a1a1a; }
            QProgressBar::chunk { background-color: #00E676; }
        """)

        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        main_layout.setSpacing(10)
        main_layout.setContentsMargins(15, 15, 15, 15)

        # 1. HEADER
        header_layout = QHBoxLayout()
        
        title_label = QLabel("TELÓ AUTO 🤖")
        title_label.setFont(QFont("Arial", 16, QFont.Weight.Bold))
        title_label.setStyleSheet("color: #00E676;")
        header_layout.addWidget(title_label)

        btn_select_mic = QPushButton("🎙️ Micròfon")
        btn_select_mic.clicked.connect(self.obrir_selector_mic)
        header_layout.addWidget(btn_select_mic)

        header_layout.addWidget(QLabel("Senyal:"))
        self.main_vu_meter = QProgressBar()
        self.main_vu_meter.setRange(0, 100)
        self.main_vu_meter.setValue(0)
        self.main_vu_meter.setFixedWidth(110)
        self.main_vu_meter.setFixedHeight(18)
        self.main_vu_meter.setTextVisible(False)
        header_layout.addWidget(self.main_vu_meter)

        header_layout.addSpacing(10)

        btn_calibrate = QPushButton("🎚️ Calibrar Soroll")
        btn_calibrate.clicked.connect(self.calibrar_mic)
        header_layout.addWidget(btn_calibrate)

        btn_toggle_proj = QPushButton("🖥️ Projector HDMI")
        btn_toggle_proj.clicked.connect(self.toggle_projector_window)
        header_layout.addWidget(btn_toggle_proj)

        header_layout.addStretch()

        ip = self.emissor.obtenir_ip_local() if self.emissor else "127.0.0.1"
        port = self.emissor.port if self.emissor else 5000
        self.ip_label = QLabel(f"🌐 Web Server: http://{ip}:{port}/auto")
        self.ip_label.setFont(QFont("Arial", 10, QFont.Weight.Bold))
        self.ip_label.setStyleSheet("color: #00E676; padding: 5px;")
        header_layout.addWidget(self.ip_label)

        main_layout.addLayout(header_layout)

        # 2. CONFIGURACIÓ DE TRANSCRIPCIÓ I IDIOMES DEL YAML
        config_box = QGroupBox("Configuració de Transcripció Whisper")
        config_layout = QHBoxLayout(config_box)
        config_layout.setContentsMargins(12, 18, 12, 12)

        config_layout.addWidget(QLabel("Idioma d'Entrada:"))
        self.combo_lang = QComboBox()
        
        # Carregam els idiomes definits a la secció 'languages' del YAML
        for code, name in self.languages_cfg.items():
            self.combo_lang.addItem(name, code)
            
        self.combo_lang.currentIndexChanged.connect(self.on_lang_changed)
        config_layout.addWidget(self.combo_lang)

        config_layout.addSpacing(30)

        engine_name = self.config_yaml.get('whisper', {}).get('engine_type', 'transformers').upper()
        self.lbl_status_ia = QLabel(f"Estat: Model carregat [{engine_name}]")
        self.lbl_status_ia.setStyleSheet("color: #29b6f6; font-weight: bold;")
        config_layout.addWidget(self.lbl_status_ia)

        config_layout.addStretch()
        main_layout.addWidget(config_box)

        # 3. PREVISUALITZACIÓ EN GRAN
        self.display_box = QLabel("Prem START per iniciar la transcripció")
        self.display_box.setFont(QFont("Arial", 16, QFont.Weight.Bold))
        self.display_box.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.display_box.setWordWrap(True)
        self.display_box.setStyleSheet("""
            QLabel {
                background-color: #1A1A1A;
                border: 2px solid #00E676;
                border-radius: 8px;
                padding: 15px;
                min-height: 50px;
            }
        """)
        main_layout.addWidget(self.display_box)

        # 4. CONTROLS DE SESSIÓ I TEMPS
        timer_box = QGroupBox("Control Temporal de la Funció")
        timer_layout = QHBoxLayout(timer_box)
        timer_layout.setContentsMargins(12, 18, 12, 12)

        self.btn_start_stop = QPushButton("▶️ Start")
        self.btn_start_stop.setStyleSheet("background-color: #1b5e20; color: white;")
        self.btn_start_stop.clicked.connect(self.on_click_start_stop)
        timer_layout.addWidget(self.btn_start_stop)

        self.btn_pause = QPushButton("⏸️ Pause")
        self.btn_pause.setStyleSheet("background-color: #333333; color: #888888;")
        self.btn_pause.setEnabled(False)
        self.btn_pause.clicked.connect(self.toggle_pause)
        timer_layout.addWidget(self.btn_pause)

        self.btn_blackout = QPushButton("⬛ Blackout (B)")
        self.btn_blackout.setStyleSheet("background-color: #C62828; color: white;")
        self.btn_blackout.clicked.connect(self.blackout)
        timer_layout.addWidget(self.btn_blackout)

        timer_layout.addSpacing(20)

        timer_layout.addWidget(QLabel("Temps Total:"))
        self.lbl_total_time = QLabel("00:00")
        self.lbl_total_time.setFont(QFont("Arial", 12, QFont.Weight.Bold))
        self.lbl_total_time.setStyleSheet("color: #00E676; background-color: #1a1a1a; padding: 4px 8px; border-radius: 4px;")
        timer_layout.addWidget(self.lbl_total_time)

        timer_layout.addSpacing(15)

        timer_layout.addWidget(QLabel("En frase:"))
        self.lbl_sub_elapsed = QLabel("00:00")
        self.lbl_sub_elapsed.setFont(QFont("Arial", 12, QFont.Weight.Bold))
        self.lbl_sub_elapsed.setStyleSheet("color: #29b6f6; background-color: #1a1a1a; padding: 4px 8px; border-radius: 4px;")
        timer_layout.addWidget(self.lbl_sub_elapsed)

        timer_layout.addStretch()
        main_layout.addWidget(timer_box)

        # 5. HISTORIAL / LOG
        log_box = QGroupBox("Historial de Transcripció en Directe")
        log_layout = QVBoxLayout(log_box)
        log_layout.setContentsMargins(12, 18, 12, 12)

        self.log_edit = QTextEdit()
        self.log_edit.setReadOnly(True)
        log_layout.addWidget(self.log_edit)

        main_layout.addWidget(log_box)

    def actualitzar_vumetre_principal(self, level):
        self.main_vu_meter.setValue(level)
        if level > 85:
            self.main_vu_meter.setStyleSheet("QProgressBar::chunk { background-color: #FF5252; }")
        else:
            self.main_vu_meter.setStyleSheet("QProgressBar::chunk { background-color: #00E676; }")

    def obrir_selector_mic(self):
        dialog = MicSelectorDialog(current_device_index=self.current_mic_index, parent=self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.current_mic_index = dialog.selected_index
            self.transcriptor.actualitzar_dispositiu_entrada(self.current_mic_index)
            self.vu_thread.set_device(self.current_mic_index)
            
            mic_name = f"Mic #{self.current_mic_index}" if self.current_mic_index is not None else "Mic per defecte"
            self.lbl_status_ia.setText(f"Estat: Actiu {mic_name}")

    def closeEvent(self, event):
        self.stop_session()
        if hasattr(self, 'vu_thread') and self.vu_thread.isRunning():
            self.vu_thread.stop()
        try:
            if hasattr(self, 'projector_window') and self.projector_window:
                self.projector_window.close()
        except Exception as e:
            print(f"Error tancant el projector: {e}")
        event.accept()

    def detectar_segona_pantalla(self):
        try:
            screens = QApplication.screens()
            if len(screens) > 1:
                second_screen = screens[1]
                self.projector_window.setScreen(second_screen)
                self.projector_window.move(second_screen.geometry().x(), second_screen.geometry().y())
                self.projector_window.showFullScreen()
            else:
                self.projector_window.hide()
        except Exception as e:
            print(f"Error detectant pantalles: {e}")

    def toggle_projector_window(self):
        if self.projector_window.isVisible():
            self.projector_window.hide()
        else:
            self.projector_window.show()

    def calibrar_mic(self):
        self.lbl_status_ia.setText("Estat: Calibrant soroll ambient (2s)...")
        QApplication.processEvents()
        self.transcriptor.calibrar(durada=2)
        self.lbl_status_ia.setText("Estat: Calibrat completat")

    def on_lang_changed(self):
        lang_code = self.combo_lang.currentData()
        nom_idioma = self.combo_lang.currentText()
        
        # Canvi immediat en el fil actiu
        if self.audio_thread:
            self.audio_thread.set_idioma(lang_code)
            
        self.lbl_status_ia.setText(f"Estat: Transcribint en {nom_idioma}")

    def on_click_start_stop(self):
        if not self.session_started:
            self.start_session()
        else:
            self.confirm_and_stop_session()

    def start_session(self):
        self.session_started = True
        self.is_paused = False

        self.timer.start()

        lang_code = self.combo_lang.currentData()
        nom_idioma = self.combo_lang.currentText()
        
        self.audio_thread = AudioThread(self.transcriptor, idioma=lang_code)
        self.audio_thread.subtitol_generat.connect(self.on_nou_subtitol_ia)
        self.audio_thread.estat_canviat.connect(lambda msg: self.lbl_status_ia.setText(f"Estat: {msg}"))
        self.audio_thread.start()

        self.btn_start_stop.setText("⏹️ Stop")
        self.btn_start_stop.setStyleSheet("background-color: #C62828; color: white;")
        
        self.btn_pause.setEnabled(True)
        self.btn_pause.setText("⏸️ Pause")
        self.btn_pause.setStyleSheet("background-color: #e65100; color: white;")

        self.display_box.setText(f"[ Escoltant en {nom_idioma}... ]")

    def confirm_and_stop_session(self):
        if not self.is_paused:
            self.timer.stop()

        reply = QMessageBox.question(
            self,
            "Confirmar Aturada (Stop)",
            "Atenció: Es tancarà la sessió de transcripció i es reiniciarà el temps total.\n\nEstàs segur que vols aturar la sessió?",
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
        self.timer.stop()

        if self.audio_thread:
            self.audio_thread.stop()
            self.audio_thread = None

        self.total_elapsed_ms = 0
        self.sub_elapsed_ms = 0

        self.lbl_total_time.setText("00:00")
        self.lbl_sub_elapsed.setText("00:00")

        self.btn_start_stop.setText("▶️ Start")
        self.btn_start_stop.setStyleSheet("background-color: #1b5e20; color: white;")

        self.btn_pause.setEnabled(False)
        self.btn_pause.setText("⏸️ Pause")
        self.btn_pause.setStyleSheet("background-color: #333333; color: #888888;")

        self.lbl_status_ia.setText("Estat: Aturat")

    def toggle_pause(self):
        if not self.session_started:
            return

        if self.is_paused:
            self.is_paused = False
            self.timer.start()
            self.btn_pause.setText("⏸️ Pause")
            self.btn_pause.setStyleSheet("background-color: #e65100; color: white;")
            nom_idioma = self.combo_lang.currentText()
            self.lbl_status_ia.setText(f"Estat: Escoltant en {nom_idioma}...")
        else:
            self.is_paused = True
            self.timer.stop()
            self.btn_pause.setText("▶️ Reprendre")
            self.btn_pause.setStyleSheet("background-color: #1b5e20; color: white;")
            self.lbl_status_ia.setText("Estat: En pausa")

    def update_timers(self):
        if not self.session_started or self.is_paused:
            return

        self.total_elapsed_ms += 100
        self.sub_elapsed_ms += 100

        self.lbl_total_time.setText(ms_to_time_str(self.total_elapsed_ms))
        self.lbl_sub_elapsed.setText(ms_to_time_str(self.sub_elapsed_ms))

    def on_nou_subtitol_ia(self, text):
        if self.is_paused:
            return

        self.sub_elapsed_ms = 0

        self.display_box.setText(text)
        self.projector_window.actualitzar_text(text)

        if self.emissor:
            self.emissor.emetre("auto", text)

        timestamp = ms_to_time_str(self.total_elapsed_ms)
        self.log_edit.append(f"[{timestamp}] {text}")
        self.log_edit.moveCursor(QTextCursor.MoveOperation.End)

    def blackout(self):
        self.display_box.setText("[ BLACKOUT ]")
        self.projector_window.actualitzar_text("")
        if self.emissor:
            self.emissor.emetre("auto", " ")

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_B:
            self.blackout()
        else:
            super().keyPressEvent(event)


# --- EXECUCIÓ PRINCIPAL ---
if __name__ == "__main__":
    app = QApplication(sys.argv)

    emissor = None
    try:
        emissor = EmissorWeb(CONFIG_SERVER, mode="AUTOMÀTIC")
        emissor.iniciar()
    except Exception as e:
        print(f"⚠️ Alerta en iniciar el servidor web: {e}")

    window = TeloAutoPyQt(emissor)
    window.show()

    sys.exit(app.exec())
