"""Package only an explicit allowlist, never runtime/player data."""
import hashlib
import sys
import zipfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from app_config import VERSION
out=ROOT/'artifacts';out.mkdir(exist_ok=True)
dest=out/f'ScreenStocksAssistant-v{VERSION}-windows-x64.zip'
files=['dist/ScreenStocksAssistant.exe','README.md','docs/启用教程.md','docs/交易规则.md','LICENSE','config.example.json']
with zipfile.ZipFile(dest,'w',zipfile.ZIP_DEFLATED) as z:
    for name in files:
        arc=name.removeprefix('dist/')
        z.write(ROOT/name,arc)
    python_license=Path(sys.base_prefix)/'LICENSE.txt'
    if python_license.exists():z.write(python_license,'third-party/Python-LICENSE.txt')
    import PyInstaller
    for license_file in Path(PyInstaller.__file__).parent.parent.glob('pyinstaller-*.dist-info/licenses/*'):
        if license_file.is_file():z.write(license_file,'third-party/PyInstaller-'+license_file.name)
digest=hashlib.sha256(dest.read_bytes()).hexdigest()
(out/'SHA256SUMS.txt').write_text(f'{digest}  {dest.name}\n',encoding='utf-8')
print(dest)
