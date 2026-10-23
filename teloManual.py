import sys
import os
import re
import yaml

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SRC_DIR = os.path.join(BASE_DIR, 'src')
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, 
    QPushButton, QLabel, QTableWidget, QTableWidgetItem, QFileDialog, 
    QHeaderView, QComboBox, QAbstractItemView, QCheckBox, QGroupBox,
    QMessageBox, QColorDialog, QTabWidget
)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QFont

try:
    from emissor import EmissorWeb
except ImportError as e:
    EmissorWeb = None
    print(f"⚠️ Alerta: No s'ha pogut importar EmissorWeb des de {SRC_DIR}: {e}")

try:
    from projector import WindowProjector
    from utils import ms_to_time_str, srt_time_to_ms
except ImportError as e:
    print(f"⚠️ Error en importar mòduls compartits des de src/: {e}")
    WindowProjector = None
    ms_to_time_str = None
    srt_time_to_ms = None

# --- CÀRREGA DE CONFIGURACIÓ UNIFICADA ---
CONFIG_PATH = os.path.join(BASE_DIR, 'configTelo.yaml')

def carregar_configuracio():
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
                return yaml.safe_load(f)
        except Exception as e:
            print(f"⚠️ Error carregant {CONFIG_PATH}: {e}")
    return {}

CONFIG_FULL = carregar_configuracio()
CONFIG_COMMON = CONFIG_FULL.get('common', {})
CONFIG_MANUAL = CONFIG_FULL.get('manual', {})
STYLES_CFG = CONFIG_COMMON.get('styles', {})

CONFIG_SERVER = CONFIG_COMMON.get('server', {'host': '0.0.0.0', 'port': 5000})
IDIOMES_CONFIG = [{'id': k.upper(), 'label': v} for k, v in CONFIG_COMMON.get('languages', {}).items()]

ESTILS_CONFIG = CONFIG_COMMON.get('interface', {})
ESTILS_CONFIG['projector_hdmi'] = STYLES_CFG.get('hdmi', {})
ESTILS_CONFIG['subtitols_previsualitzacio'] = CONFIG_MANUAL.get('subtitols_previsualitzacio', {})

IDIOMES_MAP = {item['id']: item['label'] for item in IDIOMES_CONFIG}
IDIOMES_ORDRE = [item['id'] for item in IDIOMES_CONFIG]


