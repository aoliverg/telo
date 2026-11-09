import sys
import os
import re
import yaml

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SRC_DIR = os.path.join(BASE_DIR, 'src')
CONFIG_YAML_PATH = os.path.join(BASE_DIR, 'config.yaml')

if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, 
    QPushButton, QLabel, QTableWidget, QTableWidgetItem, QFileDialog, 
    QHeaderView, QComboBox, QAbstractItemView, QGroupBox,
    QMessageBox, QColorDialog, QCheckBox
)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QFont

try:
    from emissor import EmissorWeb
except ImportError as e:
    EmissorWeb = None
    print(f"⚠️ Alerta en importar emissor des de src/: {e}")

try:
    from utils import ms_to_time_str, srt_time_to_ms
except ImportError as e:
    print(f"⚠️ Error en importar mòduls compartits des de src/: {e}")
    ms_to_time_str = None
    srt_time_to_ms = None


# --- CÀRREGA DE CONFIGURACIÓ UNIFICADA YAML ---
def carregar_configuracio_yaml():
    if os.path.exists(CONFIG_YAML_PATH):
        try:
            with open(CONFIG_YAML_PATH, 'r', encoding='utf-8') as f:
                return yaml.safe_load(f)
        except Exception as e:
            print(f"⚠️ Error llegint {CONFIG_YAML_PATH}: {e}")
    return {}


CONFIG_FULL = carregar_configuracio_yaml()
CONFIG_COMMON = CONFIG_FULL.get('common', {})
CONFIG_MANUAL = CONFIG_FULL.get('manual', {})
STYLES_CFG = CONFIG_COMMON.get('styles', {})

CONFIG_SERVER = CONFIG_COMMON.get('server', {'host': '0.0.0.0', 'port': 5000, 'enabled': True})
WEB_SERVER_ENABLED = CONFIG_SERVER.get('enabled', True)
HDMI_ENABLED = STYLES_CFG.get('hdmi', {}).get('enabled', True)

LANGUAGES_CFG = CONFIG_COMMON.get('languages', {})
IDIOMES_CONFIG = [{'id': k.upper(), 'label': v} for k, v in LANGUAGES_CFG.items()]
IDIOMES_MAP = {item['id']: item['label'] for item in IDIOMES_CONFIG}
IDIOMES_ORDRE = [item['id'] for item in IDIOMES_CONFIG]


# --- FINESTRA DEL PROJECTOR HDMI DINÀMICA PER A MULTI-IDIOMA ---
class ProjectorManualWindow(QMainWindow):
    def __init__(self, app_title="TELÓ", config_proj=None):
        super().__init__()
        self.app_title = app_title
        self.config_proj = config_proj or {}
        
        self.base_font_family = self.config_proj.get('font_family', 'Arial')
        self.base_font_size = self.config_proj.get('font_size', 38)
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

    def actualitzar_textos(self, textos_ordenats):
        self._ultims_textos = textos_ordenats

        while len(self.labels) < len(textos_ordenats):
            lbl = QLabel()
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl.setWordWrap(True)
            self.layout_proj.addWidget(lbl, 1)
            self.labels.append(lbl)

        while len(self.labels) > len(textos_ordenats):
            lbl = self.labels.pop()
            self.layout_proj.removeWidget(lbl)
            lbl.deleteLater()

        num_lignes = len(textos_ordenats)
        if num_lignes >= 4:
            font_size = max(18, int(self.base_font_size * 0.52))
        elif num_lignes == 3:
            font_size = max(22, int(self.base_font_size * 0.68))
        elif num_lignes == 2:
            font_size = max(26, int(self.base_font_size * 0.85))
        else:
            font_size = self.base_font_size

        font = QFont(self.base_font_family, font_size, QFont.Weight.Bold)

        for i, (lang_id, text, color) in enumerate(textos_ordenats):
            lbl = self.labels[i]
            lbl.setFont(font)
            lbl.setText(text)
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


