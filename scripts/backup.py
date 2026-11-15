#!/usr/bin/env python3
"""Encrypted incremental backups; explicit initialization, guarded sources, no pruning."""
import argparse,datetime,fcntl,hashlib,json,os,secrets,shutil,signal,sqlite3,subprocess,sys,tempfile,time,zipfile
from pathlib import Path
from contextlib import closing
from common import ROOT,STATE,SOURCE,WORKSPACE,CONFIG,now,read_json,write_json
PRIVATE=ROOT/'private/backup';BACKUP=STATE/'backup';EXPORT=BACKUP/'export'
SKIP={'node_modules','.venv','venv','__pycache__','.next','.pytest_cache','.cache','cache','Cache'}
def configuration():
 c=read_json(PRIVATE/'repository.json')
 if not c:raise RuntimeError('Backup is not configured. Run backup.py init explicitly.')
 return c

def run(*args):
 c=configuration();env={k:v for k,v in os.environ.items() if not k.startswith(('RESTIC_','RCLONE_'))}
 env.update(RESTIC_PASSWORD_FILE=str(PRIVATE/'password'),RCLONE_CONFIG=str(PRIVATE/'rclone.conf'),GOMAXPROCS='2')
 p=subprocess.run([str(ROOT/'bin/restic'),'--cache-dir',str(BACKUP/'cache'),'-r',c['repository'],'-o','rclone.program='+str(ROOT/'bin/rclone'),*map(str,args)],env=env,text=True,capture_output=True,timeout=7200)
 if p.returncode:
  (BACKUP/'last-error.log').write_text(p.stderr[-16000:]);(BACKUP/'last-error.log').chmod(0o600)
  raise RuntimeError('Backup command failed (exit '+str(p.returncode)+'); inspect private state/backup/last-error.log')
 return p.stdout

def inventory():
 roots=[ROOT/'config',ROOT/'apps',ROOT/'private',SOURCE]+[Path(p).expanduser().resolve() for p in CONFIG.get('backup_roots',[str(WORKSPACE)])]
 result=set()
 def walk(p):
  if p.name in SKIP or p==BACKUP or p.is_relative_to(BACKUP) or p==ROOT/'logs' or p.is_relative_to(ROOT/'logs') or p==ROOT/'vendor' or p.is_relative_to(ROOT/'vendor') or p==ROOT/'bin' or p.is_relative_to(ROOT/'bin'):return
  if p.is_symlink():result.add(p);return
  if p.is_dir():
   for child in p.iterdir():walk(child)
  elif p.is_file():result.add(p)
 for root in roots:
  if not root.exists():raise RuntimeError('Expected backup source missing: '+str(root))
  if root==Path('/'):raise RuntimeError('Backing up the entire filesystem is not supported')
  walk(root)
 # Minimal deployment identity and configuration are always included.
 result.update([ROOT/'settings.json',ROOT/'.workspace-id',ROOT/'revive.json'])
 return sorted(p for p in result if p.exists()),roots

def sqlite_export(source,dest):
 dest.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
 with closing(sqlite3.connect(source.as_uri()+'?mode=ro',uri=True,timeout=10)) as src,closing(sqlite3.connect(dest)) as dst:
  deadline=time.monotonic()+120
  def progress(*_):
   if time.monotonic()>deadline:raise TimeoutError('SQLite export timed out')
  src.backup(dst,pages=1024,progress=progress);dst.execute('pragma journal_mode=DELETE')
  if dst.execute('pragma integrity_check').fetchone()[0]!='ok':raise RuntimeError('SQLite export failed integrity check')

def bolt_export():
 source=ROOT/'apps/filebrowser.db';marker=ROOT/'paused/filebrowser'
 state=read_json(STATE/'supervisor.json',{})
 if time.time()-state.get('heartbeat',0)>15:raise RuntimeError('Supervisor must be healthy for a consistent FileBrowser export')
 owned=not marker.exists();marker.touch()
 try:
  deadline=time.monotonic()+30
  while read_json(STATE/'supervisor.json',{}).get('children',{}).get('filebrowser'):
   if time.monotonic()>deadline:raise RuntimeError('FileBrowser did not stop in time')
   time.sleep(.2)
  dest=EXPORT/'overlay'/str(source).lstrip('/');dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source,dest)
 finally:
  if owned:marker.unlink(missing_ok=True)
 return str(source)

def backup():
 from guard import inspect
 if inspect()['state']!='READY':raise RuntimeError('Expected workspace missing; backup refused')
 previous=json.loads(run('snapshots','--json'))
 if EXPORT.exists():shutil.rmtree(EXPORT)
 EXPORT.mkdir(mode=0o700)
 try:
  selected,roots=inventory();files=[];dbs=[]
  for p in selected:
   if not p.is_symlink():
    with p.open('rb') as f:header=f.read(16)
    if header==b'SQLite format 3\x00':sqlite_export(p,EXPORT/'overlay'/str(p).lstrip('/'));dbs.append(str(p));continue
   files.append(p)
  bolt=bolt_export();omit={bolt}|{p+x for p in dbs for x in ('-wal','-shm','-journal')}
  files=[p for p in files if str(p) not in omit]
  manifest={'created_at':now(),'roots':[str(p) for p in roots],'sqlite_online_exports':dbs,'filebrowser_paused_export':bolt,'overlay_relative_path':str(EXPORT/'overlay').lstrip('/'),'limitations':['External databases require their own export adapter. Symlinks are preserved, not followed. Runtime processes and OS packages are not restored automatically.'],'excluded_names':sorted(SKIP)}
  write_json(EXPORT/'manifest.json',manifest)
  files += [p for p in EXPORT.rglob('*') if p.is_file()]
  listing=BACKUP/'files.list';listing.write_bytes(b'\0'.join(os.fsencode(str(p)) for p in files)+b'\0')
  parent=['--parent',max(previous,key=lambda x:x['time'])['id']] if previous else []
  output=run('backup',*parent,'--files-from-raw',listing,'--tag','halodeck','--host','halodeck','--json')
  summary=next(json.loads(x) for x in output.splitlines() if json.loads(x).get('message_type')=='summary')
  write_json(BACKUP/'scope.json',manifest)
  return {'offsite':configuration()['offsite'],'state':'SUCCESS','completed_at':now(),'snapshot_id':summary['snapshot_id'],'files':summary['total_files_processed'],'bytes':summary['total_bytes_processed'],'added_bytes':summary['data_added'],'max_age_seconds':int(CONFIG.get('backup_interval_hours',168))*5400,'sqlite_exports':len(dbs)}
 finally:shutil.rmtree(EXPORT,ignore_errors=True);(BACKUP/'files.list').unlink(missing_ok=True)

