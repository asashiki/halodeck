(() => {
  'use strict';
  const $ = s => document.querySelector(s);
  const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  let state, model, dirty = false, busy = false;
  const iconNames = {'brand-code-server.svg':'VS Code','brand-terminal.svg':'终端','brand-dagu.svg':'任务','brand-copyparty.svg':'文件','brand-linkding.svg':'书签','brand-magpie.svg':'Magpie','brand-beszel.svg':'监控','brand-backrest.svg':'备份','brand-cloudflare.svg':'恢复','brand-github.svg':'项目','metric-network.svg':'网页','metric-cpu.svg':'设备'};
  function notice(message, kind = '') { $('#notice').textContent = message; $('#notice').className = kind; }
  function changed() { dirty = true; $('#save').disabled = busy; $('#dirty').textContent = '有未保存的修改'; notice('修改尚未发布。满意后点击“保存到首页”。'); }
  function buttons() { $('#editor').inert = busy; $('#save').disabled = busy || !dirty; $('#undo').disabled = busy || !state.can_undo; }
  async function request(path, payload) {
    const response = await fetch(path, payload ? {method:'POST',headers:{'Content-Type':'application/json','X-Lab-Editor':'1'},body:JSON.stringify(payload)} : {cache:'no-store'});
    if (!response.ok) throw new Error(await response.text());
    return response.json();
  }
  function confirm(title, description) {
    return new Promise(resolve => {
      $('#confirm-title').textContent = title; $('#confirm-description').textContent = description;
      const dialog = $('#confirm'); dialog.returnValue = 'cancel'; dialog.onclose = () => resolve(dialog.returnValue === 'ok'); dialog.showModal();
    });
  }
  const move = (array, from, to) => { if (to >= 0 && to < array.length) array.splice(to, 0, array.splice(from, 1)[0]); };
  function render(openId) {
    $('#title').value = model.title;
    $('#background').innerHTML = state.backgrounds.map(path => `<option value="${esc(path)}">${path ? (path.includes('20261105') ? '当前角色背景' : '山间夜色') : '纯色背景'}</option>`).join('');
    $('#background').value = model.background; $('#opacity').value = model.opacity; $('#opacity-label').value = model.opacity + '%';
    $('#show_metrics').checked = model.show_metrics; $('#show_recent').checked = model.show_recent;
    $('#groups').innerHTML = model.groups.map((group, gi) => `<section class="group" data-group="${gi}"><div class="group-head"><input class="group-name" value="${esc(group.name)}" maxlength="30" required aria-label="分组名称"><div class="move-buttons"><button type="button" data-action="group-up" aria-label="上移分组" ${gi===0?'disabled':''}>↑</button><button type="button" data-action="group-down" aria-label="下移分组" ${gi===model.groups.length-1?'disabled':''}>↓</button><button type="button" data-action="group-remove" class="remove">移除分组</button></div></div>${group.items.map((item, ii) => `<details class="item" data-item="${ii}" ${item.id===openId?'open':''}><summary><img src="${esc(item.icon)}" alt=""><span class="item-title"><span>${esc(item.name)}</span><small>${esc(item.href)}</small></span><span class="expand">编辑</span></summary><div class="item-fields"><label>名称<input data-field="name" value="${esc(item.name)}" maxlength="40" required></label><label>图标<select data-field="icon">${state.icons.map(icon => `<option value="${esc(icon)}" ${icon===item.icon?'selected':''}>${esc(iconNames[icon.split('/').pop()] || icon.split('/').pop())}</option>`).join('')}</select></label><label class="wide">网址<input data-field="href" value="${esc(item.href)}" placeholder="https://…" maxlength="2000" required></label><label class="wide">简短说明<input data-field="description" value="${esc(item.description)}" maxlength="100"></label><div class="item-actions"><div class="move-buttons"><button type="button" data-action="item-up" aria-label="上移入口" ${ii===0?'disabled':''}>↑</button><button type="button" data-action="item-down" aria-label="下移入口" ${ii===group.items.length-1?'disabled':''}>↓</button><button type="button" data-action="item-remove" class="remove">移除</button></div><select class="move-group" aria-label="移动到分组">${model.groups.map((g, i) => `<option value="${i}" ${i===gi?'selected':''}>${esc(g.name)}</option>`).join('')}</select></div></div></details>`).join('')}<button type="button" class="add-item" data-action="item-add">＋ 添加入口</button></section>`).join('');
    const ids = new Set(model.groups.flatMap(g => g.items.map(i => i.id)));
    $('#library').innerHTML = state.catalog.filter(i => !ids.has(i.id)).map(i => `<button type="button" data-restore="${esc(i.id)}">＋ ${esc(i.name)}</button>`).join('') || '<span class="hint">常用入口都已放在首页。</span>';
    buttons();
  }
  async function load() {
    try { state = await request('/lab-api/editor'); model = structuredClone(state.model); dirty = false; render(); $('#editor').hidden = false; notice('保存后，手机和其他电脑也会使用这份首页。'); $('#dirty').textContent = '修改会保存到服务器，所有设备同步。'; }
    catch (e) { notice('暂时无法读取：' + e.message, 'error'); }
  }
  $('#editor').onsubmit = e => e.preventDefault();
  $('#editor').addEventListener('input', e => {
    const t=e.target;
    if (t.matches('.group-name')) model.groups[+t.closest('[data-group]').dataset.group].name=t.value;
    else if (t.dataset.field) {
      const host=t.closest('[data-group]'), row=t.closest('[data-item]'), item=model.groups[+host.dataset.group].items[+row.dataset.item]; item[t.dataset.field]=t.value;
      if(t.dataset.field==='name')row.querySelector('.item-title > span').textContent=t.value;
      if(t.dataset.field==='href')row.querySelector('small').textContent=t.value;
      if(t.dataset.field==='icon')row.querySelector('img').src=t.value;
    } else if (['title','background'].includes(t.id)) model[t.id]=t.value;
    else if (t.id==='opacity') { model.opacity=+t.value; $('#opacity-label').value=t.value+'%'; }
    else if (['show_metrics','show_recent'].includes(t.id)) model[t.id]=t.checked;
    else return;
    changed();
  });
  $('#groups').addEventListener('change', e => {
    if (!e.target.matches('.move-group')) return;
    const gi=+e.target.closest('[data-group]').dataset.group, ii=+e.target.closest('[data-item]').dataset.item, to=+e.target.value;
    const item=model.groups[gi].items.splice(ii,1)[0];model.groups[to].items.push(item);changed();render(item.id);
  });
  $('#groups').addEventListener('click', async e => {
    const button=e.target.closest('button[data-action]');if(!button)return;
    const gi=+button.closest('[data-group]').dataset.group, group=model.groups[gi], row=button.closest('[data-item]'), ii=row?+row.dataset.item:null;
    let openId;
    switch(button.dataset.action){
      case 'group-up': move(model.groups,gi,gi-1);break;
      case 'group-down': move(model.groups,gi,gi+1);break;
      case 'group-remove': if(!await confirm('移除“'+group.name+'”？','只从首页移除此分组和入口，应用与数据保留。'))return;model.groups.splice(gi,1);break;
      case 'item-up': openId=group.items[ii].id;move(group.items,ii,ii-1);break;
      case 'item-down': openId=group.items[ii].id;move(group.items,ii,ii+1);break;
      case 'item-remove': if(!await confirm('移除“'+group.items[ii].name+'”？','只从首页移除入口。保存前可以重新载入；保存后可以撤销。'))return;group.items.splice(ii,1);break;
      case 'item-add': openId='custom-'+crypto.randomUUID().replaceAll('-','').slice(0,16);group.items.push({id:openId,name:'新入口',href:'https://',description:'',icon:'/lab-assets/metric-network.svg'});break;
    }
    changed();render(openId);if(openId)document.querySelector('details[open] input')?.focus();
  });
  $('#add-group').onclick=()=>{model.groups.push({name:'新分组',items:[]});changed();render();$('#groups .group:last-child .group-name').focus();};
  $('#library').onclick=e=>{const b=e.target.closest('[data-restore]');if(!b)return;if(!model.groups.length)model.groups.push({name:'常用工具',items:[]});const item=structuredClone(state.catalog.find(i=>i.id===b.dataset.restore));model.groups[0].items.push(item);changed();render(item.id);};
  $('#save').onclick=async()=>{
    if(!$('#editor').reportValidity())return;
    busy=true;buttons();notice('正在保存…');
    try{const data=await request('/lab-api/editor',{revision:state.revision,model});state={...state,...data};model=structuredClone(data.model);dirty=false;render();notice(data.reloaded?'已保存。首页已更新，所有设备同步生效。':'已保存，首页刷新暂未完成。稍后打开首页即可检查。','success');$('#dirty').textContent='已保存 '+new Date(data.saved_at).toLocaleTimeString('zh-CN');}
    catch(e){notice(e.message,'error');}finally{busy=false;buttons();}
  };
  $('#undo').onclick=async()=>{if(!await confirm('撤销上次保存？','首页会恢复到上一次保存前的样子。当前未保存的修改将舍弃。'))return;busy=true;buttons();try{await request('/lab-api/editor/undo',{revision:state.revision});await load();notice('已恢复上次保存前的首页。','success');}catch(e){notice(e.message,'error');}finally{busy=false;buttons();}};
  $('#reload').onclick=async()=>{if(dirty&&!await confirm('舍弃未保存的修改？','重新读取服务器上最新的首页。'))return;await load();};
  $('#export').onclick=()=>{const url=URL.createObjectURL(new Blob([JSON.stringify({format:'asashiki-panel-v1',model},null,2)],{type:'application/json'}));const a=document.createElement('a');a.href=url;a.download='asashiki-homepage.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);};
  $('#import').onclick=()=>$('#import-file').click();
  $('#import-file').onchange=async e=>{
    const file=e.target.files[0];if(!file)return;
    try{if(file.size>100000)throw Error('配置文件不能超过100KB');const input=JSON.parse(await file.text());if(input.format!=='asashiki-panel-v1'||!Array.isArray(input.model?.groups))throw Error('请选择从本编辑器导出的配置');
      if(dirty&&!await confirm('用导入的配置替换草稿？','首页尚不会改变，点击保存才会发布。'))return;
      // Reject unknown shapes before interpolating them into the editor.
      const m=input.model;if(typeof m.title!=='string'||!state.backgrounds.includes(m.background)||!Number.isInteger(m.opacity)||typeof m.show_metrics!=='boolean'||typeof m.show_recent!=='boolean'||m.groups.length>12)throw Error('配置字段不完整');
      for(const g of m.groups){if(typeof g.name!=='string'||!Array.isArray(g.items)||g.items.length>24)throw Error('分组格式不正确');for(const i of g.items){if(!['id','name','href','description','icon'].every(k=>typeof i[k]==='string')||!state.icons.includes(i.icon))throw Error('入口格式不正确');}}
      model=m;changed();render();notice('配置已导入草稿。检查后点击“保存到首页”。');
    }catch(error){notice('未导入：'+error.message,'error');}finally{e.target.value='';}
  };
  window.addEventListener('beforeunload',e=>{if(dirty){e.preventDefault();e.returnValue='';}});
  load();
})();
