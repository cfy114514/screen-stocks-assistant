"""Paths shared by the source launcher and the frozen Windows build."""
import json
import os
import sys
from pathlib import Path

VERSION = '0.1.2'

def configure_text_streams():
    # A status message must not abort an order after its command was submitted.
    for stream in (sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, 'reconfigure'):
            stream.reconfigure(errors='backslashreplace')

configure_text_streams()
ASSETS = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent))
DATA = Path(os.environ.get('SSA_DATA_DIR') or Path(os.environ.get('LOCALAPPDATA', Path.home()/'AppData/Local'))/'ScreenStocksAssistant').resolve()
DEFAULT_GAME = Path(os.environ.get('USERPROFILE', str(Path.home())))/'AppData/LocalLow/Conradical Games/Screen Stocks'
CONFIG_PATH = DATA/'config.json'
DEFAULTS = {'game_dir': str(DEFAULT_GAME), 'game_managed_stocks': ['$BANK', '$BUBL'], 'manage_existing_shorts': False}
CONFIG = dict(DEFAULTS)
if CONFIG_PATH.exists():
    parsed = json.loads(CONFIG_PATH.read_text(encoding='utf-8-sig'))
    if not isinstance(parsed, dict): raise ValueError('config.json must contain an object')
    CONFIG.update(parsed)
GAME = Path(os.environ.get('SSA_GAME_DIR') or CONFIG['game_dir']).expanduser().resolve()
GAME_MANAGED = set(CONFIG['game_managed_stocks'])

def prepare_runtime():
    configure_text_streams()
    DATA.mkdir(parents=True, exist_ok=True)
    if not CONFIG_PATH.exists():
        CONFIG_PATH.write_text(json.dumps(CONFIG, ensure_ascii=False, indent=2), encoding='utf-8')
    if sys.stdout is None:
        sys.stdout = (DATA/'启动日志.txt').open('a', encoding='utf-8', buffering=1)
    if sys.stderr is None: sys.stderr = sys.stdout

def worker_command():
    if getattr(sys, 'frozen', False): return [sys.executable, '--trade-worker']
    return [sys.executable, str(ASSETS/'server.py'), '--trade-worker']
