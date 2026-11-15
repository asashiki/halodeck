import sys,sqlite3,tempfile,unittest,json
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import backup
class BackupSafety(unittest.TestCase):
 def test_sqlite_online_export_keeps_committed_wal(self):
  with tempfile.TemporaryDirectory() as t:
   p=Path(t);source=p/'live.db';dest=p/'export/db'
   with sqlite3.connect(source) as db:
    db.execute('pragma journal_mode=WAL');db.execute('create table sample(value)');db.execute("insert into sample values('committed')");db.commit()
    backup.sqlite_export(source,dest)
    with sqlite3.connect(dest) as copy:self.assertEqual(copy.execute('select value from sample').fetchone()[0],'committed')
 def test_missing_workspace_never_contacts_or_initializes_repository(self):
  with patch('guard.inspect',return_value={'state':'WAITING_FOR_WORKSPACE'}),patch.object(backup,'run') as run:
   with self.assertRaises(RuntimeError):backup.backup()
   run.assert_not_called()
 def test_missing_repository_never_auto_initializes(self):
  with patch('guard.inspect',return_value={'state':'READY'}),patch.object(backup,'run',side_effect=RuntimeError('Missing repository')) as run:
   with self.assertRaises(RuntimeError):backup.backup()
   self.assertEqual(run.call_args.args,('snapshots','--json'))
 def test_missing_config_reports_unconfigured(self):
  with tempfile.TemporaryDirectory() as t,patch.object(backup,'PRIVATE',Path(t)):
   with self.assertRaises(RuntimeError):backup.configuration()
 def test_export_failure_restores_pause_marker(self):
  with tempfile.TemporaryDirectory() as t:
   root=Path(t);(root/'paused').mkdir();(root/'export').mkdir()
   with patch.object(backup,'ROOT',root),patch.object(backup,'EXPORT',root/'export'),patch.object(backup,'read_json',return_value={'heartbeat':__import__('time').time(),'children':{}}):
    with self.assertRaises(FileNotFoundError):backup.bolt_export()
   self.assertFalse((root/'paused/filebrowser').exists())
if __name__=='__main__':unittest.main()
