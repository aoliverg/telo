#!/usr/bin/env python3
"""
telo_srt_merger_gui.py
----------------------
Interfície gràfica PyQt6 per fusionar múltiples fitxers SRT monolingües
en un únic fitxer SRT multilingüe mitjançant sincronització temporal (timeline).
"""

import sys
import os
import re
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QTableWidget, QTableWidgetItem, QFileDialog,
    QHeaderView, QMessageBox, QGroupBox, QLineEdit, QAbstractItemView,
    QProgressBar
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QFont

# --- FUNCIONS DEL MOTOR DE SINCRONITZACIÓ TEMPORAL ---

def srt_time_to_ms(t_str):
    t_str = t_str.strip().replace('.', ',')
    parts = t_str.split(':')
    if len(parts) != 3:
        return 0
    h = int(parts[0])
    m = int(parts[1])
    s, ms = parts[2].split(',')
    return (h * 3600 + m * 60 + int(s)) * 1000 + int(ms)

def ms_to_srt_time(ms):
    if ms < 0:
        ms = 0
    h = ms // 3600000
    ms %= 3600000
    m = ms // 60000
    ms %= 60000
    s = ms // 1000
    ms %= 1000
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

def parse_single_srt(path):
    with open(path, 'r', encoding='utf-8', errors='ignore') as f:
        content = f.read().replace('\r\n', '\n').replace('\r', '\n')

    blocks = re.split(r'\n\s*\n', content.strip())
    subtitles = []

    for block in blocks:
        lines = [l.strip() for l in block.split('\n') if l.strip()]
        if len(lines) >= 2:
            time_idx = 1 if '-->' in lines[1] else 2
            if len(lines) > time_idx and '-->' in lines[time_idx]:
                time_str = lines[time_idx]
                times = time_str.split('-->')
                start_ms = srt_time_to_ms(times[0])
                end_ms = srt_time_to_ms(times[1])

                text_lines = lines[time_idx + 1:]
                clean_text = "\n".join(text_lines)
                subtitles.append({
                    "start_ms": start_ms,
                    "end_ms": end_ms,
                    "text": clean_text
                })
    return subtitles

def run_merge(files_dict, output_path, progress_callback=None):
    parsed = {}
    total_files = len(files_dict)
    for i, (lang, path) in enumerate(files_dict.items()):
        parsed[lang] = parse_single_srt(path)
        if progress_callback:
            progress_callback(int((i + 1) / (total_files + 3) * 30))

    langs = list(files_dict.keys())

    # 1. Punts temporals
    raw_points = set()
    for lang, subs in parsed.items():
        for s in subs:
            raw_points.add(s["start_ms"])
            raw_points.add(s["end_ms"])

    sorted_raw = sorted(list(raw_points))
    if not sorted_raw:
        raise ValueError("No s'han trobat subtítols vàlids als fitxers seleccionats.")

    clean_points = [sorted_raw[0]]
    for pt in sorted_raw[1:]:
        if pt - clean_points[-1] >= 180:
            clean_points.append(pt)

    # 2. Intervals
    raw_intervals = []
    total_intervals = len(clean_points) - 1
    for i in range(total_intervals):
        t_start = clean_points[i]
        t_end = clean_points[i + 1]
        t_mid = (t_start + t_end) // 2

        interval_texts = {}
        has_any_speech = False

        for lang in langs:
            active_text = ""
            for s in parsed[lang]:
                if s["start_ms"] <= t_mid <= s["end_ms"]:
                    active_text = s["text"]
                    break
            interval_texts[lang] = active_text
            if active_text:
                has_any_speech = True

        if has_any_speech:
            raw_intervals.append({
                "start": t_start,
                "end": t_end,
                "texts": interval_texts
            })

        if progress_callback and i % 50 == 0:
            pct = 30 + int((i / total_intervals) * 40)
            progress_callback(pct)

    # 3. Omplir buits propers (repetir text vigent)
    for item in raw_intervals:
        for lang in langs:
            if not item["texts"][lang]:
                for s in parsed[lang]:
                    if s["start_ms"] <= item["start"] and item["end"] <= s["end_ms"] + 1200:
                        item["texts"][lang] = s["text"]
                        break
                    elif abs(s["start_ms"] - item["start"]) <= 600:
                        item["texts"][lang] = s["text"]
                        break

    # 4. Fusionar idèntics consecutius
    compact_blocks = []
    for item in raw_intervals:
        if compact_blocks and all(compact_blocks[-1]["texts"][l] == item["texts"][l] for l in langs):
            compact_blocks[-1]["end"] = item["end"]
        else:
            compact_blocks.append(item)

    if progress_callback:
        progress_callback(85)

    # 5. Escriptura SRT
    final_lines = []
    sub_id = 1
    for b in compact_blocks:
        if not any(b["texts"][l].strip() for l in langs):
            continue

        time_line = f"{ms_to_srt_time(b['start'])} --> {ms_to_srt_time(b['end'])}"
        block = [str(sub_id), time_line]
        for lang in langs:
            txt = b["texts"][lang].strip()
            block.append(f"[{lang}] {txt}")

        final_lines.append("\n".join(block))
        sub_id += 1

    with open(output_path, 'w', encoding='utf-8') as f:
        f.write("\n\n".join(final_lines) + "\n")

    if progress_callback:
        progress_callback(100)

    return len(final_lines)


