# Build with: python -m PyInstaller BlueArchiveStudio.spec
from PyInstaller.utils.hooks import collect_submodules

a = Analysis(['app.py'], pathex=[], binaries=[], datas=[],
             hiddenimports=collect_submodules('keyboard'), hookspath=[],
             hooksconfig={}, runtime_hooks=[], excludes=[], noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='BlueArchiveStudio',
          debug=False, strip=False, upx=False, console=False)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='BlueArchiveStudio')
