#!/usr/bin/env python3
"""Initialize and supervise a self-contained, loopback-only HaloDeck installation."""
import argparse,fcntl,hashlib,json,os,secrets,shutil,signal,subprocess,sys,time,urllib.request
from pathlib import Path
from common import ROOT,STATE,SOURCE,WORKSPACE,CONFIG,PORT,read_json,write_json

def call(args,**kw):return subprocess.run([str(x) for x in args],check=True,**kw)
def initialize(port,hostname):
 os.umask(0o077)
 if (ROOT/'settings.json').exists():raise SystemExit('Already initialized; edit settings.json explicitly. Existing data was preserved.')
 for p in (ROOT,STATE,ROOT/'private',ROOT/'apps',ROOT/'bin',ROOT/'vendor',WORKSPACE/'projects',WORKSPACE/'inbox',WORKSPACE/'artifacts',ROOT/'paused'):p.mkdir(parents=True,exist_ok=True,mode=0o700)
 shutil.copytree(SOURCE/'templates',ROOT/'config',dirs_exist_ok=True)
 for item in (ROOT/'config').rglob('*'):
  if item.suffix in ('.json','.yaml') and item.is_file():item.write_text(item.read_text().replace('?folder=/workspace','?folder='+str(WORKSPACE)))
 password=secrets.token_urlsafe(24)
 write_json(ROOT/'private/auth.json',{'username':'admin','password_sha256':hashlib.sha256(password.encode()).hexdigest()})
 (ROOT/'private/login.txt').write_text('username=admin\npassword='+password+'\n');(ROOT/'private/login.txt').chmod(0o600)
 (ROOT/'private/magpie-web-key').write_text(secrets.token_urlsafe(32));(ROOT/'private/magpie-web-key').chmod(0o600)
 write_json(ROOT/'settings.json',{'port':port,'hostname':hostname,'magpie_enabled':True,'backup_interval_hours':168,'backup_retry_hours':24,'backup_roots':[str(WORKSPACE)],'recovery_url':'/lab-assets/recovery.html'})
 (ROOT/'.workspace-id').write_text('halodeck-v1\n')
 write_json(ROOT/'config/expected-state.json',{'required':[{'path':str(ROOT/'.workspace-id'),'kind':'marker','value':'halodeck-v1','label':'Workspace'},{'path':str(ROOT/'private/auth.json'),'kind':'json','label':'Authentication'}]})
 services=[('terminal','终端',port+2,'/term/'),('code','VS Code',port+3,'/healthz'),('files','文件',port+4,'/workbench/files/'),('magpie','Magpie',port+5,'/')]
 write_json(ROOT/'config/services.json',[{'id':i,'name':n,'health_url':f'http://127.0.0.1:{p}{u}','health_codes':[200,302,401,403]} for i,n,p,u in services])
 write_json(ROOT/'config/catalog.json',[])
 print('Initialized '+str(ROOT)+'. Private login: '+str(ROOT/'private/login.txt'))

def prepare_apps():
 db=ROOT/'apps/filebrowser.db';fb=ROOT/'bin/filebrowser'
 if not db.exists():
  call([fb,'config','init','-d',db,'--root',WORKSPACE,'--address','127.0.0.1','--port',PORT+4,'--baseURL','/workbench/files','--auth.method','noauth','--disableExec'],stdout=subprocess.DEVNULL)
  call([fb,'users','add','owner',secrets.token_urlsafe(24),'-d',db,'--perm.admin=false','--perm.execute=false','--perm.share=false','--hideDotfiles'],stdout=subprocess.DEVNULL)
 # Add persistent application databases to the expected-state guard only during explicit preparation.
 manifest=read_json(ROOT/'config/expected-state.json');manifest['required']=[x for x in manifest['required'] if x['label']!='FileBrowser']+[{'path':str(db),'kind':'bolt','min_bytes':4096,'label':'FileBrowser'}];write_json(ROOT/'config/expected-state.json',manifest)

def commands():
 py=SOURCE/'.venv/bin/python';node=shutil.which('node')
 if not node:raise RuntimeError('Node.js is missing')
 return {'homepage':[node,str(ROOT/'vendor/homepage/node_modules/next/dist/bin/next'),'start','-H','127.0.0.1','-p',str(PORT+1)],
 'gateway':[str(py),str(SOURCE/'scripts/gateway.py')],
 'liveness':[str(py),str(SOURCE/'scripts/liveness.py'),'--daemon'],
 'terminal':[str(ROOT/'bin/ttyd'),'-i','127.0.0.1','-p',str(PORT+2),'-b','/term','-W','-O','-w',str(WORKSPACE),'tmux','-L','halodeck-'+hashlib.sha256(str(ROOT).encode()).hexdigest()[:8],'new-session','-A','-s','work'],
 'code':[str(ROOT/'bin/code-server/bin/code-server'),'--bind-addr','127.0.0.1:'+str(PORT+3),'--auth','none','--disable-telemetry','--user-data-dir',str(ROOT/'apps/code'),'--extensions-dir',str(ROOT/'apps/code-extensions'),str(WORKSPACE)],
 'filebrowser':[str(ROOT/'bin/filebrowser'),'-d',str(ROOT/'apps/filebrowser.db')],
 'magpie':[str(ROOT/'bin/magpie'),'web','--addr','127.0.0.1:'+str(PORT+5),'--no-open','--gateway'],
 'backup':[str(py),str(SOURCE/'scripts/backup.py'),'daemon']}

