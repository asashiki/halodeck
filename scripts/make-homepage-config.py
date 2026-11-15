"""Copy current display configuration for an isolated preview, preserving owner edits."""
import json,shutil
from pathlib import Path
from common import ROOT

def build(destination,port):
 source=ROOT/'config/homepage-v2';destination=Path(destination)
 for name in ('services.yaml','settings.yaml','widgets.yaml'):
  if not (source/name).is_file():raise RuntimeError('Expected display configuration missing')
 if source.resolve()==destination.resolve():return
 shutil.copytree(source,destination,dirs_exist_ok=True)
 p=destination/'services.yaml';data=json.loads(p.read_text())
 for group in data:
  for cards in group.values():
   for card in cards:
    value=next(iter(card.values()))
    if 'siteMonitor' in value:
     from urllib.parse import urlsplit
     value['siteMonitor']='http://127.0.0.1:'+str(port)+urlsplit(value['siteMonitor']).path
 p.write_text(json.dumps(data,ensure_ascii=False,indent=2))
