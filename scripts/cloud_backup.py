"""Authenticated, bounded backup status and asynchronous fixed-action endpoint."""
import json,time
from aiohttp import web
from common import ROOT,STATE,CONFIG,read_json,now
import panel_editor
from backup import BACKUP,PRIVATE

def status():
 configured=(PRIVATE/'repository.json').exists()
 return {'observed_at':now(),'offsite':read_json(PRIVATE/'repository.json',{}).get('offsite',False),'state':read_json(STATE/'backup-status.json',{}).get('state','UNKNOWN'),'last_success':read_json(BACKUP/'last-success.json',{}),'schedule':read_json(BACKUP/'scheduler.json',{}),'scheduler_healthy':time.time()-read_json(STATE/'supervisor.json',{}).get('heartbeat',0)<15 and configured,'verification':read_json(STATE/'restore-status.json',{}),'recovery_url':CONFIG.get('recovery_url','/lab-assets/recovery.html'),'scope':'加密增量备份；项目、应用配置与数据。云盘需由本机所有者独立授权。','interval_hours':CONFIG.get('backup_interval_hours',168)}
async def get_status(request):
 panel_editor.host_check(request);return web.json_response(status())
async def snapshot(request):
 panel_editor.host_check(request,True)
 if len(await request.content.read(1025))>1024:raise web.HTTPRequestEntityTooLarge(max_size=1024,actual_size=1025)
 if not status()['scheduler_healthy']:raise web.HTTPServiceUnavailable(text='请先配置备份仓库并启动服务')
 (BACKUP/'request').touch(mode=0o600);return web.json_response(dict(status(),requested=True))
def register(app):
 app.router.add_get('/lab-api/backup',get_status);app.router.add_post('/lab-api/backup/snapshot',snapshot)
