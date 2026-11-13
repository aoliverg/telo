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
    QMessageBox, QColorDialog, QCheckBox, QLineEdit, QDoubleSpinBox
)
from PyQt6.QtGui import QFont, QIntValidator, QPainter, QPixmap, QColor
from PyQt6.QtCore import Qt, QTimer

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

try:
    from i18n_manager import I18nManager
except ImportError:
    class I18nManager:
        def __init__(self, **kwargs): pass
        def t(self, key, default=None, **kwargs): return default if default is not None else key


# --- CÀRREGA DE CONFIGURACIÓ UNIFICADA YAML ---
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
CONFIG_MANUAL = CONFIG_FULL.get('manual', {})
STYLES_CFG = CONFIG_COMMON.get('styles', {})

INTERFACE_CFG = CONFIG_COMMON.get('interface', {})
APP_LANG = INTERFACE_CFG.get('language', 'ca')

# Inicialització del gestor i18n
DIR_I18N = os.path.join(BASE_DIR, 'i18n')
i18n = I18nManager(modul="manual", lang=APP_LANG, base_dir=DIR_I18N)
_ = i18n.t

CONFIG_SERVER = CONFIG_COMMON.get('server', {'host': '0.0.0.0', 'port': 5000, 'enabled': True})
WEB_SERVER_ENABLED = CONFIG_SERVER.get('enabled', True)
HDMI_ENABLED = STYLES_CFG.get('hdmi', {}).get('enabled', True)

LANGUAGES_CFG = CONFIG_COMMON.get('languages', {})
IDIOMES_CONFIG = [{'id': k.upper(), 'label': v} for k, v in LANGUAGES_CFG.items()]
IDIOMES_MAP = {item['id']: item['label'] for item in IDIOMES_CONFIG}
IDIOMES_ORDRE = [item['id'] for item in IDIOMES_CONFIG]


