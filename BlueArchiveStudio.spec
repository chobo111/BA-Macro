# Build with: python -m PyInstaller --clean BlueArchiveStudio.spec
# Set BA_MACRO_ONEFILE=1 for a single executable.
import os
import sys
import runpy
from pathlib import Path
from PyInstaller.utils.win32.versioninfo import (
    VSVersionInfo, FixedFileInfo, StringFileInfo, StringTable, StringStruct,
    VarFileInfo, VarStruct,
)

# Do not collect unrelated DLLs (notably Poppler's ICU) from the host PATH.
windows = Path(os.environ['SystemRoot'])
os.environ['PATH'] = os.pathsep.join((str(Path(sys.executable).parent),
                                    str(windows / 'System32'), str(windows)))
onefile = os.environ.get('BA_MACRO_ONEFILE') == '1'
release = runpy.run_path(str(Path(SPECPATH) / 'version.py'))
app_icon = str(Path(SPECPATH) / 'assets' / 'app.ico')
version = release['VERSION']
version_numbers = tuple(int(part) for part in version.split('.'))
version_numbers += (0,) * (4 - len(version_numbers))
version_resource = VSVersionInfo(
    ffi=FixedFileInfo(filevers=version_numbers, prodvers=version_numbers,
                      mask=0x3f, flags=0, OS=0x40004, fileType=1, subtype=0, date=(0, 0)),
    kids=[StringFileInfo([StringTable('040904B0', [
        StringStruct('FileDescription', release['APP_NAME']),
        StringStruct('FileVersion', version),
        StringStruct('ProductName', release['APP_NAME']),
        StringStruct('ProductVersion', version),
        StringStruct('OriginalFilename', 'BA Macro.exe'),
    ])]), VarFileInfo([VarStruct('Translation', [0x409, 1200])])],
)

a = Analysis(['app.py'], pathex=[], binaries=[], datas=[],
             hiddenimports=[], hookspath=[], hooksconfig={}, runtime_hooks=[],
             excludes=['keyboard.__main__', 'keyboard._keyboard_tests', 'keyboard._mouse_tests',
                       'keyboard._darwinkeyboard', 'keyboard._darwinmouse',
                       'keyboard._nixcommon', 'keyboard._nixkeyboard', 'keyboard._nixmouse'],
             noarchive=False)
pyz = PYZ(a.pure)
if onefile:
    exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], name='BA Macro',
              debug=False, strip=False, upx=False, console=False, version=version_resource,
              icon=app_icon)
else:
    exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='BA Macro',
              debug=False, strip=False, upx=False, console=False, version=version_resource,
              icon=app_icon)
    coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='BA Macro')
