# -*- mode: python ; coding: utf-8 -*-

import os
from PyInstaller.utils.hooks import collect_submodules, collect_data_files

block_cipher = None

# 1. RUTES DEL PROJECTE
PROJECT_DIR = os.path.abspath(SPECPATH)
SRC_DIR = os.path.join(PROJECT_DIR, 'src')
PATHTH_LIST = [PROJECT_DIR, SRC_DIR]

# 2. FITXERS DE DADES COMPARTITS (config, plantilles web, recursos)
shared_datas = [
    ('config.yaml', '.'),
]

# Incloure carpetes web comunes si existeixen (templates/static/web)
for extra_dir in ['templates', 'static', 'web']:
    dir_p = os.path.join(PROJECT_DIR, extra_dir)
    src_dir_p = os.path.join(SRC_DIR, extra_dir)
    if os.path.isdir(dir_p):
        shared_datas.append((dir_p, extra_dir))
    elif os.path.isdir(src_dir_p):
        shared_datas.append((src_dir_p, extra_dir))

# 3. IMPORTS COMPARTITS I OCULTS
# Mòduls d'àudio, matemàtiques, xarxa i els scripts a src/
common_hiddenimports = [
    'yaml',
    'numpy',
    'sounddevice',
    '_cffi_backend',
    'PyQt6.QtCore',
    'PyQt6.QtGui',
    'PyQt6.QtWidgets',
    'emissor',
    'utils',
    'TranscriptorWhisperLiveKit',
    'sincronitzador',
]

# ----------------------------------------------------
# A) ANÀLISI I EXECUTABLE: teloManual
# ----------------------------------------------------
a_manual = Analysis(
    ['teloManual.py'],
    pathex=PATHTH_LIST,
    binaries=[],
    datas=shared_datas,
    hiddenimports=common_hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz_manual = PYZ(a_manual.pure, a_manual.zipped_data, cipher=block_cipher)
exe_manual = EXE(
    pyz_manual,
    a_manual.scripts,
    [],
    exclude_binaries=True,
    name='teloManual',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
)

# ----------------------------------------------------
# B) ANÀLISI I EXECUTABLE: teloAuto
# ----------------------------------------------------
a_auto = Analysis(
    ['teloAuto.py'],
    pathex=PATHTH_LIST,
    binaries=[],
    datas=shared_datas,
    hiddenimports=common_hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz_auto = PYZ(a_auto.pure, a_auto.zipped_data, cipher=block_cipher)
exe_auto = EXE(
    pyz_auto,
    a_auto.scripts,
    [],
    exclude_binaries=True,
    name='teloAuto',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
)

# ----------------------------------------------------
# C) ANÀLISI I EXECUTABLE: teloSincro
# ----------------------------------------------------
a_sincro = Analysis(
    ['teloSincro.py'],
    pathex=PATHTH_LIST,
    binaries=[],
    datas=shared_datas,
    hiddenimports=common_hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz_sincro = PYZ(a_sincro.pure, a_sincro.zipped_data, cipher=block_cipher)
exe_sincro = EXE(
    pyz_sincro,
    a_sincro.scripts,
    [],
    exclude_binaries=True,
    name='teloSincro',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
)

# ----------------------------------------------------
# D) DISTRIBUCIÓ ÚNICA COMPARTIDA (COLLECT)
# ----------------------------------------------------
coll = COLLECT(
    exe_manual,
    a_manual.binaries,
    a_manual.zipfiles,
    a_manual.datas,

    exe_auto,
    a_auto.binaries,
    a_auto.zipfiles,
    a_auto.datas,

    exe_sincro,
    a_sincro.binaries,
    a_sincro.zipfiles,
    a_sincro.datas,

    strip=False,
    upx=True,
    upx_exclude=[],
    name='TELO_SUITE',
)