# --- FINESTRA DEL PROJECTOR HDMI (SENSE MARCS + BUFFER HIDPI DE ROTACIÓ) ---
class ProjectorManualWindow(QMainWindow):
    def __init__(self, app_title="TELÓ", config_proj=None):
        super().__init__()
        self.app_title = app_title
        self.config_proj = config_proj or {}
        
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Window)
        
        self.base_font_family = self.config_proj.get('font_family', 'Arial')
        self.base_font_size = self.config_proj.get('font_size', 38)
        self.bg_color = self.config_proj.get('background_color', '#000000')
        
        align_h_cfg = self.config_proj.get('align_h', _('align.center', 'Centrat'))
        self.set_align_h(align_h_cfg, redibuixar=False)

        align_v_cfg = self.config_proj.get('align_v', _('align.center', 'Centrat'))
        self.set_align_v(align_v_cfg, redibuixar=False)

        self.rotation_angle = float(self.config_proj.get('rotation', 0.0))
        
        self.labels = []
        self._ultims_textos = []

        self.init_ui()

    def init_ui(self):
        self.setWindowTitle(_('projector.title', '{title} - Projector HDMI', title=self.app_title))
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
        if align_str == _('align.left', 'Esquerra'):
            self.align_h = Qt.AlignmentFlag.AlignLeft
        elif align_str == _('align.right', 'Dreta'):
            self.align_h = Qt.AlignmentFlag.AlignRight
        else:
            self.align_h = Qt.AlignmentFlag.AlignHCenter
        if redibuixar and self._ultims_textos:
            self.actualitzar_textos(self._ultims_textos)

    def set_align_v(self, align_str, redibuixar=True):
        if align_str == _('align.top', 'A dalt'):
            self.align_v_block = "top"
        elif align_str == _('align.bottom', 'A baix'):
            self.align_v_block = "bottom"
        else:
            self.align_v_block = "center"
        if redibuixar and self._ultims_textos:
            self.actualitzar_textos(self._ultims_textos)

    def set_rotation(self, angle):
        self.rotation_angle = angle
        self.update()

    def actualitzar_textos(self, textos_ordenats):
        self._ultims_textos = textos_ordenats

        while self.layout_proj.count() > 0:
            self.layout_proj.takeAt(0)

        while len(self.labels) < len(textos_ordenats):
            lbl = QLabel()
            lbl.setWordWrap(True)
            self.labels.append(lbl)

        while len(self.labels) > len(textos_ordenats):
            lbl = self.labels.pop()
            lbl.deleteLater()

        total_lignes = sum(len(text.split('\n')) for _, text, _ in textos_ordenats)
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

        if self.align_v_block in ("center", "bottom"):
            self.layout_proj.addStretch(1)

        for i, (lang_id, text, color) in enumerate(textos_ordenats):
            lbl = self.labels[i]
            lbl.setFont(font)
            lbl.setText(text)
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
        self.cerca_index_candidat = -1

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

    def t(self, key, default=None, **kwargs):
        """Mètode segur de traducció que no entra en conflicte amb variables locals."""
        global i18n
        return i18n.t(key, default, **kwargs)

    def init_ui(self):
        self.setWindowTitle(self.t('window.title', '{title} 🎭 - Control Manual Multilingüe', title=self.app_title))
        self.resize(1220, 960)
        
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
                selection-color: #FFFFFF;
            }}
            QHeaderView::section {{ 
                background-color: {self.color_btn_bg}; 
                color: {self.color_primary}; 
                font-weight: bold; 
                border: 1px solid {self.color_btn_border}; 
                padding: 4px; 
            }}
            
            QComboBox {{ background-color: {self.color_btn_bg}; border: 1px solid {self.color_btn_border}; padding: 4px 8px; border-radius: 4px; }}
            QDoubleSpinBox {{ background-color: {self.color_btn_bg}; border: 1px solid {self.color_btn_border}; color: {self.color_text}; padding: 3px; border-radius: 4px; }}
            QLineEdit {{ background-color: {self.color_btn_bg}; border: 1px solid {self.color_btn_border}; color: {self.color_text}; padding: 4px; border-radius: 4px; }}
            QCheckBox {{ color: {self.color_text}; font-weight: bold; }}
        """)

        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        main_layout.setSpacing(10)
        main_layout.setContentsMargins(15, 15, 15, 15)

        # 1. BARRA SUPERIOR
        header_layout = QHBoxLayout()
        
        self.title_label = QLabel(f"{self.app_title} 🎭")
        self.title_label.setFont(QFont("Arial", 16, QFont.Weight.Bold))
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
            self.btn_toggle_proj = QPushButton(self.t('btn.toggle_projector', '🖥️ Projector HDMI'))
            self.btn_toggle_proj.clicked.connect(self.toggle_projector_window)
            header_layout.addWidget(self.btn_toggle_proj)

        header_layout.addStretch()

        if self.web_enabled:
            ip = self.emissor.obtenir_ip_local() if hasattr(self.emissor, 'obtenir_ip_local') else "127.0.0.1"
            port = self.emissor.port if hasattr(self.emissor, 'port') else 5000
            url_web = f"http://{ip}:{port}"
            self.ip_label = QLabel(f"{self.t('lbl.web_server', '🌐 Web Server:')} <a href=\"{url_web}\" style=\"color: {self.color_primary};\">{url_web}</a>")
            self.ip_label.setOpenExternalLinks(True)
        else:
            self.ip_label = QLabel(self.t('lbl.web_server_disabled', '🌐 Web Server: <span style="color: #7f8c8d;">Desactivat</span>'))

        self.ip_label.setFont(QFont("Arial", 10, QFont.Weight.Bold))
        self.ip_label.setStyleSheet("padding: 5px;")
        header_layout.addWidget(self.ip_label)

        main_layout.addLayout(header_layout)

        # 2. PANELL D'AJUSTOS EN DIRECTE HDMI
        if self.hdmi_enabled:
            main_layout.addWidget(self.crear_panel_ajustos_estils())

        # 3. SELECCIÓ D'IDIOMES, ORDRE I COLORS
        nom_grup = self.t('group.lang_config', "Configuració d'Idiomes, Colors i Ordre Vertical HDMI") if self.hdmi_enabled else self.t('group.lang_config_no_hdmi', "Configuració d'Idiomes i Colors")
        self.config_box = QGroupBox(nom_grup)
        config_layout = QHBoxLayout(self.config_box)
        config_layout.setContentsMargins(12, 18, 12, 12)

        col_guia = QVBoxLayout()
        self.lbl_guide_title = QLabel(self.t('lbl.guide_lang', 'Idioma Guia Operador:'))
        col_guia.addWidget(self.lbl_guide_title)
        self.combo_guia = QComboBox()
        self.combo_guia.currentIndexChanged.connect(self.on_guia_lang_changed)
        col_guia.addWidget(self.combo_guia)
        col_guia.addStretch()
        config_layout.addLayout(col_guia, 1)

        config_layout.addSpacing(20)

        if self.hdmi_enabled:
            col_hdmi = QVBoxLayout()
            self.lbl_hdmi_title = QLabel(self.t('lbl.hdmi_project_langs', 'Idiomes a Projectar a HDMI:'))
            col_hdmi.addWidget(self.lbl_hdmi_title)

            hdmi_table_layout = QHBoxLayout()
            
            self.table_hdmi = QTableWidget()
            self.table_hdmi.setColumnCount(3)
            self.table_hdmi.setHorizontalHeaderLabels([
                self.t('table.hdmi.header.active_lang', 'Actiu i Idioma'),
                self.t('table.hdmi.header.sample', 'Mostra'),
                self.t('table.hdmi.header.action', 'Acció')
            ])
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
            self.btn_move_up = QPushButton(self.t('btn.move_up', '⬆️ Pujar'))
            self.btn_move_up.setMinimumHeight(35)
            self.btn_move_up.clicked.connect(self.moure_idioma_amunt)
            btn_order_box.addWidget(self.btn_move_up)

            self.btn_move_down = QPushButton(self.t('btn.move_down', '⬇️ Baixar'))
            self.btn_move_down.setMinimumHeight(35)
            self.btn_move_down.clicked.connect(self.moure_idioma_avall)
            btn_order_box.addWidget(self.btn_move_down)
            btn_order_box.addStretch()

            hdmi_table_layout.addLayout(btn_order_box, 1)
            col_hdmi.addLayout(hdmi_table_layout)

            config_layout.addLayout(col_hdmi, 4)

        main_layout.addWidget(self.config_box)

        # 4. MONITOR DE PREVISUALITZACIÓ
        cfg_prev = CONFIG_MANUAL.get('subtitols_previsualitzacio', {})
        prev_font = cfg_prev.get('font_family', 'Arial').split(',')[0].strip()
        prev_size = cfg_prev.get('font_size', 15)
        prev_text_color = cfg_prev.get('color_text', self.color_text)
        prev_bg_color = cfg_prev.get('color_fons', self.color_panel_bg)
        prev_border_color = cfg_prev.get('color_borda', self.color_primary)

        self.display_box = QLabel(self.t('preview.placeholder', 'Carrega un arxiu .srt per començar'))
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
        self.timer_box = QGroupBox(self.t('group.timer', 'Control Temporal de la Funció'))
        timer_layout = QHBoxLayout(self.timer_box)
        timer_layout.setContentsMargins(12, 18, 12, 12)

        self.btn_start_stop = QPushButton(self.t('btn.start', '▶️ Start'))
        self.btn_start_stop.setStyleSheet("background-color: #1b5e20; color: white;")
        self.btn_start_stop.clicked.connect(self.on_click_start_stop)
        timer_layout.addWidget(self.btn_start_stop)

        self.btn_pause = QPushButton(self.t('btn.pause', '⏸ Pause'))
        self.btn_pause.setStyleSheet(f"background-color: {self.color_btn_bg}; color: #888888;")
        self.btn_pause.setEnabled(False)
        self.btn_pause.clicked.connect(self.toggle_pause)
        timer_layout.addWidget(self.btn_pause)

        timer_layout.addSpacing(20)

        self.lbl_title_total = QLabel(self.t('lbl.total_time', 'Temps Total:'))
        timer_layout.addWidget(self.lbl_title_total)
        self.lbl_total_time = QLabel("00:00")
        self.lbl_total_time.setFont(QFont("Arial", 12, QFont.Weight.Bold))
        self.lbl_total_time.setStyleSheet(f"color: {self.color_primary}; background-color: #1a1a1a; padding: 4px 8px; border-radius: 4px;")
        timer_layout.addWidget(self.lbl_total_time)

        timer_layout.addSpacing(15)

        self.lbl_title_elapsed = QLabel(self.t('lbl.sub_elapsed', 'En frase:'))
        timer_layout.addWidget(self.lbl_title_elapsed)
        self.lbl_sub_elapsed = QLabel("00:00")
        self.lbl_sub_elapsed.setFont(QFont("Arial", 12, QFont.Weight.Bold))
        self.lbl_sub_elapsed.setStyleSheet(f"color: {self.color_secondary}; background-color: #1a1a1a; padding: 4px 8px; border-radius: 4px;")
        timer_layout.addWidget(self.lbl_sub_elapsed)

        timer_layout.addSpacing(15)

        self.lbl_title_countdown = QLabel(self.t('lbl.sub_countdown', 'Següent en (estimat):'))
        timer_layout.addWidget(self.lbl_title_countdown)
        self.lbl_sub_countdown = QLabel("--:--")
        self.lbl_sub_countdown.setFont(QFont("Arial", 12, QFont.Weight.Bold))
        self.lbl_sub_countdown.setStyleSheet("color: #ffca28; background-color: #1a1a1a; padding: 4px 8px; border-radius: 4px;")
        timer_layout.addWidget(self.lbl_sub_countdown)

        timer_layout.addStretch()
        main_layout.addWidget(self.timer_box)

        # 6. BOTONS DE NAVEGACIÓ
        ctrl_layout = QHBoxLayout()

        self.btn_prev = QPushButton(self.t('btn.prev', '⏮ Anterior (←)'))
        self.btn_prev.setStyleSheet("padding: 10px;")
        self.btn_prev.clicked.connect(self.prev_sub)
        self.btn_prev.setEnabled(False)
        ctrl_layout.addWidget(self.btn_prev)

        self.btn_blackout = QPushButton(self.t('btn.blackout', '⬛ Blackout (B)'))
        self.btn_blackout.setStyleSheet("background-color: #C62828; color: white; padding: 10px;")
        self.btn_blackout.clicked.connect(self.blackout)
        ctrl_layout.addWidget(self.btn_blackout)

        self.btn_next = QPushButton(self.t('btn.next', 'Següent (Espai / →) ⏭'))
        self.btn_next.setStyleSheet("background-color: #2E7D32; color: white; padding: 10px;")
        self.btn_next.clicked.connect(self.next_sub)
        self.btn_next.setEnabled(False)
        ctrl_layout.addWidget(self.btn_next)

        main_layout.addLayout(ctrl_layout)

        # 6.5 BARRA DE CERCA AL GUIÓ
        search_layout = QHBoxLayout()
        self.lbl_search_title = QLabel(self.t('lbl.search', '🔎 Cerca al guió:'))
        search_layout.addWidget(self.lbl_search_title)
        
        self.input_search = QLineEdit()
        self.input_search.setPlaceholderText(self.t('search.placeholder', 'Escriu per cercar sense projectar...'))
        self.input_search.returnPressed.connect(self.cercar_subtitol_seguent)
        search_layout.addWidget(self.input_search, 4)

        self.btn_search = QPushButton(self.t('btn.search_next', '🔍 Següent'))
        self.btn_search.setStyleSheet("background-color: #0277BD; color: white; padding: 5px 10px;")
        self.btn_search.clicked.connect(self.cercar_subtitol_seguent)
        search_layout.addWidget(self.btn_search)

        self.btn_confirm_search = QPushButton(self.t('btn.search_project', '🚀 Projectar seleccionat'))
        self.btn_confirm_search.setStyleSheet("background-color: #E65100; color: white; padding: 5px 10px;")
        self.btn_confirm_search.clicked.connect(self.confirmar_salt_candidat)
        search_layout.addWidget(self.btn_confirm_search)

        main_layout.addLayout(search_layout)

        # 7. GRAELLA DE SUBTÍTOLS
        self.table = QTableWidget()
        self.table.setColumnCount(3)
        self.table.setHorizontalHeaderLabels([
            self.t('table.grid.header.num', '#'),
            self.t('table.grid.header.time', 'Temps'),
            self.t('table.grid.header.text', 'Text del Subtítol')
        ])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)

        self.table.cellClicked.connect(self.on_cell_clicked)
        main_layout.addWidget(self.table)

    def confirmar_salt_candidat(self):
        fila_seleccionada = self.table.currentRow()
        if 0 <= fila_seleccionada < len(self.subtitles):
            self.set_active_sub(fila_seleccionada)
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
                self.t('dialog.search_not_found.title', 'Cerca'), 
                self.t('dialog.search_not_found.msg', "No s'ha trobat cap subtítol amb el text: '{query}'", query=query)
            )

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

            btn_col = QPushButton(self.t('btn.color', '🎨 Color'))
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
        curr_data = self.combo_align_h.currentData() if self.combo_align_h.count() > 0 else self.projector_cfg.get('align_h', 'Centrat')
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
        curr_data = self.combo_align_v.currentData() if self.combo_align_v.count() > 0 else self.projector_cfg.get('align_v', 'Centrat')
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

    def recollir_estat_actual(self):
        """Retorna un diccionari amb els paràmetres actuals de la sessió."""
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
        return {
            'hdmi': hdmi_actual,
            'guide_lang': self.combo_guia.currentData() or self.default_guide_lang,
            'interface_lang': getattr(self, 'app_lang', 'ca')
        }

    def guardar_configuracio_fitxer(self):
        """Desa la configuració preservant TOTA l'estructura completa del fitxer base."""
        global CONFIG_FULL
        path, _filtre = QFileDialog.getSaveFileName(
            self, 
            self.t('dialog.save_config.title', 'Desar Configuració'), 
            CONFIG_YAML_PATH, 
            self.t('dialog.config_filter', 'Configuració YAML (*.yaml *.yml)')
        )
        if not path:
            return

        import copy
        if os.path.exists(path):
            dades = carregar_configuracio_yaml(path)
        else:
            dades = copy.deepcopy(CONFIG_FULL)

        for clau_arrel in ('common', 'manual', 'auto'):
            if clau_arrel not in dades:
                dades[clau_arrel] = copy.deepcopy(CONFIG_FULL.get(clau_arrel, {}))

        estat = self.recollir_estat_actual()

        dades.setdefault('common', {}).setdefault('styles', {})['hdmi'] = estat['hdmi']
        dades.setdefault('common', {}).setdefault('interface', {})['language'] = estat['interface_lang']
        dades.setdefault('manual', {})['default_guide_language'] = estat['guide_lang']

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
        global i18n, _
        
        # 1. Canvi d'idioma d'interfície en calent
        nou_idioma = cfg.get('common', {}).get('interface', {}).get('language')
        if nou_idioma and str(nou_idioma).lower().strip() != getattr(self, 'app_lang', None):
            self.app_lang = str(nou_idioma).lower().strip()
            i18n = I18nManager(modul="manual", lang=self.app_lang, base_dir=DIR_I18N)
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
            else:
                self.combo_align_h.setCurrentText(hdmi['align_h'])
            if self.projector_window:
                self.projector_window.set_align_h(hdmi['align_h'])

        if 'align_v' in hdmi and hasattr(self, 'combo_align_v'):
            idx = self.combo_align_v.findData(hdmi['align_v'])
            if idx >= 0:
                self.combo_align_v.setCurrentIndex(idx)
            else:
                self.combo_align_v.setCurrentText(hdmi['align_v'])
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

        # 3. Idioma guia
        lang_guia = cfg.get('manual', {}).get('default_guide_language')
        if lang_guia and hasattr(self, 'combo_guia'):
            idx = self.combo_guia.findData(lang_guia.upper())
            if idx >= 0:
                self.combo_guia.setCurrentIndex(idx)

        self.actualitzar_pantalles()

    def retranslate_ui(self):
        """Actualitza tots els textos i botons de la finestra al nou idioma seleccionat."""
        self.setWindowTitle(self.t('window.title', '{title} 🎭 - Control Manual Multilingüe', title=self.app_title))
        if hasattr(self, 'projector_window') and self.projector_window:
            self.projector_window.setWindowTitle(self.t('projector.title', '{title} - Projector HDMI', title=self.app_title))

        # Botons superiors
        if hasattr(self, 'btn_open'): self.btn_open.setText(self.t('btn.open_srt', '📂 Obrir .SRT'))
        if hasattr(self, 'btn_save_cfg'): self.btn_save_cfg.setText(self.t('btn.save_config', '💾 Desar Config'))
        if hasattr(self, 'btn_load_cfg'): self.btn_load_cfg.setText(self.t('btn.load_config', '📂 Carregar Config'))
        if hasattr(self, 'btn_toggle_proj'): self.btn_toggle_proj.setText(self.t('btn.toggle_projector', '🖥️ Projector HDMI'))

        # Panell HDMI
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

        # Panell Idiomes
        if hasattr(self, 'config_box'):
            nom_grup = self.t('group.lang_config', "Configuració d'Idiomes, Colors i Ordre Vertical HDMI") if self.hdmi_enabled else self.t('group.lang_config_no_hdmi', "Configuració d'Idiomes i Colors")
            self.config_box.setTitle(nom_grup)
        if hasattr(self, 'lbl_guide_title'): self.lbl_guide_title.setText(self.t('lbl.guide_lang', 'Idioma Guia Operador:'))
        if hasattr(self, 'lbl_hdmi_title'): self.lbl_hdmi_title.setText(self.t('lbl.hdmi_project_langs', 'Idiomes a Projectar a HDMI:'))
        if hasattr(self, 'btn_move_up'): self.btn_move_up.setText(self.t('btn.move_up', '⬆️ Pujar'))
        if hasattr(self, 'btn_move_down'): self.btn_move_down.setText(self.t('btn.move_down', '⬇️ Baixar'))

        # Capçalera taula HDMI
        if hasattr(self, 'table_hdmi'):
            self.table_hdmi.setHorizontalHeaderLabels([
                self.t('table.hdmi.header.active_lang', 'Actiu i Idioma'),
                self.t('table.hdmi.header.sample', 'Mostra'),
                self.t('table.hdmi.header.action', 'Acció')
            ])

        # Controls de temps
        if hasattr(self, 'timer_box'): self.timer_box.setTitle(self.t('group.timer', 'Control Temporal de la Funció'))
        if hasattr(self, 'btn_start_stop'):
            self.btn_start_stop.setText(self.t('btn.stop', '⏹️ Stop') if self.session_started else self.t('btn.start', '▶️ Start'))
        if hasattr(self, 'btn_pause'):
            self.btn_pause.setText(self.t('btn.resume', '▶️ Reprendre') if self.is_paused else self.t('btn.pause', '⏸ Pause'))
        if hasattr(self, 'lbl_title_total'): self.lbl_title_total.setText(self.t('lbl.total_time', 'Temps Total:'))
        if hasattr(self, 'lbl_title_elapsed'): self.lbl_title_elapsed.setText(self.t('lbl.sub_elapsed', 'En frase:'))
        if hasattr(self, 'lbl_title_countdown'): self.lbl_title_countdown.setText(self.t('lbl.sub_countdown', 'Següent en (estimat):'))

        # Controls de navegació
        if hasattr(self, 'btn_prev'): self.btn_prev.setText(self.t('btn.prev', '⏮ Anterior (←)'))
        if hasattr(self, 'btn_blackout'): self.btn_blackout.setText(self.t('btn.blackout', '⬛ Blackout (B)'))
        if hasattr(self, 'btn_next'): self.btn_next.setText(self.t('btn.next', 'Següent (Espai / →) ⏭'))

        # Cerca
        if hasattr(self, 'lbl_search_title'): self.lbl_search_title.setText(self.t('lbl.search', '🔎 Cerca al guió:'))
        if hasattr(self, 'input_search'): self.input_search.setPlaceholderText(self.t('search.placeholder', 'Escriu per cercar sense projectar...'))
        if hasattr(self, 'btn_search'): self.btn_search.setText(self.t('btn.search_next', '🔍 Següent'))
        if hasattr(self, 'btn_confirm_search'): self.btn_confirm_search.setText(self.t('btn.search_project', '🚀 Projectar seleccionat'))

        # Capçalera taula de subtítols
        if hasattr(self, 'table'):
            self.table.setHorizontalHeaderLabels([
                self.t('table.grid.header.num', '#'),
                self.t('table.grid.header.time', 'Temps'),
                self.t('table.grid.header.text', 'Text del Subtítol')
            ])

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

        self.btn_start_stop.setText(self.t('btn.stop', '⏹️ Stop'))
        self.btn_start_stop.setStyleSheet("background-color: #C62828; color: white;")
        
        self.btn_pause.setEnabled(True)
        self.btn_pause.setText(self.t('btn.pause', '⏸️ Pause'))
        self.btn_pause.setStyleSheet("background-color: #e65100; color: white;")

    def confirm_and_stop_session(self):
        if not self.is_paused:
            self.timer.stop()

        reply = QMessageBox.question(
            self,
            self.t('dialog.stop_confirm.title', 'Confirmar Aturada (Stop)'),
            self.t('dialog.stop_confirm.msg', "Atenció: Es tancarà la sessió actual i es reiniciarà el temps total.\n\nEstàs segur que vols aturar la sessió?"),
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

        self.btn_start_stop.setText(self.t('btn.start', '▶️ Start'))
        self.btn_start_stop.setStyleSheet("background-color: #1b5e20; color: white;")

        self.btn_pause.setEnabled(False)
        self.btn_pause.setText(self.t('btn.pause', '⏸️ Pause'))
        self.btn_pause.setStyleSheet(f"background-color: {self.color_btn_bg}; color: #888888;")

    def toggle_pause(self):
        if not self.session_started:
            return

        if self.is_paused:
            self.is_paused = False
            self.timer.start()
            self.btn_pause.setText(self.t('btn.pause', '⏸️ Pause'))
            self.btn_pause.setStyleSheet("background-color: #e65100; color: white;")
        else:
            self.is_paused = True
            self.timer.stop()
            self.btn_pause.setText(self.t('btn.resume', '▶️ Reprendre'))
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
            self.lbl_sub_countdown.setText(self.t('lbl.time_end', 'FI'))

    def open_srt_file(self):
        file_path, _filtre = QFileDialog.getOpenFileName(
            self, 
            self.t('dialog.open_srt.title', 'Obrir SRT'), 
            "", 
            self.t('dialog.open_srt.filter', 'Subtítols SRT (*.srt)')
        )
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

        content = content.replace('\r\n', '\n').replace('\r', '\n')
        blocks = re.split(r'\n\s*\n', content.strip())
        self.subtitles = []
        detected_tag_set = set()

        for block in blocks:
            lines = [l.strip() for l in block.split('\n') if l.strip()]
            if len(lines) >= 2:
                sub_id = lines[0]
                time_str = lines[1]
                raw_text = "\n".join(lines[2:])

                times = time_str.split(' --> ')
                start_ms = srt_time_to_ms(times[0]) if len(times) > 0 else 0
                end_ms = srt_time_to_ms(times[1]) if len(times) > 1 else 0

                matches = re.findall(r'\[([A-Z]{2,4})\]\s*(.*?)(?=\n?\[[A-Z]{2,4}\]|$)', raw_text, re.DOTALL)

                texts_by_lang = {}
                if matches:
                    for lang, text in matches:
                        clean_lines = [l.strip() for l in text.strip().split('\n') if l.strip()]
                        texts_by_lang[lang] = "\n".join(clean_lines)
                        detected_tag_set.add(lang)
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
                nous_canals.append({'id': lang.lower(), 'label': label})
            self.emissor.actualitzar_canals(nous_canals)

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

    def render_grid(self):
        lang_guia = self.combo_guia.currentData() or (self.detected_langs[0] if self.detected_langs else 'UNI')
        
        self.table.setUpdatesEnabled(False)
        self.table.blockSignals(True)
        
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
            self.table.setRowHeight(row, 34)

        self.table.blockSignals(False)
        self.table.setUpdatesEnabled(True)

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
            self.display_box.setText(f"[{lang_guia}] {text_actiu}")

    def on_cell_clicked(self, row, column):
        self.set_active_sub(row)

    def set_active_sub(self, index):
        if 0 <= index < len(self.subtitles):
            self.is_blackout = False
            self.current_index = index
            self.table.selectRow(self.current_index)
            
            item = self.table.item(self.current_index, 0)
            if item:
                self.table.setCurrentItem(item)
                self.table.scrollToItem(item, QAbstractItemView.ScrollHint.PositionAtCenter)
            
            self.sub_elapsed_ms = 0
            self.actualitzar_pantalles()

    def actualitzar_pantalles(self):
        if self.is_blackout or not (0 <= self.current_index < len(self.subtitles)):
            return

        sub = self.subtitles[self.current_index]
        lang_guia = self.combo_guia.currentData() or 'UNI'

        text_guia = sub['texts'].get(lang_guia, "")
        self.display_box.setText(f"[{lang_guia}] {text_guia}")

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

        if self.web_enabled:
            for lang in self.detected_langs:
                text_lang = sub['texts'].get(lang, "")
                self.emissor.emetre(lang.lower(), text_lang)

    def blackout(self):
        if not self.is_blackout:
            self.is_blackout = True
            self.table.clearSelection()
            self.display_box.setText(self.t('preview.blackout', '[ BLACKOUT ]'))
            
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
