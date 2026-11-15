#!/usr/bin/env python3
"""Standalone recovery kit: download verified tools, restore to an EMPTY directory."""
import tempfile,argparse,bz2,hashlib,json,os,platform,shutil,sqlite3,subprocess,sys,urllib.request,zipfile,io
from pathlib import Path
BASE=Path(__file__).resolve().parent

def tools():
 if platform.system()!='Linux' or platform.machine() not in ('x86_64','AMD64'):
  raise SystemExit('This kit pins Linux x86_64 tools; obtain matching official binaries for other architectures.')
 directory=BASE/'tools';directory.mkdir(mode=0o700,exist_ok=True)
 for spec in json.loads((BASE/'binaries.lock.json').read_text()):
  name=spec['project']
  if name not in ('restic','rclone'):continue
  target=directory/name
  digestfile=directory/(name+'.sha256')
  if target.is_file() and digestfile.is_file() and hashlib.sha256(target.read_bytes()).hexdigest()==digestfile.read_text().strip():continue
  data=urllib.request.urlopen(spec['url'],timeout=120).read()
  if hashlib.sha256(data).hexdigest()!=spec['sha256']:raise SystemExit('Official download checksum mismatch')
  if name=='restic':binary=bz2.decompress(data)
  else:
   with zipfile.ZipFile(io.BytesIO(data)) as z:binary=z.read(next(n for n in z.namelist() if n.endswith('/rclone')))
  target.write_bytes(binary);target.chmod(0o700);digestfile.write_text(hashlib.sha256(binary).hexdigest())
 return directory

def main():
 ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--target',type=Path);ap.add_argument('--snapshot',default='latest');ap.add_argument('--list',action='store_true');a=ap.parse_args()
 os.umask(0o077)
 if not a.list and not a.target:ap.error('--target is required')
 if a.target and (a.target.is_symlink() or (a.target.exists() and any(a.target.iterdir()))):ap.error('Target must be empty, not a symlink; production files will never be overwritten')
 with tempfile.TemporaryDirectory(prefix='halodeck-recovery-cache-') as cache:
  directory=tools();env={k:v for k,v in os.environ.items() if not k.startswith(('RESTIC_','RCLONE_'))}
  env.update(RESTIC_PASSWORD_FILE=str(BASE/'password'),RCLONE_CONFIG=str(BASE/'rclone.conf'))
  command=[str(directory/'restic'),'--quiet','--cache-dir',cache,'-r',json.loads((BASE/'repository.json').read_text())['repository'],'-o','rclone.program='+str(directory/'rclone')]
  if a.list:subprocess.run(command+['snapshots','--compact'],env=env,check=True);return
  target=a.target.resolve();target.mkdir(mode=0o700,parents=True,exist_ok=True)
  subprocess.run(command+['restore',a.snapshot,'--target',str(target),'--verify'],env=env,check=True)
  export=target/json.loads((BASE/'repository.json').read_text())['export_path']
  manifest=json.loads((export/'manifest.json').read_text())
  overlay=export/'overlay'
  for f in overlay.rglob('*'):
   if f.is_symlink():raise SystemExit('Unexpected symlink in database overlay')
   if f.is_file():
    relative=f.relative_to(overlay);dest=target/relative
    if not dest.resolve().is_relative_to(target):raise SystemExit('Unsafe overlay destination')
    dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(f,dest)
    for suffix in ('-wal','-shm','-journal'):Path(str(dest)+suffix).unlink(missing_ok=True)
  for db in manifest['sqlite_online_exports']:
   dest=target/db.lstrip('/')
   with sqlite3.connect(dest.as_uri()+'?mode=ro',uri=True) as conn:
    if conn.execute('PRAGMA quick_check').fetchone()[0]!='ok':raise SystemExit('Restored SQLite integrity failure')
  print('RESTORE VERIFIED. Files are in '+str(target)+'. No service has been started; read RECOVERY.md before activation.')
if __name__=='__main__':main()