# --- FINESTRA PRINCIPAL DE CONTROL MANUAL ---
class TeloManualPyQt(QMainWindow):
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

        interface_cfg = CONFIG_COMMON.get('interface', {})
        self.app_title = interface_cfg.get('title', 'TELÓ')
        
        colors_cfg = interface_cfg.get('colors', {})
        self.color_bg = colors_cfg.get('background', '#121212')
        self.color_text = colors_cfg.get('text', '#E0E0E0')
        self.color_primary = colors_cfg.get('primary', '#00E676')
        self.color_secondary = colors_cfg.get('secondary', '#29B6F6')
        self.color_panel_bg = colors_cfg.get('panel_bg', '#1E1E1E')
        self.color_btn_bg = colors_cfg.get('button_bg', '#252525')
        self.color_btn_border = colors_cfg.get('button_border', '#444444')

        self.default_guide_lang = CONFIG_MANUAL.get('default_guide_language', 'CAT').upper()

        self.timer = QTimer(self)
        self.timer.setInterval(100)
        self.timer.timeout.connect(self.update_timers)

        self.session_started = False
        self.is_paused = False
        self.is_blackout = False
        self.total_elapsed_ms = 0
        self.sub_elapsed_ms = 0

        self.projector_cfg = STYLES_CFG.get('hdmi', {})
        if self.hdmi_enabled:
            self.projector_window = ProjectorManualWindow(app_title=self.app_title, config_proj=self.projector_cfg)
        else:
            self.projector_window = None

        self.init_ui()
        self.inicialitzar_llista_idiomes_per_defecte()
        if self.hdmi_enabled:
            self.detectar_segona_pantalla()

    def init_ui(self):
        self.setWindowTitle(f"{self.app_title} 🎭 - Control Manual Multilingüe")
        self.resize(1200, 960)
        
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
                left: 12px; 
                top: 0px; 
                padding: 0 5px; 
                color: {self.color_primary}; 
                background-color: {self.color_bg};
            }}

            QPushButton {{ 
                background-color: {self.color_btn_bg}; 
                border: 1px solid {self.color_btn_border}; 
                border-radius: 4px; 
                padding: 6px 14px; 
                font-weight: bold; 
                min-height: 22px;
            }}
            QPushButton:hover {{ background-color: {self.color_btn_border}; }}
            QPushButton:disabled {{ background-color: #1a1a1a; color: #555555; border: 1px solid #222; }}

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
            
            QComboBox {{ background-color: {self.color_btn_bg}; border: 1px solid {self.color_btn_border}; padding: 4px 8px; border-radius: 4px; }}
            QCheckBox {{ color: {self.color_text}; font-weight: bold; }}
        """)

        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        main_layout.setSpacing(10)
        main_layout.setContentsMargins(15, 15, 15, 15)

        # 1. BARRA SUPERIOR
        header_layout = QHBoxLayout()
        
        title_label = QLabel(f"{self.app_title} 🎭")
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
        self.ip_label.setStyleSheet("padding: 5px;")
        header_layout.addWidget(self.ip_label)

        main_layout.addLayout(header_layout)

        # 2. PANELL D'AJUSTOS EN DIRECTE HDMI
        if self.hdmi_enabled:
            main_layout.addWidget(self.crear_panel_ajustos_estils())

        # 3. SELECCIÓ D'IDIOMES, ORDRE I COLORS
        config_box = QGroupBox("Configuració d'Idiomes, Colors i Ordre Vertical" + (" HDMI" if self.hdmi_enabled else ""))
        config_layout = QHBoxLayout(config_box)
        config_layout.setContentsMargins(12, 18, 12, 12)

        # Idioma Guia
        col_guia = QVBoxLayout()
        col_guia.addWidget(QLabel("Idioma Guia Operador:"))
        self.combo_guia = QComboBox()
        self.combo_guia.currentIndexChanged.connect(self.on_guia_lang_changed)
        col_guia.addWidget(self.combo_guia)
        col_guia.addStretch()
        config_layout.addLayout(col_guia, 1)

        config_layout.addSpacing(20)

        # Taula Reordenable (HDMI)
        if self.hdmi_enabled:
            col_hdmi = QVBoxLayout()
            col_hdmi.addWidget(QLabel("Idiomes a Projectar a HDMI:"))

            hdmi_table_layout = QHBoxLayout()
            
            self.table_hdmi = QTableWidget()
            self.table_hdmi.setColumnCount(3)
            self.table_hdmi.setHorizontalHeaderLabels(["Actiu i Idioma", "Mostra", "Acció"])
            self.table_hdmi.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
            self.table_hdmi.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
            self.table_hdmi.setMinimumHeight(140)
            
            hd_header = self.table_hdmi.horizontalHeader()
            hd_header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
            hd_header.setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
            hd_header.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
            self.table_hdmi.setColumnWidth(1, 60)
            self.table_hdmi.setColumnWidth(2, 120)

            hdmi_table_layout.addWidget(self.table_hdmi, 4)

            btn_order_box = QVBoxLayout()
            self.btn_move_up = QPushButton("⬆️ Pujar")
            self.btn_move_up.setMinimumHeight(35)
            self.btn_move_up.clicked.connect(self.moure_idioma_amunt)
            btn_order_box.addWidget(self.btn_move_up)

            self.btn_move_down = QPushButton("⬇️ Baixar")
            self.btn_move_down.setMinimumHeight(35)
            self.btn_move_down.clicked.connect(self.moure_idioma_avall)
            btn_order_box.addWidget(self.btn_move_down)
            btn_order_box.addStretch()

            hdmi_table_layout.addLayout(btn_order_box, 1)
            col_hdmi.addLayout(hdmi_table_layout)

            config_layout.addLayout(col_hdmi, 4)

        main_layout.addWidget(config_box)

        # 4. MONITOR DE PREVISUALITZACIÓ
        cfg_prev = CONFIG_MANUAL.get('subtitols_previsualitzacio', {})
        prev_font = cfg_prev.get('font_family', 'Arial').split(',')[0].strip()
        prev_size = cfg_prev.get('font_size', 15)
        prev_text_color = cfg_prev.get('color_text', self.color_text)
        prev_bg_color = cfg_prev.get('color_fons', self.color_panel_bg)
        prev_border_color = cfg_prev.get('color_borda', self.color_primary)

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

        # 5. CONTROLS DE TEMPS
        timer_box = QGroupBox("Control Temporal de la Funció")
        timer_layout = QHBoxLayout(timer_box)
        timer_layout.setContentsMargins(12, 18, 12, 12)

        self.btn_start_stop = QPushButton("▶️ Start")
        self.btn_start_stop.setStyleSheet("background-color: #1b5e20; color: white;")
        self.btn_start_stop.clicked.connect(self.on_click_start_stop)
        timer_layout.addWidget(self.btn_start_stop)

        self.btn_pause = QPushButton("⏸ Pause")
        self.btn_pause.setStyleSheet(f"background-color: {self.color_btn_bg}; color: #888888;")
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

        timer_layout.addWidget(QLabel("En frase:"))
        self.lbl_sub_elapsed = QLabel("00:00")
        self.lbl_sub_elapsed.setFont(QFont("Arial", 12, QFont.Weight.Bold))
        self.lbl_sub_elapsed.setStyleSheet(f"color: {self.color_secondary}; background-color: #1a1a1a; padding: 4px 8px; border-radius: 4px;")
        timer_layout.addWidget(self.lbl_sub_elapsed)

        timer_layout.addSpacing(15)

        timer_layout.addWidget(QLabel("Següent en (estimat):"))
        self.lbl_sub_countdown = QLabel("--:--")
        self.lbl_sub_countdown.setFont(QFont("Arial", 12, QFont.Weight.Bold))
        self.lbl_sub_countdown.setStyleSheet("color: #ffca28; background-color: #1a1a1a; padding: 4px 8px; border-radius: 4px;")
        timer_layout.addWidget(self.lbl_sub_countdown)

        timer_layout.addStretch()
        main_layout.addWidget(timer_box)

        # 6. BOTONS DE NAVEGACIÓ
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
            self.combo_guia.addItem(item['label'], item['id'])
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
            cb.setStyleSheet("font-weight: bold; margin-left: 10px; color: #FFFFFF;")
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
            self.actualitzar_pantalles()

    def triar_color_idioma(self, row):
        if not (0 <= row < len(self.hdmi_langs_data)):
            return
        color = QColorDialog.getColor()
        if color.isValid():
            self.hdmi_langs_data[row]['color'] = color.name()
            self.idiomes_colors_mem[self.hdmi_langs_data[row]['lang']] = color.name()
            self.dibuixar_taula_hdmi()
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
            self.actualitzar_pantalles()

    def crear_panel_ajustos_estils(self):
        group_box = QGroupBox("🖥️ Control Global d'Estils del Projector HDMI (En Directe)")
        layout = QHBoxLayout(group_box)

        layout.addWidget(QLabel("Tipografia:"))
        combo_font = QComboBox()
        combo_font.addItems(["Arial", "Courier New", "Verdana", "Times New Roman", "Trebuchet MS"])
        combo_font.setCurrentText(self.projector_cfg.get('font_family', 'Arial'))
        combo_font.currentTextChanged.connect(self.actualitzar_font_projector)
        layout.addWidget(combo_font)

        layout.addSpacing(15)

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
            self.actualitzar_pantalles()

    def actualitzar_mida_projector(self, size_str):
        if self.projector_window:
            self.projector_window.set_font_size(int(size_str))
            self.actualitzar_pantalles()

    def canviar_fons_projector(self):
        if not self.projector_window:
            return
        color = QColorDialog.getColor()
        if color.isValid():
            self.projector_window.set_bg_color(color.name())

    def closeEvent(self, event):
        try:
            if self.hdmi_enabled and hasattr(self, 'projector_window') and self.projector_window:
                self.projector_window.close()
        except Exception as e:
            print(f"Error tancant el projector: {e}")
        event.accept()

    def detectar_segona_pantalla(self):
        if self.hdmi_enabled and self.projector_window:
            self.projector_window.assignar_a_segona_pantalla()

    def toggle_projector_window(self):
        if not self.projector_window:
            return
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
        self.is_blackout = False

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
        self.is_blackout = False
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
        self.btn_pause.setStyleSheet(f"background-color: {self.color_btn_bg}; color: #888888;")

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
        if self.web_enabled and hasattr(self.emissor, 'actualitzar_canals'):
            nous_canals = []
            for lang in self.detected_langs:
                label = IDIOMES_MAP.get(lang, lang)
                nous_canals.append({'id': lang.lower(), 'label': label})
            self.emissor.actualitzar_canals(nous_canals)

        # 1. Combo Guia
        self.combo_guia.blockSignals(True)
        self.combo_guia.clear()
        index_per_defecte = 0
        for i, lang in enumerate(self.detected_langs):
            self.combo_guia.addItem(IDIOMES_MAP.get(lang, lang), lang)
            if lang == self.default_guide_lang:
                index_per_defecte = i

        if self.combo_guia.count() > 0:
            self.combo_guia.setCurrentIndex(index_per_defecte)
        self.combo_guia.blockSignals(False)

        # 2. Actualitza les dades d'idiomes detectats conservant ordre i colors previs
        existents = {item['lang']: item for item in self.hdmi_langs_data}
        nous_hdmi_data = []

        for i, lang in enumerate(self.detected_langs):
            label = IDIOMES_MAP.get(lang, lang)
            if lang in existents:
                item_ex = existents[lang]
                nous_hdmi_data.append(item_ex)
            else:
                color = self.idiomes_colors_mem.get(lang, self.PALETA_PER_DEFECTE[i % len(self.PALETA_PER_DEFECTE)])
                self.idiomes_colors_mem[lang] = color
                nous_hdmi_data.append({
                    'lang': lang,
                    'label': label,
                    'color': color,
                    'enabled': True
                })

        self.hdmi_langs_data = nous_hdmi_data
        if self.hdmi_enabled:
            self.dibuixar_taula_hdmi()

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
            self.is_blackout = False
            self.current_index = index
            self.table.selectRow(self.current_index)
            self.table.scrollToItem(self.table.item(self.current_index, 0), QAbstractItemView.ScrollHint.PositionAtCenter)
            
            self.sub_elapsed_ms = 0
            self.actualitzar_pantalles()

    def actualitzar_pantalles(self):
        if self.is_blackout or not (0 <= self.current_index < len(self.subtitles)):
            return

        sub = self.subtitles[self.current_index]
        lang_guia = self.combo_guia.currentData() or 'UNI'

        # Previsualització operador
        text_guia = sub['texts'].get(lang_guia, "")
        self.display_box.setText(f"[{lang_guia}] {text_guia}")

        # Recull dels textos seguint l'ordre i colors de la taula
        if self.hdmi_enabled and self.projector_window:
            textos_projector = []
            for item in self.hdmi_langs_data:
                if item['enabled']:
                    lang = item['lang']
                    color = item['color']
                    text = sub['texts'].get(lang, "")
                    if text:
                        textos_projector.append((lang, text, color))

            self.projector_window.actualitzar_textos(textos_projector)

        # Emissió web
        if self.web_enabled:
            for lang in self.detected_langs:
                text_lang = sub['texts'].get(lang, "")
                self.emissor.emetre(lang.lower(), text_lang)

    def blackout(self):
        if not self.is_blackout:
            self.is_blackout = True
            self.table.clearSelection()
            self.display_box.setText("[ BLACKOUT ]")
            
            if self.hdmi_enabled and self.projector_window:
                self.projector_window.blackout()
            
            if self.web_enabled:
                if self.detected_langs:
                    for lang in self.detected_langs:
                        self.emissor.emetre(lang.lower(), "")
                else:
                    self.emissor.emetre("uni", "")
        else:
            self.is_blackout = False
            if self.current_index >= 0:
                self.set_active_sub(self.current_index)

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
    if WEB_SERVER_ENABLED and EmissorWeb is not None:
        try:
            CONFIG_SERVER['channels'] = [{'id': item['id'].lower(), 'label': item['label']} for item in IDIOMES_CONFIG]
            emissor = EmissorWeb(
                config_server=CONFIG_SERVER, 
                mode="MANUAL",
                config_styles=STYLES_CFG
            )
            emissor.iniciar()
        except Exception as e:
            print(f"⚠️ Alerta en iniciar el servidor web: {e}")

    window = TeloManualPyQt(emissor)
    window.show()

    sys.exit(app.exec())