# --- FIL DE TREBALL EN SEGON PLA ---

class MergeWorker(QThread):
    finished_signal = pyqtSignal(int)
    error_signal = pyqtSignal(str)
    progress_signal = pyqtSignal(int)

    def __init__(self, files_dict, output_path):
        super().__init__()
        self.files_dict = files_dict
        self.output_path = output_path

    def run(self):
        try:
            count = run_merge(self.files_dict, self.output_path, self.progress_signal.emit)
            self.finished_signal.emit(count)
        except Exception as e:
            self.error_signal.emit(str(e))


# --- FINESTRA PRINCIPAL DE LA GUI ---

class TeloSrtMergerGUI(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("TELÓ 🎭 - Fusionador de Subtítols Multilingüe")
        self.resize(850, 620)
        self.init_ui()

    def init_ui(self):
        # Estil fosc integrat amb l'ecosistema TELÓ
        self.setStyleSheet("""
            QMainWindow { background-color: #121212; }
            QWidget { background-color: #121212; color: #E0E0E0; font-family: 'Segoe UI', Arial; font-size: 10pt; }
            
            QGroupBox { 
                border: 1px solid #444444; 
                border-radius: 6px; 
                margin-top: 16px; 
                font-weight: bold;
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
                border: 1px solid #444444; 
                border-radius: 4px; 
                padding: 6px 14px; 
                font-weight: bold; 
                min-height: 24px;
            }
            QPushButton:hover { background-color: #383838; border-color: #00E676; }
            QPushButton:disabled { background-color: #1a1a1a; color: #555555; border: 1px solid #222; }

            QTableWidget { 
                background-color: #1E1E1E; 
                gridline-color: #2A2A2A; 
                border: 1px solid #444444; 
                selection-background-color: #2E7D32;
                selection-color: #FFFFFF;
            }
            QHeaderView::section { 
                background-color: #252525; 
                color: #00E676; 
                font-weight: bold; 
                border: 1px solid #444444; 
                padding: 5px; 
            }

            QLineEdit {
                background-color: #1E1E1E;
                border: 1px solid #444444;
                border-radius: 4px;
                padding: 6px;
                color: #FFFFFF;
            }
            QLineEdit:focus { border: 1px solid #00E676; }

            QProgressBar {
                border: 1px solid #444444;
                border-radius: 4px;
                text-align: center;
                background-color: #1E1E1E;
                color: #FFFFFF;
                font-weight: bold;
            }
            QProgressBar::chunk {
                background-color: #2E7D32;
                border-radius: 3px;
            }
        """)

        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(20, 20, 20, 20)
        main_layout.setSpacing(14)

        # 1. Capçalera
        header_layout = QHBoxLayout()
        title = QLabel("TELÓ 🎭 Fusionador de Subtítols")
        title.setFont(QFont("Arial", 16, QFont.Weight.Bold))
        title.setStyleSheet("color: #00E676;")
        header_layout.addWidget(title)
        header_layout.addStretch()
        desc = QLabel("Sincronització intel·ligent per línia de temps")
        desc.setStyleSheet("color: #888888; font-style: italic;")
        header_layout.addWidget(desc)
        main_layout.addLayout(header_layout)

        # 2. Taula d'arxius d'entrada
        box_inputs = QGroupBox("Fitxers SRT d'Entrada (L'ordre defineix com es mostren a TELÓ)")
        layout_inputs = QHBoxLayout(box_inputs)
        layout_inputs.setContentsMargins(12, 20, 12, 12)

        self.table = QTableWidget()
        self.table.setColumnCount(3)
        self.table.setHorizontalHeaderLabels(["Codi Idioma", "Ruta de l'Arxiu .SRT", "Accions"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(0, 110)
        self.table.setColumnWidth(2, 90)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        layout_inputs.addWidget(self.table, 4)

        # Botons d'edició lateral
        btn_box = QVBoxLayout()
        btn_add = QPushButton("➕ Afegir SRT")
        btn_add.clicked.connect(self.afegir_fitxer)
        btn_box.addWidget(btn_add)

        self.btn_up = QPushButton("⬆️ Pujar")
        self.btn_up.clicked.connect(self.moure_amunt)
        btn_box.addWidget(self.btn_up)

        self.btn_down = QPushButton("⬇️ Baixar")
        self.btn_down.clicked.connect(self.moure_avall)
        btn_box.addWidget(self.btn_down)

        self.btn_del = QPushButton("🗑️ Eliminar")
        self.btn_del.setStyleSheet("QPushButton:hover { border-color: #ff5252; color: #ff5252; }")
        self.btn_del.clicked.connect(self.eliminar_fila)
        btn_box.addWidget(self.btn_del)

        btn_box.addStretch()
        layout_inputs.addLayout(btn_box, 1)

        main_layout.addWidget(box_inputs)

        # 3. Fitxer de sortida
        box_output = QGroupBox("Fitxer SRT Multilingüe Resultant")
        layout_output = QHBoxLayout(box_output)
        layout_output.setContentsMargins(12, 20, 12, 12)

        self.txt_output = QLineEdit()
        self.txt_output.setPlaceholderText("Selecciona on vols desar el fitxer .srt multilingüe...")
        layout_output.addWidget(self.txt_output, 4)

        btn_browse_out = QPushButton("📁 Examinar...")
        btn_browse_out.clicked.connect(self.triar_sortida)
        layout_output.addWidget(btn_browse_out, 1)

        main_layout.addWidget(box_output)

        # 4. Barra de progrés
        self.progress_bar = QProgressBar()
        self.progress_bar.setValue(0)
        self.progress_bar.setFixedHeight(22)
        self.progress_bar.setTextVisible(True)
        main_layout.addWidget(self.progress_bar)

        # 5. Botó d'acció principal
        self.btn_merge = QPushButton("🚀 Fusionar Subtítols en un únic .SRT")
        self.btn_merge.setFont(QFont("Arial", 11, QFont.Weight.Bold))
        self.btn_merge.setStyleSheet("""
            QPushButton {
                background-color: #2E7D32;
                color: #FFFFFF;
                padding: 10px;
                border-radius: 6px;
                min-height: 32px;
            }
            QPushButton:hover { background-color: #388E3C; }
            QPushButton:disabled { background-color: #1c3b1e; color: #777; }
        """)
        self.btn_merge.clicked.connect(self.iniciar_fusio)
        main_layout.addWidget(self.btn_merge)

    def afegir_fitxer(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "Seleccionar fitxers SRT", "", "Subtítols SRT (*.srt)")
        if not paths:
            return

        for path in paths:
            nom = os.path.basename(path).upper()
            # Suggeriment automàtic de codi d'idioma
            codi = "CAT"
            if any(k in nom for k in ["FR", "FRA", "CHUTE"]):
                codi = "FRA"
            elif any(k in nom for k in ["ES", "SPA", "CAIDA", "CAST"]):
                codi = "SPA"
            elif any(k in nom for k in ["EN", "ENG", "FALL"]):
                codi = "ENG"
            elif any(k in nom for k in ["CAT", "CAIGUDA"]):
                codi = "CAT"

            row = self.table.rowCount()
            self.table.insertRow(row)

            # Camp editable de codi d'idioma
            txt_lang = QLineEdit(codi)
            txt_lang.setAlignment(Qt.AlignmentFlag.AlignCenter)
            txt_lang.setMaxLength(5)
            self.table.setCellWidget(row, 0, txt_lang)

            # Ruta del fitxer
            item_path = QTableWidgetItem(path)
            item_path.setToolTip(path)
            self.table.setItem(row, 1, item_path)

            # Botó d'eliminar cel·la
            btn_remove = QPushButton("❌")
            btn_remove.setFixedWidth(40)
            btn_remove.clicked.connect(lambda _, r=row: self.eliminar_fila_index(r))
            
            cont = QWidget()
            c_lay = QHBoxLayout(cont)
            c_lay.setContentsMargins(0, 0, 0, 0)
            c_lay.setAlignment(Qt.AlignmentFlag.AlignCenter)
            c_lay.addWidget(btn_remove)
            self.table.setCellWidget(row, 2, cont)

    def eliminar_fila_index(self, row):
        self.table.removeRow(row)

    def eliminar_fila(self):
        curr = self.table.currentRow()
        if curr >= 0:
            self.table.removeRow(curr)

    def moure_amunt(self):
        curr = self.table.currentRow()
        if curr > 0:
            self.intercanviar_files(curr, curr - 1)
            self.table.selectRow(curr - 1)

    def moure_avall(self):
        curr = self.table.currentRow()
        if 0 <= curr < self.table.rowCount() - 1:
            self.intercanviar_files(curr, curr + 1)
            self.table.selectRow(curr + 1)

    def intercanviar_files(self, r1, r2):
        w1_code = self.table.cellWidget(r1, 0).text()
        w1_path = self.table.item(r1, 1).text()

        w2_code = self.table.cellWidget(r2, 0).text()
        w2_path = self.table.item(r2, 1).text()

        self.table.cellWidget(r1, 0).setText(w2_code)
        self.table.item(r1, 1).setText(w2_path)

        self.table.cellWidget(r2, 0).setText(w1_code)
        self.table.item(r2, 1).setText(w1_path)

    def triar_sortida(self):
        path, _ = QFileDialog.getSaveFileName(self, "Desar SRT Multilingüe", "subtitols-multilingue.srt", "Subtítols SRT (*.srt)")
        if path:
            self.txt_output.setText(path)

    def iniciar_fusio(self):
        if self.table.rowCount() < 2:
            QMessageBox.warning(self, "Atenció", "Cal afegir almenys dos fitxers SRT per fer la fusió multilingüe.")
            return

        out_path = self.txt_output.text().strip()
        if not out_path:
            QMessageBox.warning(self, "Atenció", "Has de triar una ruta de sortida per al fitxer resultant.")
            return

        files_dict = {}
        for r in range(self.table.rowCount()):
            lang_widget = self.table.cellWidget(r, 0)
            lang = lang_widget.text().strip().upper() if lang_widget else ""
            item_path = self.table.item(r, 1)
            path = item_path.text().strip() if item_path else ""

            if not lang or not path:
                QMessageBox.warning(self, "Atenció", f"La fila {r + 1} té camps buits.")
                return

            if lang in files_dict:
                QMessageBox.warning(self, "Atenció", f"El codi d'idioma '{lang}' està duplicat. Cada arxiu ha de tenir un codi únic.")
                return

            if not os.path.exists(path):
                QMessageBox.critical(self, "Error", f"No s'ha trobat el fitxer:\n{path}")
                return

            files_dict[lang] = path

        self.btn_merge.setEnabled(False)
        self.progress_bar.setValue(0)

        self.worker = MergeWorker(files_dict, out_path)
        self.worker.progress_signal.connect(self.progress_bar.setValue)
        self.worker.finished_signal.connect(self.on_fusio_completada)
        self.worker.error_signal.connect(self.on_fusio_error)
        self.worker.start()

    def on_fusio_completada(self, count):
        self.progress_bar.setValue(100)
        self.btn_merge.setEnabled(True)
        QMessageBox.information(
            self,
            "Èxit 🎭",
            f"S'ha generat correctament el fitxer multilingüe:\n\n"
            f"📁 {self.txt_output.text().strip()}\n\n"
            f"Total de subtítols combinats: {count}"
        )

    def on_fusio_error(self, err_msg):
        self.progress_bar.setValue(0)
        self.btn_merge.setEnabled(True)
        QMessageBox.critical(self, "Error en la fusió", f"S'ha produït un error:\n\n{err_msg}")


if __name__ == '__main__':
    app = QApplication(sys.argv)
    window = TeloSrtMergerGUI()
    window.show()
    sys.exit(app.exec())