# --- FINESTRA PRINCIPAL DE CONTROL ---
class TeloManualPyQt(QMainWindow):
    def __init__(self, emissor):
        super().__init__()
        self.emissor = emissor
        self.subtitles = []
        self.current_index = -1
        self.detected_langs = []
        self.proj_checkboxes = {}

        self.timer = QTimer(self)
        self.timer.setInterval(100)
        self.timer.timeout.connect(self.update_timers)

        self.session_started = False
        self.is_paused = False
        self.total_elapsed_ms = 0
        self.sub_elapsed_ms = 0

        self.projector_cfg = STYLES_CFG.get('hdmi', {})
        self.projector_window = WindowProjector(app_title="TELÓ", config_proj=self.projector_cfg)

        self.init_ui()
        self.detectar_segona_pantalla()

    def init_ui(self):
        self.setWindowTitle("TELÓ 🎭 - Control Manual Multilingüe")
        self.resize(1150, 920)
        
        cfg_ui = ESTILS_CONFIG.get('interface', {})
        font_family = cfg_ui.get('font_family', 'Arial, Helvetica')
        font_size = cfg_ui.get('font_size', 10)
        color_text = cfg_ui.get('colors', {}).get('text', '#E0E0E0')
        color_fons = cfg_ui.get('colors', {}).get('background', '#121212')
        color_destacat = cfg_ui.get('colors', {}).get('primary', '#00E676')

        self.setStyleSheet(f"""
            QMainWindow {{ background-color: {color_fons}; }}
            QWidget {{ background-color: {color_fons}; color: {color_text}; font-family: {font_family}; font-size: {font_size}pt; }}
            
            QGroupBox {{ 
                border: 1px solid #333333; 
                border-radius: 6px; 
                margin-top: 16px; 
                font-weight: bold;
                font-size: {font_size}pt;
            }}
            QGroupBox::title {{ 
                subcontrol-origin: margin; 
                subcontrol-position: top left;
                left: 12px; 
                top: 0px;
                padding: 0 5px; 
                color: {color_destacat}; 
                background-color: {color_fons};
            }}

            QPushButton {{ 
                background-color: #252525; 
                border: 1px solid #444; 
                border-radius: 4px; 
                padding: 6px 14px; 
                font-weight: bold; 
                min-height: 20px;
            }}
            QPushButton:hover {{ background-color: #333333; }}
            QPushButton:disabled {{ background-color: #1a1a1a; color: #555555; border: 1px solid #222; }}

            QTableWidget {{ background-color: #1E1E1E; gridline-color: #2A2A2A; border: 1px solid #333; selection-background-color: #2E7D32; }}
            QHeaderView::section {{ background-color: #252525; color: {color_destacat}; font-weight: bold; border: 1px solid #333; padding: 4px; }}
            
            QComboBox {{ background-color: #252525; border: 1px solid #444; padding: 4px 8px; border-radius: 4px; }}
            QCheckBox {{ spacing: 8px; }}
            QTabWidget::pane {{ border: 1px solid #333; border-radius: 4px; }}
            QTabBar::tab {{ background: #252525; padding: 6px 16px; margin-right: 2px; border-top-left-radius: 4px; border-top-right-radius: 4px; }}
            QTabBar::tab:selected {{ background: {color_destacat}; color: black; font-weight: bold; }}
        """)

        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        main_layout.setSpacing(10)
        main_layout.setContentsMargins(15, 15, 15, 15)

        # 1. BARRA SUPERIOR
        header_layout = QHBoxLayout()
        
        title_label = QLabel("TELÓ 🎭")
        title_label.setFont(QFont("Arial", 16, QFont.Weight.Bold))
        title_label.setStyleSheet(f"color: {color_destacat};")
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
        self.ip_label.setStyleSheet(f"color: {color_destacat}; padding: 5px;")
        header_layout.addWidget(self.ip_label)

        main_layout.addLayout(header_layout)

        # 2. PANELL D'AJUSTOS EN DIRECTE HDMI
        main_layout.addWidget(self.crear_panel_ajustos_estil_separats())

        # 3. SELECCIÓ D'IDIOMES
        config_box = QGroupBox("Configuració d'Idiomes")
        config_layout = QHBoxLayout(config_box)
        config_layout.setContentsMargins(12, 18, 12, 12)

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

        # 4. MONITOR DE PREVISUALITZACIÓ EN GRAN
        cfg_prev = ESTILS_CONFIG.get('subtitols_previsualitzacio', {})
        prev_font = cfg_prev.get('font_family', 'Arial').split(',')[0].strip()
        prev_size = cfg_prev.get('font_size', 15)
        prev_text_color = cfg_prev.get('color_text', '#FFFFFF')
        prev_bg_color = cfg_prev.get('color_fons', '#1A1A1A')
        prev_border_color = cfg_prev.get('color_borda', '#00E676')

        self.display_box = QLabel("Carrega un arxiu .srt per començar")
        self.display_box.setFont(QFont(prev_font, prev_size, QFont.Weight.Bold))
        self.display_box.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.display_box.setWordWrap(True)
        self.display_box.setStyleSheet(f"""
            QLabel {{
                background-color: {prev_bg_color};
                color: {prev_text_color};
                border: 2px solid {prev_border_color};
                border-radius: 8px;
                padding: 12px;
                min-height: 45px;
            }}
        """)
        main_layout.addWidget(self.display_box)

        # 5. SECCIÓ DE TEMPS I CONTROLS
        timer_box = QGroupBox("Control Temporal de la Funció")
        timer_layout = QHBoxLayout(timer_box)
        timer_layout.setContentsMargins(12, 18, 12, 12)

        self.btn_start_stop = QPushButton("▶️ Start")
        self.btn_start_stop.setStyleSheet("background-color: #1b5e20; color: white;")
        self.btn_start_stop.clicked.connect(self.on_click_start_stop)
        timer_layout.addWidget(self.btn_start_stop)

        self.btn_pause = QPushButton("⏸ Pause")
        self.btn_pause.setStyleSheet("background-color: #333333; color: #888888;")
        self.btn_pause.setEnabled(False)
        self.btn_pause.clicked.connect(self.toggle_pause)
        timer_layout.addWidget(self.btn_pause)

        timer_layout.addSpacing(20)

        timer_layout.addWidget(QLabel("Temps Total:"))
        self.lbl_total_time = QLabel("00:00")
        self.lbl_total_time.setFont(QFont("Arial", 12, QFont.Weight.Bold))
        self.lbl_total_time.setStyleSheet(f"color: {color_destacat}; background-color: #1a1a1a; padding: 4px 8px; border-radius: 4px;")
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

        # 6. BOTONS DE NAVEGACIÓ I CONTROL DIRECTE
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

        # 7. GRAELLA DE SUBTÍTOLS
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

    def crear_panel_ajustos_estil_separats(self):
        group_box = QGroupBox("🖥️ Control d'Estils del Projector HDMI (En Directe)")
        group_layout = QVBoxLayout(group_box)

        tabs = QTabWidget()

        for target_key, target_label in [('orig', '🟢 Text Superior / Principal'), ('trans', '🔵 Text Inferior / Secundari')]:
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

    def closeEvent(self, event):
        try:
            if hasattr(self, 'projector_window') and self.projector_window:
                self.projector_window.close()
        except Exception as e:
            print(f"Error tancant el projector: {e}")
        event.accept()

    def detectar_segona_pantalla(self):
        self.projector_window.assignar_a_segona_pantalla()

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

        self.detected_langs = [lang for lang in IDIOMES_ORDRE if lang in detected_tag_set]
        for lang in detected_tag_set:
            if lang not in self.detected_langs:
                self.detected_langs.append(lang)

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
    if EmissorWeb is not None:
        try:
            CONFIG_SERVER['channels'] = [{'id': item['id'].lower(), 'label': item['label']} for item in IDIOMES_CONFIG]
            CONFIG_SERVER['estils'] = ESTILS_CONFIG

            emissor = EmissorWeb(CONFIG_SERVER)
            emissor.iniciar()
        except Exception as e:
            print(f"⚠️ Alerta en iniciar el servidor web: {e}")
    else:
        print("⚠️ Advertència: El servidor web funcionarà desactivat per falta de mòduls.")

    window = TeloManualPyQt(emissor)
    window.show()

    sys.exit(app.exec())
