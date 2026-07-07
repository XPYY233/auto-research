const state = { summary: {}, papers: [], queue: [], selected: null };
const labels = {
  measured: "直接测量", derived: "推导量", calculated: "计算量", qualitative: "定性结论",
  exact_table: "表格精确值", exact_text: "正文精确值", trend: "趋势", figure_only: "仅图中可见",
  figure_digitization: "待读曲线", ocr: "等待OCR", missing_supplement: "缺少补充材料", ambiguous_condition: "条件待确认"
};

async function api(url, options={}) {
  const response = await fetch(url, options);
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.error || `请求失败 ${response.status}`);
  return payload;
}

function esc(value) { return String(value ?? "").replace(/[&<>'"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c])); }
function toast(message, error=false) { const el=document.querySelector('#toast'); el.textContent=message; el.className=error?'show error':'show'; clearTimeout(toast.timer); toast.timer=setTimeout(()=>el.className='',2800); }

async function loadAll() {
  [state.summary, state.papers, state.queue] = await Promise.all([
    api('/api/summary'), api('/api/papers'), api('/api/measurements?status=draft&include_drafts=1')
  ]);
  renderSummary(); renderQueue(); renderPapers(); await renderTasks();
}

function renderSummary() {
  const s=state.summary, review=s.review||{};
  setText('paper-count',s.pilot_papers); setText('queue-count',review.draft||0); setText('verified-count',review.verified||0); setText('task-count',s.open_tasks);
  setText('stat-papers',s.pilot_papers); setText('stat-measurements',s.measurements); setText('stat-verified',review.verified||0);
  setText('stat-context',`${s.materials||0} / ${s.experiments||0}`);
  const complete=(review.verified||0)+(review.rejected||0)+(review.ambiguous||0), inBatch=complete%5;
  setText('batch-label',`第 ${Math.floor(complete/5)+1} 组 · ${inBatch}/5`);
  document.querySelector('#batch-bar').style.width=`${inBatch*20}%`;
}

function setText(id,value){ document.getElementById(id).textContent=value ?? 0; }

function renderQueue() {
  const el=document.querySelector('#queue-list');
  if (!state.queue.length) { el.innerHTML='<div class="empty-state"><h3>待核验队列为空</h3><p>导入新的AI抽取结果后会出现在这里。</p></div>'; return; }
  el.innerHTML=state.queue.map((row,index)=>`<button class="queue-item ${state.selected?.id===row.id?'active':''}" data-index="${index}"><span class="qid">${esc(row.pilot_code||'P?')} · ${row.id}</span><span><strong>${esc(row.parameter)}</strong><span>${esc(row.value_raw)} ${esc(row.unit_raw||'')} · ${esc(row.condition_text||'条件待确认')}</span></span></button>`).join('');
  el.querySelectorAll('.queue-item').forEach(btn=>btn.addEventListener('click',()=>selectQueue(Number(btn.dataset.index))));
}

function selectQueue(index) { state.selected=state.queue[index]; renderQueue(); renderReviewCard(state.selected); }

function renderReviewCard(row) {
  const card=document.querySelector('#review-card'); card.classList.remove('empty');
  card.innerHTML=`
    <header class="card-paper"><div class="meta"><span>${esc(row.pilot_code||'—')}</span><span>${esc(row.year||'年份未知')}</span><span>DOI ${esc(row.doi||'—')}</span></div><h3>${esc(row.paper_title)}</h3></header>
    <div class="review-body">
      <div class="chain"><div><small>样品</small><strong>${esc(row.material_label||row.composition||'待确认')}</strong></div><span>→</span><div><small>辐照</small><strong>${esc(row.particle||row.irradiation_type||'待确认')}</strong></div><span>→</span><div><small>测量</small><strong>${esc(row.parameter)}</strong></div></div>
      <div class="form-grid">
        <label>物理量<input id="f-parameter" value="${esc(row.parameter)}"></label>
        <label>报告值<input id="f-value" value="${esc(row.value_raw)}"></label>
        <label>单位<input id="f-unit" value="${esc(row.unit_raw||'')}"></label>
        <label>证据类型<select id="f-type">${options(['measured','derived','calculated','qualitative'],row.evidence_type)}</select></label>
        <label>来源精度<select id="f-precision">${options(['exact_table','exact_text','trend','figure_only'],row.source_precision)}</select></label>
        <label>条件<input id="f-condition" value="${esc(row.condition_text||'')}"></label>
      </div>
      <div class="evidence-box">
        <div class="source-actions"><strong>原文证据</strong><button class="text-link" id="open-pdf">打开PDF${row.page_number?'第 '+row.page_number+'页':''}</button></div>
        <div class="form-grid"><label>页码<input id="f-page" type="number" min="1" value="${esc(row.page_number||'')}"></label><label>表/图/章节<input id="f-locator" value="${esc(row.locator||'')}"></label><label class="wide">必要原文片段<textarea id="f-excerpt">${esc(row.excerpt||'')}</textarea></label></div>
      </div>
      <div class="review-actions"><button class="button verify" data-decision="verified">确认并发布</button><button class="button warn" data-decision="ambiguous">条件有歧义</button><button class="button reject" data-decision="rejected">驳回</button></div>
    </div>`;
  card.querySelector('#open-pdf').addEventListener('click',()=>window.open(`/api/papers/${row.paper_id}/pdf${row.page_number?'#page='+row.page_number:''}`,'_blank'));
  card.querySelectorAll('[data-decision]').forEach(btn=>btn.addEventListener('click',()=>review(row,btn.dataset.decision)));
}

function options(values,current){return values.map(v=>`<option value="${v}" ${v===current?'selected':''}>${labels[v]}</option>`).join('');}

async function review(row,decision) {
  const page=document.querySelector('#f-page').value, locator=document.querySelector('#f-locator').value.trim(), excerpt=document.querySelector('#f-excerpt').value.trim();
  if (decision==='verified' && !page && !locator) return toast('确认前必须填写页码或表/图编号。',true);
  const changes={parameter:document.querySelector('#f-parameter').value.trim(),value_raw:document.querySelector('#f-value').value.trim(),unit_raw:document.querySelector('#f-unit').value.trim()||null,evidence_type:document.querySelector('#f-type').value,source_precision:document.querySelector('#f-precision').value,condition_text:document.querySelector('#f-condition').value.trim()||null};
  try {
    await api(`/api/measurements/${row.id}/review`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({decision,reviewer:'本地研究者',changes,evidence:{page_number:page?Number(page):null,locator:locator||null,excerpt:excerpt||null}})});
    toast(decision==='verified'?'已确认并进入默认检索。':decision==='rejected'?'已驳回。':'已标记为条件有歧义。'); state.selected=null; await loadAll(); document.querySelector('#review-card').className='review-card empty'; document.querySelector('#review-card').innerHTML='<div class="empty-state"><span>✓</span><h3>记录已处理</h3><p>选择下一条继续核验。</p></div>';
  } catch(error){toast(error.message,true);}
}

async function search(event) {
  event?.preventDefault(); const form=new FormData(document.querySelector('#filters')); const query=new URLSearchParams();
  for(const [key,value] of form.entries()) if(value) query.set(key,value);
  document.querySelector('#export-link').href='/api/export.csv?'+query.toString();
  try { const rows=await api('/api/measurements?'+query.toString()); renderSearch(rows); } catch(error){toast(error.message,true);}
}

function renderSearch(rows) {
  const body=document.querySelector('#search-results');
  if(!rows.length){body.innerHTML='<tr><td colspan="5">没有符合条件的已确认记录。可先去核验队列发布数据。</td></tr>';return;}
  body.innerHTML=rows.map(r=>`<tr><td><strong>${esc(r.material_label||r.composition||r.material_focus||'—')}</strong><small>${esc(r.temperature_raw||'')} ${esc(r.dose_raw||'')}</small></td><td><strong>${esc(r.parameter)}</strong><small>${esc(r.measurement_method||r.category)}</small></td><td><strong>${esc(r.value_raw)} ${esc(r.unit_raw||'')}</strong><small>${r.normalized_value!=null?'标准化 '+esc(r.normalized_value)+' '+esc(r.normalized_unit):'保留原始单位'}</small></td><td><span class="type-pill ${r.evidence_type}">${labels[r.evidence_type]}</span></td><td><strong>${esc(r.pilot_code||'')} ${esc(r.locator||('p.'+(r.page_number||'?')))}</strong><small>${esc(r.paper_title)}</small></td></tr>`).join('');
}

function renderPapers(){const el=document.querySelector('#paper-grid');const papers=state.papers.filter(p=>String(p.pilot_code||'').startsWith('P'));el.innerHTML=papers.map(p=>`<article class="paper-card"><header><span>${esc(p.pilot_code||'候选')}${p.pilot_code==='P01'?' · 首篇教学':''}</span><span>${esc(p.material_focus||'未分类')}</span></header><h3>${esc(p.title)}</h3><footer><span>${esc(p.year||'—')} · ${p.measurement_count||0}条 · ${esc(p.parse_status)}</span><button class="text-link" data-prompt="${p.id}">生成抽取包</button></footer></article>`).join('');el.querySelectorAll('[data-prompt]').forEach(btn=>btn.addEventListener('click',async()=>{try{const result=await api(`/api/papers/${btn.dataset.prompt}/prompt`,{method:'POST',headers:{'Content-Type':'application/json'},body:'{"max_pages":8}'});toast('抽取包已生成：'+result.path);await loadAll();}catch(e){toast(e.message,true)}}));}

async function renderTasks(){const tasks=await api('/api/tasks');const el=document.querySelector('#task-list');if(!tasks.length){el.innerHTML='<div class="empty-state"><h3>没有开放任务</h3></div>';return;}el.innerHTML=tasks.map(t=>`<article class="task-item"><code>${labels[t.task_type]||t.task_type}</code><div><strong>${esc(t.paper_title)}</strong><p>${esc(t.description)} ${t.locator?'· '+esc(t.locator):''}</p></div><button class="button secondary" data-task="${t.id}">标记已处理</button></article>`).join('');el.querySelectorAll('[data-task]').forEach(btn=>btn.addEventListener('click',async()=>{try{await api('/api/tasks/'+btn.dataset.task,{method:'POST',headers:{'Content-Type':'application/json'},body:'{"status":"resolved"}'});toast('待办已处理。');await loadAll();}catch(e){toast(e.message,true)}}));}

document.querySelectorAll('.nav-item').forEach(btn=>btn.addEventListener('click',()=>{document.querySelectorAll('.nav-item,.view').forEach(x=>x.classList.remove('active'));btn.classList.add('active');document.querySelector('#view-'+btn.dataset.view).classList.add('active');if(btn.dataset.view==='search')search();}));
document.querySelector('#filters').addEventListener('submit',search);
loadAll().catch(error=>toast(error.message,true));
