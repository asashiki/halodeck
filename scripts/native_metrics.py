"""Bounded local kernel history and read-only process details; never command lines."""
import asyncio
from collections import deque
import json
import math
from pathlib import Path
import subprocess
import time
from common import STATE, now, WORKSPACE

HISTORY = STATE / 'metrics-history.jsonl'
MAX_POINTS = 5760

def read_history(path=None, seconds=86400):
    path = path or HISTORY
    if not path.is_file(): return []
    cutoff = time.time() - seconds
    points = []
    with path.open() as stream:
        for line in deque(stream, maxlen=MAX_POINTS):
            try:
                p = json.loads(line)
                if not cutoff <= p['t'] <= time.time() + 1: continue
                if any(p.get(k) is not None and (not isinstance(p[k], (int,float)) or not math.isfinite(p[k])) for k in ('cpu','memory','disk','network')): continue
                points.append(p)
            except (ValueError, KeyError, TypeError): continue
    return points

def record(r, path=None):
    path = path or HISTORY
    point = {'t':time.time(), 'cpu':r['cpu_percent'],
             'memory':round(100*(1-r['memory_available']/r['memory_total']),2),
             'disk':round(100*(1-r['disk_free']/r['disk_total']),2),
             'network':r['network_rx']+r['network_tx']}
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a') as f: f.write(json.dumps(point,separators=(',',':'))+'\n')
    # Keep writes atomic for readers, at most one day's raw samples.
    if path.stat().st_size > 700000 or int(point['t']/15)%60 == 0:
        tmp=path.with_suffix('.tmp')
        tmp.write_text(''.join(json.dumps(p,separators=(',',':'))+'\n' for p in read_history(path)[-MAX_POINTS:]))
        tmp.replace(path)

def history(path=None):
    points = read_history(path,7200)
    buckets={}
    for p in points: buckets.setdefault(int(p['t']//60),[]).append(p)
    return [dict(t=rows[-1]['t'], **{k:sum(p[k] for p in rows if p.get(k) is not None)/len([p for p in rows if p.get(k) is not None]) if any(p.get(k) is not None for p in rows) else None for k in ('cpu','memory','disk','network')}) for rows in list(buckets.values())[-120:]]

def processes():
    raw=subprocess.check_output(['ps','-eo','pid=,user=,pcpu=,pmem=,rss=,comm='],text=True,timeout=4)
    rows=[]
    for line in raw.splitlines():
        p=line.split(None,5)
        if len(p)!=6:continue
        rows.append(dict(pid=int(p[0]),user=p[1],cpu=float(p[2]),memory=float(p[3]),rss=int(p[4])*1024,name=p[5][:80]))
    return {'top_cpu':sorted(rows,key=lambda p:p['cpu'],reverse=True)[:8], 'top_mem':sorted(rows,key=lambda p:p['rss'],reverse=True)[:8]}

_disk_cache={'at':0, 'items':[], 'error':None}

def disk_dirs():
    if time.monotonic()-_disk_cache['at']<300:return dict(_disk_cache)
    try:
        r=subprocess.run(['du','-x','-B1','--max-depth=1',str(WORKSPACE)],text=True,capture_output=True,timeout=12)
        rows=[]
        for line in r.stdout.splitlines():
            size,path=line.split('\t',1)
            if path!=str(WORKSPACE):rows.append({'name':Path(path).name,'bytes':int(size)})
        _disk_cache.update(at=time.monotonic(),items=sorted(rows,key=lambda p:p['bytes'],reverse=True)[:8],error='部分目录不可读取' if r.returncode else None)
    except (OSError,ValueError,subprocess.TimeoutExpired):_disk_cache.update(at=time.monotonic(),error='目录统计超时或不可读取')
    return dict(_disk_cache)

async def top(snapshot):
    p,d=await asyncio.gather(asyncio.to_thread(processes),asyncio.to_thread(disk_dirs))
    network=[]
    for line in Path('/proc/net/dev').read_text().splitlines()[2:]:
        name,fields=line.split(':',1)
        if name.strip()=='lo':continue
        f=fields.split();network.append({'name':name.strip(),'rx_bytes':int(f[0]),'tx_bytes':int(f[8])})
    return dict(p,observed_at=now(),resources=snapshot.get('resources',{}),disk_dirs=d['items'],disk_error=d['error'],network=network,cpu_scope='ps 显示进程启动以来的平均 CPU；系统卡片是最近采样区间')
