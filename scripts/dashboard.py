"""Allowlisted, read-only summaries for Homepage's native Custom API widgets."""
import asyncio
from contextlib import closing
import datetime as dt
import json
from pathlib import Path
import sqlite3
import time
from aiohttp import ClientTimeout
from common import ROOT, STATE, now, read_json, WORKSPACE, PORT

NAMES={'service-health':'服务巡检','inventory':'环境盘点','config-backup':'本地配置快照',
       'release-watch':'上游版本检查','overnight-soak':'长时观察','agent-task':'Agent 任务',
       'repo-inspect':'仓库盘点','background-demo':'后台任务演示','agent-preflight':'Agent 环境检查'}
STATUS={'succeeded':'已完成','failed':'失败','running':'执行中','queued':'排队中','canceled':'已取消','not_started':'未运行','waiting':'等待中','partially_succeeded':'部分完成'}

def human_bytes(value, rate=False):
    if value is None:return '未知'
    for unit in ['B','KiB','MiB','GiB','TiB']:
        if value<1024 or unit=='TiB':return f'{value:.1f} {unit}'+('/s' if rate else '')
        value/=1024

def date_label(value):
    if not value:return '尚无记录'
    try:
        value=dt.datetime.fromisoformat(value.replace('Z','+00:00'))
        return value.astimezone(dt.timezone(dt.timedelta(hours=8))).strftime('%m-%d %H:%M')
    except (ValueError,TypeError):return '未知'

def offsite_summary(backup, current=None):
    """A configured repository is not evidence of a successful recent snapshot."""
    if not backup.get('offsite'):return {'state':'未配置','age':'尚无异地快照'}
    state=backup.get('state')
    if state!='SUCCESS':
        return {'state':{'FAILED':'失败','FAIL':'失败','ERROR':'失败','RUNNING':'进行中'}.get(state,'未知'),
                'age':'最近一次未确认成功'}
    try:
        completed=dt.datetime.fromisoformat(backup['completed_at'].replace('Z','+00:00'))
        age=((current or dt.datetime.now(dt.timezone.utc))-completed).total_seconds()
        if age<0:raise ValueError('Future completion timestamp')
        threshold=float(backup.get('max_age_seconds',7200))
    except (KeyError,ValueError,TypeError):return {'state':'未知','age':'缺少有效快照时间'}
    label=f'{int(age/60)} 分钟前' if age<3600 else f'{age/3600:.1f} 小时前'
    return {'state':'成功' if age<=threshold else '超时','age':label}

def sql_scalar(path, query):
    if not path.is_file():return None
    with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=2)) as db:
        return db.execute(query).fetchone()[0]

def history():
    import native_metrics
    return native_metrics.history(STATE/'metrics-history.jsonl')

async def fetch_json(client,url,headers=None):
    async with client.get(url,headers=headers,timeout=ClientTimeout(total=4),allow_redirects=False) as response:
        if response.status!=200:raise ValueError('Upstream status '+str(response.status))
        return await response.json(content_type=None)

def local_summaries():
    folders=[p for p in (WORKSPACE/'projects').iterdir() if p.is_dir() and not p.name.startswith('.')]
    inbox=(WORKSPACE/'inbox')
    return {'projects':len(folders),'inbox_files':sum(p.is_file() for p in inbox.iterdir())}

async def collect(client,snapshot):
    endpoints=[]
    results=await asyncio.gather(*(fetch_json(client,url,headers) for _,url,headers in endpoints),return_exceptions=True)
    data={};errors=[]
    for (name,_,_),value in zip(endpoints,results):
        if isinstance(value,Exception):errors.append(name)
        else:data[name]=value
    try:local=await asyncio.to_thread(local_summaries)
    except Exception:local={};errors.append('local')
    try:points=await asyncio.to_thread(history)
    except Exception:points=[];errors.append('history')
    r=snapshot['resources'];checks=snapshot['services'];okay=sum(s['ok'] for s in checks)
    mem_pct=100*(1-r['memory_available']/r['memory_total'])
    disk_pct=100*(1-r['disk_free']/r['disk_total'])
    widgets={
      'overview':{'state':'工作区就绪' if snapshot['recovery']['state']=='READY' else '工作区恢复中','services':f'{okay} / {len(checks)} 在线','sample':date_label(snapshot['collected_at'])},
      'cpu':{'value':f"{r['cpu_percent']:.1f}%" if r['cpu_percent'] is not None else '未知','detail':str(r['cpu_visible'])+' 逻辑核','scope':'Linux 可见范围'},
      'memory':{'value':f'{mem_pct:.1f}%','detail':human_bytes(r['memory_available'])+' 可用','capacity':human_bytes(r['memory_total'])+' 总量'},
      'disk':{'value':f'{disk_pct:.1f}%','detail':human_bytes(r['disk_free'])+' 可用','capacity':human_bytes(r['disk_total'])+' 总量'},
      'network':{'value':human_bytes(r.get('network_rx'),True),'detail':human_bytes(r.get('network_tx'),True),'scope':'当前系统流量'},
      'code':{'projects':local.get('projects','未知'),'environment':'浏览器 VS Code','state':'运行中' if any(s['id']=='code' and s['ok'] for s in checks) else '不可用'},
      'files':{'files':local.get('inbox_files','未知'),'upload':'inbox 可读写','outputs':'项目与产物可下载'},
      'terminal':{'session':'tmux 持久会话','connection':'支持断线重连'},
      'services':{'healthy':okay,'total':len(checks),'state':'全部检查通过' if okay==len(checks) else '有异常服务'},
    }
    magpie=data.get('magpie',{});gateway=magpie.get('gateway',{})
    widgets['magpie']={'state':'网关在线' if gateway.get('running') else '状态未知','models':gateway.get('models','未知'),
       'providers':len(magpie['providers']) if 'providers' in magpie else '未知','note':'已登记渠道；账号额度请在管理页查看'}
    tunnel=data.get('tunnel',{})
    widgets['tunnel']={'state':'连接正常' if tunnel.get('status')==200 else '未知','connections':tunnel.get('readyConnections','未知'),'scope':'同一隧道的连接数'}
    backup=snapshot['backup'];restore=snapshot['restore']
    offsite=offsite_summary(backup)
    widgets['backup']={'offsite':offsite['state'],'age':offsite['age'],
        'last_offsite':date_label(backup.get('completed_at')) if backup.get('offsite') else '尚无成功快照',
        'local':'恢复验证通过' if restore.get('state')=='PASS' else '尚未验证','checked':str(restore.get('checked_files','未知'))+' 文件',
        'last_local':date_label(restore.get('completed_at'))}
    widgets['restore']={'workspace':'预期数据齐全' if snapshot['recovery']['state']=='READY' else '等待数据恢复',
        'alerts':'尚未接入','platform':'整机恢复未验证','guard':'缺文件先等待，保留旧快照'}
    catalog=read_json(ROOT/'config/catalog.json',[])
    widgets['catalog']={'items':[{'name':c['name'],'label':c['state'],'detail':c['value'],'requirements':c['requirements'],'recovery':c['recovery'],'request':c['request']} for c in catalog]}
    return {'collected_at':snapshot['collected_at'],'expires_after_seconds':60,'widgets':widgets,'history':points,
            'capacity_reduced':r.get('capacity_reduced',False),
            'source_errors':errors,'scope':r['scope'],'history_scope':'本机内核实时采样（15s），最近120分钟聚合；无数据不补造曲线'}
