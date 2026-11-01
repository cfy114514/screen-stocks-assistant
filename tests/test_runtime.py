import io
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import app_config

class Runtime(unittest.TestCase):
    def test_status_output_cannot_crash_on_english_windows(self):
        raw=io.BytesIO()
        stream=io.TextIOWrapper(raw,encoding='cp1252')
        with patch.object(sys,'stdout',stream):
            app_config.configure_text_streams()
            print('已提交，等待游戏确认。',flush=True)
        self.assertIn(b'\\u5df2',raw.getvalue())
        stream.detach()

if __name__=='__main__':unittest.main()
