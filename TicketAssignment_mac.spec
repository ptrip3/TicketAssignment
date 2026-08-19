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
        # installed in every build venv per requirements.txt, unlike
        # pyodbc below.
        'spnego',
        # pyodbc is optional here (unlike on Windows, where it's required)
        # -- it's how macOS Domain Authentication (Kerberos) works, see
        # README's "Domain Authentication (Kerberos) on macOS" section.
        # Most Macs won't have it installed, which is fine: an unresolved
        # hiddenimport is just a build-time warning, not a failure, and
        # the app already handles pyodbc being unimportable at runtime
        # (falls back to pytds/SQL login -- see the top of
        # name_selector.py). If you *did* set up Kerberos support in this
        # build's venv, this is what makes sure it actually gets bundled.
        'pyodbc',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    noarchive=False,
)

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
