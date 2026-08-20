(() => {
  "use strict";
  const VIEWS = Object.freeze(["paper", "search", "personal", "package", "settings"]);
  const VIEW_LABELS = Object.freeze({paper:"文献",search:"搜索",personal:"实验",package:"资料包",settings:"设置"});
  const STORAGE_KEY = "auto-research-fusion-appearance-v1";
  const ROUTES = Object.freeze({papers:"/api/search-papers",evidence:"/api/search-v2",visualAsset:"/api/visual-assets",federatedSearch:"/api/desktop/federated-search",federatedEvidence:"/api/desktop/federated-evidence",settings:"/api/desktop/settings",preferences:"/api/desktop/settings/preferences",aiProviders:"/api/desktop/ai/providers",aiSettings:"/api/desktop/ai/settings",upload:"/api/uploads/pdf",consent:"/api/desktop/ai/consents",personalPreview:"/api/desktop/personal-imports/preview"});
  const AI_SCOPES = new Set(["librarian","literature_extraction","personal_suggestion"]);
  const AI_CALL_LIMITS = Object.freeze({librarian:8,literature_extraction:512,personal_suggestion:1});
  const CSRF_HEADER = "X-Auto-Research-CSRF";
  const syntheticSheets = {
    hardness: {
      label:"硬度测量", summary:"硬度测量 · 12 行 · 5 列 · 合成数据",
      columns:[
        {name:"温度", role:"实验条件", meaning:"测试时样品温度", unit:"°C"},
        {name:"纳米硬度", role:"测量值", meaning:"压入硬度", unit:"GPa"},
        {name:"误差", role:"不确定度", meaning:"硬度标准差", unit:"GPa"},
        {name:"Ta含量", role:"实验条件", meaning:"钽原子百分比", unit:"at.%"},
        {name:"样品编号", role:"标识符", meaning:"合成样品标识", unit:"—"},
      ],
      rows:Object.freeze([
        [25,4.12,0.08,0,"W-00"],[100,4.08,0.07,0,"W-00"],[200,4.01,0.09,2,"W-Ta02"],
        [300,3.96,0.08,2,"W-Ta02"],[400,3.89,0.10,5,"W-Ta05"],[500,3.82,0.09,5,"W-Ta05"],
        [25,4.36,0.06,8,"W-Ta08"],[100,4.31,0.07,8,"W-Ta08"],[200,4.24,0.08,10,"W-Ta10"],
        [300,4.18,0.07,10,"W-Ta10"],[400,4.09,0.09,12,"W-Ta12"],[500,4.02,0.08,12,"W-Ta12"],
      ]),
    },
    metadata: {
      label:"样品信息", summary:"样品信息 · 4 行 · 3 列 · 合成数据",
      columns:[
        {name:"样品编号",role:"标识符",meaning:"合成样品标识",unit:"—"},
        {name:"材料",role:"样品信息",meaning:"名义成分",unit:"—"},
        {name:"测试方法",role:"方法",meaning:"合成演示方法",unit:"—"},
      ],
      rows:Object.freeze([["W-00","W","纳米压痕"],["W-Ta02","W-2Ta","纳米压痕"],["W-Ta08","W-8Ta","纳米压痕"],["W-Ta12","W-12Ta","纳米压痕"]]),
    },
  };
  const state = {view:"paper",sheet:"hardness",settingsSection:"appearance",row:0,column:0,drawer:null,focusReturn:null,paletteOpen:false,paletteFocusReturn:null,theme:"system",density:"comfortable",settingsRevision:null,csrfToken:"",aiContext:null,aiCatalog:null,aiSettings:null,aiCredential:null,papers:[],paper:null,evidence:[],searchResults:[],selectedEvidence:null,evidenceIndex:0,evidenceDetail:null,evidenceDetailOpen:false,evidenceDetailRequest:0,evidenceDetailReturn:null,evidenceDetailPDF:false,literatureStatus:"loading",literatureRequest:0,searchRequest:0,searchMode:"precise",searchSource:"workspace",literatureAction:0,librarianAction:0,librarianConversationId:null,librarianMessages:[],librarianResearch:null,librarianPending:null,personalAction:0,personalPreview:null,personalStatus:null,personalSuggestion:null,personalSheetIndex:0};
  const q = selector => document.querySelector(selector);
  const qa = selector => [...document.querySelectorAll(selector)];
  const esc = value => String(value ?? "").replace(/[&<>"']/g, character => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[character]));

  function isAllowedRoute(url,method) {
    const path=String(url).split("?")[0];
    if(method==="GET")return [ROUTES.papers,ROUTES.evidence,ROUTES.federatedSearch,ROUTES.federatedEvidence,ROUTES.settings,ROUTES.aiProviders,ROUTES.aiSettings].includes(path)||/^\/api\/visual-assets\/[1-9][0-9]*$/.test(path)||/^\/api\/desktop\/ai\/credentials\/(deepseek|openai)$/.test(path);
    if(method==="PATCH")return path===ROUTES.preferences||path===ROUTES.aiSettings;
    if(method==="DELETE")return /^\/api\/desktop\/ai\/credentials\/(deepseek|openai)$/.test(path);
    if(method!=="POST")return false;
    return path===ROUTES.upload||path===ROUTES.consent||path===ROUTES.personalPreview||/^\/api\/desktop\/ai\/credentials\/(deepseek|openai)$/.test(path)||/^\/api\/desktop\/ai\/providers\/(deepseek|openai)\/(test-actions|test)$/.test(path)||/^\/api\/desktop\/ai\/actions\/(librarian|literature_extraction|personal_suggestion)\/(prepare|execute)$/.test(path)||/^\/api\/desktop\/personal-imports\/personal_import_[A-Za-z0-9_-]{16,96}\/reviewed-import$/.test(path);
  }
  async function request(url,{method="GET",headers={},body=null,signal=null}={}) {
    method=String(method).toUpperCase();if(!isAllowedRoute(url,method))throw safeError("fusion_route_blocked","该功能路由未获 Fusion 工作台授权。");
    const requestHeaders=new Headers(headers);requestHeaders.set("Accept","application/json");if(method!=="GET"&&state.csrfToken)requestHeaders.set(CSRF_HEADER,state.csrfToken);
    const response=await fetch(url,{method,headers:requestHeaders,body,credentials:"same-origin",cache:"no-store",signal});const csrf=response.headers?.get?.(CSRF_HEADER);if(csrf)state.csrfToken=csrf;
    const payload=await response.json().catch(()=>({}));if(!response.ok)throw safeError(String(payload.code||"fusion_request_failed"),String(payload.message||payload.error||"操作未完成，请稍后重试。"));return payload;
  }
  function safeError(code,message){const error=new Error(String(message||"操作未完成，请稍后重试。"));error.code=String(code||"fusion_request_failed");return error;}
  async function readOnlyJSON(url, signal) {
    const path=String(url).split("?")[0],fixed=[ROUTES.papers,ROUTES.evidence,ROUTES.federatedSearch,ROUTES.federatedEvidence,ROUTES.settings,ROUTES.aiProviders,ROUTES.aiSettings];
    if (!fixed.includes(path)&&!/^\/api\/visual-assets\/[1-9][0-9]*$/.test(path)) throw safeError("fusion_read_route_invalid","只读请求无效。");
    return request(url,{method:"GET",signal});
  }
  async function patchPreferences() {
    if(!Number.isInteger(state.settingsRevision)||!state.csrfToken)throw new Error("fusion_settings_unavailable");
    const payload=await request(ROUTES.preferences,{method:"PATCH",headers:{"Content-Type":"application/json"},body:JSON.stringify({expected_revision:state.settingsRevision,preferences:{appearance:{theme:state.theme,density:state.density}}})});hydrateSettings(payload);return payload;
  }
  function literatureState(kind, message) {
    return `<div class="fusion-state-card large" data-literature-state="${kind}"><strong>${esc(message)}</strong><span>${kind==="error"?"请确认本机工作区可用后重试；系统不会自动发起收费 AI 请求。":"结果仅展示公开安全字段；写入与收费动作由你分别明确启动。"}</span></div>`;
  }
  function publicPaper(raw) {
    const id = Number(raw?.id);
    return Number.isSafeInteger(id) && id > 0 && String(raw?.title || "").trim() ? {id,title:String(raw.title).trim(),doi:String(raw.doi||"").trim(),year:raw.year||"",firstAuthor:String(raw.first_author||"").trim(),material:String(raw.material_focus||"").trim(),count:Math.max(0,Number(raw.six_row_count)||0),status:String(raw.six_workflow_label||raw.six_workflow_state||"只读")} : null;
  }
  const cleanText=(value,limit=8000)=>String(value??"").trim().slice(0,limit);
  const cleanList=(value,limit=200)=>Array.isArray(value)?value.slice(0,limit).map(item=>cleanText(item,800)).filter(Boolean):[];
  const cleanVariables=value=>value&&typeof value==="object"&&!Array.isArray(value)?Object.fromEntries(Object.entries(value).slice(0,200).map(([key,item])=>[cleanText(key,300),cleanText(item,1200)]).filter(([key])=>key)):{};
  const positiveId=value=>{const id=Number(value);return Number.isSafeInteger(id)&&id>0?id:null;};
  function publicEvidence(raw) {
    const type=cleanText(raw?.entity_type||raw?.asset_type,20);if(!["item","finding","table","figure"].includes(type))return null;
    const sourceScope=["workspace","official","private"].includes(raw?.source_scope)?String(raw.source_scope):"workspace",sourceId=sourceScope==="workspace"?"":cleanText(raw?.source_id,500),entityUid=sourceScope==="workspace"?"":cleanText(raw?.entity_uid,500);
    if(sourceScope!=="workspace"&&(!sourceId||!entityUid))return null;
    const assetId=sourceScope==="workspace"&&["table","figure"].includes(type)?positiveId(raw?.id):null,itemId=sourceScope==="workspace"&&["item","finding"].includes(type)?positiveId(raw?.item_id??raw?.id):null,paperId=sourceScope==="workspace"?positiveId(raw?.paper_id):null;
    const imageCandidate=cleanText(raw?.image_url,500),imageUrl=assetId&&imageCandidate===`${ROUTES.visualAsset}/${assetId}/image`?imageCandidate:"";
    return {type,title:cleanText(raw?.meaning||raw?.display_title||raw?.display_name||raw?.label||raw?.caption||raw?.finding_text||"未命名证据",1200),value:cleanText(raw?.value_text||(type==="finding"?"定性结论":""),1000),findingText:cleanText(raw?.finding_text,8000),unit:cleanText(raw?.unit,300),page:raw?.source_page??raw?.page_start??"—",pageEnd:raw?.page_end??null,excerpt:cleanText(raw?.source_excerpt||raw?.original_source_excerpt||raw?.finding_text,8000),context:cleanText(raw?.context_explanation,8000),sourceContext:cleanText(raw?.source_context,8000),locator:cleanText(raw?.source_locator||raw?.original_source_locator,1000),articleTitle:cleanText(raw?.article_title,1200),doi:cleanText(raw?.doi,500),firstAuthor:cleanText(raw?.first_author,500),correspondingAuthor:cleanText(raw?.corresponding_author,500),label:cleanText(raw?.label,300),caption:cleanText(raw?.caption,8000),materials:cleanList(raw?.materials),conditions:cleanText(raw?.conditions_text,6000),methods:cleanText(raw?.methods_text,4000),quantities:cleanList(raw?.physical_quantities),variables:cleanVariables(raw?.variables),tags:cleanList(raw?.tags),linkedItemCount:Math.max(0,Math.min(100000,Number(raw?.linked_item_count)||0)),qualityStatus:cleanText(raw?.quality_gate_status,80),sourceKind:cleanText(raw?.source_kind,80),sourceScope,sourceId,entityUid,assetId,itemId,paperId,imageUrl};
  }
  function renderPaperCatalog() {
    const host=q("#fusion-paper-catalog");if(!host)return;
    host.setAttribute("aria-busy","false");
    if(!state.papers.length){host.innerHTML='<div class="fusion-state-card" data-literature-state="empty"><strong>当前没有可浏览的文献</strong><span>请使用中央顶部“导入 PDF”加入本机文献工作区。</span></div>';return;}
    host.innerHTML=state.papers.map(paper=>`<button type="button" class="fusion-tree-row${paper.id===state.paper?.id?" active":""}" data-paper-id="${paper.id}" aria-pressed="${paper.id===state.paper?.id?"true":"false"}"><span>${esc(paper.title)}</span><b>${paper.count}</b></button>`).join("");
    qa("[data-paper-id]").forEach(button=>button.addEventListener("click",()=>selectPaper(Number(button.dataset.paperId))));
  }
  function resetEvidenceCounts(value="—") { ["item","finding","table","figure"].forEach(type=>{const node=q(`#fusion-count-${type}`);if(node)node.textContent=String(value);}); }
  function renderLiterature() {
    const host=q("#fusion-literature-content");if(!host)return;host.setAttribute("aria-busy","false");
    if(state.literatureStatus==="error"){resetEvidenceCounts();q("#fusion-current-paper-state").textContent="读取失败";q("#fusion-current-paper-count").textContent="—";host.innerHTML=literatureState("error","真实文献暂时无法载入");return;}
    if(!state.paper){resetEvidenceCounts(0);q("#fusion-current-paper-state").textContent="空";q("#fusion-current-paper-count").textContent="0";host.innerHTML=literatureState("empty","当前没有可浏览的文献");return;}
    const paper=state.paper,counts={item:0,finding:0,table:0,figure:0};state.evidence.forEach(row=>counts[row.type]+=1);
    ["item","finding","table","figure"].forEach(type=>{const node=q(`#fusion-count-${type}`);if(node)node.textContent=String(counts[type]);});
    q("#fusion-current-paper-state").textContent=paper.status||"只读";q("#fusion-current-paper-count").textContent=String(state.evidence.length);
    const lines=state.evidence.slice(0,12).map((row,index)=>`<button type="button" class="fusion-evidence-line${index===0?" selected":""}" data-evidence-index="${index}" aria-label="打开${esc(row.title)}的证据详情"><b>${esc(row.value||row.label||({table:"原始表格",figure:"论文图片"})[row.type]||"证据")}${row.unit?` <small>${esc(row.unit)}</small>`:""}</b><span>${esc(row.title)} · 第 ${esc(row.page)} 页</span><em>${({item:"测量条目",finding:"研究结论",table:"完整表格",figure:"论文图片"})[row.type]}</em></button>`).join("");
    host.innerHTML=`<article class="fusion-paper-heading"><span>READ-ONLY LITERATURE · LIVE PUBLIC SNAPSHOT</span><h1>${esc(paper.title)}</h1><p>${esc([paper.firstAuthor,paper.year,paper.doi,paper.material].filter(Boolean).join(" · ")||"公开元数据未提供")}</p><dl><div><dt>处理状态</dt><dd>${esc(paper.status)}</dd></div><div><dt>证据条目</dt><dd>${state.evidence.length}</dd></div><div><dt>模式</dt><dd>只读 GET</dd></div></dl></article><section class="fusion-readonly-block"><header><strong>公开四类证据概览</strong><span>只读</span></header>${lines||'<div class="fusion-state-card" data-literature-state="empty"><strong>这篇文献暂无公开证据</strong><span>目录信息仍可只读浏览。</span></div>'}</section>`;
    qa("[data-evidence-index]").forEach(button=>button.addEventListener("click",()=>void openEvidenceDetail(Number(button.dataset.evidenceIndex),button)));
    renderSearchEvidence();selectEvidence(0,{focus:false});
  }
  function renderSearchEvidence() {
    const host=q("#fusion-search-results");if(!host)return;const rows=state.searchResults;const results=rows.map((row,index)=>`<button type="button" class="fusion-result${index===state.evidenceIndex?" selected":""}" role="option" aria-selected="${index===state.evidenceIndex?"true":"false"}" data-search-evidence-index="${index}" aria-label="打开${esc(row.title)}的证据详情"><span>${({item:"测量条目",finding:"研究结论",table:"完整表格",figure:"论文图片"})[row.type]} · ${{workspace:"本机文献",official:"官方资料库",private:"我的实验"}[row.sourceScope]}</span><b>${esc(row.value||row.label||({table:"原始表格",figure:"论文图片"})[row.type]||"证据")}${row.unit?` <small>${esc(row.unit)}</small>`:""}</b><h2>${esc(row.title)}</h2><span class="fusion-result-meta">${esc(row.articleTitle||state.paper?.title||"")} · 第 ${esc(row.page)} 页</span>${row.excerpt?`<span class="fusion-result-excerpt">${esc(row.excerpt)}</span>`:""}</button>`).join("");
    const terminal=state.literatureStatus==="error"?literatureState("error","真实证据暂时无法载入"):literatureState("empty",state.paper?"当前论文暂无公开证据":"当前没有可浏览的文献");
    host.innerHTML=results||terminal;
    qa("[data-search-evidence-index]").forEach(button=>button.addEventListener("click",()=>void openEvidenceDetail(Number(button.dataset.searchEvidenceIndex),button)));
  }
  function selectEvidence(index,{focus=true}={}) {
    const rows=state.view==="search"?state.searchResults:state.evidence,row=rows[index];if(!row){state.selectedEvidence=null;updateInspector();return false;}
    state.evidenceIndex=index;state.selectedEvidence=row;
    qa("[data-evidence-index],[data-search-evidence-index]").forEach(button=>{const active=Number(button.dataset.evidenceIndex??button.dataset.searchEvidenceIndex)===index;button.classList.toggle("selected",active);button.setAttribute("aria-selected",active?"true":"false");});
    const detailTab=q('[data-tab="detail"]');if(detailTab)detailTab.disabled=false;updateInspector();
    if(focus)buttonForEvidence(index)?.focus({preventScroll:true});return true;
  }
  function buttonForEvidence(index){return state.view==="search"?q(`[data-search-evidence-index="${index}"]`):q(`[data-evidence-index="${index}"]`);}
  const EVIDENCE_LABELS=Object.freeze({item:"测量条目",finding:"研究结论",table:"原始表格",figure:"论文图片"});
  function evidenceIdentity(row){return row?.sourceScope==="workspace"?`workspace:${row.type}:${row.assetId||row.itemId||0}`:`${row?.sourceScope||""}:${row?.sourceId||""}:${row?.entityUid||""}`;}
  function detailDefinitionList(row){const values=[["论文",row.articleTitle],["DOI",row.doi],["来源页",row.pageEnd&&row.pageEnd!==row.page?`${row.page}–${row.pageEnd}`:row.page],["原文定位",row.locator],["作者",[row.firstAuthor,row.correspondingAuthor].filter(Boolean).join(" · ")],["物理量",row.quantities.join("、")],["变量 / 表头",Object.entries(row.variables).map(([key,value])=>`${key}：${value}`).join("；")],["材料 / 样品",row.materials.join("、")],["实验条件",row.conditions],["方法",row.methods],["关联条目",["table","figure"].includes(row.type)?`${row.linkedItemCount} 条`:""]].filter(([,value])=>String(value??"").trim());return values.length?`<dl class="fusion-detail-fields">${values.map(([label,value])=>`<div><dt>${esc(label)}</dt><dd>${esc(value)}</dd></div>`).join("")}</dl>`:"";}
  function detailState(kind,title,message){return `<div class="fusion-detail-terminal" data-detail-state="${kind}"><strong>${esc(title)}</strong><p>${esc(message)}</p></div>`;}
  function renderEvidenceDetail(row,{imageState="ready"}={}) {
    const host=q("#fusion-evidence-detail-body");if(!host||!row)return;host.setAttribute("aria-busy","false");q("#fusion-detail-heading").textContent=row.title;q("#fusion-detail-identity").textContent=`${EVIDENCE_LABELS[row.type]} · ${{workspace:"本机文献工作区",official:"官方资料库",private:"我的实验"}[row.sourceScope]}`;
    const visual=["table","figure"].includes(row.type),quantity=row.type==="finding"?(row.value||"定性结论"):(row.value?`${row.value}${row.unit?` ${row.unit}`:""}`:row.label||EVIDENCE_LABELS[row.type]);
    let media="";
    if(visual&&row.sourceScope!=="workspace")media=detailState("unavailable","该资料源不提供原图","这里只展示资料包或私人库公开的结构化字段；不会借用本机工作区编号，也不会生成替代图。");
    else if(visual&&imageState==="loading")media=detailState("loading","正在读取权威原图","图片来自本机 PDF 的 PyMuPDF 截图；不会调用模型或重新绘制。");
    else if(visual&&imageState==="error")media=detailState("error","原图暂时无法显示","结构化证据仍可核对；可返回列表后重试，现有数据未改变。");
    else if(visual&&row.imageUrl)media=`<figure class="fusion-detail-visual"><img id="fusion-detail-image" src="${esc(row.imageUrl)}" alt="${esc(`${row.label||row.title}的PDF权威截图`)}"><figcaption>${esc(row.caption||"原文未提供图注")}</figcaption></figure>`;
    else if(visual)media=detailState("unavailable","原图未提供","当前公开证据没有可安全加载的原图地址；系统不会伪造图片。");
    host.innerHTML=`<article class="fusion-detail-sheet" data-evidence-type="${row.type}"><header><span>${esc(EVIDENCE_LABELS[row.type])}</span><h1>${esc(row.title)}</h1><p>${esc([row.label,row.articleTitle,row.doi].filter(Boolean).join(" · "))}</p></header><div class="fusion-detail-layout"><section class="fusion-detail-primary"><div class="fusion-detail-measure"><small>${row.type==="finding"?"证据类型":"证据值 / 对象"}</small><strong>${esc(quantity)}</strong></div>${row.findingText?`<section><h2>论文报告的结论</h2><p>${esc(row.findingText)}</p></section>`:""}${media}${row.caption&&!media.includes("figcaption")?`<section><h2>原始图注</h2><p>${esc(row.caption)}</p></section>`:""}${row.context?`<section><h2>文章中的含义</h2><p>${esc(row.context)}</p></section>`:""}${row.sourceContext?`<section><h2>原文语境</h2><p>${esc(row.sourceContext)}</p></section>`:""}${row.excerpt?`<blockquote><span>原文证据</span><p>${esc(row.excerpt)}</p></blockquote>`:""}</section><aside>${detailDefinitionList(row)}${row.tags.length?`<div class="fusion-detail-tags">${row.tags.map(tag=>`<span>${esc(tag)}</span>`).join("")}</div>`:""}<p class="fusion-detail-provenance">截图由本机 PyMuPDF 视觉链建立；AI 不读取图片像素，也不从曲线推断数值。</p></aside></div></article>`;
    const image=q("#fusion-detail-image");if(image){image.addEventListener("load",()=>{if(state.evidenceDetailOpen&&evidenceIdentity(state.evidenceDetail)===evidenceIdentity(row))image.closest?.("figure")?.setAttribute("data-image-state","ready");},{once:true});image.addEventListener("error",()=>{if(state.evidenceDetailOpen&&evidenceIdentity(state.evidenceDetail)===evidenceIdentity(row))renderEvidenceDetail(row,{imageState:"error"});},{once:true});}
    const pdf=q("#fusion-detail-open-pdf");if(pdf)pdf.disabled=!(row.sourceScope==="workspace"&&row.paperId);
  }
  function showEvidenceDetailShell(row,focusReturn) {
    state.evidenceDetailOpen=true;state.evidenceDetail=row;state.evidenceDetailReturn={view:state.view,element:focusReturn||buttonForEvidence(state.evidenceIndex)};state.evidenceDetailPDF=false;
    qa("[data-view-panel]").forEach(panel=>{panel.hidden=true;panel.classList.remove("active");});const detail=q("#fusion-evidence-detail");if(detail)detail.hidden=false;q("#fusion-detail-pdf").hidden=true;q("#fusion-evidence-detail-body").hidden=false;
    qa(".fusion-tabs [role='tab']").forEach(tab=>{const active=tab.dataset.tab==="detail";tab.classList.toggle("active",active);tab.setAttribute("aria-selected",active?"true":"false");tab.tabIndex=active?0:-1;});const detailTab=q('[data-tab="detail"]');if(detailTab)detailTab.disabled=false;
    q("#fusion-detail-heading").textContent=row.title;q("#fusion-detail-identity").textContent=`${EVIDENCE_LABELS[row.type]} · 正在读取`;q("#fusion-evidence-detail-body").setAttribute("aria-busy","true");q("#fusion-evidence-detail-body").innerHTML=detailState("loading","正在准备证据详情","只读取当前公开证据，不调用 AI，也不修改数据。");
  }
  async function openEvidenceDetail(index,focusReturn=null) {
    const rows=state.view==="search"?state.searchResults:state.evidence,row=rows[index];if(!row)return false;selectEvidence(index,{focus:false});showEvidenceDetailShell(row,focusReturn);const request=++state.evidenceDetailRequest,identity=evidenceIdentity(row),originView=state.view;
    if(["item","finding"].includes(row.type)){renderEvidenceDetail(row);return true;}
    if(row.sourceScope==="workspace"&&!row.assetId){renderEvidenceDetail(row,{imageState:"error"});return false;}
    renderEvidenceDetail(row,{imageState:row.sourceScope==="workspace"?"loading":"ready"});
    try {const url=row.sourceScope==="workspace"?`${ROUTES.visualAsset}/${row.assetId}`:`${ROUTES.federatedEvidence}?source_scope=${encodeURIComponent(row.sourceScope)}&source_id=${encodeURIComponent(row.sourceId)}&entity_uid=${encodeURIComponent(row.entityUid)}`,raw=await readOnlyJSON(url);if(request!==state.evidenceDetailRequest||!state.evidenceDetailOpen||state.view!==originView||evidenceIdentity(state.evidenceDetail)!==identity)return false;const detail=publicEvidence(raw);if(!detail||detail.type!==row.type||detail.sourceScope!==row.sourceScope||(row.sourceScope==="workspace"&&detail.assetId!==row.assetId)||(row.sourceScope!=="workspace"&&evidenceIdentity(detail)!==identity))throw safeError("fusion_evidence_identity_changed","证据身份已变化。");state.evidenceDetail=detail;renderEvidenceDetail(detail);return true;}
    catch(_error){if(request!==state.evidenceDetailRequest||!state.evidenceDetailOpen||state.view!==originView||evidenceIdentity(state.evidenceDetail)!==identity)return false;renderEvidenceDetail(row,{imageState:"error"});return false;}
  }
  function closeDetailPDF({focus=true}={}){if(!state.evidenceDetailPDF)return false;state.evidenceDetailPDF=false;q("#fusion-detail-pdf").hidden=true;q("#fusion-detail-pdf-frame").removeAttribute?.("src");q("#fusion-evidence-detail-body").hidden=false;if(focus)q("#fusion-detail-open-pdf")?.focus({preventScroll:true});return true;}
  function openDetailPDF(){const row=state.evidenceDetail;if(!state.evidenceDetailOpen||row?.sourceScope!=="workspace"||!row.paperId)return false;state.evidenceDetailPDF=true;q("#fusion-evidence-detail-body").hidden=true;q("#fusion-detail-pdf").hidden=false;q("#fusion-detail-pdf-frame").src=`/api/papers/${encodeURIComponent(String(row.paperId))}/pdf${row.page&&row.page!=="—"?`#page=${encodeURIComponent(String(row.page))}`:""}`;q("#fusion-detail-pdf-title").textContent=row.articleTitle||"证据来源原文";q("#fusion-detail-close-pdf")?.focus({preventScroll:true});return true;}
  function closeEvidenceDetail({focus=true}={}){if(!state.evidenceDetailOpen)return false;state.evidenceDetailRequest+=1;closeDetailPDF({focus:false});const previous=state.evidenceDetailReturn;state.evidenceDetailOpen=false;state.evidenceDetail=null;state.evidenceDetailReturn=null;q("#fusion-evidence-detail").hidden=true;qa("[data-view-panel]").forEach(panel=>{const active=panel.dataset.viewPanel===state.view;panel.hidden=!active;panel.classList.toggle("active",active);});qa(".fusion-tabs [role='tab']").forEach(tab=>{const active=tab.dataset.tab==="primary";tab.classList.toggle("active",active);tab.setAttribute("aria-selected",active?"true":"false");tab.tabIndex=active?0:-1;});const detailTab=q('[data-tab="detail"]');if(detailTab)detailTab.disabled=!state.selectedEvidence;if(focus&&previous?.view===state.view&&previous.element?.isConnected)previous.element.focus({preventScroll:true});return true;}
  async function selectPaper(id) {
    const paper=state.papers.find(candidate=>candidate.id===id);if(!paper)return false;state.literatureAction+=1;closeEvidenceDetail({focus:false});closeCurrentPDF({focus:false});state.paper=paper;renderPaperCatalog();
    const request=++state.literatureRequest;state.evidenceIndex=0;state.literatureStatus="loading";q("#fusion-literature-content").innerHTML=literatureState("loading","正在读取真实文献证据…");
    q("#fusion-search-results").innerHTML=literatureState("loading","正在读取真实文献证据…");resetEvidenceCounts();q("#fusion-current-paper-state").textContent="载入中";q("#fusion-current-paper-count").textContent="—";
    try {const page=await readOnlyJSON(`${ROUTES.evidence}?q=&paper_ids=${encodeURIComponent(String(id))}&limit=100`);if(request!==state.literatureRequest)return false;state.evidence=(Array.isArray(page?.rows)?page.rows:[]).map(publicEvidence).filter(Boolean);state.searchResults=state.evidence;state.literatureStatus="ready";renderLiterature();renderPaperCatalog();syncPaperActions();return true;}
    catch(_error){if(request!==state.literatureRequest)return false;state.evidence=[];state.literatureStatus="error";renderLiterature();renderSearchEvidence();renderPaperCatalog();return false;}
  }
  async function loadLiterature() {
    const request=++state.literatureRequest;state.literatureStatus="loading";
    try {const papers=await readOnlyJSON(ROUTES.papers);if(request!==state.literatureRequest)return false;state.papers=(Array.isArray(papers)?papers:[]).map(publicPaper).filter(Boolean);renderPaperCatalog();if(!state.papers.length){state.paper=null;state.evidence=[];state.searchResults=[];state.literatureStatus="empty";renderLiterature();renderSearchEvidence();syncPaperActions();return true;}return selectPaper(state.papers[0].id);}
    catch(_error){if(request!==state.literatureRequest)return false;state.papers=[];state.paper=null;state.evidence=[];state.literatureStatus="error";renderPaperCatalog();renderLiterature();renderSearchEvidence();return false;}
  }
  function setOperation(message,kind="info") {const node=q("#fusion-status-operation");if(node){node.textContent=message;node.dataset.kind=kind;}}
  function setLiteratureActionStatus(message,kind="info") {const node=q("#fusion-literature-action-status");if(node){node.textContent=message;node.dataset.kind=kind;}setOperation(message,kind);}
  function syncPaperActions() {const ready=Boolean(state.paper?.id);const extraction=q("#fusion-start-extraction"),pdf=q("#fusion-open-pdf");if(extraction)extraction.disabled=!ready;if(pdf)pdf.disabled=!ready;}
  function aiRoute(scope,action){if(!AI_SCOPES.has(scope)||!["prepare","execute"].includes(action))throw safeError("ai_action_scope_invalid","该 AI 功能未受支持。");return `/api/desktop/ai/actions/${scope}/${action}`;}
  async function loadAIContext() {
    if(state.aiContext)return state.aiContext;
    const gate=globalThis.AutoResearchAIConsent;if(typeof gate?.ensure!=="function"||typeof gate?.updateTrustedProviders!=="function")throw safeError("ai_consent_gate_unavailable","AI 数据披露授权组件未载入；本次请求已安全停止。");
    const [catalog,settings]=await Promise.all([readOnlyJSON(ROUTES.aiProviders),readOnlyJSON(ROUTES.aiSettings)]);
    if(catalog?.schema_version!=="ai-desktop-catalog-v1"||!Array.isArray(catalog.providers)||settings?.schema_version!=="ai-runtime-public-state-v1")throw safeError("ai_public_state_invalid","受信 AI 提供商状态不可用。");
    if(gate.updateTrustedProviders(catalog.providers)!==true)throw safeError("ai_provider_catalog_invalid","受信 AI 提供商目录不可用。");
    const profile=catalog.providers.find(provider=>provider?.provider_id===settings.provider_id);if(!profile||typeof profile.display_name!=="string")throw safeError("ai_provider_untrusted","当前 AI 提供商不在受信目录中。");
    state.aiCatalog=catalog;state.aiSettings=settings;state.aiContext={provider_id:settings.provider_id,label:profile.display_name};return state.aiContext;
  }
  function aiStatus(message,kind="info"){const node=q("#fusion-ai-settings-status");if(node){node.textContent=message;node.dataset.kind=kind;}}
  function selectedAIProfile(){const id=q("#fusion-ai-provider")?.value;return state.aiCatalog?.providers?.find(provider=>provider.provider_id===id)||null;}
  function renderAISettings(providerId=state.aiSettings?.provider_id){const provider=q("#fusion-ai-provider");if(!provider||!state.aiCatalog||!state.aiSettings)return false;provider.innerHTML=state.aiCatalog.providers.map(profile=>`<option value="${esc(profile.provider_id)}">${esc(profile.display_name)}</option>`).join("");provider.value=providerId;provider.disabled=false;const profile=selectedAIProfile(),tasks={extraction:"文献提取",analysis:"证据分析",librarian_planning:"图书管理员规划",librarian_synthesis:"图书管理员回答"};q("#fusion-ai-models").innerHTML=Object.entries(tasks).map(([task,label])=>{const models=profile?.model_options?.[task]||[],selected=providerId===state.aiSettings.provider_id?state.aiSettings.task_models?.[task]:models[0];return `<div class="fusion-setting-row"><div><strong>${label}</strong><small>固定受信模型</small></div><select data-fusion-ai-task="${task}">${models.map(model=>`<option value="${esc(model)}"${model===selected?" selected":""}>${esc(model)}</option>`).join("")}</select></div>`;}).join("");q("#fusion-ai-model-save").disabled=!profile;q("#fusion-ai-key-save").disabled=!profile;q("#fusion-ai-key-delete").disabled=providerId!==state.aiSettings.provider_id||state.aiCredential?.configured!==true;q("#fusion-ai-test").disabled=providerId!==state.aiSettings.provider_id||state.aiCredential?.configured!==true;const plan=state.aiCatalog.capability_test;q("#fusion-ai-test-plan").textContent=plan?.provider_id===state.aiSettings.provider_id?`连接测试最多 ${Number(plan.maximum_model_calls||0)} 次模型调用（${Number(plan.unique_model_count||0)} 个模型），每次都需明确确认。`:"保存提供商后生成精确测试计划。";return true;}
  async function loadAISettingsUI(){try{state.aiContext=null;await loadAIContext();state.aiCredential=await request(`/api/desktop/ai/credentials/${state.aiSettings.provider_id}`);q("#fusion-ai-credential-state").textContent=state.aiCredential?.configured?"系统安全存储：已保存":"系统安全存储：未配置";renderAISettings();aiStatus(`已载入 ${state.aiContext.label} 受信配置。`,"success");return true;}catch(_error){aiStatus("无法读取受信 AI 设置；所有 AI 动作保持关闭。","error");return false;}}
  async function saveAIModels(){const profile=selectedAIProfile();if(!profile||!Number.isInteger(state.aiSettings?.revision))return;const taskModels=Object.fromEntries(qa("[data-fusion-ai-task]").map(select=>[select.dataset.fusionAiTask,select.value]));aiStatus("正在保存提供商与模型…","loading");try{await request(ROUTES.aiSettings,{method:"PATCH",headers:{"Content-Type":"application/json"},body:JSON.stringify({provider_id:profile.provider_id,task_models:taskModels,expected_revision:state.aiSettings.revision})});state.aiContext=null;await loadAISettingsUI();}catch(_error){aiStatus("AI 设置未保存；请重新读取后再试。","error");}}
  async function saveAIKey(){const profile=selectedAIProfile(),input=q("#fusion-ai-key"),apiKey=String(input?.value||"");if(!profile||!apiKey)return aiStatus("请先粘贴 API 密钥。","error");input.value="";aiStatus("正在保存到系统安全凭据存储…","loading");try{await request(`/api/desktop/ai/credentials/${profile.provider_id}`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({api_key:apiKey})});await loadAISettingsUI();}catch(_error){aiStatus("密钥未保存；请检查格式或稍后重试。","error");}}
  async function deleteAIKey(){const profile=selectedAIProfile();if(!profile||typeof globalThis.confirm!=="function"||!globalThis.confirm(`确认删除 ${profile.display_name} 的本机 API 密钥？`))return;try{await request(`/api/desktop/ai/credentials/${profile.provider_id}`,{method:"DELETE"});await loadAISettingsUI();aiStatus("本机 API 密钥已删除。","success");}catch(_error){aiStatus("密钥未删除，请稍后重试。","error");}}
  async function testAIConnection(){const profile=selectedAIProfile(),plan=state.aiCatalog?.capability_test;if(!profile||profile.provider_id!==state.aiSettings?.provider_id||!Number.isInteger(state.aiSettings.revision)||plan?.provider_id!==profile.provider_id)return;try{const prepared=await request(`/api/desktop/ai/providers/${profile.provider_id}/test-actions`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({expected_revision:state.aiSettings.revision})});const calls=Number(prepared?.maximum_calls);if(prepared?.schema_version!=="server-prepared-ai-action-v1"||prepared.scope!=="capability_test"||prepared.provider_id!==profile.provider_id||!Number.isInteger(calls)||calls<1||calls>Number(plan.maximum_model_calls||0))throw safeError("ai_test_invalid","能力测试计划无效。");if(typeof globalThis.confirm!=="function"||!globalThis.confirm(`测试 ${profile.display_name} 连接\n\n模型：${Array.isArray(prepared.model)?prepared.model.join("、"):prepared.model}\n最多调用 ${calls} 次，可能产生 API 费用。\n\n是否继续？`))return;const issued=await request(ROUTES.consent,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({action_id:prepared.action_id})});if(issued?.schema_version!=="ai-consent-v1"||issued.scope!=="capability_test"||typeof issued.nonce!=="string"||!issued.nonce)throw safeError("ai_test_consent_invalid","能力测试授权无效。");await request(`/api/desktop/ai/providers/${profile.provider_id}/test`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({action_id:prepared.action_id,consent_nonce:issued.nonce})});await loadAISettingsUI();aiStatus("连接与固定模型能力测试通过。","success");}catch(_error){aiStatus("能力测试未完成；不会回显密钥或自动重试。","error");}}
  async function preparedAuthorization(scope,domainRequest,detail="") {
    const prepared=await request(aiRoute(scope,"prepare"),{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(domainRequest)});
    if(scope==="librarian"&&prepared?.librarian_core_version==="librarian-v3")return {localResult:prepared};
    const context=await loadAIContext(),disclosureVersion=globalThis.AutoResearchAIConsent?.disclosureVersions?.[scope];
    const calls=Number(prepared?.maximum_calls);if(prepared?.schema_version!=="server-prepared-ai-action-v1"||prepared.scope!==scope||prepared.provider_id!==context.provider_id||prepared.disclosure_version!==disclosureVersion||typeof prepared.action_id!=="string"||!prepared.action_id||!Number.isInteger(calls)||calls<1||calls>AI_CALL_LIMITS[scope])throw safeError("ai_prepared_action_invalid","AI 操作准备结果无效，请重试。");
    if(globalThis.AutoResearchAIConsent.ensure(scope,{...context,disclosure_version:disclosureVersion})!==true)return null;
    const model=Array.isArray(prepared.model)?prepared.model.join("、"):String(prepared.model||"当前模型"),label=String(prepared.display||"AI 操作");
    if(typeof globalThis.confirm!=="function"||!globalThis.confirm(`${label}\n\n${detail?`${detail}\n\n`:""}模型：${model}\n本阶段最多调用 ${calls} 次，可能产生 API 费用。\n\n是否继续？`))return null;
    const issued=await request(ROUTES.consent,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({action_id:prepared.action_id})});
    if(issued?.schema_version!=="ai-consent-v1"||issued.scope!==scope||typeof issued.nonce!=="string"||!issued.nonce)throw safeError("ai_consent_invalid","AI 授权凭证无效，请重试。");
    return {actionId:prepared.action_id,nonce:issued.nonce};
  }
  async function executePrepared(scope,authorization){return request(aiRoute(scope,"execute"),{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({action_id:authorization.actionId,consent_nonce:authorization.nonce})});}
  async function uploadPDF(file) {
    if(!(file instanceof Blob)||file.type!=="application/pdf"&&!String(file.name||"").toLowerCase().endsWith(".pdf"))throw safeError("pdf_required","请选择 PDF 文件。");
    const generation=++state.literatureAction;setLiteratureActionStatus("正在验证 PDF、识别重复项并加入工作区…","loading");
    const result=await request(`${ROUTES.upload}?filename=${encodeURIComponent(String(file.name||"paper.pdf"))}`,{method:"POST",headers:{"Content-Type":"application/pdf"},body:file});if(generation!==state.literatureAction)return null;
    await loadLiterature();if(result?.paper_id)await selectPaper(Number(result.paper_id));setLiteratureActionStatus(String(result?.message||"PDF 已导入；不会自动调用模型。"),"success");return result;
  }
  function validLiteratureStage(value){const sending=value?.sending_scope;return value?.schema_version==="literature-extraction-stage-summary-v1"&&typeof value.job_token==="string"&&value.job_token.length>0&&value.job_token.length<=256&&Number.isInteger(value.call_count)&&value.call_count>0&&value.call_count<=512&&Number.isInteger(value.max_token_budget)&&value.max_token_budget>0&&sending&&["pdf_page_count","page_block_count","branch_count","focus_count"].every(key=>Number.isInteger(sending[key])&&sending[key]>=0)&&value.requires_confirmation===true&&value.persistence_allowed===false;}
  function validLiteratureCommit(value){return value?.schema_version==="literature-extraction-commit-result-v1"&&value.status==="completed"&&["candidate_count","published_item_count","existing_item_count","manual_review_count"].every(key=>Number.isInteger(value[key])&&value[key]>=0);}
  async function runLiteratureExtraction() {
    if(!state.paper?.id)return;const generation=++state.literatureAction;let domainRequest={paper_id:state.paper.id,force_rescan:false},detail="";setLiteratureActionStatus("正在准备自动提取与核验…","loading");
    try {for(let stage=0;stage<32&&generation===state.literatureAction;stage+=1){const authorization=await preparedAuthorization("literature_extraction",domainRequest,detail);if(!authorization){setLiteratureActionStatus("已取消；没有发送下一阶段，也没有显示保存成功。","info");return;}const result=await executePrepared("literature_extraction",authorization);if(generation!==state.literatureAction)return;if(validLiteratureCommit(result)){setLiteratureActionStatus(`已安全保存：发布 ${result.published_item_count} 条，人工复核 ${result.manual_review_count} 条。`,"success");await selectPaper(state.paper.id);return;}if(!validLiteratureStage(result))throw safeError("literature_stage_invalid","文献处理返回了无法识别的阶段；未显示保存成功。");const sending=result.sending_scope;detail=`下一阶段：${result.stage}\n${result.call_count} 次调用 / 最多 ${result.max_token_budget} tokens\n发送 ${sending.pdf_page_count} 页 PDF、${sending.page_block_count} 个文本块、${sending.focus_count} 个重点区域`;setLiteratureActionStatus(`等待确认下一阶段：${result.stage}`,"info");domainRequest={job_token:result.job_token};}throw safeError("literature_stage_limit","文献处理阶段超过安全上限；未显示保存成功。");}
    catch(_error){if(generation===state.literatureAction)setLiteratureActionStatus("处理未完成；本机已有数据不受影响，可稍后重试。","error");}
  }
  function openCurrentPDF(){if(!state.paper?.id)return false;const viewer=q("#fusion-pdf-viewer"),frame=q("#fusion-pdf-frame"),content=q("#fusion-literature-content");if(!viewer||!frame||!content)return false;frame.src=`/api/papers/${encodeURIComponent(String(state.paper.id))}/pdf`;q("#fusion-pdf-title").textContent=state.paper.title;content.hidden=true;viewer.hidden=false;q("#fusion-close-pdf")?.focus({preventScroll:true});setLiteratureActionStatus("正在中央查看当前论文原文；可随时返回。","info");return true;}
  function closeCurrentPDF({focus=true}={}){const viewer=q("#fusion-pdf-viewer"),content=q("#fusion-literature-content");if(!viewer||viewer.hidden)return false;viewer.hidden=true;content.hidden=false;if(focus)q("#fusion-open-pdf")?.focus({preventScroll:true});return true;}
  function setSearchMode(mode) {if(!["precise","librarian"].includes(mode))return false;if(state.searchMode==="librarian"&&mode!=="librarian")cancelLibrarian("已切换到精确检索，未继续后续调用。");state.searchMode=mode;qa("[data-search-mode-panel]").forEach(panel=>panel.hidden=panel.dataset.searchModePanel!==mode);for(const [id,value]of [["#fusion-run-precise-search","precise"],["#fusion-open-librarian","librarian"]]){const button=q(id);button?.classList.toggle("active",value===mode);button?.setAttribute("aria-selected",value===mode?"true":"false");}q("#fusion-primary-tab-label").textContent=mode==="precise"?"精确检索":"图书管理员";return true;}
  function setSearchSource(source,{run=false}={}){if(!["workspace","official","private","all"].includes(source))return false;state.searchSource=source;state.searchRequest+=1;qa("[data-search-source]").forEach(button=>{const active=button.dataset.searchSource===source;button.classList.toggle("active",active);button.setAttribute("aria-pressed",active?"true":"false");});if(run)void runPreciseSearch();return true;}
  async function runPreciseSearch() {closeEvidenceDetail({focus:false});const query=String(q("#fusion-search-query")?.value||"").trim(),source=state.searchSource,generation=++state.searchRequest;setSearchMode("precise");q("#fusion-search-results").innerHTML=literatureState("loading","正在检索四类真实证据…");try{const federated=source!=="workspace",scope=source==="official"?"&source_scope=official":source==="private"?"&source_scope=private":"",url=federated?`${ROUTES.federatedSearch}?q=${encodeURIComponent(query)}&page=1&page_size=100${scope}`:`${ROUTES.evidence}?q=${encodeURIComponent(query)}&limit=100`,page=await readOnlyJSON(url);if(generation!==state.searchRequest||state.view!=="search"||state.searchMode!=="precise"||source!==state.searchSource)return;const raw=federated?(Array.isArray(page?.results)?page.results.map(hit=>hit?.document):[]):(Array.isArray(page?.rows)?page.rows:[]);state.searchResults=raw.map(publicEvidence).filter(Boolean);state.evidenceIndex=0;state.selectedEvidence=state.searchResults[0]||null;renderSearchEvidence();const detailTab=q('[data-tab="detail"]');if(detailTab)detailTab.disabled=!state.selectedEvidence;updateInspector();const sourceLabel={workspace:"本机文献",official:"官方资料库",private:"我的实验",all:"全部资料源"}[source];setOperation(`${sourceLabel}检索完成 · ${state.searchResults.length} 条`,"success");}catch(_error){if(generation===state.searchRequest)q("#fusion-search-results").innerHTML=literatureState("error","当前资料源检索暂时不可用");}}
  function newConversationId(){return globalThis.crypto?.randomUUID?.()||`fusion-${Date.now()}-${Math.random().toString(36).slice(2)}`;}
  function librarianRequest(question){const payload={question,history:state.librarianMessages.slice(-8).map(({role,content})=>({role,content})),conversation_id:state.librarianConversationId||(state.librarianConversationId=newConversationId())};if(state.librarianResearch){payload.research_state=state.librarianResearch.state;payload.state_token=state.librarianResearch.token;}return payload;}
  function validLibrarianStage(value){return value?.schema_version==="librarian-ai-stage-v1"&&value.stage==="synthesis_ready"&&value.requires_second_consent===true&&typeof value.job_token==="string"&&value.job_token.length>0&&value.job_token.length<=4096&&[value.candidate_count,value.bundle_count,value.review_theme_count].every(count=>Number.isInteger(count)&&count>=0)&&value.next_call?.maximum_calls===1;}
  function renderLibrarianFinal(result,question){if(result?.librarian_core_version!=="librarian-v3"||typeof result.answer!=="string")throw safeError("librarian_result_invalid","图书管理员最终结果无效。");state.librarianPending=null;state.librarianMessages.push({role:"user",content:question},{role:"assistant",content:result.answer});state.librarianResearch=result.research_state&&result.state_token?{state:result.research_state,token:result.state_token}:null;const rows=(Array.isArray(result.results)?result.results:[]).map(publicEvidence).filter(Boolean);q("#fusion-librarian-output").innerHTML=`<article class="fusion-librarian-answer"><span>LIBRARIAN V3 · 官方全库</span><h2>回答</h2><p>${esc(result.answer)}</p></article>${rows.map(row=>`<article class="fusion-librarian-citation"><strong>${esc(row.title)}</strong><span>${esc(row.articleTitle)} · 第 ${esc(row.page)} 页</span><p>${esc(row.excerpt)}</p></article>`).join("")}`;q("#fusion-librarian-stage").hidden=true;q("#fusion-librarian-status").textContent=`回答完成 · 引用 ${rows.length} 条公开证据`;}
  async function submitLibrarian(event){event?.preventDefault?.();const question=String(q("#fusion-librarian-question")?.value||"").trim();if(!question)return;const generation=++state.librarianAction;state.librarianPending=null;q("#fusion-librarian-stage").hidden=true;q("#fusion-librarian-status").textContent="正在准备图书管理员请求…";try{const authorization=await preparedAuthorization("librarian",librarianRequest(question));if(generation!==state.librarianAction||state.view!=="search"||state.searchMode!=="librarian")return;if(!authorization){q("#fusion-librarian-status").textContent="已取消，没有发起模型调用。";return;}const result=authorization.localResult||await executePrepared("librarian",authorization);if(generation!==state.librarianAction)return;if(validLibrarianStage(result)){state.librarianPending={generation,jobToken:result.job_token,question,inFlight:false};q("#fusion-librarian-candidates").textContent=String(result.candidate_count);q("#fusion-librarian-bundles").textContent=String(result.bundle_count);q("#fusion-librarian-themes").textContent=String(result.review_theme_count);q("#fusion-librarian-continue").disabled=false;q("#fusion-librarian-stage").hidden=false;q("#fusion-librarian-status").textContent="规划完成；尚未执行第二次收费调用。";return;}renderLibrarianFinal(result,question);q("#fusion-librarian-question").value="";}catch(_error){if(generation===state.librarianAction)q("#fusion-librarian-status").textContent="本次请求未完成；精确检索仍可使用。";}}
  async function continueLibrarian(){const pending=state.librarianPending;if(!pending||pending.inFlight)return;pending.inFlight=true;q("#fusion-librarian-continue").disabled=true;try{const authorization=await preparedAuthorization("librarian",{job_token:pending.jobToken},"这是第二次独立收费确认，将生成最终证据回答。");if(!authorization){cancelLibrarian("已停止，未进行第二次收费调用。");return;}const result=await executePrepared("librarian",authorization);if(pending!==state.librarianPending||pending.generation!==state.librarianAction)return;renderLibrarianFinal(result,pending.question);}catch(_error){if(pending.generation===state.librarianAction){q("#fusion-librarian-status").textContent="最终回答未完成；可重新开始研究请求。";state.librarianPending=null;q("#fusion-librarian-stage").hidden=true;}}finally{q("#fusion-librarian-continue").disabled=false;}}
  function cancelLibrarian(message="已停止。") {state.librarianAction+=1;state.librarianPending=null;const proceed=q("#fusion-librarian-continue");if(proceed)proceed.disabled=false;q("#fusion-librarian-stage").hidden=true;q("#fusion-librarian-status").textContent=message;}
  function personalStatus(message,kind="info") {const node=q("#fusion-personal-status");if(node){node.textContent=message;node.dataset.kind=kind;}setOperation(message,kind);}
  function previewSheet(){return state.personalPreview?.sheets?.[state.personalSheetIndex]||null;}
  function personalColumnRoleOptions(selected="ignore") {const roles=["independent","dependent","uncertainty","condition","identifier","note","ignore"];if(!roles.includes(selected))selected="ignore";return roles.map(value=>`<option value="${value}"${value===selected?" selected":""}>${({independent:"自变量",dependent:"因变量",uncertainty:"不确定度",condition:"实验条件",identifier:"标识符",note:"备注",ignore:"忽略"})[value]}</option>`).join("");}
  function renderPersonalColumns(columns) {
    const host=q("#fusion-personal-columns");if(!host)return;host.innerHTML=columns.map((column,index)=>`<article class="fusion-personal-column" data-personal-column="${index}" data-source-name="${esc(column.source_name)}"><strong>${esc(column.source_name)}</strong><label>角色<select data-personal-role>${personalColumnRoleOptions(column.role||"ignore")}</select></label><label>物理意义<input data-personal-meaning maxlength="500" value="${esc(column.meaning||column.source_name||"")}"></label><label>单位<input data-personal-unit maxlength="80" value="${esc(column.unit||"")}"></label><small data-personal-ai-note>${column.rationale?esc(column.rationale):"请逐列核验；不会自动确认。"}</small></article>`).join("");
    host.querySelectorAll("input,select").forEach(control=>control.addEventListener("input",()=>{state.personalAction+=1;q("#fusion-personal-confirm").disabled=false;personalStatus("内容已修改；请完成集中核验后一次确认导入。","info");}));
  }
  function previewCellValue(row,column,columnIndex){return Array.isArray(row)?row[columnIndex]:row&&typeof row==="object"?row[column.source_name]:"";}
  function renderPersonalPreviewSheet() {
    const sheet=previewSheet();if(!sheet)return false;const columns=Array.isArray(sheet.columns)?sheet.columns:[],rows=Array.isArray(sheet.sample_rows)?sheet.sample_rows:[];
    q("#fusion-sheet-summary").textContent=`${sheet.sheet_name} · ${Number(sheet.row_count||0)} 行 · ${columns.length} 列 · 本机安全预览`;
    const table=q("#fusion-data-grid");table.querySelector("thead").innerHTML=`<tr><th>#</th>${columns.map(column=>`<th scope="col">${esc(column.source_name)}<small>${esc(column.data_type||"未知类型")}</small></th>`).join("")}</tr>`;table.querySelector("tbody").innerHTML=rows.map((row,rowIndex)=>`<tr><th scope="row">${rowIndex+1}</th>${columns.map((column,columnIndex)=>`<td>${esc(previewCellValue(row,column,columnIndex))}</td>`).join("")}</tr>`).join("");
    renderPersonalColumns(columns);return true;
  }
  function renderPersonalPreviewResponse(response) {
    if(response?.schema_version!=="personal-import-preview-v1"||response.status?.schema_version!=="personal-import-status-v1"||!/^personal_import_[A-Za-z0-9_-]{16,96}$/.test(String(response.status.import_id||""))||!Array.isArray(response.preview?.sheets))throw safeError("personal_preview_invalid","实验数据预览结果无效。");
    state.personalPreview=response.preview;state.personalStatus=response.status;state.personalSuggestion=null;state.personalSheetIndex=0;
    q("#fusion-personal-filename").textContent=String(response.preview.source_file?.original_name||"本机实验数据");q("#fusion-personal-review").hidden=false;q(".fusion-sheet-tabs").hidden=true;const contextFile=q('[data-context-view="personal"] .fusion-tree-row.active span');if(contextFile)contextFile.textContent=String(response.preview.source_file?.original_name||"本机实验数据");qa('[data-context-view="personal"] [data-sheet]').forEach(button=>button.hidden=true);
    const select=q("#fusion-personal-sheet");select.innerHTML=response.preview.sheets.map((sheet,index)=>`<option value="${index}">${esc(sheet.sheet_name)} · ${Number(sheet.row_count||0)} 行</option>`).join("");
    const stem=String(response.preview.source_file?.original_name||"个人实验数据").replace(/\.[^.]+$/," ").trim();q("#fusion-project-name").value=stem||"个人实验数据";q("#fusion-sample-name").value=String(response.preview.sheets[0]?.sheet_name||"实验样品");q("#fusion-run-name").value=String(response.preview.sheets[0]?.sheet_name||"实验批次");q("#fusion-run-method").value="未注明";
    q("#fusion-personal-ai").disabled=false;q("#fusion-personal-confirm").disabled=false;q("#fusion-reviewed-state").textContent="待集中核验";q("#fusion-review-context-state").textContent="待核验";q("#fusion-ai-demo-context-state").textContent="未运行";renderPersonalPreviewSheet();personalStatus("安全预览完成。可选用 AI 辅助预填；无论是否使用，都要由你集中核验后一次确认。","success");
  }
  async function choosePersonalFile() {
    const picker=globalThis.pywebview?.api?.select_personal_data_file;if(typeof picker!=="function"){personalStatus("请在桌面 App 中使用系统文件选择器。","error");return null;}
    const generation=++state.personalAction;personalStatus("正在打开系统文件选择器…","loading");try{const selected=await picker.call(globalThis.pywebview.api);if(generation!==state.personalAction||state.view!=="personal")return null;if(selected?.cancelled){personalStatus("已取消选择，没有读取文件。","info");return null;}if(selected?.ok!==true||typeof selected.selection?.selection_id!=="string")throw safeError("personal_picker_failed","实验数据文件选择失败。");const response=await request(ROUTES.personalPreview,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({selection_id:selected.selection.selection_id})});if(generation!==state.personalAction||state.view!=="personal")return null;renderPersonalPreviewResponse(response);return response;}catch(_error){if(generation===state.personalAction)personalStatus("实验数据预览未完成；未保存任何内容。","error");return null;}
  }
  function applyPersonalSuggestion(suggestion) {
    if(suggestion?.schema_version!=="personal-import-suggestion-v1"||suggestion.import_id!==state.personalStatus?.import_id)return false;state.personalSuggestion=suggestion;
    const values={"#fusion-project-name":suggestion.project?.name,"#fusion-sample-name":suggestion.sample?.name,"#fusion-sample-material":suggestion.sample?.material,"#fusion-run-name":suggestion.run?.name,"#fusion-run-method":suggestion.run?.method};for(const [selector,value]of Object.entries(values)){if(typeof value==="string"&&value.trim())q(selector).value=value;}
    const byName=new Map((Array.isArray(suggestion.columns)?suggestion.columns:[]).map(column=>[column.source_name,column]));qa("[data-personal-column]").forEach(row=>{const column=byName.get(row.dataset.sourceName);if(!column)return;row.querySelector("[data-personal-role]").value=column.role||"ignore";row.querySelector("[data-personal-meaning]").value=column.meaning||"";row.querySelector("[data-personal-unit]").value=column.unit||"";row.querySelector("[data-personal-ai-note]").textContent=String(column.rationale||"AI 建议，请人工核验。");});q("#fusion-personal-confirm").disabled=false;q("#fusion-ai-demo-context-state").textContent="待人工核验";personalStatus("AI 预填完成；建议尚未确认，请逐列检查后一次导入。","success");return true;
  }
  async function requestPersonalSuggestion() {
    if(!state.personalStatus?.import_id)return;const generation=++state.personalAction;q("#fusion-personal-ai").disabled=true;q("#fusion-personal-confirm").disabled=true;personalStatus("正在准备 AI 辅助预填…","loading");try{const authorization=await preparedAuthorization("personal_suggestion",{import_id:state.personalStatus.import_id,sheet_index:state.personalSheetIndex});if(generation!==state.personalAction||state.view!=="personal")return;if(!authorization){personalStatus("已取消；没有向 AI 提供商发送工作表内容。","info");return;}const suggestion=await executePrepared("personal_suggestion",authorization);if(generation!==state.personalAction||state.view!=="personal")return;applyPersonalSuggestion(suggestion);}catch(_error){if(generation===state.personalAction)personalStatus("AI 预填未完成；本地预览仍可人工核验。","error");}finally{if(generation===state.personalAction){q("#fusion-personal-ai").disabled=false;q("#fusion-personal-confirm").disabled=false;}}
  }
  function collectPersonalDraft() {
    const sheet=previewSheet(),columns=qa("[data-personal-column]").map(row=>{const role=row.querySelector("[data-personal-role]").value,meaning=row.querySelector("[data-personal-meaning]").value.trim(),unit=row.querySelector("[data-personal-unit]").value.trim();if(role!=="ignore"&&!meaning)throw safeError("personal_review_incomplete",`请填写“${row.dataset.sourceName}”的物理意义。`);return {source_name:row.dataset.sourceName,role,meaning:meaning||null,unit:unit||null};});
    const project=q("#fusion-project-name").value.trim(),sample=q("#fusion-sample-name").value.trim(),run=q("#fusion-run-name").value.trim(),method=q("#fusion-run-method").value.trim();if(!sheet||!project||!sample||!run||!method)throw safeError("personal_review_incomplete","请填写完整的项目、样品和实验批次信息。");
    const series=(Array.isArray(state.personalSuggestion?.series)?state.personalSuggestion.series:[]).map((value,index)=>({series_id:String(value.series_id||`series-${index+1}`),name:String(value.name||`测量序列 ${index+1}`),x_column:String(value.x_column||""),y_column:String(value.y_column||""),...(value.uncertainty_column?{uncertainty_column:String(value.uncertainty_column)}:{}),...(value.description?{description:String(value.description)}:{})}));const draft={sheet_index:state.personalSheetIndex,project:{name:project},sample:{name:sample},run:{name:run,method,conditions:{}},columns,series};const material=q("#fusion-sample-material").value.trim();if(material)draft.sample.material=material;if(Number.isInteger(state.personalStatus?.revision))draft.expected_revision=state.personalStatus.revision;return draft;
  }
  async function confirmPersonalImport() {
    const importId=String(state.personalStatus?.import_id||"");if(!/^personal_import_[A-Za-z0-9_-]{16,96}$/.test(importId))return;let draft;try{draft=collectPersonalDraft();}catch(error){personalStatus(error.message,"error");return;}
    if(typeof globalThis.confirm!=="function"||!globalThis.confirm("确认将当前核验结果保存到本机私人实验库？\n\n这是一次本地写入，不会发送给 AI。"))return;const generation=++state.personalAction;q("#fusion-personal-confirm").disabled=true;personalStatus("正在保存核验结果并更新私人搜索…","loading");try{const result=await request(`/api/desktop/personal-imports/${encodeURIComponent(importId)}/reviewed-import`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({reviewed:true,draft})});if(generation!==state.personalAction)return;if(result?.schema_version!=="personal-import-status-v1"||result.indexable!==true)throw safeError("personal_import_incomplete","实验数据尚未进入可检索状态。");state.personalStatus=result;q("#fusion-reviewed-state").textContent="✓ 已核验并导入";q("#fusion-review-context-state").textContent="已导入";personalStatus("实验数据已保存到本机私人库，正在打开“我的实验”搜索。","success");switchView("search");setSearchSource("private");await runPreciseSearch();}catch(_error){if(generation===state.personalAction){personalStatus("导入未完成；不会显示假成功，请核对后重试。","error");q("#fusion-personal-confirm").disabled=false;}}
  }

  function safeStoreAppearance() {
    try { localStorage.setItem(STORAGE_KEY, JSON.stringify({version:1,theme:state.theme,density:state.density})); } catch (_error) {}
  }
  function showSettingsStatus(message,kind="info") {const node=q("#fusion-settings-status");if(!node)return;node.hidden=false;node.dataset.kind=kind;node.textContent=message;}
  function applyAppearance(theme = state.theme, density = state.density,{cache=true}={}) {
    if (!["system","light","dark"].includes(theme) || !["comfortable","compact"].includes(density)) return false;
    state.theme=theme; state.density=density;
    document.documentElement.dataset.theme=theme; document.documentElement.dataset.density=density;
    qa("[data-theme-choice]").forEach(button=>button.classList.toggle("active",button.dataset.themeChoice===theme));
    qa("[data-density-choice]").forEach(button=>button.classList.toggle("active",button.dataset.densityChoice===density));
    if(cache)safeStoreAppearance(); return true;
  }
  function readAppearance() {
    try { const saved=JSON.parse(localStorage.getItem(STORAGE_KEY)||"null"); return applyAppearance(saved?.theme||document.documentElement.dataset.theme||"system",saved?.density||document.documentElement.dataset.density||"comfortable"); }
    catch (_error) { return applyAppearance("system","comfortable"); }
  }
  function hydrateSettings(dto) {
    if(dto?.schema_version!=="desktop-settings-v1"||!Number.isInteger(dto.revision)||!dto.appearance)return false;
    if(!applyAppearance(dto.appearance.theme,dto.appearance.density))return false;state.settingsRevision=dto.revision;showSettingsStatus("桌面外观设置已同步。","success");return true;
  }
  async function loadSettings() {
    try {const dto=await readOnlyJSON(ROUTES.settings);if(!hydrateSettings(dto))throw new Error("fusion_settings_invalid");return true;}
    catch(_error){showSettingsStatus("桌面设置暂时不可用；本次会话继续使用当前外观。","error");return false;}
  }
  async function chooseAppearance(theme,density) {
    applyAppearance(theme,density);showSettingsStatus("正在保存桌面外观设置…","loading");
    try {await patchPreferences();return true;}catch(_error){
      try {const latest=await readOnlyJSON(ROUTES.settings);if(latest?.schema_version==="desktop-settings-v1"&&Number.isInteger(latest.revision))state.settingsRevision=latest.revision;}catch(_ignored){}
      showSettingsStatus("外观保存失败；本次会话仍保留当前选择，可稍后重试。","error");state.theme=theme;state.density=density;safeStoreAppearance();return false;
    }
  }
  function evidenceInspector() {
    const row=state.view==="search"?state.selectedEvidence:state.evidence[state.evidenceIndex];if(!row)return `<section class="fusion-inspector-section"><span>真实文献 · 只读</span><h2>${state.literatureStatus==="error"?"证据暂时无法载入":"尚未选择证据"}</h2><p>精确检索不会调用 AI；收费动作会在执行前逐次确认。</p></section>`;
    return `<section class="fusion-inspector-section"><span>${esc(row.type.toUpperCase())} · 真实只读证据</span><div class="metric">${esc(row.value)}${row.unit?` <small>${esc(row.unit)}</small>`:""}</div><h2>${esc(row.title)}</h2></section><section class="fusion-inspector-section"><dl><dt>来源页</dt><dd>${esc(row.page)}</dd><dt>论文</dt><dd>${esc(row.articleTitle||state.paper?.title||"—")}</dd><dt>状态</dt><dd>只读</dd></dl>${row.excerpt?`<p>${esc(row.excerpt)}</p>`:""}</section>`;
  }
  function inspectorForView(view) {
    if(view==="search"||view==="paper") return evidenceInspector();
    if(view==="personal") return personalInspector();
    if(view==="package") return `<section class="fusion-inspector-section"><span>资料包安全</span><h2>三类数据彼此隔离</h2><p>官方资料包只读且经签名审计；私人实验留在本机。本页暂不执行资料包更换、回退或导出。</p></section>`;
    if(view==="settings") return `<section class="fusion-inspector-section"><span>设置边界</span><h2>外观与受信 AI</h2><p>外观由桌面设置同步；API 密钥只写入系统安全凭据存储，页面不会回显。</p></section>`;
    return `<section class="fusion-inspector-section"><span>Fusion 工作台</span><h2>详情检查器</h2></section>`;
  }
  function updateInspector() {
    const title={paper:"论文检查器",search:"证据检查器",personal:"列检查器",package:"安全边界",settings:"设置说明"}[state.view];
    q("#fusion-inspector-title").textContent=title; q("#fusion-inspector-body").innerHTML=inspectorForView(state.view);bindColumnEditor();
  }
  const SETTINGS_LABELS={appearance:"设置：外观",language:"设置：语言",ai:"设置：AI 与密钥",data:"设置：数据",about:"设置：关于"};
  const DETAIL_TAB_LABELS={paper:"证据详情",search:"证据与来源",personal:"列核验",package:"任务详情",settings:"设置说明"};
  function switchView(name,{focus=true}={}) {
    if(!VIEWS.includes(name)) return false;
    const previous=state.view;if(previous!==name){closeEvidenceDetail({focus:false});if(previous==="paper"){state.literatureAction+=1;closeCurrentPDF({focus:false});}if(previous==="search"){state.searchRequest+=1;cancelLibrarian("已离开图书管理员，未继续后续调用。");}if(previous==="personal")state.personalAction+=1;} state.view=name; document.body.dataset.view=name;
    qa("[data-view-panel]").forEach(panel=>{const active=panel.dataset.viewPanel===name;panel.hidden=!active;panel.classList.toggle("active",active);});
    qa(".fusion-nav[data-view]").forEach(button=>{const active=button.dataset.view===name;button.classList.toggle("active",active);active?button.setAttribute("aria-current","page"):button.removeAttribute("aria-current");});
    qa("[data-context-view]").forEach(panel=>{const active=panel.dataset.contextView===name;panel.hidden=!active;panel.classList.toggle("active",active);});
    q("#fusion-context-title").textContent=VIEW_LABELS[name]; q("#fusion-breadcrumb").textContent=`${VIEW_LABELS[name]} / Fusion 科研工作台`;
    q("#fusion-primary-tab-label").textContent=name==="settings"?SETTINGS_LABELS[state.settingsSection]:{paper:"当前论文",search:state.searchMode==="librarian"?"图书管理员":"精确检索",personal:state.personalPreview?"实验数据核验":syntheticSheets[state.sheet].label,package:"资料包中心"}[name];
    q("#fusion-detail-tab-label").textContent=DETAIL_TAB_LABELS[name];
    q("#fusion-status-context").textContent={paper:"真实文献与核验",search:"多来源证据检索",personal:state.personalPreview?"本机实验集中核验":"合成示例 · 可选择真实文件",package:"资料包管理未启用",settings:"外观与受信 AI"}[name];
    closeDrawers(false); updateInspector();syncDrawerAccessibility();
    if(focus&&previous!==name) q("#fusion-editor")?.focus({preventScroll:true}); return true;
  }
  function closeDrawers(restore=true) {
    q("#fusion-context")?.classList.remove("drawer-open");q("#fusion-inspector")?.classList.remove("drawer-open");qa("[data-open-drawer]").forEach(button=>button.setAttribute("aria-expanded","false"));q("[data-close-all-drawers]").hidden=true;state.drawer=null;syncDrawerAccessibility();
    if(restore&&state.focusReturn?.isConnected) state.focusReturn.focus(); state.focusReturn=null;
  }
  function openDrawer(kind,trigger) {
    if(!["context","inspector"].includes(kind)) return;closeDrawers(false);state.drawer=kind;state.focusReturn=trigger;
    const panel=q(kind==="context"?"#fusion-context":"#fusion-inspector");panel?.classList.add("drawer-open");panel?.setAttribute("aria-hidden","false");trigger?.setAttribute("aria-expanded","true");q("[data-close-all-drawers]").hidden=false;
    panel?.querySelector("button")?.focus();
  }
  function syncDrawerAccessibility() {
    const width=Number(globalThis.innerWidth||1440),contextHidden=width<900&&state.drawer!=="context",inspectorHidden=(state.view==="settings"&&width>=1280)||(width<1280&&state.drawer!=="inspector");q("#fusion-context")?.setAttribute("aria-hidden",contextHidden?"true":"false");q("#fusion-inspector")?.setAttribute("aria-hidden",inspectorHidden?"true":"false");
  }
  function openCommandPalette(trigger=q("#fusion-command")) {
    const palette=q("#fusion-command-palette");if(!palette)return false;state.paletteOpen=true;state.paletteFocusReturn=trigger;palette.hidden=false;trigger?.setAttribute("aria-expanded","true");const options=qa("[data-command-view]");options.forEach((button,index)=>button.tabIndex=index===0?0:-1);options[0]?.focus();return true;
  }
  function closeCommandPalette(restore=true) {
    const palette=q("#fusion-command-palette");if(palette)palette.hidden=true;q("#fusion-command")?.setAttribute("aria-expanded","false");state.paletteOpen=false;if(restore&&state.paletteFocusReturn?.isConnected)state.paletteFocusReturn.focus();state.paletteFocusReturn=null;
  }
  function handlePaletteKey(event) {
    if(event.key==="Escape"){event.preventDefault();closeCommandPalette();return;}handleRovingTabs(event,"[data-command-view]",button=>button.focus());if(event.key==="Enter"||event.key===" "){event.preventDefault();const view=event.currentTarget.dataset.commandView;closeCommandPalette(false);switchView(view);q(`.fusion-nav[data-view="${view}"]`)?.focus();}
  }
  function selectCell(row,column,{focus=true}={}) {
    const sheet=syntheticSheets[state.sheet];state.row=Math.max(0,Math.min(row,sheet.rows.length-1));state.column=Math.max(0,Math.min(column,sheet.columns.length-1));
    qa("#fusion-data-grid [data-cell]").forEach(cell=>{const active=Number(cell.dataset.row)===state.row&&Number(cell.dataset.column)===state.column;cell.classList.toggle("selected",active);cell.tabIndex=active?0:-1;cell.setAttribute("aria-selected",active?"true":"false");});
    qa("#fusion-data-grid th[data-column]").forEach(cell=>cell.classList.toggle("column-selected",Number(cell.dataset.column)===state.column));updateInspector();
    if(focus) q(`#fusion-data-grid [data-row="${state.row}"][data-column="${state.column}"]`)?.focus({preventScroll:true});
  }
  function personalInspector() {
    if(state.personalPreview){const sheet=previewSheet(),columns=sheet?.columns||[];return `<section class="fusion-inspector-section"><span>本机安全预览</span><h2>${esc(sheet?.sheet_name||"实验工作表")}</h2><p>${Number(sheet?.row_count||0)} 行 · ${columns.length} 列。AI 预填不是确认，只有顶部“确认并导入一次”才写入私人库。</p></section><section class="fusion-inspector-section"><span>当前状态</span><p>${state.personalStatus?.indexable===true?"已核验并进入私人搜索。":"尚未确认；可在中央逐列修改角色、意义和单位。"}</p></section>`;}
    const sheet=syntheticSheets[state.sheet],column=sheet.columns[state.column],value=sheet.rows[state.row]?.[state.column];
    return `<section class="fusion-inspector-section"><span>列 ${state.column+1} · 合成示例</span><div class="metric">${esc(value)}${column.unit!=="—"?` <small>${esc(column.unit)}</small>`:""}</div><h2>${esc(column.name)}</h2><p>第 ${state.row+1} 行 · 合成数据，不来自生产数据库。</p></section><section class="fusion-inspector-section fusion-column-editor"><label>角色<select data-column-field="role"><option>实验条件</option><option>测量值</option><option>不确定度</option><option>标识符</option><option>样品信息</option><option>方法</option><option>忽略</option></select></label><label>物理意义<input data-column-field="meaning" value="${esc(column.meaning)}" maxlength="120"></label><label>单位<input data-column-field="unit" value="${esc(column.unit)}" maxlength="32"></label><small>仅修改当前会话内的合成列定义；重启即清除。选择真实文件后会进入正式集中核验。</small></section>`;
  }
  function bindColumnEditor() {
    if(state.view!=="personal"||state.personalPreview)return;const column=syntheticSheets[state.sheet].columns[state.column];const role=q('[data-column-field="role"]');if(role){role.value=column.role;role.addEventListener("change",()=>updateColumnDefinition("role",role.value));}for(const field of ["meaning","unit"]){const input=q(`[data-column-field="${field}"]`);input?.addEventListener("input",()=>updateColumnDefinition(field,input.value));}
  }
  function updateColumnDefinition(field,value) {
    if(!["role","meaning","unit"].includes(field))return false;const column=syntheticSheets[state.sheet].columns[state.column];column[field]=String(value).trim().slice(0,field==="meaning"?120:32);renderSheet(state.sheet,{focus:false,preserveSelection:true});return true;
  }
  function selectSettingsSection(name,{focus=false}={}) {
    if(!["appearance","language","ai","data","about"].includes(name))return false;state.settingsSection=name;qa("[data-settings-panel]").forEach(panel=>panel.hidden=panel.dataset.settingsPanel!==name);qa("[data-settings-section]").forEach(button=>{const active=button.dataset.settingsSection===name;button.classList.toggle("active",active);button.setAttribute("aria-selected",active?"true":"false");button.tabIndex=active?0:-1;});if(state.view==="settings")q("#fusion-primary-tab-label").textContent=SETTINGS_LABELS[name];if(focus)q(`[data-settings-section="${name}"]`)?.focus();return true;
  }
  function renderSheet(name,{focus=false,preserveSelection=false}={}) {
    if(!syntheticSheets[name]) return false;state.sheet=name;if(!preserveSelection){state.row=0;state.column=0;}const sheet=syntheticSheets[name];
    q("#fusion-sheet-summary").textContent=sheet.summary;q("#fusion-primary-tab-label").textContent=sheet.label;
    qa("[data-sheet]").forEach(button=>{const active=button.dataset.sheet===name;button.classList.toggle("active",active);button.setAttribute("aria-selected",active?"true":"false");button.tabIndex=active?0:-1;});
    const table=q("#fusion-data-grid");table.querySelector("thead").innerHTML=`<tr><th>#</th>${sheet.columns.map((column,index)=>`<th data-column="${index}" scope="col">${column.name}<small>${column.role} · ${column.unit}</small></th>`).join("")}</tr>`;
    table.querySelector("tbody").innerHTML=sheet.rows.map((row,rowIndex)=>`<tr><th scope="row">${rowIndex+1}</th>${row.map((value,columnIndex)=>`<td tabindex="-1" role="gridcell" aria-selected="false" data-cell data-row="${rowIndex}" data-column="${columnIndex}">${String(value)}</td>`).join("")}</tr>`).join("");
    table.setAttribute("role","grid");table.setAttribute("aria-rowcount",String(sheet.rows.length+1));table.setAttribute("aria-colcount",String(sheet.columns.length+1));
    qa("#fusion-data-grid [data-cell]").forEach(cell=>cell.addEventListener("click",()=>selectCell(Number(cell.dataset.row),Number(cell.dataset.column))));qa("#fusion-data-grid th[data-column]").forEach(header=>{header.tabIndex=-1;header.setAttribute("aria-selected","false");header.addEventListener("click",()=>selectCell(state.row,Number(header.dataset.column)));});selectCell(state.row,state.column,{focus});return true;
  }
  function handleGridKey(event) {
    if(!event.target.matches?.("#fusion-data-grid [data-cell]")) return;const moves={ArrowLeft:[0,-1],ArrowRight:[0,1],ArrowUp:[-1,0],ArrowDown:[1,0]};
    if(moves[event.key]){event.preventDefault();selectCell(state.row+moves[event.key][0],state.column+moves[event.key][1]);}
    else if(event.key==="Home"){event.preventDefault();selectCell(event.metaKey||event.ctrlKey?0:state.row,0);}
    else if(event.key==="End"){event.preventDefault();const sheet=syntheticSheets[state.sheet];selectCell(event.metaKey||event.ctrlKey?sheet.rows.length-1:state.row,sheet.columns.length-1);}
    else if(event.key==="PageUp"){event.preventDefault();selectCell(state.row-10,state.column);}
    else if(event.key==="PageDown"){event.preventDefault();selectCell(state.row+10,state.column);}
  }
  function handleShortcut(event) {
    if(event.key==="Escape"&&state.evidenceDetailPDF){event.preventDefault();closeDetailPDF();return;}
    if(event.key==="Escape"&&state.evidenceDetailOpen){event.preventDefault();closeEvidenceDetail();return;}
    if(event.key==="Escape"&&state.paletteOpen){event.preventDefault();closeCommandPalette();return;}
    if(event.key==="Escape"&&state.drawer){event.preventDefault();closeDrawers();return;}
    if(event.target.matches?.("input,textarea,select,[contenteditable='true']")) return;const modifier=event.metaKey||event.ctrlKey;
    const byKey={"1":"paper","2":"search","3":"personal","4":"package"};if(modifier&&byKey[event.key]){event.preventDefault();switchView(byKey[event.key]);}
    else if(modifier&&event.key.toLowerCase()==="k"){event.preventDefault();openCommandPalette();}
    else if(modifier&&event.key===","){event.preventDefault();switchView("settings");}
  }
  function handleRovingTabs(event,selector,activate) {
    if(!["ArrowLeft","ArrowRight","ArrowUp","ArrowDown","Home","End"].includes(event.key)) return;const buttons=qa(selector).filter(button=>!button.disabled),current=buttons.indexOf(event.currentTarget);if(current<0||!buttons.length)return;event.preventDefault();
    const forward=event.key==="ArrowRight"||event.key==="ArrowDown",next=event.key==="Home"?0:event.key==="End"?buttons.length-1:(current+(forward?1:-1)+buttons.length)%buttons.length;buttons[next].focus();activate(buttons[next]);
  }
  function initialize() {
    readAppearance();renderSheet("hardness");switchView("paper",{focus:false});void Promise.all([loadSettings(),loadLiterature(),loadAISettingsUI()]);
    qa(".fusion-nav[data-view]").forEach(button=>{button.addEventListener("click",()=>switchView(button.dataset.view));button.addEventListener("keydown",event=>handleRovingTabs(event,".fusion-nav[data-view]",next=>{switchView(next.dataset.view,{focus:false});next.focus();}));});
    qa("[data-theme-choice]").forEach(button=>button.addEventListener("click",()=>void chooseAppearance(button.dataset.themeChoice,state.density)));
    qa("[data-density-choice]").forEach(button=>button.addEventListener("click",()=>void chooseAppearance(state.theme,button.dataset.densityChoice)));
    qa("[data-open-drawer]").forEach(button=>{const kind=button.dataset.openDrawer;button.setAttribute("aria-controls",kind==="context"?"fusion-context":"fusion-inspector");button.setAttribute("aria-expanded","false");button.addEventListener("click",()=>openDrawer(kind,button));});
    qa("[data-close-drawer]").forEach(button=>button.addEventListener("click",()=>closeDrawers()));q("[data-close-all-drawers]")?.addEventListener("click",()=>closeDrawers());
    q("#fusion-command")?.addEventListener("click",event=>openCommandPalette(event.currentTarget));qa("[data-command-view]").forEach(button=>{button.addEventListener("keydown",handlePaletteKey);button.addEventListener("click",()=>{const view=button.dataset.commandView;closeCommandPalette(false);switchView(view);});});
    qa("[data-settings-section]").forEach(button=>{button.addEventListener("click",()=>selectSettingsSection(button.dataset.settingsSection));button.addEventListener("keydown",event=>handleRovingTabs(event,"[data-settings-section]",next=>selectSettingsSection(next.dataset.settingsSection,{focus:true})));});selectSettingsSection("appearance");
    q("#fusion-ai-provider")?.addEventListener("change",event=>renderAISettings(event.currentTarget.value));q("#fusion-ai-model-save")?.addEventListener("click",()=>void saveAIModels());q("#fusion-ai-key-save")?.addEventListener("click",()=>void saveAIKey());q("#fusion-ai-key-delete")?.addEventListener("click",()=>void deleteAIKey());q("#fusion-ai-test")?.addEventListener("click",()=>void testAIConnection());
    qa("[data-sheet]").forEach(button=>{button.addEventListener("click",()=>renderSheet(button.dataset.sheet,{focus:state.view==="personal"}));button.addEventListener("keydown",event=>handleRovingTabs(event,".fusion-sheet-tabs [data-sheet]",next=>renderSheet(next.dataset.sheet,{focus:true})));});
    qa(".fusion-tabs [role='tab']").forEach(button=>{button.addEventListener("keydown",event=>handleRovingTabs(event,".fusion-tabs [role='tab']",next=>next.focus()));button.addEventListener("click",()=>{if(button.dataset.tab==="primary")closeEvidenceDetail();else if(state.selectedEvidence)void openEvidenceDetail(state.evidenceIndex,button);});});
    q("#fusion-data-grid")?.addEventListener("keydown",handleGridKey);
    q("#fusion-detail-back")?.addEventListener("click",()=>closeEvidenceDetail());q("#fusion-detail-open-pdf")?.addEventListener("click",openDetailPDF);q("#fusion-detail-close-pdf")?.addEventListener("click",()=>closeDetailPDF());
    q("#fusion-import-pdf")?.addEventListener("click",()=>q("#fusion-pdf-file")?.click());q("#fusion-pdf-file")?.addEventListener("change",event=>{const file=event.currentTarget.files?.[0];event.currentTarget.value="";if(file)void uploadPDF(file).catch(()=>setLiteratureActionStatus("PDF 导入未完成；现有文献不受影响。","error"));});q("#fusion-start-extraction")?.addEventListener("click",()=>void runLiteratureExtraction());q("#fusion-open-pdf")?.addEventListener("click",openCurrentPDF);q("#fusion-close-pdf")?.addEventListener("click",()=>closeCurrentPDF());
    qa("[data-search-source]").forEach(button=>button.addEventListener("click",()=>setSearchSource(button.dataset.searchSource,{run:state.view==="search"&&state.searchMode==="precise"})));setSearchSource("workspace");q("#fusion-run-precise-search")?.addEventListener("click",()=>void runPreciseSearch());q("#fusion-open-librarian")?.addEventListener("click",()=>{setSearchMode("librarian");q("#fusion-librarian-question")?.focus();});q("#fusion-librarian-form")?.addEventListener("submit",event=>void submitLibrarian(event));q("#fusion-librarian-continue")?.addEventListener("click",()=>void continueLibrarian());q("#fusion-librarian-cancel")?.addEventListener("click",()=>cancelLibrarian());q("#fusion-search-query")?.addEventListener("keydown",event=>{if(event.key==="Enter"){event.preventDefault();void runPreciseSearch();}});
    q("#fusion-select-data-file")?.addEventListener("click",()=>void choosePersonalFile());q("#fusion-personal-ai")?.addEventListener("click",()=>void requestPersonalSuggestion());q("#fusion-personal-confirm")?.addEventListener("click",()=>void confirmPersonalImport());q("#fusion-personal-sheet")?.addEventListener("change",event=>{state.personalAction+=1;state.personalSheetIndex=Number(event.currentTarget.value)||0;state.personalSuggestion=null;renderPersonalPreviewSheet();q("#fusion-personal-confirm").disabled=false;});
    document.addEventListener("keydown",handleShortcut);globalThis.addEventListener?.("resize",syncDrawerAccessibility);syncDrawerAccessibility();
  }
  globalThis.AutoResearchFusion=Object.freeze({initialize,switchView,renderSheet,selectCell,selectPaper,selectEvidence,openEvidenceDetail,closeEvidenceDetail,openDetailPDF,closeDetailPDF,publicEvidence,loadLiterature,loadSettings,hydrateSettings,chooseAppearance,openDrawer,closeDrawers,openCommandPalette,closeCommandPalette,applyAppearance,updateColumnDefinition,selectSettingsSection,runPreciseSearch,setSearchMode,setSearchSource,uploadPDF,runLiteratureExtraction,openCurrentPDF,closeCurrentPDF,choosePersonalFile,requestPersonalSuggestion,confirmPersonalImport,preparedAuthorization,loadAISettingsUI,saveAIModels,saveAIKey,deleteAIKey,testAIConnection,previewCellValue,state,syntheticSheets});
  if(document.readyState==="loading") document.addEventListener("DOMContentLoaded",initialize,{once:true});else initialize();
})();
