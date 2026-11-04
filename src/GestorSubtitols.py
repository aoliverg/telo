import sys
import os
import re
import time
import yaml

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, 
    QPushButton, QLabel, QTextEdit, QComboBox, QGroupBox, QMessageBox,
    QDialog, QProgressBar
)
from PyQt6.QtCore import Qt, QTimer, QThread, QObject, pyqtSignal
from PyQt6.QtGui import QFont, QTextCursor

# Directoris del projecte
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SRC_DIR = os.path.join(BASE_DIR, 'src')
CONFIG_YAML_PATH = os.path.join(BASE_DIR, 'configTranscriptorTelo.yaml')

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

CONFIG_SERVER = {
    'host': '0.0.0.0',
    'port': 5000,
    'channels': [{'id': 'auto', 'label': 'Transcripció en Directe'}]
}


def carregar_configuracio_yaml():
    """Carrega la configuració del YAML o la crea per defecte si no existeix."""
    config_defecte = {
        'general': {
            'show_info': True
        },
        'audio': {
            'samplerate': 16000,
            'block_ms': 100,
            'device': None
        },
        'whisper_live': {
            'host': 'localhost',
            'port': 8000,
            'endpoint': '/asr'
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


# --- GESTOR DE SUBTÍTOLS (RITME DE LECTURA I FRAGMENTACIÓ) ---
class GestorSubtitols(QObject):
    """Garanteix que les oracions es mostrin d'una en una durant un temps suficient per llegir-les."""
    nova_frase_pantalla = pyqtSignal(str)

    def __init__(self, temps_minim_ms=3000, ms_per_paraula=300):
        super().__init__()
        self.temps_minim_ms = temps_minim_ms  # Temps mínim de lectura (3 segons)
        self.ms_per_paraula = ms_per_paraula  # Temps extra per paraula (0.3 segons)

        self.cua_frases = []
        self.frase_actual = ""

        self.timer_pantalla = QTimer()
        self.timer_pantalla.setSingleShot(True)
        self.timer_pantalla.timeout.connect(self._mostrar_següent_frase)

    def processar_text_entrada(self, text_complet):
        """Rep el text complet de WhisperLive, extreu les oracions acabades i les posa a la cua."""
        if not text_complet or not isinstance(text_complet, str):
            return

        try:
            # Separem per puntuació de final d'oració (. ! ?)
            frases = re.split(r'(?<=[.!?])\s+', text_complet)

            if not frases:
                return

            # Les frases tancades amb punt es van afegint a la cua
            for frase in frases[:-1]:
                frase_neta = frase.strip()
                if frase_neta and frase_neta not in self.cua_frases and frase_neta != self.frase_actual:
                    self.cua_frases.append(frase_neta)

            frase_en_curs = frases[-1].strip()

            # Si el temporitzador està lliure, iniciem la cua o mostrem la frase en curs
            if not self.timer_pantalla.isActive():
                if self.cua_frases:
                    self._mostrar_següent_frase()
                elif frase_en_curs and frase_en_curs != self.frase_actual:
                    self.nova_frase_pantalla.emit(frase_en_curs)

        except Exception as e:
            print(f"⚠️ Error al GestorSubtitols: {e}")

    def _mostrar_següent_frase(self):
        """Mostra la següent oració i calcula el temps que ha d'estar a pantalla."""
        if not self.cua_frases:
            return

        self.frase_actual = self.cua_frases.pop(0)
        self.nova_frase_pantalla.emit(self.frase_actual)

        # Càlcul dinàmic segons la longitud de l'oració
        num_paraules = len(self.frase_actual.split())
        durada_ms = self.temps_minim_ms + (num_paraules * self.ms_per_paraula)

        self.timer_pantalla.start(durada_ms)

    def netejar(self):
        """Atura els temporitzadors i esborra la cua acumulada."""
        self.timer_pantalla.stop()
        self.cua_frases.clear()
        self.frase_actual = ""


# --- FIL CONTROLADOR DE TRANSCRIPCIÓ PER WHISPER LIVE KIT ---
class AudioThread(QThread):
    subtitol_generat = pyqtSignal(str)
    estat_canviat = pyqtSignal(str)

    def __init__(self, transcriptor):
        super().__init__()
        self.transcriptor = transcriptor
        self.running = False

    def run(self):
        self.running = True
        self.estat_canviat.emit("Connectant al servidor WhisperLive...")
        
        def callback_resultat(text_complet, linies):
            if self.running and text_complet:
                self.subtitol_generat.emit(text_complet)

        try:
            self.transcriptor.iniciar_escolta(callback_resultat=callback_resultat)
            self.estat_canviat.emit("Escoltant i transcribint en directe...")
            
            while self.running and self.transcriptor.is_listening:
                time.sleep(0.1)
        except Exception as e:
            self.estat_canviat.emit(f"Error en la transcripció: {e}")

    def stop(self):
        self.running = False
        if self.transcriptor:
            self.transcriptor.aturar_escolta()
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
        
        # 1. Carregar configuració
        self.config_yaml = carregar_configuracio_yaml()
        self.languages_cfg = self.config_yaml.get('languages', {'ca': 'Català'})
        
        audio_cfg = self.config_yaml.get('audio', {})
        self.current_mic_index = audio_cfg.get('device', None)

        self.total_elapsed_ms = 0
        self.sub_elapsed_ms = 0

        self.timer = QTimer(self)
        self.timer.setInterval(100)
        self.timer.timeout.connect(self.update_timers)

        # 2. Inicialitzar Gestor de Subtítols i TranscriptorWhisperLiveKit
        self.gestor_subtitols = GestorSubtitols(temps_minim_ms=3000, ms_per_paraula=300)
        self.gestor_subtitols.nova_frase_pantalla.connect(self.actualitzar_pantalla_oracio)

        if TranscriptorWhisperLiveKit is not None:
            self.transcriptor = TranscriptorWhisperLiveKit(config_path=CONFIG_YAML_PATH)
            try:
                self.transcriptor.inicialitzar_motor()
            except Exception as e:
                print(f"⚠️ Alerta en inicialitzar el motor del transcriptor: {e}")
        else:
            self.transcriptor = None

        self.audio_thread = None
        self.projector_window = WindowProjector()

        self.init_ui()
        self.detectar_segona_pantalla()

    def init_ui(self):
        self.setWindowTitle("TELÓ 🤖 - Control Automàtic amb WhisperLiveKit")
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

        # 2. CONFIGURACIÓ DE TRANSCRIPCIÓ
        config_box = QGroupBox("Configuració de Transcripció WhisperLive")
        config_layout = QHBoxLayout(config_box)
        config_layout.setContentsMargins(12, 18, 12, 12)

        config_layout.addWidget(QLabel("Idioma d'Entrada:"))
        self.combo_lang = QComboBox()
        
        for code, name in self.languages_cfg.items():
            self.combo_lang.addItem(name, code)
            
        config_layout.addWidget(self.combo_lang)

        config_layout.addSpacing(30)

        ws_url = self.transcriptor.url if self.transcriptor else "N/A"
        self.lbl_status_ia = QLabel(f"Estat: WhisperLive ({ws_url})")
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

    def closeEvent(self, event):
        self.stop_session()
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

    def on_click_start_stop(self):
        if not self.session_started:
            self.start_session()
        else:
            self.confirm_and_stop_session()

    def start_session(self):
        if not self.transcriptor:
            QMessageBox.critical(self, "Error", "El transcriptor no s'ha pogut inicialitzar.")
            return

        self.session_started = True
        self.is_paused = False

        self.timer.start()

        nom_idioma = self.combo_lang.currentText()
        
        self.audio_thread = AudioThread(self.transcriptor)
        
        # Enllacem el senyal de l'AudioThread al GestorSubtitols de manera segura
        self.audio_thread.subtitol_generat.connect(
            lambda text: self.gestor_subtitols.processar_text_entrada(text)
        )
        self.audio_thread.estat_canviat.connect(
            lambda msg: self.lbl_status_ia.setText(f"Estat: {msg}")
        )
        
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

        if hasattr(self, 'gestor_subtitols'):
            self.gestor_subtitols.netejar()

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

    def actualitzar_pantalla_oracio(self, oracio):
        """Mètode cridat pel GestorSubtitols quan una oració s'ha de dibuixar."""
        if self.is_paused:
            return

        self.sub_elapsed_ms = 0

        # Actualitza la vista previa gran i la finestra del projector
        self.display_box.setText(oracio)
        self.projector_window.actualitzar_text(oracio)

        if self.emissor:
            self.emissor.emetre("auto", oracio)

        # Manté el log complet guardat al panell d'Historial
        timestamp = ms_to_time_str(self.total_elapsed_ms)
        self.log_edit.append(f"[{timestamp}] {oracio}")
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
    if EmissorWeb is not None:
        try:
            emissor = EmissorWeb(CONFIG_SERVER, mode="AUTOMÀTIC")
            emissor.iniciar()
        except Exception as e:
            print(f"⚠️ Alerta en iniciar el servidor web: {e}")

    window = TeloAutoPyQt(emissor)
    window.show()

    sys.exit(app.exec())