def health():
 data=read_json(STATE/'supervisor.json',{});pid=data.get('pid',0)
 try:alive=b'manage.py' in (Path('/proc')/str(pid)/'cmdline').read_bytes()
 except OSError:alive=False
 if not alive or time.time()-data.get('heartbeat',0)>15:return 1
 try:
  # A 401 challenge proves the private HTTP gateway is listening without exposing content.
  urllib.request.urlopen(f'http://127.0.0.1:{PORT}/',timeout=3)
 except urllib.error.HTTPError as e:return 0 if e.code==401 else 1
 except OSError:return 1
 return 1

def serve():
 from guard import inspect
 if inspect()['state']!='READY':raise SystemExit('Expected state missing; refusing to initialize empty databases.')
 with (STATE/'supervisor.lock').open('a') as lock:
  try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
  except BlockingIOError:return
  stop=False;children={};retry={};logs=ROOT/'logs';logs.mkdir(exist_ok=True)
  def stopping(*_):
   nonlocal stop
   stop=True
  signal.signal(signal.SIGTERM,stopping);signal.signal(signal.SIGINT,stopping)
  env={k:v for k,v in os.environ.items() if k in ('PATH','LANG','LC_ALL','TZ')}
  env.update(HALODECK_HOME=str(ROOT),HOME=str(ROOT/'apps/home'),HOMEPAGE_CONFIG_DIR=str(ROOT/'config/homepage-v2'),HOMEPAGE_ALLOWED_HOSTS='localhost,127.0.0.1:'+str(PORT+1),NEXT_TELEMETRY_DISABLED='1',NODE_ENV='production',MAGPIE_ADDR='127.0.0.1:'+str(PORT+6),MAGPIE_WEB_KEY=(ROOT/'private/magpie-web-key').read_text().strip(),XDG_CONFIG_HOME=str(ROOT/'apps/home/.config'))
  Path(env['HOME']).mkdir(parents=True,exist_ok=True)
  specs=commands()
  if not CONFIG.get('magpie_enabled',True):specs.pop('magpie')
  try:
   while not stop:
    for name,cmd in specs.items():
     child=children.get(name);paused=(ROOT/'paused'/name).exists()
     if child and child.poll() is None:
      if paused:child.terminate()
      continue
     if paused or time.time()<retry.get(name,0):continue
     with (logs/(name+'.log')).open('ab') as output:
      children[name]=subprocess.Popen(cmd,cwd=str(ROOT/'vendor/homepage') if name=='homepage' else str(ROOT),env=env,stdin=subprocess.DEVNULL,stdout=output,stderr=output,start_new_session=True)
     retry[name]=time.time()+15
    write_json(STATE/'supervisor.json',{'pid':os.getpid(),'heartbeat':time.time(),'children':{n:p.pid for n,p in children.items() if p.poll() is None}})
    time.sleep(1)
  finally:
   for child in children.values():
    if child.poll() is None:os.killpg(child.pid,signal.SIGTERM)
   for child in children.values():
    try:child.wait(timeout=15)
    except subprocess.TimeoutExpired:os.killpg(child.pid,signal.SIGKILL)

def main():
 ap=argparse.ArgumentParser();ap.add_argument('action',choices=['init','prepare-apps','serve','start','stop','health','register']);ap.add_argument('--port',type=int,default=8100);ap.add_argument('--hostname',default='localhost');a=ap.parse_args()
 if a.action=='init':
  if not 1024<=a.port<=65520:ap.error('port must be 1024..65520')
  if not __import__('re').fullmatch(r'[A-Za-z0-9.-]+',a.hostname):ap.error('hostname only, no scheme or path')
  return initialize(a.port,a.hostname)
 if a.action=='prepare-apps':return prepare_apps()
 if a.action=='health':return health()
 if a.action=='serve':return serve()
 if a.action=='stop':
  d=read_json(STATE/'supervisor.json',{});pid=d.get('pid',0)
  try:
   if b'manage.py' in (Path('/proc')/str(pid)/'cmdline').read_bytes():os.kill(pid,signal.SIGTERM)
  except OSError:pass
  return
 if a.action=='register':
  spec={'description':'HaloDeck workspace and backup supervisor','start':[sys.executable,str(SOURCE/'scripts/manage.py'),'start'],'check':[sys.executable,str(SOURCE/'scripts/manage.py'),'health'],'wait':60,'env':{'HALODECK_HOME':str(ROOT)}}
  # Revive receives HALODECK_HOME through an argv env wrapper, not a private host profile.
  spec.pop('env');spec['start']=['/usr/bin/env','HALODECK_HOME='+str(ROOT),'PATH='+os.environ.get('PATH','/usr/local/bin:/usr/bin:/bin')]+spec['start'];spec['check']=['/usr/bin/env','HALODECK_HOME='+str(ROOT),'PATH='+os.environ.get('PATH','/usr/local/bin:/usr/bin:/bin')]+spec['check']
  write_json(ROOT/'revive.json',spec)
  call([sys.executable,SOURCE/'integrations/revive/revive.py','register','halodeck','--file',ROOT/'revive.json']);return
 if a.action=='start':
  if health()==0:return
  (ROOT/'logs').mkdir(exist_ok=True)
  with (ROOT/'logs/supervisor.log').open('ab') as output:subprocess.Popen([sys.executable,str(SOURCE/'scripts/manage.py'),'serve'],env=dict(os.environ,HALODECK_HOME=str(ROOT)),stdin=subprocess.DEVNULL,stdout=output,stderr=output,start_new_session=True)
if __name__=='__main__':sys.exit(main() or 0)
