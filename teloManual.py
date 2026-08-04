import sys
import os
import re

# Afegim la ruta absoluta del subdirectori 'src' al sys.path
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SRC_DIR = os.path.join(BASE_DIR, 'src')
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, 
    QPushButton, QLabel, QTableWidget, QTableWidgetItem, QFileDialog, 
    QHeaderView, QComboBox, QAbstractItemView, QCheckBox, QGroupBox,
    QMessageBox
)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QFont

# Importem EmissorWeb directament des del directori src
try:
    from emissor import EmissorWeb
except ImportError as e:
    print(f"⚠️ Alerta: No s'ha pogut importar EmissorWeb des de {SRC_DIR}: {e}")
    


CONFIG_SERVER = {
    'host': '0.0.0.0',
    'port': 5000,
    'channels': []
}

IDIOMES_MAP = {
    'FRA': 'Français',
    'CAT': 'Català',
    'SPA': 'Español',
    'ENG': 'English',
    'ITA': 'Italiano',
    'GER': 'Deutsch',
    'UNI': 'Monolingüe'
}

def srt_time_to_ms(time_str):
    """Converteix el temps del format SRT (00:01:28,998) a mil·lissegons"""
    try:
        time_str = time_str.replace(',', '.')
        parts = time_str.split(':')
        hours = int(parts[0])
        minutes = int(parts[1])
        sec_parts = parts[2].split('.')
        seconds = int(sec_parts[0])
        ms = int(sec_parts[1])
        return (hours * 3600 + minutes * 60 + seconds) * 1000 + ms
    except Exception:
        return 0

