(() => {
  'use strict';
  if (window.__homeTerminalImages) return;
  window.__homeTerminalImages = true;
  const limit = 12 * 1024 * 1024;
  const allowed = new Set(['image/png', 'image/jpeg']);
  let busy = false;
  // Let the browser produce a real clipboard paste event instead of xterm
  // consuming Ctrl+V as the terminal's literal control character.
  function installPasteShortcut(attempt=0) {
    if (window.term?.attachCustomKeyEventHandler) {
      window.term.attachCustomKeyEventHandler(event => !((event.ctrlKey || event.metaKey) && !event.altKey && event.key.toLowerCase()==='v'));
    } else if (attempt<200) setTimeout(()=>installPasteShortcut(attempt+1),50);
  }
  installPasteShortcut();
  const bar = document.createElement('div');
  bar.id = 'terminal-images';
  bar.innerHTML = '<button type="button" title="上传图片，也可粘贴截图或拖放 PNG/JPG">＋ 图片</button><span role="status" aria-live="polite"></span><input type="file" accept="image/png,image/jpeg" multiple hidden>';
  const css = document.createElement('style');
  css.textContent = '#terminal-images{position:fixed;top:8px;right:12px;z-index:10000;display:flex;align-items:center;gap:8px;max-width:calc(100vw - 24px);font:13px/1.5 system-ui,sans-serif}#terminal-images button{color:#d3e2de;background:#172721;border:1px solid #446255;border-radius:7px;padding:5px 9px;cursor:pointer;white-space:nowrap}#terminal-images button:focus-visible{outline:2px solid #7cd6b3;outline-offset:2px}#terminal-images span:not(:empty){background:#172721;color:#e3eee9;border:1px solid #446255;border-radius:7px;padding:5px 9px;max-width:440px;overflow-wrap:anywhere}#terminal-images [data-error="true"]{color:#ffcebf!important}#terminal-images button:disabled{opacity:.6;cursor:wait}@media(max-width:600px){#terminal-images{align-items:flex-start;gap:4px;right:6px;top:4px;font-size:12px}#terminal-images span:not(:empty){max-width:calc(100vw - 115px)}}';
  function mount() { document.head.append(css); document.body.append(bar); }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', mount, {once:true}); else mount();
  const button = bar.querySelector('button'), notice = bar.querySelector('span'), input = bar.querySelector('input');
  function status(text, error=false) { notice.textContent=text; notice.dataset.error=String(error); }
  function terminal() { return window.term && typeof window.term.paste==='function' && (!window.labTerminalConnected || window.labTerminalConnected()) ? window.term : null; }
  async function submit(files) {
    if (busy) { status('上一张图片仍在上传，请稍候。'); return; }
    if (!terminal()) { status('终端尚未连接，请连接后重试。',true); return; }
    if (!files.length) return;
    if (files.length>8) { status('一次最多上传 8 张图片。',true); return; }
    if (files.some(f=>!allowed.has(f.type))) { status('请选择 PNG 或 JPG 图片。',true); return; }
    if (files.some(f=>!f.size || f.size>limit)) { status('每张图片最大 12 MB。',true); return; }
    busy=true; button.disabled=true;
    try {
      let count=0;
      for (const file of files) {
        status(`正在上传图片 ${++count}/${files.length}…`);
        const response = await fetch('/lab-api/terminal-images', {method:'POST',headers:{'X-Terminal-Image':'1','Content-Type':'application/octet-stream'},body:file,credentials:'same-origin',redirect:'error',signal:AbortSignal.timeout(60000)});
        if (!response.ok) { const message=(await response.text()).slice(0,200); throw new Error(message || `上传失败（${response.status}）`); }
        const result=await response.json();
        if (!/^\/workspace\/workbench\/inbox\/terminal-images\/[a-f0-9]{64}\.(png|jpg)$/.test(result.path)) throw new Error('上传响应无效，未向终端写入路径。');
        const term=terminal();
        if (!term) throw new Error('图片已保存，但终端断开；可从文件页 inbox/terminal-images 找到它。');
        // xterm's paste API uses bracketed paste when the foreground program
        // requests it. Paths contain no spaces/control characters; never Enter.
        term.paste(result.path);
      }
      status(`${files.length} 张图片已放入输入框；确认后再发送。`);
      terminal()?.focus();
    } catch (e) { status(e instanceof TypeError ? '上传未完成，请检查连接或刷新页面重新登录。' : e.message,true); }
    finally { busy=false; button.disabled=false; input.value=''; }
  }
  button.addEventListener('click',()=>input.click());
  input.addEventListener('change',()=>submit(Array.from(input.files||[])));
  document.addEventListener('paste',event=>{
    const items=Array.from(event.clipboardData?.items||[]);
    const files=items.filter(x=>x.kind==='file' && x.type.startsWith('image/')).map(x=>x.getAsFile()).filter(Boolean);
    if (!files.length) return; // Preserve normal text paste.
    event.preventDefault(); event.stopImmediatePropagation(); submit(files);
  },true);
  document.addEventListener('dragover',event=>{if(Array.from(event.dataTransfer?.types||[]).includes('Files')){event.preventDefault();event.dataTransfer.dropEffect='copy';}},true);
  document.addEventListener('drop',event=>{
    const files=Array.from(event.dataTransfer?.files||[]); if(!files.length)return;
    event.preventDefault();event.stopImmediatePropagation();submit(files);
  },true);
})();
