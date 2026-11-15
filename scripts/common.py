"""Portable runtime paths; source and private mutable state are separate."""
import datetime as dt,json,os
from pathlib import Path
SOURCE=Path(__file__).resolve().parents[1]
ROOT=Path(os.environ.get('HALODECK_HOME',str(Path.home()/'.local/share/halodeck'))).expanduser().resolve()
STATE=ROOT/'state'
WORKSPACE=ROOT/'workspace'
def now():return dt.datetime.now(dt.timezone.utc).isoformat()
def read_json(path,default=None):
 try:return json.loads(Path(path).read_text())
 except (OSError,ValueError):return default
CONFIG=read_json(ROOT/'settings.json',{})
PORT=int(CONFIG.get('port',8100))
for field,default in [('backup_interval_hours',168),('backup_retry_hours',24)]:
 if not isinstance(CONFIG.get(field,default),int) or not 1<=CONFIG.get(field,default)<=8760:raise ValueError(field+' must be 1..8760 hours')
HOSTS={'127.0.0.1','localhost',CONFIG.get('hostname','localhost')}
def write_json(path,data):
 path=Path(path);path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
 tmp=path.with_suffix('.tmp');tmp.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n');tmp.chmod(0o600);tmp.replace(path)