def ms_to_time_str(ms):
    """Converteix mil·lissegons a format llegible mm:ss o -mm:ss"""
    sign = ""
    if ms < 0:
        sign = "-"
        ms = abs(ms)
    
    seconds = int(ms // 1000)
    minutes = int(seconds // 60)
    hours = int(minutes // 60)
    
    sec_rem = seconds % 60
    min_rem = minutes % 60
    
    if hours > 0:
        return f"{sign}{hours:02d}:{min_rem:02d}:{sec_rem:02d}"
    else:
        return f"{sign}{min_rem:02d}:{sec_rem:02d}"


# --- FINESTRA AUXILIAR PROJECTOR HDMI ---
class WindowProjector(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("TELÓ - Projector HDMI")
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


# --- FINESTRA PRINCIPAL DE CONTROL ---
class TeloManualPyQt(QMainWindow):
    def __init__(self, emissor):
        super().__init__()
        self.emissor = emissor
        self.subtitles = []
        self.current_index = -1
        self.detected_langs = []
        self.proj_checkboxes = {}

        # --- TIMER I COMPTADORS ---
        self.timer = QTimer(self)
        self.timer.setInterval(100)  # S'actualitza cada 100ms
        self.timer.timeout.connect(self.update_timers)

        self.session_started = False
        self.is_paused = False
        self.total_elapsed_ms = 0
        self.sub_elapsed_ms = 0

        self.projector_window = WindowProjector()

        self.init_ui()
        self.detectar_segona_pantalla()

    def init_ui(self):
        self.setWindowTitle("TELÓ 🎭 - Control Manual Multilingüe")
        self.resize(1150, 820)
        
        # POLIMENT DE L'ESTIL CSS GLOBAL (QSS)
        self.setStyleSheet("""
            QMainWindow { background-color: #121212; }
            QWidget { background-color: #121212; color: #E0E0E0; font-family: 'Segoe UI', Arial; font-size: 10pt; }
            
            /* Títols dels marcs (QGroupBox) */
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

            /* Botons generals */
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

            /* Graella */
            QTableWidget { background-color: #1E1E1E; gridline-color: #2A2A2A; border: 1px solid #333; selection-background-color: #2E7D32; }
            QHeaderView::section { background-color: #252525; color: #00E676; font-weight: bold; border: 1px solid #333; padding: 4px; }
            
            QComboBox { background-color: #252525; border: 1px solid #444; padding: 4px 8px; border-radius: 4px; }
            QCheckBox { spacing: 8px; }
        """)

        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        main_layout.setSpacing(10)
        main_layout.setContentsMargins(15, 15, 15, 15)

        # 1. BARRA SUPERIOR (Header)
        header_layout = QHBoxLayout()
        
        title_label = QLabel("TELÓ 🎭")
        title_label.setFont(QFont("Arial", 16, QFont.Weight.Bold))
        title_label.setStyleSheet("color: #00E676;")
        header_layout.addWidget(title_label)

        btn_open = QPushButton("📂 Obrir arxiu .SRT")
        btn_open.clicked.connect(self.open_srt_file)
        header_layout.addWidget(btn_open)

        btn_toggle_proj = QPushButton("🖥️ Projector HDMI")
        btn_toggle_proj.clicked.connect(self.toggle_projector_window)
        header_layout.addWidget(btn_toggle_proj)

        header_layout.addStretch()

        ip = self.emissor.obtenir_ip_local() if self.emissor else "127.0.0.1"
        port = self.emissor.port if self.emissor else 5000
        self.ip_label = QLabel(f"🌐 Web Server: http://{ip}:{port}")
        self.ip_label.setFont(QFont("Arial", 10, QFont.Weight.Bold))
        self.ip_label.setStyleSheet("color: #00E676; padding: 5px;")
        header_layout.addWidget(self.ip_label)

        main_layout.addLayout(header_layout)

        # 2. SELECCIÓ D'IDIOMES
        config_box = QGroupBox("Configuració d'Idiomes")
        config_layout = QHBoxLayout(config_box)
        config_layout.setContentsMargins(12, 18, 12, 12)  # Més marge a dalt per no tapar el títol

        lbl_op = QLabel("Idioma Guia Operador:")
        config_layout.addWidget(lbl_op)
        
        self.combo_guia = QComboBox()
        self.combo_guia.currentIndexChanged.connect(self.on_guia_lang_changed)
        config_layout.addWidget(self.combo_guia)

        config_layout.addSpacing(30)

        lbl_proj = QLabel("Projectar a HDMI:")
        config_layout.addWidget(lbl_proj)

        self.proj_checkboxes_layout = QHBoxLayout()
        config_layout.addLayout(self.proj_checkboxes_layout)

        config_layout.addStretch()
        main_layout.addWidget(config_box)

        # 3. MONITOR DE PREVISUALITZACIÓ EN GRAN
        self.display_box = QLabel("Carrega un arxiu .srt per començar")
        self.display_box.setFont(QFont("Arial", 15, QFont.Weight.Bold))
        self.display_box.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.display_box.setWordWrap(True)
        self.display_box.setStyleSheet("""
            QLabel {
                background-color: #1A1A1A;
                border: 2px solid #00E676;
                border-radius: 8px;
                padding: 12px;
                min-height: 45px;
            }
        """)
        main_layout.addWidget(self.display_box)

        # 4. SECCIÓ DE TEMPS I CONTROLS (START/STOP i PAUSE)
        timer_box = QGroupBox("Control Temporal de la Funció")
        timer_layout = QHBoxLayout(timer_box)
        timer_layout.setContentsMargins(12, 18, 12, 12)  # Espai interior ajustat

        self.btn_start_stop = QPushButton("▶️ Start")
        self.btn_start_stop.setStyleSheet("background-color: #1b5e20; color: white;")
        self.btn_start_stop.clicked.connect(self.on_click_start_stop)
        timer_layout.addWidget(self.btn_start_stop)

        self.btn_pause = QPushButton("⏸️ Pause")
        self.btn_pause.setStyleSheet("background-color: #333333; color: #888888;")
        self.btn_pause.setEnabled(False)
        self.btn_pause.clicked.connect(self.toggle_pause)
        timer_layout.addWidget(self.btn_pause)

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

        timer_layout.addSpacing(15)

        timer_layout.addWidget(QLabel("Següent en (estimat):"))
        self.lbl_sub_countdown = QLabel("--:--")
        self.lbl_sub_countdown.setFont(QFont("Arial", 12, QFont.Weight.Bold))
        self.lbl_sub_countdown.setStyleSheet("color: #ffca28; background-color: #1a1a1a; padding: 4px 8px; border-radius: 4px;")
        timer_layout.addWidget(self.lbl_sub_countdown)

        timer_layout.addStretch()
        main_layout.addWidget(timer_box)

        # 5. BOTONS DE NAVEGACIÓ I CONTROL DIRECTE
        ctrl_layout = QHBoxLayout()

        self.btn_prev = QPushButton("⏮ Anterior (←)")
        self.btn_prev.setStyleSheet("padding: 10px;")
        self.btn_prev.clicked.connect(self.prev_sub)
        self.btn_prev.setEnabled(False)
        ctrl_layout.addWidget(self.btn_prev)

        self.btn_blackout = QPushButton("⬛ Blackout (B)")
        self.btn_blackout.setStyleSheet("background-color: #C62828; color: white; padding: 10px;")
        self.btn_blackout.clicked.connect(self.blackout)
        ctrl_layout.addWidget(self.btn_blackout)

        self.btn_next = QPushButton("Següent (Espai / →) ⏭")
        self.btn_next.setStyleSheet("background-color: #2E7D32; color: white; padding: 10px;")
        self.btn_next.clicked.connect(self.next_sub)
        self.btn_next.setEnabled(False)
        ctrl_layout.addWidget(self.btn_next)

        main_layout.addLayout(ctrl_layout)

        # 6. GRAELLA DE SUBTÍTOLS
        self.table = QTableWidget()
        self.table.setColumnCount(3)
        self.table.setHorizontalHeaderLabels(["#", "Temps", "Text del Subtítol"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)

        self.table.cellClicked.connect(self.on_cell_clicked)
        main_layout.addWidget(self.table)

    def closeEvent(self, event):
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

    # --- LÒGICA DE CONTROLS TEMPORALS ---
    def on_click_start_stop(self):
        if not self.session_started:
            self.start_session()
        else:
            self.confirm_and_stop_session()

    def start_session(self):
        if not self.subtitles:
            return

        self.session_started = True
        self.is_paused = False

        if self.current_index < 0:
            self.set_active_sub(0)
        else:
            self.set_active_sub(self.current_index)

        self.timer.start()

        self.btn_start_stop.setText("⏹️ Stop")
        self.btn_start_stop.setStyleSheet("background-color: #C62828; color: white;")
        
        self.btn_pause.setEnabled(True)
        self.btn_pause.setText("⏸️ Pause")
        self.btn_pause.setStyleSheet("background-color: #e65100; color: white;")

    def confirm_and_stop_session(self):
        if not self.is_paused:
            self.timer.stop()

        reply = QMessageBox.question(
            self,
            "Confirmar Aturada (Stop)",
            "Atenció: Es tancarà la sessió actual i es reiniciarà el temps total.\n\nEstàs segur que vols aturar la sessió?",
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

        self.total_elapsed_ms = 0
        self.sub_elapsed_ms = 0

        self.lbl_total_time.setText("00:00")
        self.lbl_sub_elapsed.setText("00:00")
        self.lbl_sub_countdown.setText("--:--")

        self.btn_start_stop.setText("▶️ Start")
        self.btn_start_stop.setStyleSheet("background-color: #1b5e20; color: white;")

        self.btn_pause.setEnabled(False)
        self.btn_pause.setText("⏸️ Pause")
        self.btn_pause.setStyleSheet("background-color: #333333; color: #888888;")

    def toggle_pause(self):
        if not self.session_started:
            return

        if self.is_paused:
            self.is_paused = False
            self.timer.start()
            self.btn_pause.setText("⏸️ Pause")
            self.btn_pause.setStyleSheet("background-color: #e65100; color: white;")
        else:
            self.is_paused = True
            self.timer.stop()
            self.btn_pause.setText("▶️ Reprendre")
            self.btn_pause.setStyleSheet("background-color: #1b5e20; color: white;")

    def update_timers(self):
        if not self.session_started or self.is_paused:
            return

        self.total_elapsed_ms += 100
        self.sub_elapsed_ms += 100

        self.lbl_total_time.setText(ms_to_time_str(self.total_elapsed_ms))
        self.lbl_sub_elapsed.setText(ms_to_time_str(self.sub_elapsed_ms))

        if 0 <= self.current_index < len(self.subtitles) - 1:
            current_start_ms = self.subtitles[self.current_index]['start_ms']
            next_start_ms = self.subtitles[self.current_index + 1]['start_ms']
            
            theoretical_duration = next_start_ms - current_start_ms
            remaining_ms = theoretical_duration - self.sub_elapsed_ms
            
            self.lbl_sub_countdown.setText(ms_to_time_str(remaining_ms))
            
            if remaining_ms < 0:
                self.lbl_sub_countdown.setStyleSheet("color: #ff5252; background-color: #1a1a1a; padding: 4px 8px; border-radius: 4px;")
            else:
                self.lbl_sub_countdown.setStyleSheet("color: #ffca28; background-color: #1a1a1a; padding: 4px 8px; border-radius: 4px;")
        else:
            self.lbl_sub_countdown.setText("FI")

    # --- PARSEJADOR DE SUBTÍTOLS ---
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
                sub_id = lines[0]
                time_str = lines[1]
                raw_text = "\n".join(lines[2:])

                times = time_str.split(' --> ')
                start_ms = srt_time_to_ms(times[0]) if len(times) > 0 else 0
                end_ms = srt_time_to_ms(times[1]) if len(times) > 1 else 0

                matches = re.findall(r'\[([A-Z]{3})\]\s*(.*?)(?=\[[A-Z]{3}\]|$)', raw_text, re.DOTALL)

                texts_by_lang = {}
                if matches:
                    for lang, text in matches:
                        clean_t = text.strip()
                        texts_by_lang[lang] = clean_t
                        detected_tag_set.add(lang)
                else:
                    clean_t = raw_text.strip()
                    texts_by_lang['UNI'] = clean_t
                    detected_tag_set.add('UNI')

                self.subtitles.append({
                    "id": sub_id,
                    "time": time_str,
                    "start_ms": start_ms,
                    "end_ms": end_ms,
                    "texts": texts_by_lang
                })

        self.detected_langs = list(detected_tag_set)

    def configurar_canals_i_ui(self):
        if self.emissor:
            nous_canals = []
            for lang in self.detected_langs:
                label = IDIOMES_MAP.get(lang, lang)
                nous_canals.append({'id': lang.lower(), 'label': label})
            self.emissor.actualitzar_canals(nous_canals)

        self.combo_guia.blockSignals(True)
        self.combo_guia.clear()
        for lang in self.detected_langs:
            self.combo_guia.addItem(IDIOMES_MAP.get(lang, lang), lang)
        self.combo_guia.blockSignals(False)

        for cb in self.proj_checkboxes.values():
            cb.destroy()
        self.proj_checkboxes.clear()

        for lang in self.detected_langs:
            cb = QCheckBox(IDIOMES_MAP.get(lang, lang))
            cb.setChecked(True)
            cb.stateChanged.connect(self.actualitzar_pantalles)
            self.proj_checkboxes_layout.addWidget(cb)
            self.proj_checkboxes[lang] = cb

    def on_guia_lang_changed(self):
        self.render_grid()
        if self.current_index >= 0:
            self.set_active_sub(self.current_index)

    def render_grid(self):
        lang_guia = self.combo_guia.currentData() or (self.detected_langs[0] if self.detected_langs else 'UNI')
        
        self.table.setRowCount(len(self.subtitles))
        for row, sub in enumerate(self.subtitles):
            item_id = QTableWidgetItem(sub['id'])
            item_id.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            
            item_time = QTableWidgetItem(sub['time'])
            item_time.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            
            text_guia = sub['texts'].get(lang_guia, "")
            item_text = QTableWidgetItem(text_guia)

            self.table.setItem(row, 0, item_id)
            self.table.setItem(row, 1, item_time)
            self.table.setItem(row, 2, item_text)

    def on_cell_clicked(self, row, column):
        self.set_active_sub(row)

    def set_active_sub(self, index):
        if 0 <= index < len(self.subtitles):
            self.current_index = index
            self.table.selectRow(self.current_index)
            self.table.scrollToItem(self.table.item(self.current_index, 0), QAbstractItemView.ScrollHint.PositionAtCenter)
            
            self.sub_elapsed_ms = 0
            self.actualitzar_pantalles()

    def actualitzar_pantalles(self):
        if not (0 <= self.current_index < len(self.subtitles)):
            return

        sub = self.subtitles[self.current_index]
        lang_guia = self.combo_guia.currentData() or 'UNI'

        text_guia = sub['texts'].get(lang_guia, "")
        self.display_box.setText(f"[{lang_guia}] {text_guia}")

        texts_projector = []
        for lang, cb in self.proj_checkboxes.items():
            if cb.isChecked() and lang in sub['texts']:
                texts_projector.append(sub['texts'][lang])

        text_final_proj = "\n---\n".join(texts_projector)
        self.projector_window.actualitzar_text(text_final_proj)

        if self.emissor:
            for lang in self.detected_langs:
                text_lang = sub['texts'].get(lang, "")
                self.emissor.emetre(lang.lower(), text_lang)

    def blackout(self):
        self.table.clearSelection()
        self.display_box.setText("[ BLACKOUT ]")
        self.projector_window.actualitzar_text("")
        
        if self.emissor:
            if self.detected_langs:
                for lang in self.detected_langs:
                    self.emissor.emetre(lang.lower(), " ")
            else:
                self.emissor.emetre("uni", " ")

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
            self.blackout()
        else:
            super().keyPressEvent(event)


# --- EXECUCIÓ PRINCIPAL ---
if __name__ == "__main__":
    app = QApplication(sys.argv)

    emissor = None
    try:
        emissor = EmissorWeb(CONFIG_SERVER)
        emissor.iniciar()
        emissor.mostrar_benvinguda()
    except Exception as e:
        print(f"⚠️ Alerta en iniciar el servidor web: {e}")

    window = TeloManualPyQt(emissor)
    window.show()

    sys.exit(app.exec())
