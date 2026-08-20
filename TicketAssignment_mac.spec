# PyInstaller spec for a double-click-and-run macOS .app bundle.
#
# Must be run ON a Mac -- PyInstaller doesn't cross-compile, so this can't
# be built from Windows. See README.md's "Building the macOS app" section
# for the full setup/build/troubleshooting steps.
#
# Usage (from the Mac, in this directory, inside a venv with
# `pip install -r requirements.txt` already done):
#
#     pyinstaller TicketAssignment_mac.spec
#
# Output: dist/Ticket Assignment.app

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
        # pytds is pure Python and PyInstaller's static analysis should
        # find it automatically via name_selector.py -> db.py's imports,
        # but listing it explicitly costs nothing and guards against a
        # module PyInstaller's analyzer doesn't walk into on its own (its
        # SQL Browser resolution submodule in particular). If the built
        # app fails at launch with a pytds-related ModuleNotFoundError,
        # try `pyinstaller --collect-all pytds TicketAssignment_mac.spec`
        # instead.
        'pytds',
        'pytds.tds_base',
        'pytds.instance_browser_client',
        'sv_ttk',
        # spnego powers NTLM domain-login auth (db.py's _pytds_ntlm_auth())
        # -- listed explicitly since it's only ever imported lazily, deep
        # inside pytds.login.SpnegoAuth, not at module level anywhere
        # PyInstaller's analyzer would trivially see it. Should be
        # installed in every build venv per requirements.txt.
        'spnego',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Never imported here: verified against pytds and pyspnego, the
        # two macOS-specific dependencies, as well as the app itself.
        '_zstd', 'lzma', '_lzma', 'bz2', '_bz2',
        # NOTE: do not add 'unicodedata' here. It looks unused -- nothing
        # imports it directly and it never shows up in sys.modules -- but
        # Python's IDNA codec (encodings/idna.py) does `from unicodedata
        # import ucd_3_2_0`, and socket.getaddrinfo() encodes every
        # hostname through that codec. Excluding it produces
        # "LookupError: unknown encoding: idna" on any hostname
        # connection.
        # macOS connects only via python-tds (SQL Server login or NTLM),
        # so pyodbc is never used here even if it happens to be installed
        # in the build venv.
        'pyodbc',
        # NOTE: unlike the Windows spec, ssl/_ssl and _hashlib are NOT
        # excluded here. pyspnego (NTLM domain login) imports ssl, so
        # removing it would break domain authentication on macOS.
    ],
    noarchive=False,
)

# Same unused Tcl/Tk support data the Windows spec drops -- this is
# Tcl/Tk's own payload, so it's identical on both platforms:
#   tzdata  -- Tcl's timezone database (hundreds of files). Only Tcl's
#              `clock` command uses it; all dates here are Python's.
#   msgs    -- localised message catalogs for Tcl/Tk's built-in dialogs.
#   images  -- Tk's bundled sample images.
#
# The `encoding` directory is deliberately KEPT: Tcl needs it to render
# non-ASCII text, and names can contain accented characters.
def _is_unused_tcl_data(entry):
    dest = entry[1].replace("\\", "/")
    return "/tzdata" in dest or "/msgs" in dest or "_tk_data/images" in dest


a.datas = [d for d in a.datas if not _is_unused_tcl_data(d)]

pyz = PYZ(a.pure)

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
    target_arch=None,  # build for whatever arch this Mac is (arm64/x86_64)
    codesign_identity=None,
    entitlements_file=None,
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

app = BUNDLE(
    coll,
    name='Ticket Assignment.app',
    icon=None,  # drop an .icns file next to this spec and point this at it for a real icon
    bundle_identifier='com.ticketassignment.app',
    info_plist={
        'CFBundleName': 'Ticket Assignment',
        'CFBundleDisplayName': 'Ticket Assignment',
        'CFBundleShortVersionString': '2.0.0',
        'NSHighResolutionCapable': True,
        # Not a background/agent app -- show a normal Dock icon and menu bar.
        'LSUIElement': False,
    },
)
