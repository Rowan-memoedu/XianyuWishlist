'use strict';
const $ = s => document.querySelector(s);
let data = null, view = 'inbox', editing = null, busy = false, pollTimer = null;
const labels = {inbox:'待整理',queue:'比价队列',archive:'已处理'};
const stateLabels = {inbox:'待整理',queued:'已加入比价',removed:'已移除',purchased:'已购',remote_removed:'闲鱼已取消'};
const node = (tag, cls, text) => { const n=document.createElement(tag); if(cls)n.className=cls; if(text!==undefined)n.textContent=text; return n; };
function toast(text) { $('#toast').textContent=text; $('#toast').hidden=false; clearTimeout(toast.timer); toast.timer=setTimeout(()=>$('#toast').hidden=true,5000); }
async function api(url, body) {
  const response=await fetch(url,body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json','X-Wishlist-Token':data.csrf},body:JSON.stringify(body)});
  const payload=await response.json();
  if(!response.ok) throw new Error(typeof payload.detail==='string'?payload.detail:'操作未保存，请检查输入后重试');
  return payload;
}
async function load() {
  try { data=await api('/api/state'); render(); schedulePoll(); }
  catch(e) { $('#sync-text').textContent=e.message; $('.sync-status').classList.add('error'); }
}
function schedulePoll() {
  clearTimeout(pollTimer);
  if(data?.job.running) pollTimer=setTimeout(load,1800);
}
function changeView(next) { view=next; $('#filter').value='all'; render(); }
function matching(item) {
  const term=$('#search').value.toLocaleLowerCase().trim();
  const filter=$('#filter').value;
  return (!term||[item.display_title,item.title,item.notes,item.group_label].some(x=>(x||'').toLocaleLowerCase().includes(term))) &&
    (filter!=='pending'||item.pending) && (filter!=='invalid'||item.invalid);
}
function button(text, handler, cls='') { const b=node('button',cls,text); b.disabled=busy||data.job.running; b.addEventListener('click',handler); return b; }
async function action(item,actionName) {
  busy=true;
  try { await api(`/api/items/${item.id}/action`,{action:actionName}); await load(); toast({queue:'已保存到比价队列；点击同步后取消收藏',remove:'已移除；点击同步后取消收藏',purchase:'已标记已购',undo:'已撤销待提交操作',restore:'已恢复到本地待整理'}[actionName]); }
  catch(e) { toast(e.message); } finally {busy=false;render();}
}
function row(item) {
  const article=node('article','item'); article.dataset.id=item.id;
  const check=node('label','check');
  const input=node('input');input.type='checkbox';input.checked=item.state==='queued';
  input.setAttribute('aria-label',`加入比价：${item.display_title}`);
  input.disabled=busy||data.job.running||(item.state==='queued'&&!item.pending)||!['inbox','queued'].includes(item.state);
  input.addEventListener('change',()=>action(item,input.checked?'queue':'undo'));check.append(input);article.append(check);
  const image=node('img','item-image');image.width=72;image.height=72;image.loading='lazy';image.referrerPolicy='no-referrer';image.alt='';
  let pic=item.image||''; if(pic.startsWith('//'))pic='https:'+pic;
  try { const u=new URL(pic); if(/(^|\.)(alicdn\.com|tbcdn\.cn|goofish\.com)$/.test(u.hostname)&&['http:','https:'].includes(u.protocol)){u.protocol='https:';image.src=u.href;} } catch(_){}
  image.addEventListener('error',()=>image.removeAttribute('src'),{once:true});article.append(image);
  const main=node('div','item-main'), title=node('h3','item-title'),link=node('a','',item.display_title);
  link.href=item.url;link.target='_blank';link.rel='noreferrer';title.append(link);main.append(title);
  const meta=node('div','meta');meta.append(node('span','price',item.price!=null?'¥ '+item.price:'价格待确认'));
  if(item.seller)meta.append(node('span','',item.seller));
  if(item.invalid)meta.append(node('span','tag invalid','已失效'));
  if(item.pending)meta.append(node('span','tag','待取消收藏'));
  else if(item.state!=='inbox')meta.append(node('span','tag',stateLabels[item.state]));
  if(!item.remote_present&&item.state==='inbox')meta.append(node('span','tag','仅在本地'));
  main.append(meta);
  main.append(node('p','type','比价类型 · '+item.group_label));
  if(item.notes)main.append(node('p','note',item.notes));
  if(item.error)main.append(node('p','row-error',item.error));
  article.append(main);
  const actions=node('div','item-actions');actions.append(button('编辑',()=>edit(item,'item')));
  if(item.state==='inbox')actions.append(button('× 移除',()=>action(item,'remove'),'danger'),button('已购',()=>action(item,'purchase')));
  else if(item.pending)actions.append(button('撤销',()=>action(item,'undo')));
  else if(item.state!=='queued')actions.append(button('恢复本地',()=>action(item,'restore')));
  if(item.state==='queued')actions.append(button('移出队列',()=>action(item,'remove'),'danger'));
  article.append(actions);return article;
}
function render() {
  if(!data)return;
  const inbox=data.items.filter(x=>x.state==='inbox'), archive=data.items.filter(x=>!['inbox','queued'].includes(x.state));
  const tasks=data.tasks.filter(x=>x.state==='queued');
  $('#count-inbox').textContent=inbox.length;$('#count-queue').textContent=tasks.length;$('#count-archive').textContent=archive.length;
  $('#pending-badge').textContent=data.pending;
  document.querySelectorAll('[data-view]').forEach(b=>{b.classList.toggle('active',b.dataset.view===view);b.setAttribute('aria-current',b.dataset.view===view?'page':'false');});
  $('#crumb').textContent=labels[view];$('#heading').textContent={inbox:'先留下真正想买的。',queue:'把想买的，变成可比较的。',archive:'处理过的，都有记录。'}[view];
  $('#description').textContent={inbox:'勾选商品进入比价队列；同步后，它会从闲鱼收藏中取消。',queue:'同一类型合成一个任务。补充版次、规格和预算，供下一轮搜集与比价。',archive:'已移除、已购和在闲鱼取消的商品保留在这里，不会重新挤进清单。'}[view];
  $('#summary-number').textContent={inbox:inbox.length,queue:tasks.length,archive:archive.length}[view];
  $('#summary-label').textContent={inbox:'件待整理',queue:'个比价任务',archive:'件已处理'}[view];
  $('#refresh').disabled=$('#sync').disabled=busy||data.job.running;
  $('#sync-text').textContent=data.job.running?data.job.message:data.job.error||((data.last_sync&&data.job.result)?data.job.message:`本地已保存 · ${data.pending} 项待同步`);
  $('.sync-status').classList.toggle('error',!!data.job.error);
  $('#read-time').textContent=data.last_read?.observed_at_utc?'最近读取 '+new Date(data.last_read.observed_at_utc).toLocaleString('zh-CN',{month:'numeric',day:'numeric',hour:'2-digit',minute:'2-digit'}):'';
  $('#notice').hidden=!data.pending;
  $('#notice').textContent=`${data.pending} 项操作尚未提交到闲鱼。点击“同步”后统一取消收藏，队列和本地备注会保留。`;
  const list=$('#list');list.replaceChildren();list.setAttribute('aria-busy','false');let shown=0;
  if(view==='queue') {
    for(const task of tasks) {
      const allMembers=data.items.filter(x=>x.task_id===task.id&&x.state==='queued'),members=allMembers.filter(matching);
      if(!members.length)continue;
      shown++;
      const section=node('article','task'),head=node('div','task-head'),intro=node('div');
      intro.append(node('h2','',task.title),node('p','',task.criteria||`${allMembers.length} 个参考商品 · 可编辑购买要求`));
      const actions=node('div','task-buttons');
      actions.append(button('编辑要求',()=>edit(task,'task')),button('已购，归档',async()=>{
        busy=true;
        try{await api(`/api/tasks/${task.id}/purchase`,{});await load();toast('该类型已购，已从比价队列归档');}catch(e){toast(e.message);}finally{busy=false;render();}
      }));
      head.append(intro,actions);section.append(head);members.forEach(x=>section.append(row(x)));list.append(section);
    }
  } else {
    const items=(view==='inbox'?inbox:archive).filter(matching);shown=items.length;items.forEach(x=>list.append(row(x)));
  }
  $('#visible-count').textContent=`显示 ${shown} ${view==='queue'?'个任务':'项'}`;
  if(!shown) {
    const empty=node('div','empty');empty.append(node('strong','','✓ '+(view==='queue'?'比价队列是空的':'这里暂时没有商品')),
      node('p','',view==='queue'?'到待整理中勾选想买的商品。':'可以调整筛选，或读取最新的闲鱼收藏。'),button(view==='queue'?'去待整理':'清除筛选',()=>{if(view==='queue')changeView('inbox');else{$('#search').value='';$('#filter').value='all';render();}}));list.append(empty);
  }
}
function edit(value,kind) {
  editing={value,kind};const isTask=kind==='task';
  $('#edit-heading').textContent=isTask?'编辑比价任务':'编辑商品';$('#title-label').textContent=isTask?'比价类型':'清单名称';
  $('#edit-title').value=isTask?value.title:value.display_title;$('#edit-group').value=value.group_label||'';
  $('#group-field').hidden=isTask;$('#edit-group').required=!isTask;
  $('#edit-notes').value=isTask?value.criteria:value.notes;$('#edit-error').textContent='';
  const options=$('#group-options');options.replaceChildren();[...new Set(data.items.map(x=>x.group_label))].forEach(x=>{const option=node('option');option.value=x;options.append(option);});
  $('#edit-dialog').showModal();$('#edit-title').focus();
}
$('#edit-form').addEventListener('submit',async e=>{
  e.preventDefault();const {value,kind}=editing;const save=e.submitter;save.disabled=true;
  try { await api(kind==='task'?`/api/tasks/${value.id}/edit`:`/api/items/${value.id}/edit`,kind==='task'?{title:$('#edit-title').value,criteria:$('#edit-notes').value}:{title:$('#edit-title').value,notes:$('#edit-notes').value,group_label:$('#edit-group').value});$('#edit-dialog').close();await load();toast('修改已保存'); }
  catch(e){$('#edit-error').textContent=e.message;}finally{save.disabled=false;}
});
$('#close-dialog').onclick=$('#cancel-edit').onclick=()=>$('#edit-dialog').close();
$('#edit-dialog').addEventListener('click',e=>{if(e.target===$('#edit-dialog')){const r=e.target.getBoundingClientRect();if(e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom)e.target.close();}});
document.querySelectorAll('[data-view]').forEach(b=>b.onclick=()=>changeView(b.dataset.view));
$('#search').addEventListener('input',render);$('#filter').addEventListener('change',render);
async function sync(submit) {try{await api('/api/sync',{submit});await load();}catch(e){toast(e.message);}}
$('#refresh').onclick=()=>sync(false);$('#sync').onclick=()=>sync(true);
load();