def do_backup():
 with (BACKUP/'task.lock').open('a') as lock:
  try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
  except BlockingIOError:raise RuntimeError('Backup already running')
  write_json(STATE/'backup-status.json',{'state':'RUNNING','offsite':configuration()['offsite'],'started_at':now()})
  try:result=backup()
  except Exception as e:
   write_json(STATE/'backup-status.json',{'state':'FAILED','offsite':configuration()['offsite'],'completed_at':now(),'error':type(e).__name__});raise
  write_json(STATE/'backup-status.json',result);write_json(BACKUP/'last-success.json',result)
  return result

def daemon():
 with (BACKUP/'daemon.lock').open('a') as lock:
  try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
  except BlockingIOError:return
  while True:
   enabled=(BACKUP/'enabled').exists();requested=(BACKUP/'request').exists()
   last=read_json(BACKUP/'last-success.json',{});attempt=read_json(BACKUP/'last-attempt.json',{}).get('time',0)
   try:stamp=datetime.datetime.fromisoformat(last['completed_at']).timestamp()
   except (KeyError,ValueError):stamp=0
   interval=int(CONFIG.get('backup_interval_hours',168))*3600;retry=int(CONFIG.get('backup_retry_hours',24))*3600
   if (requested or (enabled and time.time()>=max(stamp+interval,attempt+retry))) and (PRIVATE/'repository.json').exists():
    (BACKUP/'request').unlink(missing_ok=True);write_json(BACKUP/'last-attempt.json',{'time':time.time()})
    try:do_backup()
    except Exception as e:print(type(e).__name__+': '+str(e),flush=True)
   write_json(BACKUP/'scheduler.json',{'enabled':enabled,'heartbeat':time.time(),'next_at':datetime.datetime.fromtimestamp(max(stamp+interval,attempt+retry),datetime.timezone.utc).isoformat() if enabled else None})
   time.sleep(10)

def main():
 os.umask(0o077);BACKUP.mkdir(parents=True,exist_ok=True,mode=0o700)
 ap=argparse.ArgumentParser();ap.add_argument('action',choices=['init','backup','daemon','kit','enable','disable','check','snapshots']);ap.add_argument('--repository');ap.add_argument('--rclone-config',type=Path);ap.add_argument('--output',type=Path);ap.add_argument('--local-test',action='store_true');args=ap.parse_args()
 if args.action=='init':
  if (PRIVATE/'repository.json').exists():ap.error('Already configured; never reinitialize or replace a recovery key')
  if not args.repository or (not args.local_test and not args.repository.startswith('rclone:')):ap.error('Use rclone:remote:path for offsite backups, or explicit --local-test')
  if not args.local_test and (not args.rclone_config or not args.rclone_config.is_file()):ap.error('--rclone-config is required')
  PRIVATE.mkdir(parents=True,exist_ok=True,mode=0o700)
  if args.rclone_config:shutil.copy2(args.rclone_config,PRIVATE/'rclone.conf')
  else:(PRIVATE/'rclone.conf').write_text('')
  (PRIVATE/'password').write_text(secrets.token_urlsafe(48)+'\n')
  write_json(PRIVATE/'repository.json',{'repository':args.repository,'offsite':not args.local_test,'export_path':str(EXPORT).lstrip('/')})
  run('init');print('Repository initialized. Export a recovery kit before enabling the schedule.');return
 if args.action=='backup':print(json.dumps(do_backup()));return
 if args.action=='daemon':return daemon()
 if args.action=='kit':
  configuration()
  if not args.output or args.output.exists():ap.error('--output must be a new file outside the source repository')
  if args.output.resolve().is_relative_to(SOURCE):ap.error('Never put recovery credentials in the source checkout')
  with zipfile.ZipFile(args.output,'w',zipfile.ZIP_DEFLATED) as z:
   for name in ('password','rclone.conf','repository.json'):z.write(PRIVATE/name,'halodeck-recovery/'+name)
   for name,source in [('recover.py',SOURCE/'scripts/recover.py'),('binaries.lock.json',SOURCE/'binaries.lock.json'),('RECOVERY.md',SOURCE/'docs/RECOVERY.md')]:z.write(source,'halodeck-recovery/'+name)
  args.output.chmod(0o600);print('Private recovery kit written. Store it outside this machine.');return
 if args.action=='enable':
  if not configuration()['offsite']:ap.error('Local test repositories cannot enable an offsite schedule')
  if not (BACKUP/'last-success.json').exists():ap.error('Complete a successful backup first')
  (BACKUP/'enabled').touch();return
 if args.action=='disable':(BACKUP/'enabled').unlink(missing_ok=True);return
 if args.action=='check':print(run('check'));return
 if args.action=='snapshots':
  for s in json.loads(run('snapshots','--json')):print(s['short_id'],s['time'])
if __name__=='__main__':main()
