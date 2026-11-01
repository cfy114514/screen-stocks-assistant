import sys,tempfile,unittest,json
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import auto_trade as a
class Storage(unittest.TestCase):
    def test_transient_replace_recovers(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'state.json';p.write_text('{"old":true}')
            original=a.os.replace;calls=[]
            def replace(src,dst):
                calls.append(1)
                if len(calls)==1:raise PermissionError('busy')
                original(src,dst)
            with patch.object(a.os,'replace',side_effect=replace),patch.object(a.time,'sleep'):
                a.save(p,{'new':True},retry_replace=True)
            self.assertEqual(len(calls),2);self.assertEqual(json.loads(p.read_text()),{'new':True})
    def test_heartbeat_can_wait_but_submission_cannot(self):
        with tempfile.TemporaryDirectory() as d:
            t=a.Trader(Path(d),Path(d)/'state.json')
            with patch.object(a.os,'replace',side_effect=PermissionError('busy')),patch.object(a.time,'sleep'):
                t.persist(force=False)
                with self.assertRaises(PermissionError):t.persist(force=True)
    def test_command_not_repeated_by_storage_retry(self):
        with tempfile.TemporaryDirectory() as d:
            with patch.object(a.os,'replace',side_effect=PermissionError('busy')) as rename:
                with self.assertRaises(PermissionError):a.save(Path(d)/'buymax.json',{'execute':True})
                self.assertEqual(rename.call_count,1)
if __name__=='__main__':unittest.main()
