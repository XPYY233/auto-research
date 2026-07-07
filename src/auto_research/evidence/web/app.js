const state={paper:null,rows:[],selected:null,filter:"",search:""};
const fields=["value_text","meaning","unit","article_title","doi","context_explanation"];
const fieldLabels={value_text:"具体数值",meaning:"具体意义",unit:"单位",article_title:"文章题目",doi:"DOI",context_explanation:"数据在文中的解释"};

async function api(url,options={}){const r=await fetch(url,options);const body=await r.json().catch(()=>({}));if(!r.ok)throw new Error(body.error||`请求失败 ${r.status}`);return body}
function esc(v){return String(v??"").replace(/[&<>'"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]))}
function toast(message,error=false){const el=document.querySelector('#toast');el.textContent=message;el.className=error?'show error':'show';clearTimeout(toast.timer);toast.timer=setTimeout(()=>el.className='',3200)}

async function load(){[state.paper,state.rows]=await Promise.all([api('/api/target-paper'),api('/api/six-data')]);renderPaper();renderTable();renderHistory();fillManualDefaults()}

function renderPaper(){const p=state.paper;setText('paper-key',p.local_article_key||p.zotero_key||p.pilot_code);setText('paper-title',p.title);setText('paper-doi',p.doi);setText('mini-title',p.title);setText('mini-doi',p.doi);setText('nav-count',state.rows.length);document.querySelector('#open-paper').href=`/api/papers/${p.id}/pdf`}
function setText(id,v){document.getElementById(id).textContent=v??''}

function filteredRows(){const q=state.filter.trim().toLowerCase();if(!q)return state.rows;return state.rows.filter(row=>fields.some(f=>String(row[f]||'').toLowerCase().includes(q)))}
function renderTable(){const rows=filteredRows();setText('row-count',`${rows.length} 条`);const body=document.querySelector('#edit-rows');body.innerHTML=rows.map(row=>{
  const cls=[state.selected===row.item_id?'selected':'',row.version_no>0?'revised':'',row.origin_type==='manual'?'manual':''].filter(Boolean).join(' ');
  const cells=fields.map(field=>`<td><textarea class="cell ${field==='context_explanation'?'context':''}" data-field="${field}" aria-label="${fieldLabels[field]}">${esc(row[field])}</textarea></td>`).join('');
  const badge=row.origin_type==='manual'?'人工':row.version_no>0?`已修正 v${row.version_no}`:'未修正';
  return `<tr class="${cls}" data-item="${row.item_id}">${cells}<td><div class="row-action"><button class="confirm" data-confirm="${row.item_id}">确认修正</button><button data-original="${row.item_id}">查看原始</button><small>${badge}</small></div></td></tr>`
}).join('');
  body.querySelectorAll('tr[data-item]').forEach(tr=>tr.addEventListener('click',e=>{if(e.target.closest('button[data-confirm]'))return;selectRow(Number(tr.dataset.item))}));
  body.querySelectorAll('[data-original]').forEach(btn=>btn.addEventListener('click',e=>{e.stopPropagation();selectRow(Number(btn.dataset.original))}));
  body.querySelectorAll('[data-confirm]').forEach(btn=>btn.addEventListener('click',e=>{e.stopPropagation();confirmRow(Number(btn.dataset.confirm))}));
}

function selectRow(id){state.selected=id;const row=state.rows.find(r=>r.item_id===id);renderOriginal(row);document.querySelectorAll('#edit-rows tr').forEach(tr=>tr.classList.toggle('selected',Number(tr.dataset.item)===id))}
function originalValue(row,field){return row.origin_type==='automatic'?row[`original_${field}`]:null}
function renderOriginal(row){const pane=document.querySelector('#original-pane');if(row.origin_type==='manual'){pane.innerHTML=`<div class="blank"><span>＋</span><h3>人工补录数据</h3><p>这条记录不是自动提取结果，因此没有不可变的原始版本。</p></div>`;return}
  const sourcePage=row.original_source_page;
  pane.innerHTML=`<header class="original-head"><span>IMMUTABLE ORIGINAL · #${row.item_id}</span><h3>${esc(row.original_meaning)}</h3></header><div class="original-grid">${fields.map(f=>`<div class="original-field ${f==='context_explanation'?'context':''}"><small>${fieldLabels[f]}</small><p>${esc(originalValue(row,f))}</p></div>`).join('')}</div><div class="provenance"><strong>论文定位</strong><p>PDF第 ${esc(sourcePage||'?')} 页 · ${esc(row.original_source_locator||'未标注')}<br>${esc(row.original_source_excerpt||'')}</p><button class="source-open" type="button" data-source-open="${row.item_id}">打开原文定位并高亮 →</button></div>`;
  pane.querySelector('[data-source-open]')?.addEventListener('click',()=>openSourceViewer(row.item_id))
}

function sourceMetaLoading(row){return `<article class="source-meta-card"><strong>正在定位</strong><p>${esc(row.original_meaning||row.meaning)}<br>PDF第 ${esc(row.original_source_page||row.source_page||'?')} 页 · ${esc(row.original_source_locator||row.source_locator||'未标注')}</p></article>`}
function sourceMetaHtml(data){return `<article class="source-meta-card"><strong>${esc(data.match_label)}</strong><p>PDF第 ${esc(data.page_number)} 页 · ${esc(data.locator||'未标注')}<br>${esc(data.match_note)}</p></article><article class="source-meta-card"><strong>证据片段</strong><p>${esc(data.excerpt||'未保留原始证据片段')}</p></article>`}

async function openSourceViewer(id){const row=state.rows.find(r=>r.item_id===id);if(!row)return;const dialog=document.querySelector('#source-dialog');const meta=document.querySelector('#source-meta');const image=document.querySelector('#source-image');const pdfLink=document.querySelector('#source-open-pdf');meta.innerHTML=sourceMetaLoading(row);image.removeAttribute('src');image.alt='正在加载高亮原文页';pdfLink.removeAttribute('href');if(typeof dialog.showModal==='function'){if(!dialog.open)dialog.showModal()}else{dialog.setAttribute('open','open')}
  try{const data=await api(`/api/six-data/${id}/source-view`);meta.innerHTML=sourceMetaHtml(data);image.src=`${data.image_url}?ts=${Date.now()}`;image.alt=`${data.match_label}：PDF 第 ${data.page_number} 页`;pdfLink.href=data.pdf_url}catch(e){meta.innerHTML=`<article class="source-meta-card error"><strong>定位失败</strong><p>${esc(e.message)}</p></article>`;toast(e.message,true)}}

function collectRowFields(id){const tr=document.querySelector(`tr[data-item="${id}"]`);if(!tr)throw new Error('当前表格中找不到这条数据');const values={};tr.querySelectorAll('[data-field]').forEach(input=>values[input.dataset.field]=input.value);return values}
async function confirmRow(id){try{const values=collectRowFields(id);const current=state.rows.find(r=>r.item_id===id);const changed=fields.filter(f=>String(values[f]??'')!==String(current[f]??''));if(!changed.length){toast('没有检测到修改；原始数据保持不变。');return}const result=await api(`/api/six-data/${id}/confirm`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({fields:values,editor:'本地研究者',note:`修改字段：${changed.map(f=>fieldLabels[f]).join('、')}`})});state.rows=state.rows.map(r=>r.item_id===id?result:r);state.selected=id;renderTable();renderOriginal(result);renderHistory();toast(`已确认修正并创建版本 v${result.version_no}；自动提取原始版本未改变。`)}catch(e){toast(e.message,true)}}

function fillManualDefaults(){const form=document.querySelector('#manual-form');form.elements.article_title.value=state.paper.title;form.elements.doi.value=state.paper.doi}
async function saveManual(event){event.preventDefault();const form=event.currentTarget;const values={};fields.forEach(f=>values[f]=form.elements[f].value);try{const result=await api('/api/six-data/manual',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({paper_id:state.paper.id,fields:values,editor:'本地研究者'})});state.rows.push(result);form.reset();fillManualDefaults();renderTable();renderHistory();setText('nav-count',state.rows.length);toast('人工数据已保存；该记录没有自动提取原始版本。')}catch(e){toast(e.message,true)}}

async function runSearch(event){event?.preventDefault();const q=document.querySelector('#search-query').value.trim();state.search=q;try{const rows=await api('/api/six-search?q='+encodeURIComponent(q));setText('search-summary',q?`“${q}” 找到 ${rows.length} 条相关数据`:`显示全部 ${rows.length} 条数据`);document.querySelector('#search-export').href='/api/six-export.csv?q='+encodeURIComponent(q);renderResults(rows)}catch(e){toast(e.message,true)}}
function renderResults(rows){const el=document.querySelector('#search-results');if(!rows.length){el.innerHTML='<div class="blank"><h3>没有找到相关数据</h3><p>尝试材料名称、环境条件、物理量或它们的组合。</p></div>';return}el.innerHTML=rows.map(r=>`<article class="result"><div class="value">${esc(r.value_text)}<small> ${esc(r.unit)}</small></div><strong>${esc(r.meaning)}</strong><em>${r.search_score!=null?'相关度 '+esc(r.search_score):''}</em><p>${esc(r.context_explanation)}</p><button class="row-link" data-jump="${r.item_id}">去校对</button></article>`).join('');el.querySelectorAll('[data-jump]').forEach(btn=>btn.addEventListener('click',()=>jumpToRow(Number(btn.dataset.jump))))}
function jumpToRow(id){state.filter='';document.querySelector('#table-filter').value='';switchView('review');renderTable();selectRow(id);const tr=document.querySelector(`tr[data-item="${id}"]`);tr?.scrollIntoView({block:'center',behavior:'smooth'})}

function renderHistory(){const changed=state.rows.filter(r=>r.version_no>0||r.origin_type==='manual').sort((a,b)=>String(b.created_at).localeCompare(String(a.created_at)));const el=document.querySelector('#history-list');if(!changed.length){el.innerHTML='<div class="blank"><h3>还没有已确认修正</h3><p>左侧表格里的临时输入不会出现在这里。</p></div>';return}el.innerHTML=changed.map(r=>`<article class="history-card"><span>${r.origin_type==='manual'?'人工补录':`版本 v${r.version_no}`}</span><div><strong>${esc(r.meaning)} · ${esc(r.value_text)} ${esc(r.unit)}</strong><p>${esc(r.context_explanation)}</p></div><div><strong>${esc(r.editor)}</strong><p>${esc(r.edit_note||'')}<br>${esc(r.created_at)}</p></div></article>`).join('')}

function switchView(name){document.querySelectorAll('.nav,.view').forEach(el=>el.classList.remove('active'));document.querySelector(`.nav[data-view="${name}"]`).classList.add('active');document.querySelector(`#view-${name}`).classList.add('active');if(name==='search'&&!document.querySelector('#search-results').children.length)runSearch()}
document.querySelectorAll('.nav').forEach(btn=>btn.addEventListener('click',()=>switchView(btn.dataset.view)));
document.querySelector('#table-filter').addEventListener('input',event=>{state.filter=event.target.value;renderTable()});
document.querySelector('#search-form').addEventListener('submit',runSearch);
document.querySelector('#manual-form').addEventListener('submit',saveManual);
document.querySelector('[data-close-source]')?.addEventListener('click',()=>document.querySelector('#source-dialog')?.close());
document.querySelector('#source-dialog')?.addEventListener('click',event=>{const dialog=event.currentTarget;if(event.target===dialog)dialog.close()});
load().catch(e=>toast(e.message,true));
