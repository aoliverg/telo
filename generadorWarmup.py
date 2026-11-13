import sys
import os
import tempfile
import subprocess
import yaml

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QLineEdit, QComboBox, QTextEdit,
    QFileDialog, QGroupBox, QMessageBox, QProgressBar
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QFont

try:
    from gtts import gTTS
    from gtts.lang import tts_langs
except ImportError:
    gTTS = None
    tts_langs = None

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_YAML_PATH = os.path.join(BASE_DIR, 'config.yaml')


def carregar_configuracio_yaml(path=CONFIG_YAML_PATH):
    if os.path.exists(path):
        try:
            with open(path, 'r', encoding='utf-8') as f:
                return yaml.safe_load(f) or {}
        except Exception:
            pass
    return {}


CONFIG_FULL = carregar_configuracio_yaml()
CONFIG_COMMON = CONFIG_FULL.get('common', {})
INTERFACE_CFG = CONFIG_COMMON.get('interface', {})
FALLBACK_LANGS = CONFIG_COMMON.get(
    'languages',
    {'ca': 'Català', 'es': 'Español', 'fr': 'Français', 'en': 'English', 'de': 'Deutsch', 'it': 'Italiano'}
)


class GeneradorWavThread(QThread):
    acabat = pyqtSignal(str)
    error = pyqtSignal(str)

    def __init__(self, text, lang, output_wav):
        super().__init__()
        self.text = text
        self.lang = lang
        self.output_wav = output_wav

    def run(self):
        if gTTS is None:
            self.error.emit("La llibreria 'gTTS' no està instal·lada. Executa: pip install gTTS")
            return

        tmp_mp3 = None
        try:
            os.makedirs(os.path.dirname(os.path.abspath(self.output_wav)), exist_ok=True)

            with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tf:
                tmp_mp3 = tf.name

            tts = gTTS(text=self.text, lang=self.lang)
            tts.save(tmp_mp3)

            cmd = [
                "ffmpeg", "-y", "-i", tmp_mp3,
                "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le",
                self.output_wav
            ]
            subprocess.run(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                check=True
            )
            self.acabat.emit(self.output_wav)

        except FileNotFoundError:
            self.error.emit("No s'ha trobat 'ffmpeg' al sistema. Instal·la'l (ex: sudo apt install ffmpeg).")
        except subprocess.CalledProcessError as e:
            err_msg = e.stderr.decode('utf-8', errors='ignore') if e.stderr else str(e)
            self.error.emit(f"Error executant ffmpeg:\n{err_msg}")
        except Exception as e:
            self.error.emit(f"Error generant l'àudio: {e}")
        finally:
            if tmp_mp3 and os.path.exists(tmp_mp3):
                try:
                    os.remove(tmp_mp3)
                except OSError:
                    pass


class GeneradorWarmupWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.worker = None

        self.app_title = INTERFACE_CFG.get('title', 'TELÓ')
        colors = INTERFACE_CFG.get('colors', {})
        self.color_bg = colors.get('background', '#121212')
        self.color_text = colors.get('text', '#E0E0E0')
        self.color_primary = colors.get('primary', '#00E676')
        self.color_secondary = colors.get('secondary', '#29B6F6')
        self.color_panel_bg = colors.get('panel_bg', '#1E1E1E')
        self.color_btn_bg = colors.get('button_bg', '#252525')
        self.color_btn_border = colors.get('button_border', '#444444')

        self.init_ui()

    def obtenir_tots_els_idiomes(self):
        """Obté tots els idiomes disponibles realment a gTTS (codi -> nom) ordenats per nom."""
        if tts_langs is not None:
            try:
                langs_dict = tts_langs()
                if langs_dict:
                    return dict(sorted(langs_dict.items(), key=lambda item: item[1].lower()))
            except Exception as e:
                print(f"⚠️ No s'han pogut consultar els idiomes de gTTS: {e}")
        return FALLBACK_LANGS

    def init_ui(self):
        self.setWindowTitle(f"{self.app_title} 🔊 - Generador d'Àudio Warmup (16kHz WAV)")
        self.resize(760, 540)

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
                border-radius: 4px;
                padding: 6px 14px;
                font-weight: bold;
            }}
            QPushButton:hover {{ background-color: {self.color_btn_border}; }}
            QPushButton:disabled {{ background-color: #1a1a1a; color: #555555; }}
            QLineEdit, QComboBox, QTextEdit {{
                background-color: {self.color_panel_bg};
                border: 1px solid {self.color_btn_border};
                color: {self.color_text};
                border-radius: 4px;
                padding: 5px;
            }}
            QProgressBar {{
                border: 1px solid {self.color_btn_border};
                border-radius: 4px;
                background-color: #1a1a1a;
                height: 12px;
            }}
            QProgressBar::chunk {{ background-color: {self.color_primary}; }}
        """)

        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setSpacing(12)
        main_layout.setContentsMargins(16, 16, 16, 16)

        # Títol
        lbl_title = QLabel(f"{self.app_title} 🔊 - Generador d'Àudios de Warmup")
        lbl_title.setFont(QFont("Arial", 14, QFont.Weight.Bold))
        lbl_title.setStyleSheet(f"color: {self.color_primary};")
        main_layout.addWidget(lbl_title)

        # 1. Fitxers d'entrada i sortida
        grp_files = QGroupBox("1. Selecció de Fitxers")
        files_layout = QVBoxLayout(grp_files)
        files_layout.setContentsMargins(12, 16, 12, 12)
        files_layout.setSpacing(10)

        # Entrada (.txt)
        row_in = QHBoxLayout()
        lbl_in = QLabel("Fitxer de text d'entrada:")
        lbl_in.setFixedWidth(160)
        self.input_txt_path = QLineEdit()
        self.input_txt_path.setPlaceholderText("Selecciona un fitxer .txt o escriu el text directament a sota...")
        btn_browse_in = QPushButton("📂 Examinar...")
        btn_browse_in.clicked.connect(self.seleccionar_fitxer_entrada)
        row_in.addWidget(lbl_in)
        row_in.addWidget(self.input_txt_path, 1)
        row_in.addWidget(btn_browse_in)
        files_layout.addLayout(row_in)

        # Sortida (.wav)
        row_out = QHBoxLayout()
        lbl_out = QLabel("Fitxer WAV de sortida:")
        lbl_out.setFixedWidth(160)
        self.output_wav_path = QLineEdit()
        self.output_wav_path.setPlaceholderText("Ruta de destinació (ex: ./warmups/warmup_fr.wav)")
        btn_browse_out = QPushButton("💾 Desa com a...")
        btn_browse_out.clicked.connect(self.seleccionar_fitxer_sortida)
        row_out.addWidget(lbl_out)
        row_out.addWidget(self.output_wav_path, 1)
        row_out.addWidget(btn_browse_out)
        files_layout.addLayout(row_out)

        # Idioma de la veu (tots els disponibles a gTTS)
        row_lang = QHBoxLayout()
        lbl_lang = QLabel("Idioma de la veu (TTS):")
        lbl_lang.setFixedWidth(160)
        self.combo_lang = QComboBox()
        self.combo_lang.setEditable(True)  # Permet escriure per filtrar ràpidament entre els +60 idiomes
        self.combo_lang.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)

        tots_idiomes = self.obtenir_tots_els_idiomes()
        for code, name in tots_idiomes.items():
            self.combo_lang.addItem(f"{name} ({code})", str(code))

        # Selecciona per defecte el català (o l'idioma d'interfície configurat)
        lang_defecte = INTERFACE_CFG.get('language', 'ca').lower()
        idx_def = self.combo_lang.findData(lang_defecte)
        if idx_def >= 0:
            self.combo_lang.setCurrentIndex(idx_def)

        self.combo_lang.currentIndexChanged.connect(self.actualitzar_nom_sortida_per_idioma)
        row_lang.addWidget(lbl_lang)
        row_lang.addWidget(self.combo_lang, 1)
        files_layout.addLayout(row_lang)

        main_layout.addWidget(grp_files)

        # 2. Previsualització / Edició del text
        grp_preview = QGroupBox("2. Contingut del Text a Sintetitzar (Editable)")
        preview_layout = QVBoxLayout(grp_preview)
        preview_layout.setContentsMargins(12, 16, 12, 12)

        self.text_edit = QTextEdit()
        self.text_edit.setPlaceholderText(
            "Aquí es carregarà el contingut del fitxer de text seleccionat.\n"
            "Recomanació: un paràgraf de 2 o 3 frases (uns 6-8 segons de locució) amb signes de puntuació."
        )
        preview_layout.addWidget(self.text_edit)
        main_layout.addWidget(grp_preview, 1)

        # 3. Barra d'estat i botó d'acció
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 0)
        self.progress_bar.hide()
        main_layout.addWidget(self.progress_bar)

        bottom_layout = QHBoxLayout()
        self.lbl_status = QLabel(f"Preparat ({len(tots_idiomes)} idiomes disponibles).")
        self.lbl_status.setStyleSheet(f"color: {self.color_secondary}; font-weight: bold;")
        bottom_layout.addWidget(self.lbl_status, 1)

        self.btn_generate = QPushButton("🔊 Generar Arxiu WAV (16kHz Mono)")
        self.btn_generate.setStyleSheet("background-color: #1b5e20; color: white; padding: 10px 20px; font-size: 11pt;")
        self.btn_generate.clicked.connect(self.iniciar_generacio)
        bottom_layout.addWidget(self.btn_generate)

        main_layout.addLayout(bottom_layout)

        # Ruta per defecte inicial
        default_dir = os.path.join(BASE_DIR, "warmups")
        lang_init = self.combo_lang.currentData() or "ca"
        self.output_wav_path.setText(os.path.join(default_dir, f"warmup_{lang_init}.wav"))

    def actualitzar_nom_sortida_per_idioma(self):
        current_out = self.output_wav_path.text().strip()
        lang = self.combo_lang.currentData()
        if not lang:
            return
        if not current_out or os.path.basename(current_out).startswith("warmup_"):
            folder = os.path.dirname(current_out) if current_out else os.path.join(BASE_DIR, "warmups")
            self.output_wav_path.setText(os.path.join(folder, f"warmup_{lang}.wav"))

    def seleccionar_fitxer_entrada(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Seleccionar fitxer de text",
            BASE_DIR,
            "Fitxers de text (*.txt *.srt *.md);;Tots els fitxers (*)"
        )
        if not path:
            return

        self.input_txt_path.setText(path)
        try:
            with open(path, 'r', encoding='utf-8', errors='ignore') as f:
                contingut = f.read().strip()
            self.text_edit.setPlainText(contingut)

            nom_base = os.path.splitext(os.path.basename(path))[0]
            dest_suggerit = os.path.join(BASE_DIR, "warmups", f"{nom_base}.wav")
            self.output_wav_path.setText(dest_suggerit)
            self.lbl_status.setText(f"Fitxer carregat ({len(contingut.split())} paraules).")
        except Exception as e:
            QMessageBox.critical(self, "Error de lectura", f"No s'ha pogut llegir el fitxer:\n{e}")

    def seleccionar_fitxer_sortida(self):
        inicial = self.output_wav_path.text().strip() or os.path.join(BASE_DIR, "warmups", "warmup.wav")
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Desar fitxer WAV",
            inicial,
            "Àudio WAV (*.wav)"
        )
        if path:
            if not path.lower().endswith(".wav"):
                path += ".wav"
            self.output_wav_path.setText(path)

    def iniciar_generacio(self):
        text = " ".join(self.text_edit.toPlainText().split())
        out_wav = self.output_wav_path.text().strip()
        lang = self.combo_lang.currentData()

        if not lang:
            QMessageBox.warning(self, "Idioma invàlid", "Selecciona un idioma vàlid de la llista desplegable.")
            return

        if not text:
            QMessageBox.warning(self, "Text buit", "Selecciona un fitxer amb text o escriu alguna frase al quadre abans de generar l'àudio.")
            return

        if not out_wav:
            QMessageBox.warning(self, "Sense destinació", "Indica la ruta del fitxer WAV de sortida.")
            return

        if not out_wav.lower().endswith(".wav"):
            out_wav += ".wav"
            self.output_wav_path.setText(out_wav)

        self.btn_generate.setEnabled(False)
        self.progress_bar.show()
        self.lbl_status.setText(f"Sintetitzant veu ({lang}) i convertint a 16kHz PCM...")

        self.worker = GeneradorWavThread(text=text, lang=lang, output_wav=out_wav)
        self.worker.acabat.connect(self.on_generacio_acabada)
        self.worker.error.connect(self.on_generacio_error)
        self.worker.start()

    def on_generacio_acabada(self, wav_path):
        self.progress_bar.hide()
        self.btn_generate.setEnabled(True)
        self.lbl_status.setText(f"✅ Generat correctament: {wav_path}")
        QMessageBox.information(
            self,
            "Àudio generat",
            f"El fitxer de warmup s'ha creat correctament en format 16kHz Mono PCM:\n\n{wav_path}"
        )

    def on_generacio_error(self, missatge):
        self.progress_bar.hide()
        self.btn_generate.setEnabled(True)
        self.lbl_status.setText("⚠️ Error en la generació.")
        QMessageBox.critical(self, "Error", missatge)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    win = GeneradorWarmupWindow()
    win.show()
    sys.exit(app.exec())
