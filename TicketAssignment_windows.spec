# PyInstaller spec for a single-file, drag-anywhere-and-run Windows .exe
# (one .exe, nothing else needed on the target machine).
#
# Usage (from PowerShell, in this directory, inside a venv with
# `pip install -r requirements.txt` already done):
#
#     python -m PyInstaller TicketAssignment_windows.spec
#
# Output: dist\Ticket Assignment.exe (this one file is the whole app --
# copy just it to another machine, nothing else required)

# -*- mode: python ; coding: utf-8 -*-

from PyInstaller.utils.hooks import collect_data_files

a = Analysis(
    ['name_selector.py'],
    pathex=[],
    binaries=[],
    # schema.sql is read at runtime (db.py's ensure_schema()); bundling it
    # here is what makes db.py's sys._MEIPASS-based path resolution work
    # inside the frozen app.
    #
    # sv_ttk (the ttk theme) ships its actual theme as non-Python resource
    # files (a .tcl script plus sprite-sheet PNGs) that PyInstaller's
    # import analysis can't discover on its own -- collect_data_files()
    # bundles those explicitly. Without this the app would still launch,
    # just silently fall back to the default Tk look with no error.
    datas=[('schema.sql', '.')] + collect_data_files('sv_ttk'),
    hiddenimports=[
        # pyodbc is a C-extension module; PyInstaller's static analysis
        # should find it automatically via name_selector.py -> db.py's
        # imports, but listing it explicitly costs nothing. If the built
        # app fails at launch with a pyodbc-related ModuleNotFoundError,
        # try `pyinstaller --collect-all pyodbc TicketAssignment_windows.spec`
        # instead.
        'pyodbc',
        'sv_ttk',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # python-tds is macOS/Linux-only in this app (see db.py) and won't
        # even be installed in a Windows build venv if requirements.txt's
        # platform markers were respected -- excluded explicitly in case
        # it somehow ended up installed anyway, to keep it out of the
        # bundle.
        'pytds',
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

# Onefile mode: a.binaries/a.datas go directly into EXE() (and there's no
# separate COLLECT() step) instead of being left out via
# exclude_binaries=True -- that's what makes this build one single .exe
# instead of an .exe next to an _internal folder it depends on. At launch,
# the bootloader unpacks everything to a temp dir (cleaned up afterward)
# and runs from there -- a small one-time-per-launch cost (usually well
# under a second) in exchange for true single-file portability.
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='Ticket Assignment',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,  # windowed app, no terminal window
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,  # drop an .ico file next to this spec and point this at it for a real icon
)
