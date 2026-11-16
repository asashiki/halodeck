/* HaloDeck — Homepage stays the renderer and canonical layout owner. */
(() => {
  if(window.__halodeck)return;window.__halodeck=true;
  const $=s=>document.querySelector(s), $$=s=>[...document.querySelectorAll(s)];
  const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const time=t=>new Date(t).toLocaleTimeString('zh-CN',{hour:'2-digit',minute:'2-digit',hour12:false});
  const full=t=>new Date(t).toLocaleString('zh-CN',{hour12:false});
  const bytes=(n,rate=false)=>{if(!Number.isFinite(n))return '未知';let i=0;while(n>=1024&&i<4){n/=1024;i++;}return n.toFixed(i?1:0)+' '+['B','KiB','MiB','GiB','TiB'][i]+(rate?'/s':'');};
  const duration=s=>{s=Math.round(s||0);return s>=86400?(s/86400).toFixed(1)+' 天':s>=3600?(s/3600).toFixed(1)+' 小时':s>=60?Math.floor(s/60)+' 分钟':s+' 秒';};
  const fresh=d=>d?.collected_at&&!d.stale&&Date.now()-Date.parse(d.collected_at)>=0&&Date.now()-Date.parse(d.collected_at)<60000;
  let installPrompt=null;
  window.addEventListener('beforeinstallprompt',e=>{e.preventDefault();installPrompt=e;});
  window.addEventListener('appinstalled',()=>{installPrompt=null;$('#hd-install-modal')?.close();toast('HaloDeck 已添加到此设备');});
  let data,snapshot,live,windowSize='24h',pinned=false,editing=false,editBase=null,original=[],saving=false;
  const keys=['cpu','memory','disk','network'],names={cpu:'CPU',memory:'内存',disk:'工作区磁盘',network:'网络'};
  async function request(url,options={}){
    const r=await fetch(url,{cache:'no-store',signal:AbortSignal.timeout(45000),...options});
    if(!r.ok){const e=Error(r.status===409?'首页已在别处修改，已同步最新排序，请重新调整。':'操作未完成，请稍后重试。');e.status=r.status;throw e;}
    try{return await r.json();}catch{throw Error('登录可能已过期，请刷新页面。');}
  }
  function dialog(id,title){
    let d=$('#'+id);if(d)return d;
    d=document.createElement('dialog');d.id=id;d.className='hd-dialog';d.setAttribute('aria-labelledby',id+'-title');
    d.innerHTML='<header><h2 id="'+id+'-title">'+title+'</h2><button type="button" class="hd-close" aria-label="关闭">×</button></header><div class="hd-dialog-body"></div>';
    document.body.append(d);d.querySelector('.hd-close').onclick=()=>d.close();d.addEventListener('click',e=>{if(e.target===d){const r=d.getBoundingClientRect();if(e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom)d.close();}});return d;
  }
  function toast(text){let n=$('#hd-toast');if(!n){n=document.createElement('div');n.id='hd-toast';n.setAttribute('role','status');document.body.append(n);}n.textContent=text;n.hidden=false;clearTimeout(n.timer);n.timer=setTimeout(()=>n.hidden=true,5500);}
  function setup(){
    if(!$('#layout-groups')||!$('#information-widgets')||!$('#app-terminal')){setTimeout(setup,150);return;}
    document.title='HaloDeck · 云端工作空间';
    const toolbar=document.createElement('div');toolbar.className='lab-toolbar';toolbar.innerHTML='<label class="lab-search"><span aria-hidden="true">⌕</span><input id="lab-search" type="search" placeholder="查找应用" aria-label="查找应用"><kbd>/</kbd></label>';
    toolbar.insertAdjacentHTML('beforeend','<button id="hd-install" title="安装到此设备" aria-label="安装到此设备"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3v12m-4-4 4 4 4-4M4 16v4h16v-4"/></svg></button>');
    $('#widgets-wrap').append(toolbar);$('#hd-install').onclick=showInstall;
    const bar=document.createElement('div');bar.id='lab-statusline';bar.setAttribute('role','status');$('#information-widgets').after(bar);
    const l=document.createElement('section');l.id='lab-liveness';l.setAttribute('aria-label','本机存活时间轴');
    l.innerHTML='<div class="lab-liveness-head"><div class="lab-liveness-title-wrap"><i class="lab-liveness-dot"></i><h2>本机存活</h2><span class="lab-liveness-badge" title="仅计算已有监测记录的时间段">读取中</span></div><div class="lab-liveness-tabs" role="group" aria-label="时间范围"><button data-window="24h" aria-pressed="true">24h</button><button data-window="7d" aria-pressed="false">7d</button></div></div><div class="lab-liveness-track" aria-label="存活区间"></div><div class="lab-liveness-axis"></div>';
    bar.after(l);
    const pop=document.createElement('div');pop.id='halodeck-liveness-popover';pop.setAttribute('role','tooltip');pop.hidden=true;document.body.append(pop);
    $$('[data-window]').forEach(b=>b.onclick=async()=>{windowSize=b.dataset.window;$$('[data-window]').forEach(x=>x.setAttribute('aria-pressed',String(x===b)));hidePopover();try{const w=windowSize;const v=await request('/lab-api/liveness?window='+w);if(w===windowSize){live=v;drawLive();}}catch{toast('暂时无法读取时间轴');}});
    keys.forEach(k=>{const card=$('#metric-'+k+' .service-card');if(!card)return;
      const value=document.createElement('div');value.className='hd-metric';value.innerHTML='<div class="hd-metric-main">—</div><div class="hd-metric-detail">等待采集</div><div class="lab-chart" data-metric="'+k+'" role="img"></div>';card.append(value);
      card.querySelectorAll('a').forEach(a=>a.setAttribute('href','/#metric-'+k));
      card.addEventListener('click',e=>{e.preventDefault();resource(k);});
      card.setAttribute('tabindex','0');card.setAttribute('role','button');card.setAttribute('aria-label',names[k]+'详情');card.onkeydown=e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();resource(k);}};
    });
    const footer=document.createElement('div');footer.className='lab-footer';footer.innerHTML='<span>HaloDeck <span class="hd-footer-dot">·</span> 你的云端工作空间</span><span></span>';$('#footer').prepend(footer);
    setupDock();setupOrdering();
    $('#lab-search').oninput=()=>{const q=$('#lab-search').value.toLowerCase().trim();$$('[id^="app-"],[id^="custom-"]').forEach(n=>n.hidden=!n.querySelector('.service-name')?.textContent.toLowerCase().includes(q));};
    document.addEventListener('keydown',e=>{if(e.key==='/'&&!['INPUT','TEXTAREA','SELECT'].includes(document.activeElement.tagName)&&!$('dialog[open]')){e.preventDefault();$('#lab-search').focus();}if(e.key==='Escape'){hidePopover();if(editing&&!saving)cancelEdit();}});
    document.addEventListener('click',e=>{if(!e.target.closest('.lab-liveness-seg')&&!e.target.closest('#halodeck-liveness-popover'))hidePopover();});
    window.addEventListener('scroll',hidePopover,{passive:true});
    const backup=$('#app-backup');backup?.addEventListener('click',e=>{if(editing)return;e.preventDefault();showBackup();});
    refresh();setInterval(refresh,15000);setInterval(checkFresh,2000);
  }
  function showInstall(){
    const d=dialog('hd-install-modal','安装 HaloDeck'),body=d.querySelector('.hd-dialog-body');
    const standalone=matchMedia('(display-mode: standalone)').matches||navigator.standalone;
    body.innerHTML=standalone?'<p>你正在独立应用窗口中使用 HaloDeck。</p>':'<p>把入口放到桌面或主屏幕，下次直接打开。</p>'+(installPrompt?'<button id="hd-install-now" class="hd-primary">安装到此设备</button>':'')+'<ol class="hd-recovery-steps"><li><strong>iPhone / iPad</strong>：用 Safari 打开本站，点分享 → 添加到主屏幕；如有“作为网页 App 打开”，将其开启。</li><li><strong>Mac Safari</strong>：文件 → 添加到程序坞（macOS Sonoma 或更新版本）。</li><li><strong>Chrome / Edge / Android</strong>：打开浏览器菜单，寻找“安装应用”或“添加到主屏幕”。不同版本名称可能略有不同。</li></ol><p class="hd-note">仍需联网并登录。安装会保存入口，不会把 VPS 的文件备份到这台设备。</p>';
    if(!d.open)d.showModal();
    $('#hd-install-now')?.addEventListener('click',async()=>{const e=installPrompt;if(!e)return;installPrompt=null;$('#hd-install-now').disabled=true;try{await e.prompt();const r=await e.userChoice;if(r.outcome==='accepted')d.close();else showInstall();}catch{showInstall();}});
  }
  function setupDock(){
    const dock=document.createElement('nav');dock.id='halodeck-dock';dock.setAttribute('aria-label','常用应用与桌面工具');
    for(const id of ['terminal','magpie','files','code']){
      const card=$('#app-'+id),source=card?.querySelector('.service-title-text')||card?.querySelector('a');if(!source)continue;
      const a=document.createElement('a');a.href=source.href;a.target='_blank';a.rel='noopener';a.title=card.querySelector('.service-name')?.textContent?.trim()||id;a.setAttribute('aria-label',a.title);a.innerHTML=card.querySelector('.service-icon')?.innerHTML||id;dock.append(a);
    }
    dock.insertAdjacentHTML('beforeend','<span class="hd-dock-separator"></span><button id="hd-edit" title="编辑布局" aria-label="编辑布局"><svg viewBox="0 0 24 24"><rect x="3" y="3" width="7" height="7" rx="2"/><rect x="14" y="3" width="7" height="7" rx="2"/><rect x="3" y="14" width="7" height="7" rx="2"/><path d="M14 17h7m-3.5-3.5v7"/></svg></button><button id="hd-wallpaper" title="壁纸与质感" aria-label="壁纸与质感"><svg viewBox="0 0 24 24"><rect x="3" y="3" width="18" height="18" rx="4"/><circle cx="9" cy="9" r="1.5"/><path d="m4 18 5-5 4 4 4-6 4 7"/></svg></button><button id="hd-settings" title="编辑首页" aria-label="编辑首页"><svg viewBox="0 0 24 24"><path d="M4 6h16M4 12h16M4 18h16"/><circle cx="9" cy="6" r="2"/><circle cx="16" cy="12" r="2"/><circle cx="8" cy="18" r="2"/></svg></button>');
    document.body.append(dock);$('#hd-edit').onclick=()=>editing?saveEdit():startEdit();$('#hd-wallpaper').onclick=()=>$('#lab-wallpaper-open')?.click();$('#hd-settings').onclick=openEditor;$('#hd-settings').addEventListener('pointerenter',preloadEditor,{once:true});
    const editbar=document.createElement('div');editbar.id='hd-edit-bar';editbar.hidden=true;editbar.innerHTML='<span>拖动应用调整顺序</span><button id="hd-save">完成并保存</button><button id="hd-cancel">取消</button>';document.body.append(editbar);for(const [id,action] of [['hd-save',saveEdit],['hd-cancel',cancelEdit]]){const button=$('#'+id);let touched=0;button.onpointerup=e=>{if(e.pointerType==='touch'){e.preventDefault();touched=Date.now();action();}};button.onclick=()=>{if(Date.now()-touched>700)action();};}
  }
  let refreshing=false,lastRender='';
  async function refresh(){
    if(document.hidden||refreshing)return;refreshing=true;
    try{
      const w=windowSize;const results=await Promise.allSettled(['/lab-api/dashboard','/lab-api/status','/lab-api/liveness?window='+w].map(u=>request(u)));
      if(results[0].status==='fulfilled')data=results[0].value;if(results[1].status==='fulfilled')snapshot=results[1].value;if(results[2].status==='fulfilled'&&w===windowSize)live=results[2].value;
      checkFresh();drawLive();
    }catch{checkFresh();}finally{refreshing=false;}
  }
  document.addEventListener('visibilitychange',()=>{if(!document.hidden){checkFresh();refresh();}});
  function checkFresh(){
    if(document.hidden)return;
    const valid=fresh(data)&&fresh(snapshot);document.documentElement.classList.toggle('lab-stale',!valid);
    const bar=$('#lab-statusline');if(!bar)return;
    const revision=String(data?.collected_at)+'|'+String(snapshot?.collected_at)+'|'+valid;
    if(revision===lastRender)return;lastRender=revision;
    const services=snapshot?.services||[],ok=services.filter(x=>x.ok).length;
    bar.classList.toggle('hd-attention',!valid||ok<services.length);
    bar.innerHTML='<span><i class="hd-status-dot '+(!valid?'unknown':ok<services.length?'warning':'')+'"></i>'+(!valid?'连接中断，等待重新采集':ok===services.length?'全部 '+ok+' 项服务在线':ok+' / '+services.length+' 项服务在线')+'</span><time>'+(!valid?'上次采集 ':'采集于 ')+(data?.collected_at?time(data.collected_at):'—')+'</time>';
    keys.forEach(k=>{
      const n=$('#metric-'+k+' .hd-metric');if(!n)return;const r=snapshot?.resources||{},w=data?.widgets?.[k]||{};
      n.querySelector('.hd-metric-main').textContent=valid?w.value:'未知';
      n.querySelector('.hd-metric-detail').textContent=valid?(k==='network'?'↑ '+w.detail:k==='cpu'?w.detail:w.detail):'等待采集恢复';
      const title=$('#metric-'+k+' .service-title');let capacity=title.querySelector('.hd-capacity');if(!capacity){capacity=document.createElement('span');capacity.className='hd-capacity';title.append(capacity);}capacity.textContent=valid?(k==='memory'?bytes(r.memory_total):k==='disk'?bytes(r.disk_total):k==='cpu'?r.cpu_visible+' 核':'↓ / ↑'):'';
      chart(n.querySelector('.lab-chart'),k,valid?data.history:[]);
    });
    $$('[id^="app-"], [id^="custom-"]').forEach(card=>{const id=card.id.slice(4);let badge=card.querySelector('.hd-app-dot');if(!badge){badge=document.createElement('i');badge.className='hd-app-dot';card.querySelector('.service-card')?.append(badge);}
      const health=services.find(x=>x.id===id);badge.dataset.state=id==='backup'?'local':!valid||!health?'unknown':health.ok?'online':'offline';badge.title=id==='backup'?'Google Drive 异地备份':!valid?'采集已过期':health?.ok?'入口可用':'入口尚未确认';});
    if(!valid){$('.lab-liveness-badge').textContent='状态未知';$('.lab-liveness-dot').dataset.state='unknown';hidePopover();}
  }
  function chart(node,k,points=[]){
    const p=points.filter(p=>Number.isFinite(p[k]));if(p.length<2){node.replaceChildren();node.setAttribute('aria-label','等待更多历史样本');return;}
    const max=k==='network'?Math.max(1,...p.map(v=>v[k])):100,start=p[0].t,end=p.at(-1).t;
    const pairs=p.map(v=>[((v.t-start)/Math.max(1,end-start)*300).toFixed(1),(38-v[k]/max*32).toFixed(1)].join(','));
    node.innerHTML='<svg viewBox="0 0 300 42" preserveAspectRatio="none" aria-hidden="true"><defs><linearGradient id="hd-'+k+'" x1="0" y1="0" x2="0" y2="1"><stop stop-color="currentColor" stop-opacity=".3"/><stop offset="1" stop-color="currentColor" stop-opacity="0"/></linearGradient></defs><polygon points="0,42 '+pairs.join(' ')+' 300,42" fill="url(#hd-'+k+')"/><polyline points="'+pairs.join(' ')+'" fill="none" stroke="currentColor" stroke-width="2" vector-effect="non-scaling-stroke"/></svg>';
    node.title='本机内核实测 · 截至 '+time(end*1000);node.setAttribute('aria-label',node.title+' · '+names[k]+'趋势');
  }
  function hidePopover(){pinned=false;const p=$('#halodeck-liveness-popover');if(p)p.hidden=true;}
  function showPopover(seg,node){
    const p=$('#halodeck-liveness-popover'),ev=seg.evidence||{};
    const evidence=[seg.boot_id?'boot_id '+seg.boot_id:'',ev.prev_boot_id?'boot_id '+ev.prev_boot_id+' → '+ev.new_boot_id:'',ev.delta_mono_sec!=null?'单调时钟 +'+ev.delta_mono_sec+'秒':'',ev.delta_wall_sec!=null?'墙钟 +'+ev.delta_wall_sec+'秒':'',seg.sample_count?'连续样本 '+seg.sample_count+' 次':''].filter(Boolean).join(' · ');
    p.innerHTML='<strong>'+esc(seg.label)+' · '+esc(duration(seg.duration_sec))+'</strong><p>'+esc(full(seg.start_ts*1000))+' — '+esc(full(seg.end_ts*1000))+'</p><p>'+esc(seg.reason||'')+'</p><small>'+esc(evidence)+'</small>';p.hidden=false;
    const r=node.getBoundingClientRect(),w=p.offsetWidth,h=p.offsetHeight;p.style.left=Math.max(12,Math.min(innerWidth-w-12,r.left+r.width/2-w/2))+'px';p.style.top=Math.max(12,r.top-h-12)+'px';
  }
  function drawLive(){
    if(!live)return;const valid=fresh(data)&&fresh(snapshot)&&Date.now()/1000-live.now_ts<65;
    const badge=$('.lab-liveness-badge'),dot=$('.lab-liveness-dot');dot.dataset.state=valid?live.current_state:'unknown';badge.textContent=!valid?'状态未知':live.current_state==='alive'&&live.observed_alive_ratio!=null?live.observed_alive_ratio.toFixed(2)+'%':live.current_state_label||'未知';
    const track=$('.lab-liveness-track');track.replaceChildren();
    for(const seg of live.segments||[]){
      const b=document.createElement('button');b.className='lab-liveness-seg seg-'+seg.kind;b.style.flexGrow=String(Math.max(.01,seg.duration_sec));b.setAttribute('aria-label',seg.label+'，'+duration(seg.duration_sec));
      b.onmouseenter=()=>{if(valid&&!pinned)showPopover(seg,b);};b.onfocus=()=>{if(valid)showPopover(seg,b);};b.onblur=()=>{if(!pinned)hidePopover();};b.onclick=e=>{e.stopPropagation();if(!valid)return;pinned=true;showPopover(seg,b);};track.append(b);
    }
    track.onmouseleave=()=>{if(!pinned)hidePopover();};$('.lab-liveness-axis').innerHTML='<span>'+(windowSize==='24h'?'24 小时前':'7 天前')+'</span><span>现在 '+time(live.now_ts*1000)+'</span>';
  }
  async function resource(key){
    const d=dialog('halodeck-resource-modal','本机资源');if(!d.open)d.showModal();const body=d.querySelector('.hd-dialog-body');body.innerHTML='<p role="status">正在读取本机资源…</p>';
    try{const v=await request('/lab-api/top');
      function render(k){
        body.innerHTML='<nav class="hd-tabs">'+keys.map(x=>'<button data-key="'+x+'" aria-pressed="'+(x===k)+'">'+names[x]+'</button>').join('')+'</nav><div class="hd-resource-content"></div>';
        body.querySelectorAll('[data-key]').forEach(b=>b.onclick=()=>render(b.dataset.key));const content=body.querySelector('.hd-resource-content');
        if(k==='cpu'||k==='memory'){
          const rows=v[k==='cpu'?'top_cpu':'top_mem'];content.innerHTML='<p class="hd-note">'+(k==='cpu'?esc(v.cpu_scope):'进程实际驻留内存（RSS），共享页可能重复计入。')+'</p><div class="hd-table-wrap"><table><thead><tr><th>进程</th><th>PID</th><th>'+(k==='cpu'?'CPU':'内存')+'</th></tr></thead><tbody>'+rows.map(r=>'<tr><td>'+esc(r.name)+'</td><td>'+r.pid+'</td><td>'+(k==='cpu'?r.cpu+'%':bytes(r.rss))+'</td></tr>').join('')+'</tbody></table></div>';
        }else if(k==='disk')content.innerHTML='<p class="hd-resource-summary">'+bytes(v.resources.disk_free)+' 可用 <span>/ '+bytes(v.resources.disk_total)+'</span></p><p class="hd-note">工作区一级目录 · 最多缓存 5 分钟</p>'+v.disk_dirs.map(r=>'<div class="hd-row"><span>'+esc(r.name)+'</span><strong>'+bytes(r.bytes)+'</strong></div>').join('')+(v.disk_error?'<p>'+esc(v.disk_error)+'</p>':'');
        else content.innerHTML='<p class="hd-resource-summary">↓ '+bytes(v.resources.network_rx,true)+'<br>↑ '+bytes(v.resources.network_tx,true)+'</p><p class="hd-note">网卡累计流量</p>'+v.network.map(n=>'<div class="hd-row"><strong>'+esc(n.name)+'</strong><span>↓ '+bytes(n.rx_bytes)+' · ↑ '+bytes(n.tx_bytes)+'</span></div>').join('');
        content.insertAdjacentHTML('beforeend','<p class="hd-note">采集于 '+full(v.observed_at)+' · Linux 可见范围</p>');
      }render(key);
    }catch(e){body.textContent=e.message;}
  }
  async function showBackup(){
    const d=dialog('halodeck-backup-modal','备份与恢复');if(!d.open)d.showModal();const body=d.querySelector('.hd-dialog-body');body.innerHTML='<p role="status">读取快照…</p>';
    try{renderBackup(await request('/lab-api/backup'));}catch(e){body.textContent=e.message;}
  }
  function renderBackup(v){
    const body=$('#halodeck-backup-modal .hd-dialog-body'),last=v.last_success||{};
    const state={SUCCESS:'最近备份成功',RUNNING:'正在上传备份',FAILED:'最近备份失败',UNKNOWN:'尚未完成备份'}[v.state]||'状态待确认';
    body.innerHTML='<section class="hd-backup-overview"><p class="hd-eyebrow">加密异地备份</p><h3>'+state+'</h3><p>'+esc(v.scope)+'</p><button class="hd-primary" id="hd-snapshot" '+(!v.scheduler_healthy||v.state==='RUNNING'?'disabled':'')+'>立即备份</button><p id="hd-backup-result" role="status"></p></section><h3>最近成功记录</h3><p>'+(last.snapshot_id?esc(full(last.completed_at))+' · '+bytes(last.bytes)+' · '+esc(String(last.files))+' 个文件':'首轮备份尚未完成')+'</p><p class="hd-note">'+(v.schedule?.enabled?'每 '+v.interval_hours+' 小时自动备份。':'授权云盘、完成备份与恢复验证后启用自动计划。')+'</p><h3>新机恢复</h3><p>'+(v.verification?.state==='PASS'?'数据库及抽查文件已通过云端恢复校验。':'正式业务恢复校验尚未完成。')+'真实新 VPS 部署仍需接手 Agent 核验依赖和登录状态。</p><a class="hd-text-link" href="'+v.recovery_url+'" target="_blank" rel="noopener">打开恢复说明 ↗</a>';
    $('#hd-snapshot').onclick=async e=>{e.target.disabled=true;try{await request('/lab-api/backup/snapshot',{method:'POST',headers:{'Content-Type':'application/json','X-Lab-Editor':'1'},body:'{}'});$('#hd-backup-result').textContent='已加入备份队列，稍后重新打开查看结果。';}catch(error){$('#hd-backup-result').textContent=error.message;e.target.disabled=false;}};
  }
  const cards=()=>$$('[id^="app-"],[id^="custom-"]');
  async function startEdit(){
    if(editing)return;
    try{editBase=await request('/lab-api/editor');}catch(e){toast(e.message);return;}
    original=cards().map(n=>({node:n,parent:n.parentElement}));editing=true;document.body.classList.add('halodeck-editing');$('#hd-edit-bar').hidden=false;$('#hd-edit').setAttribute('aria-pressed','true');$('#lab-search').value='';$('#lab-search').oninput();cards().forEach(n=>n.draggable=true);toast('拖动排序，也可聚焦应用后按 Alt + 方向键。');
  }
  function endEdit(){editing=false;saving=false;document.body.classList.remove('halodeck-editing');$('#hd-edit-bar').hidden=true;$('#hd-edit').setAttribute('aria-pressed','false');cards().forEach(n=>n.draggable=false);$('#hd-save').disabled=false;$('#hd-cancel').disabled=false;}
  function cancelEdit(){if(saving)return;original.forEach(o=>o.parent.append(o.node));endEdit();}
  async function saveEdit(){
    if(!editing||saving)return;saving=true;$('#hd-save').disabled=true;$('#hd-cancel').disabled=true;
    try{
      const latest=await request('/lab-api/editor');
      if(latest.revision!==editBase.revision){const e=Error('首页已在别处修改，已同步最新排序，请重新调整。');e.status=409;throw e;}
      const order=cards().map(n=>n.id),model=latest.model;
      model.groups.forEach(g=>g.items.sort((a,b)=>order.indexOf(a.id)-order.indexOf(b.id)));
      await request('/lab-api/editor',{method:'POST',headers:{'Content-Type':'application/json','X-Lab-Editor':'1'},body:JSON.stringify({revision:latest.revision,model})});endEdit();toast('桌面排序已保存');
    }catch(e){if(e.status===409){try{const latest=await request('/lab-api/editor');latest.model.groups.forEach(g=>g.items.forEach(item=>{const n=document.getElementById(item.id);n?.parentElement.append(n);}));endEdit();}catch{saving=false;$('#hd-save').disabled=false;$('#hd-cancel').disabled=false;}}else{saving=false;$('#hd-save').disabled=false;$('#hd-cancel').disabled=false;}toast(e.message);}
  }
  function setupOrdering(){
    let drag=null,press=null,origin=null,moved=false,lastTarget=null;
    cards().forEach(n=>{
      n.tabIndex=0;n.addEventListener('click',e=>{if(editing){e.preventDefault();e.stopImmediatePropagation();}},true);
      n.addEventListener('keydown',e=>{if(editing&&e.altKey&&['ArrowLeft','ArrowRight','ArrowUp','ArrowDown'].includes(e.key)){e.preventDefault();const prev=e.key==='ArrowLeft'||e.key==='ArrowUp',other=prev?n.previousElementSibling:n.nextElementSibling;if(other)prev?other.before(n):other.after(n);n.focus();}});
      n.addEventListener('dragstart',e=>{if(!editing){e.preventDefault();return;}drag=n;lastTarget=null;n.classList.add('hd-dragging');e.dataTransfer.effectAllowed='move';e.dataTransfer.setData('text/plain',n.id);});
      n.addEventListener('dragover',e=>{if(editing&&drag&&drag!==n&&drag.parentElement===n.parentElement){e.preventDefault();if(lastTarget===n)return;lastTarget=n;(n.compareDocumentPosition(drag)&Node.DOCUMENT_POSITION_PRECEDING)?n.after(drag):n.before(drag);}});
      n.addEventListener('drop',e=>{e.preventDefault();});n.addEventListener('dragend',()=>{n.classList.remove('hd-dragging');drag=null;});
      n.addEventListener('pointerdown',e=>{if(e.button!==0||saving)return;origin={x:e.clientX,y:e.clientY,id:e.pointerId,node:n};moved=false;
        if(!editing)press=setTimeout(()=>{startEdit();},550);else if(e.pointerType!=='mouse'){drag=n;lastTarget=null;n.setPointerCapture(e.pointerId);}
      });
      n.addEventListener('pointermove',e=>{if(!origin)return;if(Math.hypot(e.clientX-origin.x,e.clientY-origin.y)>8){clearTimeout(press);moved=true;}if(editing&&drag&&e.pointerType!=='mouse'){
        e.preventDefault();const other=document.elementFromPoint(e.clientX,e.clientY)?.closest('[id^="app-"],[id^="custom-"]');if(other&&other!==drag&&other.parentElement===drag.parentElement){if(lastTarget!==other){lastTarget=other;(other.compareDocumentPosition(drag)&Node.DOCUMENT_POSITION_PRECEDING)?other.after(drag):other.before(drag);}}}});
      const clear=e=>{clearTimeout(press);origin=null;if(e.pointerType!=='mouse')drag=null;};n.addEventListener('pointerup',clear);n.addEventListener('pointercancel',clear);
    });
  }
  const glassKey='halodeck.glass.v1';
  const glassLabel=v=>v<20?'经典磨砂':v<55?'轻柔玻璃':v<85?'液态玻璃':'极境透光';
  function getGlass(){try{const v=Number(localStorage.getItem(glassKey));return Number.isFinite(v)?Math.max(0,Math.min(100,Math.round(v))):65;}catch{return 65;}}
  function applyGlass(val=getGlass(),persist=false){
    const g=Math.max(0,Math.min(100,Math.round(Number(val)||0))),t=g/100,light=document.documentElement.dataset.hdTheme==='light',s=document.documentElement.style;
    const lerp=(a,b)=>a+(b-a)*t;
    s.setProperty('--hd-glass-blur',lerp(8,32).toFixed(1)+'px');
    s.setProperty('--hd-glass-sat',Math.round(lerp(110,205))+'%');
    s.setProperty('--hd-glass-bright',lerp(1.0,1.08).toFixed(3));
    if(light){
      s.setProperty('--hd-plate',`rgba(255,255,255,${lerp(.94,.44).toFixed(3)})`);
      s.setProperty('--hd-glass-bg',`linear-gradient(135deg,rgba(255,255,255,${lerp(.42,.82).toFixed(3)}) 0%,rgba(255,255,255,${lerp(.12,.32).toFixed(3)}) 48%,rgba(255,255,255,${lerp(.04,.16).toFixed(3)}) 100%),rgba(246,250,255,${lerp(.93,.26).toFixed(3)})`);
      s.setProperty('--hd-glass-hover',`linear-gradient(135deg,rgba(255,255,255,${lerp(.60,.94).toFixed(3)}) 0%,rgba(255,255,255,${lerp(.20,.46).toFixed(3)}) 50%,rgba(255,255,255,${lerp(.10,.26).toFixed(3)}) 100%),rgba(255,255,255,${lerp(.97,.44).toFixed(3)})`);
      s.setProperty('--hd-glass-border',`rgba(255,255,255,${lerp(.48,.92).toFixed(3)})`);
      s.setProperty('--hd-rim-grad',`linear-gradient(135deg,rgba(255,255,255,${lerp(.55,1).toFixed(3)}) 0%,rgba(255,255,255,${lerp(.25,.38).toFixed(3)}) 35%,rgba(255,255,255,${lerp(.15,.08).toFixed(3)}) 65%,rgba(255,255,255,${lerp(.40,.78).toFixed(3)}) 100%)`);
      s.setProperty('--hd-rim-hover',`linear-gradient(135deg,rgba(255,255,255,1) 0%,rgba(255,255,255,${lerp(.35,.58).toFixed(3)}) 36%,rgba(255,255,255,${lerp(.20,.16).toFixed(3)}) 65%,rgba(255,255,255,${lerp(.55,.92).toFixed(3)}) 100%)`);
      s.setProperty('--hd-glass-shadow',`inset 0 1px 1px rgba(255,255,255,${lerp(.55,.95).toFixed(3)}),0 ${Math.round(lerp(6,16))}px ${Math.round(lerp(18,38))}px rgba(15,23,42,${lerp(.06,.11).toFixed(3)})`);
      s.setProperty('--hd-icon-bg',`linear-gradient(145deg,rgba(255,255,255,${lerp(.50,.88).toFixed(3)}),rgba(235,244,255,${lerp(.25,.42).toFixed(3)}))`);
      s.setProperty('--hd-icon-border',`rgba(255,255,255,${lerp(.45,.86).toFixed(3)})`);
      s.setProperty('--hd-icon-shadow',`inset 0 1px 1px rgba(255,255,255,${lerp(.65,.98).toFixed(3)}),0 6px 14px rgba(15,23,42,.09)`);
    }else{
      s.setProperty('--hd-plate',`rgba(16,20,30,${lerp(.92,.36).toFixed(3)})`);
      s.setProperty('--hd-glass-bg',`linear-gradient(135deg,rgba(255,255,255,${lerp(.04,.20).toFixed(3)}) 0%,rgba(255,255,255,${lerp(.01,.05).toFixed(3)}) 45%,rgba(255,255,255,${lerp(0,.015).toFixed(3)}) 100%),rgba(14,18,28,${lerp(.90,.24).toFixed(3)})`);
      s.setProperty('--hd-glass-hover',`linear-gradient(135deg,rgba(255,255,255,${lerp(.08,.28).toFixed(3)}) 0%,rgba(255,255,255,${lerp(.02,.08).toFixed(3)}) 48%,rgba(255,255,255,${lerp(.01,.03).toFixed(3)}) 100%),rgba(20,26,40,${lerp(.94,.34).toFixed(3)})`);
      s.setProperty('--hd-glass-border',`rgba(255,255,255,${lerp(.09,.24).toFixed(3)})`);
      s.setProperty('--hd-rim-grad',`linear-gradient(135deg,rgba(255,255,255,${lerp(.14,.68).toFixed(3)}) 0%,rgba(255,255,255,${lerp(.06,.16).toFixed(3)}) 32%,rgba(255,255,255,${lerp(.02,.03).toFixed(3)}) 65%,rgba(255,255,255,${lerp(.08,.34).toFixed(3)}) 100%)`);
      s.setProperty('--hd-rim-hover',`linear-gradient(135deg,rgba(255,255,255,${lerp(.22,.86).toFixed(3)}) 0%,rgba(255,255,255,${lerp(.10,.25).toFixed(3)}) 34%,rgba(255,255,255,${lerp(.04,.07).toFixed(3)}) 65%,rgba(255,255,255,${lerp(.14,.48).toFixed(3)}) 100%)`);
      s.setProperty('--hd-glass-shadow',`inset 0 1px 1px rgba(255,255,255,${lerp(.06,.28).toFixed(3)}),0 ${Math.round(lerp(8,18))}px ${Math.round(lerp(20,42))}px rgba(0,0,0,${lerp(.24,.38).toFixed(3)})`);
      s.setProperty('--hd-icon-bg',`linear-gradient(145deg,rgba(255,255,255,${lerp(.12,.30).toFixed(3)}),rgba(255,255,255,${lerp(.03,.06).toFixed(3)}))`);
      s.setProperty('--hd-icon-border',`rgba(255,255,255,${lerp(.10,.30).toFixed(3)})`);
      s.setProperty('--hd-icon-shadow',`inset 0 1px 1px rgba(255,255,255,${lerp(.14,.46).toFixed(3)}),0 6px 16px rgba(0,0,0,.22)`);
    }
    if(persist){try{localStorage.setItem(glassKey,String(g));}catch{}}
    $$('.hd-glass-slider').forEach(r=>{if(+r.value!==g)r.value=String(g);});
    $$('.hd-glass-output').forEach(o=>o.textContent=glassLabel(g)+' · '+g+'%');
    return g;
  }
  function createGlassControl(hostDialog){
    const box=document.createElement('div');box.className='hd-glass-control';
    const g=getGlass();
    box.innerHTML='<div class="hd-glass-head"><span>图标与卡片质感</span><output class="hd-glass-output">'+glassLabel(g)+' · '+g+'%</output></div><input class="hd-glass-slider" type="range" min="0" max="100" value="'+g+'" aria-label="液态玻璃质感"><div class="hd-glass-scale"><span>经典磨砂</span><span>液态玻璃 (Liquid Glass)</span></div>';
    const slider=box.querySelector('.hd-glass-slider');
    const peekOn=()=>{if(hostDialog)hostDialog.classList.add('hd-peeking');};
    const peekOff=()=>{if(hostDialog)hostDialog.classList.remove('hd-peeking');};
    slider.addEventListener('pointerdown',peekOn);
    slider.addEventListener('pointerup',peekOff);
    slider.addEventListener('pointercancel',peekOff);
    slider.addEventListener('blur',peekOff);
    slider.addEventListener('input',()=>applyGlass(slider.value,true));
    return box;
  }
  window.__hdApplyGlass=applyGlass;
  window.__hdCreateGlassControl=createGlassControl;
  window.addEventListener('storage',e=>{if(e.key===glassKey||e.key===null)applyGlass();});
  applyGlass();
  let edState=null,edModel=null,edDirty=false,edBusy=false,edPreload=null;
  const edIconNames={'brand-code-server.svg':'VS Code','brand-terminal.svg':'终端','brand-dagu.svg':'任务','brand-copyparty.svg':'文件','brand-files-ipados.svg':'文件','brand-backup-ipados.svg':'备份','brand-linkding.svg':'书签','brand-magpie.svg':'Magpie','brand-beszel.svg':'监控','brand-backrest.svg':'备份','brand-cloudflare.svg':'恢复','brand-github.svg':'项目','metric-network.svg':'网页','metric-cpu.svg':'设备'};
  function preloadEditor(){if(!edPreload)edPreload=request('/lab-api/editor').then(s=>{edState=s;return s;}).catch(()=>{edPreload=null;});return edPreload;}
  async function openEditor(){
    const d=dialog('hd-editor-modal','编辑首页');d.classList.add('hd-editor-dialog');
    const body=d.querySelector('.hd-dialog-body');
    if(!d.open)d.showModal();
    if(edState&&!edDirty){edModel=structuredClone(edState.model);edBusy=false;renderEditor(d,body);}
    else body.innerHTML='<p class="hd-ed-notice" role="status">正在读取首页配置…</p>';
    try{
      const freshState=await request('/lab-api/editor');
      if(!edDirty){edState=freshState;edModel=structuredClone(edState.model);edBusy=false;renderEditor(d,body);}
    }catch(e){if(!edState)body.innerHTML='<p class="hd-ed-notice error">'+esc(e.message)+'</p>';}
  }
  function renderEditor(d,body,openId){
    const move=(arr,from,to)=>{if(to>=0&&to<arr.length)arr.splice(to,0,arr.splice(from,1)[0]);};
    body.innerHTML='<p class="hd-ed-notice" id="hd-ed-notice" role="status">'+(edDirty?'有未保存的修改。点击“保存到首页”后生效。':'直接在此修改，保存后自动更新首页。')+'</p><form id="hd-ed-form"><section class="hd-ed-section"><h3>外观与显示</h3><div id="hd-ed-glass-slot"></div><div class="hd-ed-row"><label class="hd-ed-field">首页名称<input id="hd-ed-title" maxlength="60" required value="'+esc(edModel.title)+'"></label><div class="hd-ed-quick"><button type="button" id="hd-ed-open-wp">壁纸与明暗</button><button type="button" id="hd-ed-sort">拖动排序</button></div></div><div class="hd-ed-toggles"><label><input type="checkbox" id="hd-ed-metrics" '+(edModel.show_metrics?'checked':'')+'> 显示实时资源监控</label></div></section><section class="hd-ed-section"><div class="hd-ed-head"><h3>入口与分组</h3><button type="button" id="hd-ed-add-group">＋ 添加分组</button></div><div id="hd-ed-groups"></div></section><section class="hd-ed-section"><h3>找回常用入口</h3><div class="hd-ed-library" id="hd-ed-library"></div></section><details class="hd-ed-section"><summary style="cursor:pointer;color:#9fb7d5;font-size:13px">导入、导出与撤销</summary><div class="hd-ed-tools"><button type="button" id="hd-ed-export">导出配置</button><button type="button" id="hd-ed-import">导入配置</button><input type="file" id="hd-ed-import-file" accept="application/json,.json" hidden><button type="button" id="hd-ed-undo">撤销上次保存</button></div></details><div class="hd-ed-footer"><span id="hd-ed-dirty">'+(edDirty?'有未保存的修改':'所有设备同步生效')+'</span><button type="submit" id="hd-ed-save" class="hd-primary" '+(edDirty&&!edBusy?'':'disabled')+'>保存到首页</button></div></form>';
    $('#hd-ed-glass-slot').append(createGlassControl(d));
    const notice=(msg,cls='')=>{const n=$('#hd-ed-notice');if(n){n.textContent=msg;n.className='hd-ed-notice'+(cls?' '+cls:'');}};
    const markDirty=()=>{edDirty=true;$('#hd-ed-save').disabled=edBusy;$('#hd-ed-dirty').textContent='有未保存的修改';notice('有未保存的修改。点击“保存到首页”后生效。');};
    const syncButtons=()=>{$('#hd-ed-save').disabled=edBusy||!edDirty;$('#hd-ed-undo').disabled=edBusy||!edState.can_undo;};
    const renderGroups=()=>{
      $('#hd-ed-groups').innerHTML=edModel.groups.map((g,gi)=>'<div class="hd-ed-group" data-group="'+gi+'"><div class="hd-ed-group-head"><input class="hd-ed-group-name" value="'+esc(g.name)+'" maxlength="30" required aria-label="分组名称"><div class="hd-ed-mini-btns"><button type="button" data-act="g-up" '+(gi===0?'disabled':'')+'>↑</button><button type="button" data-act="g-down" '+(gi===edModel.groups.length-1?'disabled':'')+'>↓</button><button type="button" data-act="g-del" class="hd-ed-remove">移除</button></div></div>'+g.items.map((item,ii)=>'<details class="hd-ed-item" data-item="'+ii+'" '+(item.id===openId?'open':'')+'><summary><img src="'+esc(item.icon)+'" alt=""><div class="hd-ed-item-title"><strong>'+esc(item.name)+'</strong><small>'+esc(item.href)+'</small></div><span class="hd-ed-expand">编辑</span></summary><div class="hd-ed-item-grid"><label class="hd-ed-field">名称<input data-f="name" value="'+esc(item.name)+'" maxlength="40" required></label><label class="hd-ed-field">图标<select data-f="icon">'+edState.icons.map(ic=>'<option value="'+esc(ic)+'" '+(ic===item.icon?'selected':'')+'>'+esc(edIconNames[ic.split('/').pop()]||ic.split('/').pop())+'</option>').join('')+'</select></label><label class="hd-ed-field hd-ed-wide">网址<input data-f="href" value="'+esc(item.href)+'" maxlength="2000" required></label><label class="hd-ed-field hd-ed-wide">简短说明<input data-f="description" value="'+esc(item.description)+'" maxlength="100"></label><div class="hd-ed-item-actions"><div class="hd-ed-mini-btns"><button type="button" data-act="i-up" '+(ii===0?'disabled':'')+'>↑</button><button type="button" data-act="i-down" '+(ii===g.items.length-1?'disabled':'')+'>↓</button><button type="button" data-act="i-del" class="hd-ed-remove">移除</button></div><select class="hd-ed-move-group" aria-label="移动到分组">'+edModel.groups.map((grp,idx)=>'<option value="'+idx+'" '+(idx===gi?'selected':'')+'>移至：'+esc(grp.name)+'</option>').join('')+'</select></div></div></details>').join('')+'<button type="button" class="hd-ed-add-item" data-act="i-add">＋ 添加入口</button></div>').join('');
      const ids=new Set(edModel.groups.flatMap(g=>g.items.map(i=>i.id)));
      $('#hd-ed-library').innerHTML=edState.catalog.filter(i=>!ids.has(i.id)).map(i=>'<button type="button" data-restore="'+esc(i.id)+'">＋ '+esc(i.name)+'</button>').join('')||'<span class="hd-note">常用入口都已放在首页。</span>';
      syncButtons();
    };
    renderGroups();
    $('#hd-ed-open-wp').onclick=()=>{d.close();$('#lab-wallpaper-open')?.click();};
    $('#hd-ed-sort').onclick=()=>{d.close();startEdit();};
    $('#hd-ed-form').addEventListener('input',e=>{
      const t=e.target;
      if(t.id==='hd-ed-title')edModel.title=t.value;
      else if(t.id==='hd-ed-metrics')edModel.show_metrics=t.checked;
      else if(t.matches('.hd-ed-group-name'))edModel.groups[+t.closest('[data-group]').dataset.group].name=t.value;
      else if(t.dataset.f){
        const row=t.closest('[data-item]'),gi=+t.closest('[data-group]').dataset.group,item=edModel.groups[gi].items[+row.dataset.item];
        item[t.dataset.f]=t.value;
        if(t.dataset.f==='name')row.querySelector('.hd-ed-item-title strong').textContent=t.value;
        if(t.dataset.f==='href')row.querySelector('.hd-ed-item-title small').textContent=t.value;
        if(t.dataset.f==='icon')row.querySelector('img').src=t.value;
      }else return;
      markDirty();
    });
    $('#hd-ed-groups').onchange=e=>{
      if(!e.target.matches('.hd-ed-move-group'))return;
      const gi=+e.target.closest('[data-group]').dataset.group,ii=+e.target.closest('[data-item]').dataset.item,to=+e.target.value;
      const item=edModel.groups[gi].items.splice(ii,1)[0];edModel.groups[to].items.push(item);
      openId=item.id;markDirty();renderGroups();
    };
    $('#hd-ed-groups').onclick=e=>{
      const btn=e.target.closest('button[data-act]');if(!btn)return;
      const gi=+btn.closest('[data-group]').dataset.group,g=edModel.groups[gi],row=btn.closest('[data-item]'),ii=row?+row.dataset.item:null;
      openId=undefined;
      switch(btn.dataset.act){
        case 'g-up':move(edModel.groups,gi,gi-1);break;
        case 'g-down':move(edModel.groups,gi,gi+1);break;
        case 'g-del':if(!confirm('移除分组“'+g.name+'”？'))return;edModel.groups.splice(gi,1);break;
        case 'i-up':openId=g.items[ii].id;move(g.items,ii,ii-1);break;
        case 'i-down':openId=g.items[ii].id;move(g.items,ii,ii+1);break;
        case 'i-del':if(!confirm('移除入口“'+g.items[ii].name+'”？'))return;g.items.splice(ii,1);break;
        case 'i-add':openId='custom-'+crypto.randomUUID().replaceAll('-','').slice(0,16);g.items.push({id:openId,name:'新入口',href:'https://',description:'',icon:'/lab-assets/metric-network.svg'});break;
      }
      markDirty();renderGroups();if(openId)body.querySelector('details[open] input')?.focus();
    };
    $('#hd-ed-add-group').onclick=()=>{edModel.groups.push({name:'新分组',items:[]});markDirty();renderGroups();$('#hd-ed-groups .hd-ed-group:last-child .hd-ed-group-name')?.focus();};
    $('#hd-ed-library').onclick=e=>{
      const b=e.target.closest('[data-restore]');if(!b)return;
      if(!edModel.groups.length)edModel.groups.push({name:'常用工具',items:[]});
      const item=structuredClone(edState.catalog.find(i=>i.id===b.dataset.restore));if(!item)return;
      edModel.groups[0].items.push(item);openId=item.id;markDirty();renderGroups();
    };
    $('#hd-ed-export').onclick=()=>{const url=URL.createObjectURL(new Blob([JSON.stringify({format:'asashiki-panel-v1',model:edModel},null,2)],{type:'application/json'}));const a=document.createElement('a');a.href=url;a.download='asashiki-homepage.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);};
    $('#hd-ed-import').onclick=()=>$('#hd-ed-import-file').click();
    $('#hd-ed-import-file').onchange=async e=>{
      const file=e.target.files[0];e.target.value='';if(!file)return;
      try{
        if(file.size>100000)throw Error('配置文件不能超过 100KB');
        const input=JSON.parse(await file.text());
        if(input.format!=='asashiki-panel-v1'||!Array.isArray(input.model?.groups))throw Error('请选择有效配置');
        const m=input.model;
        if(typeof m.title!=='string'||!edState.backgrounds.includes(m.background)||!Number.isInteger(m.opacity)||typeof m.show_metrics!=='boolean'||typeof m.show_recent!=='boolean'||m.groups.length>12)throw Error('配置字段不完整');
        for(const g of m.groups){if(typeof g.name!=='string'||!Array.isArray(g.items)||g.items.length>24)throw Error('分组格式不正确');for(const i of g.items){if(!['id','name','href','description','icon'].every(k=>typeof i[k]==='string')||!edState.icons.includes(i.icon))throw Error('入口格式不正确');}}
        edModel=m;markDirty();renderEditor(d,body);notice('配置已导入草稿，点击“保存到首页”生效。');
      }catch(err){notice('未导入：'+err.message,'error');}
    };
    $('#hd-ed-undo').onclick=async()=>{
      if(!confirm('撤销上次保存？当前未保存的修改将舍弃。'))return;
      edBusy=true;syncButtons();notice('正在撤销…');
      try{await request('/lab-api/editor/undo',{method:'POST',headers:{'Content-Type':'application/json','X-Lab-Editor':'1'},body:JSON.stringify({revision:edState.revision})});toast('已恢复上次保存，正在刷新首页…');d.close();location.reload();}
      catch(err){notice(err.message,'error');edBusy=false;syncButtons();}
    };
    $('#hd-ed-form').onsubmit=async e=>{
      e.preventDefault();if(!$('#hd-ed-form').reportValidity())return;
      edBusy=true;syncButtons();notice('正在保存…');
      try{
        const res=await fetch('/lab-api/editor',{method:'POST',headers:{'Content-Type':'application/json','X-Lab-Editor':'1'},body:JSON.stringify({revision:edState.revision,model:edModel})});
        if(!res.ok)throw Error(await res.text());
        edDirty=false;d.close();toast('首页已更新，正在刷新…');setTimeout(()=>location.reload(),350);
      }catch(err){notice(err.message,'error');edBusy=false;syncButtons();}
    };
  }
  setup();
})();
/* Server-persisted wallpaper library, with device-local current selection. */
(() => {
  if(window.__labWallpapers)return;window.__labWallpapers=true;
  const key='asashiki.wallpaper.v1',builtins=[
    {id:'default',name:'跟随首页设置'},
    {id:'character',name:'蓝紫角色',url:'/lab-assets/default-wallpaper.svg'},
    {id:'mountain',name:'雪山星空',url:'/lab-assets/alpine-night.jpg'}
  ];
  let selected='default',generation=0,uploads=[],busy=false;
  function saved(){try{return localStorage.getItem(key)||'default';}catch{return 'default';}}
  function setup(){
    const footer=document.querySelector('.lab-footer>span:last-child'),bg=document.querySelector('#background');
    if(!footer||!bg){setTimeout(setup,200);return;}
    const initial=bg.style.getPropertyValue('background-image'),priority=bg.style.getPropertyPriority('background-image');
    const trigger=document.createElement('button');trigger.type='button';trigger.textContent='切换背景';trigger.id='lab-wallpaper-open';trigger.setAttribute('aria-haspopup','dialog');footer.prepend(trigger);
    const dialog=document.createElement('dialog');dialog.id='lab-wallpapers';dialog.setAttribute('aria-labelledby','lab-wallpaper-title');
    dialog.innerHTML='<div class="lab-wallpaper-heading"><h2 id="lab-wallpaper-title">我的壁纸</h2><button type="button" class="lab-wallpaper-close" aria-label="关闭背景选择">×</button></div><p>上传后保存在服务器，各设备都能选择。当前背景由各浏览器分别记住。</p><div class="lab-wallpaper-actions"><button type="button" id="lab-wallpaper-add">＋ 添加壁纸</button><span>JPG / PNG · 每张最多12 MB</span><input type="file" id="lab-wallpaper-file" accept="image/jpeg,image/png,.jpg,.jpeg,.png" hidden></div><div class="lab-wallpaper-options"></div><p role="status" aria-live="polite"></p>';
    document.body.append(dialog);
    const result=dialog.querySelector('[role=status]'),options=dialog.querySelector('.lab-wallpaper-options'),fileInput=dialog.querySelector('input'),addButton=dialog.querySelector('#lab-wallpaper-add');
    const themeKey='halodeck.appearance.v1';let appearance='auto',detected='dark',currentImage=null,sampleTimer;
    try{appearance=localStorage.getItem(themeKey)||'auto';}catch{}
    if(!['auto','light','dark'].includes(appearance))appearance='auto';
    const theme=document.createElement('label');theme.className='hd-appearance';theme.innerHTML='<span>界面明暗</span><select aria-label="界面明暗"><option value="auto">跟随壁纸</option><option value="light">浅色</option><option value="dark">深色</option></select>';dialog.querySelector('.lab-wallpaper-actions').before(theme);
    if(window.__hdCreateGlassControl)theme.after(window.__hdCreateGlassControl(dialog));
    const select=theme.querySelector('select');select.value=appearance;
    function paintTheme(){const value=appearance==='auto'?detected:appearance;document.documentElement.dataset.hdTheme=value;document.querySelector('meta[name="theme-color"]')?.setAttribute('content',value==='light'?'#eef4fa':'#0b1120');window.__hdApplyGlass?.();}
    function sampleTheme(){
      detected='dark';
      if(currentImage)try{
        const c=document.createElement('canvas');c.width=48;c.height=32;const ctx=c.getContext('2d',{willReadFrequently:true});
        const scale=Math.max(innerWidth/currentImage.naturalWidth,innerHeight/currentImage.naturalHeight),sw=innerWidth/scale,sh=innerHeight/scale;
        ctx.fillStyle='#0b1426';ctx.fillRect(0,0,48,32);ctx.drawImage(currentImage,(currentImage.naturalWidth-sw)/2,(currentImage.naturalHeight-sh)*.4,sw,sh,0,0,48,32);
        const px=ctx.getImageData(0,0,48,32).data;let sum=0;
        for(let i=0;i<px.length;i+=4)sum+=(.2126*px[i]+.7152*px[i+1]+.0722*px[i+2])/255;
        detected=sum/(48*32)>.57?'light':'dark';
      }catch{}paintTheme();
    }
    select.onchange=()=>{appearance=select.value;try{localStorage.setItem(themeKey,appearance);}catch{}paintTheme();};
    window.addEventListener('resize',()=>{clearTimeout(sampleTimer);sampleTimer=setTimeout(sampleTheme,160);});
    window.addEventListener('storage',e=>{if(e.key===themeKey||e.key===null){appearance=['light','dark'].includes(e.newValue)?e.newValue:'auto';select.value=appearance;paintTheme();}});paintTheme();
    function mark(){options.querySelectorAll('[data-wallpaper]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.wallpaper===selected)));bg.dataset.labWallpaper=selected;}
    function state(value){busy=value;addButton.disabled=value;options.querySelectorAll('.lab-wallpaper-delete').forEach(b=>b.disabled=value);}
    async function request(url,init){
      let response;
      try{response=await fetch(url,{...init,signal:AbortSignal.timeout(60000),cache:'no-store'});}
      catch(e){throw Error(e.name==='TimeoutError'?'操作超时，请稍后重试':'连接中断，请刷新登录后重试');}
      if(!response.ok){const body=await response.text();throw Error(response.status===413?'图片最大12 MB':response.headers.get('content-type')?.includes('text/plain')?body.slice(0,200):'操作未完成，请刷新登录后重试');}
      try{return await response.json();}catch{throw Error('登录可能已过期，请刷新页面后重试');}
    }
    async function apply(id,persist=false){
      const choice=[...builtins,...uploads].find(c=>c.id===id)||builtins[0],serial=++generation;
      let loadedImage=null;
      if(choice.url){
        result.textContent='正在加载…';
        try{await new Promise((resolve,reject)=>{const image=new Image();image.onload=()=>{loadedImage=image;resolve();};image.onerror=reject;image.src=choice.url;});}
        catch{if(serial===generation)result.textContent='图片暂时无法加载，请重试。';return;}
      }
      if(serial!==generation)return;
      if(choice.url)bg.style.setProperty('background-image','url("'+choice.url+'")');
      else if(initial)bg.style.setProperty('background-image',initial,priority);
      else bg.style.removeProperty('background-image');
      currentImage=loadedImage;sampleTheme();
      selected=choice.id;mark();result.textContent='已选择：'+choice.name;
      if(persist){try{if(selected==='default')localStorage.removeItem(key);else localStorage.setItem(key,selected);}catch{result.textContent+='。浏览器未允许记住选择。';}}
    }
    function render(){
      options.replaceChildren();
      [...builtins,...uploads].forEach(choice=>{
        const item=document.createElement('div');item.className='lab-wallpaper-item';
        const button=document.createElement('button');button.type='button';button.dataset.wallpaper=choice.id;button.title=choice.name;
        const preview=document.createElement('span');preview.className='lab-wallpaper-preview';preview.setAttribute('aria-hidden','true');
        if(choice.url)preview.style.backgroundImage='url("'+choice.url+'")';else preview.textContent='随首页';
        const label=document.createElement('span');label.textContent=choice.name;button.append(preview,label);item.append(button);button.onclick=()=>apply(choice.id,true);
        if(!builtins.some(b=>b.id===choice.id)){
          const remove=document.createElement('button');remove.type='button';remove.className='lab-wallpaper-delete';remove.textContent='移除';remove.setAttribute('aria-label','移除壁纸：'+choice.name);remove.disabled=busy;
          remove.onclick=async()=>{
            if(busy||!confirm('从壁纸库移除“'+choice.name+'”？所有设备都将无法再选择这张图片。'))return;
            state(true);
            try{uploads=(await request('/lab-api/wallpapers/'+choice.id,{method:'DELETE',headers:{'X-Lab-Wallpaper':'1'}})).items;if(selected===choice.id)await apply('default',true);render();result.textContent='已移除壁纸';}
            catch(e){result.textContent=e.message;}finally{state(false);}
          };item.append(remove);
        }
        options.append(item);
      });mark();
    }
    async function load(){
      try{uploads=(await request('/lab-api/wallpapers')).items;render();return true;}
      catch{result.textContent='暂时无法读取上传的壁纸，内置背景仍可选择。';return false;}
    }
    addButton.onclick=()=>fileInput.click();
    fileInput.onchange=async()=>{
      const file=fileInput.files[0];fileInput.value='';if(!file||busy)return;
      state(true);result.textContent='正在检查图片…';
      try{
        if(file.size>12*1024*1024)throw Error('图片最大12 MB');
        if(!['image/jpeg','image/png'].includes(file.type))throw Error('请选择JPG或PNG图片');
        const image=await createImageBitmap(file);const width=image.width,height=image.height;image.close();
        if(width*height>24000000||Math.max(width,height)>12000)throw Error('图片最多2400万像素、单边12000像素');
        result.textContent='正在上传…';
        const old=new Set(uploads.map(i=>i.id));uploads=(await request('/lab-api/wallpapers',{method:'POST',headers:{'Content-Type':'application/octet-stream','X-Lab-Wallpaper':'1','X-Lab-Wallpaper-Name':encodeURIComponent(file.name)},body:file})).items;
        render();const added=uploads.find(i=>!old.has(i.id));
        if(added){await apply(added.id,true);result.textContent='已添加并设为当前背景';}else result.textContent='这张图片已经在壁纸库中';
      }catch(e){result.textContent=e.name==='InvalidStateError'?'无法读取这张图片，请换一个有效的JPG或PNG文件':e.message;}
      finally{state(false);}
    };
    trigger.onclick=async()=>{dialog.showModal();render();if(!busy)await load();};dialog.querySelector('.lab-wallpaper-close').onclick=()=>dialog.close();dialog.addEventListener('close',()=>document.querySelector('#hd-wallpaper')?.focus());
    window.addEventListener('storage',async e=>{if(e.key===key||e.key===null){await load();apply(saved());}});
    render();load().then(()=>apply(saved()));
  }
  setup();
})();
