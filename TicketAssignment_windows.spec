# PyInstaller spec for the Windows build.
#
# Usage (from PowerShell, in this directory, inside a venv with
# `pip install -r requirements.txt` already done):
#
#     python -m PyInstaller TicketAssignment_windows.spec
#
# Output: dist\Ticket Assignment\ -- the launcher .exe plus the
# _internal folder it needs beside it. Deploy with install.ps1, which
# copies that folder somewhere per-user and makes a Start Menu shortcut,
# so nobody has to see _internal.
#
# This is "onedir" rather than PyInstaller's single-file mode on purpose:
# onefile re-extracts the whole bundle to a temp directory on every
# launch, which measured ~1.9s to first window versus ~0.46s here.

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
        # OpenSSL, ~6MB of libcrypto-3.dll + libssl-3.dll, pulled in by
        # these two C extensions. Nothing on Windows needs it: pyodbc
        # talks to SQL Server through the ODBC driver, which does its own
        # TLS via Schannel, and the app makes no other network calls.
        #
        # Excluding the '_hashlib' C module (NOT the 'hashlib' wrapper,
        # which pyodbc does import and which falls back to Python's
        # built-in hash implementations) is what actually drops the DLLs.
        # Both it and _ssl must go, since either one alone keeps them.
        #
        # Do NOT copy the ssl/_ssl exclusion to the macOS spec: pyspnego
        # (NTLM domain login) imports ssl. python-tds itself does not --
        # it uses pyOpenSSL, and only when a cafile is supplied, which
        # db.py never does.
        '_hashlib', 'ssl', '_ssl',
        # Never imported by this app: verified by running it end to end
        # (connect, load, status dialog, save) and checking sys.modules.
        # unicodedata is Python's table, unrelated to Tk's own Unicode
        # handling, so removing it doesn't affect rendering.
        'unicodedata', '_zstd', 'lzma', '_lzma', 'bz2', '_bz2',
    ],
    noarchive=False,
)

# Tcl/Tk ships support data this app never touches:
#   tzdata   -- Tcl's own timezone database (609 files). Only used by
#               Tcl's `clock` command; all date handling here is Python's.
#   msgs     -- localised message catalogs for Tcl/Tk's own dialogs.
#               English-only app.
#   images   -- Tk's bundled sample/demo images.
#
# The `encoding` directory is deliberately KEPT: Tcl needs it to render
# non-ASCII text, and team member names can legitimately contain accented
# characters.
def _is_unused_tcl_data(entry):
    dest = entry[1].replace("\\", "/")
    return "/tzdata" in dest or "/msgs" in dest or "_tk_data/images" in dest


a.datas = [d for d in a.datas if not _is_unused_tcl_data(d)]

pyz = PYZ(a.pure)

# exclude_binaries=True keeps the binaries/datas out of the .exe so
# COLLECT below can lay them out in _internal\ beside it -- nothing is
# unpacked at launch, which is where the startup win comes from.
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='Ticket Assignment',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,  # windowed app, no terminal window
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,  # drop an .ico file next to this spec and point this at it for a real icon
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='Ticket Assignment',
)
