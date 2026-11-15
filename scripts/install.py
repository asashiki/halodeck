#!/usr/bin/env python3
"""Install pinned upstream tools into this instance, without starting services."""
import bz2,hashlib,io,json,os,platform,subprocess,sys,tarfile,urllib.request,zipfile
from pathlib import Path
from common import ROOT,SOURCE

def main():
 if platform.system()!='Linux' or platform.machine()!='x86_64':raise SystemExit('This release supports Linux x86_64 only.')
 if not (ROOT/'settings.json').is_file():raise SystemExit('Run manage.py init first')
 for spec in json.loads((SOURCE/'binaries.lock.json').read_text()):
  name=spec['project']
  if name=='homepage':continue
  target=ROOT/'bin'/name
  if target.exists():continue
  data=urllib.request.urlopen(spec['url'],timeout=180).read()
  if hashlib.sha256(data).hexdigest()!=spec['sha256']:raise RuntimeError('Checksum mismatch: '+name)
  if name=='code-server':
   with tarfile.open(fileobj=io.BytesIO(data),mode='r:gz') as tf:
    folder=ROOT/'vendor/code-install';folder.mkdir(exist_ok=True)
    tf.extractall(folder,filter='data');next(folder.iterdir()).rename(target)
  else:
   if spec['asset'].endswith('.bz2'):data=bz2.decompress(data)
   elif spec['asset'].endswith('.zip'):
    with zipfile.ZipFile(io.BytesIO(data)) as z:data=z.read(next(n for n in z.namelist() if n.endswith('/rclone')))
   elif spec['asset'].endswith('.tar.gz'):
    with tarfile.open(fileobj=io.BytesIO(data)) as tf:data=tf.extractfile(next(m for m in tf.getmembers() if m.isfile() and Path(m.name).name==name)).read()
   target.write_bytes(data);target.chmod(0o700)
  print('Installed '+name+' '+spec['version'])
 hp=next(s for s in json.loads((SOURCE/'binaries.lock.json').read_text()) if s['project']=='homepage');folder=ROOT/'vendor/homepage'
 if subprocess.check_output(['node','--version'],text=True).strip()!=hp['node']:raise RuntimeError('Use the Node version in binaries.lock.json')
 if subprocess.check_output(['pnpm','--version'],text=True).strip()!=hp['pnpm']:raise RuntimeError('Use the pnpm version in binaries.lock.json')
 if not folder.exists():subprocess.run(['git','clone','--depth','1','--branch',hp['version'],hp['repo'],str(folder)],check=True)
 if subprocess.check_output(['git','rev-parse','HEAD'],cwd=folder,text=True).strip()!=hp['commit']:raise RuntimeError('Homepage commit mismatch')
 subprocess.run(['pnpm','install','--frozen-lockfile'],cwd=folder,check=True)
 subprocess.run(['pnpm','build'],cwd=folder,env=dict(os.environ,NEXT_TELEMETRY_DISABLED='1',HOMEPAGE_CONFIG_DIR=str(ROOT/'config/homepage-v2')),check=True)
 print('Installed; now run manage.py prepare-apps, then register with Revive.')
if __name__=='__main__':main()
