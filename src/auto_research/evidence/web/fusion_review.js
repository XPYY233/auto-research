(() => {
  "use strict";
  const VIEWS = Object.freeze(["paper", "search", "personal", "package", "settings"]);
  const VIEW_LABELS = Object.freeze({paper:"文献",search:"搜索",personal:"实验",package:"资料包",settings:"设置"});
  const STORAGE_KEY = "auto-research-fusion-appearance-v1";
  const READ_ONLY_ROUTES = Object.freeze({papers:"/api/search-papers",evidence:"/api/search-v2",settings:"/api/desktop/settings",preferences:"/api/desktop/settings/preferences"});
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
  const state = {view:"paper",sheet:"hardness",settingsSection:"appearance",row:0,column:0,drawer:null,focusReturn:null,paletteOpen:false,paletteFocusReturn:null,theme:"system",density:"comfortable",settingsRevision:null,csrfToken:"",papers:[],paper:null,evidence:[],evidenceIndex:0,literatureStatus:"loading",literatureRequest:0,reviewed:false,demoSuggestion:false};
  const q = selector => document.querySelector(selector);
  const qa = selector => [...document.querySelectorAll(selector)];
  const esc = value => String(value ?? "").replace(/[&<>"']/g, character => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[character]));

  async function readOnlyJSON(url, signal) {
    if (![READ_ONLY_ROUTES.papers,READ_ONLY_ROUTES.evidence,READ_ONLY_ROUTES.settings].some(route => url === route || url.startsWith(`${route}?`))) throw new Error("fusion_read_route_invalid");
    const response = await fetch(url, {method:"GET",headers:{Accept:"application/json"},credentials:"same-origin",cache:"no-store",signal});
    const csrf=response.headers?.get?.(CSRF_HEADER);if(csrf)state.csrfToken=csrf;
    if (!response.ok) throw new Error("fusion_read_failed");
    return response.json();
  }
  async function patchPreferences() {
    if(!Number.isInteger(state.settingsRevision)||!state.csrfToken)throw new Error("fusion_settings_unavailable");
    const response=await fetch(READ_ONLY_ROUTES.preferences,{method:"PATCH",headers:{Accept:"application/json","Content-Type":"application/json",[CSRF_HEADER]:state.csrfToken},credentials:"same-origin",cache:"no-store",body:JSON.stringify({expected_revision:state.settingsRevision,preferences:{appearance:{theme:state.theme,density:state.density}}})});
    const csrf=response.headers?.get?.(CSRF_HEADER);if(csrf)state.csrfToken=csrf;const payload=await response.json().catch(()=>({}));
    if(!response.ok)throw new Error("fusion_settings_save_failed");hydrateSettings(payload);return payload;
  }
  function literatureState(kind, message) {
    return `<div class="fusion-state-card large" data-literature-state="${kind}"><strong>${esc(message)}</strong><span>${kind==="error"?"请确认本机工作区可用后重新打开体验版。不会自动重试或访问网络服务。":"体验版只读取公开安全字段，不打开 PDF，也不修改数据库。"}</span></div>`;
  }
  function publicPaper(raw) {
    const id = Number(raw?.id);
    return Number.isSafeInteger(id) && id > 0 && String(raw?.title || "").trim() ? {id,title:String(raw.title).trim(),doi:String(raw.doi||"").trim(),year:raw.year||"",firstAuthor:String(raw.first_author||"").trim(),material:String(raw.material_focus||"").trim(),count:Math.max(0,Number(raw.six_row_count)||0),status:String(raw.six_workflow_label||raw.six_workflow_state||"只读")} : null;
  }
  function publicEvidence(raw) {
    const type=String(raw?.entity_type||raw?.asset_type||"");if(!["item","finding","table","figure"].includes(type))return null;
    return {type,title:String(raw.meaning||raw.display_name||raw.label||raw.caption||raw.finding_text||"未命名证据"),value:String(raw.value_text|| (type==="finding"?"定性":type==="table"?"表格":"图像")),unit:String(raw.unit||""),page:raw.source_page??raw.page_start??"—",excerpt:String(raw.source_excerpt||raw.context_explanation||raw.source_context||raw.finding_text||""),articleTitle:String(raw.article_title||"")};
  }
  function renderPaperCatalog() {
    const host=q("#fusion-paper-catalog");if(!host)return;
    host.setAttribute("aria-busy","false");
    if(!state.papers.length){host.innerHTML='<div class="fusion-state-card" data-literature-state="empty"><strong>当前没有可浏览的文献</strong><span>可在 0.9.2 完整工作台中导入文献。</span></div>';return;}
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
    const lines=state.evidence.slice(0,12).map((row,index)=>`<button type="button" class="fusion-evidence-line${index===0?" selected":""}" data-evidence-index="${index}"><b>${esc(row.value)}${row.unit?` <small>${esc(row.unit)}</small>`:""}</b><span>${esc(row.title)} · 第 ${esc(row.page)} 页</span><em>${({item:"测量条目",finding:"研究结论",table:"完整表格",figure:"论文图片"})[row.type]}</em></button>`).join("");
    host.innerHTML=`<article class="fusion-paper-heading"><span>READ-ONLY LITERATURE · LIVE PUBLIC SNAPSHOT</span><h1>${esc(paper.title)}</h1><p>${esc([paper.firstAuthor,paper.year,paper.doi,paper.material].filter(Boolean).join(" · ")||"公开元数据未提供")}</p><dl><div><dt>处理状态</dt><dd>${esc(paper.status)}</dd></div><div><dt>证据条目</dt><dd>${state.evidence.length}</dd></div><div><dt>模式</dt><dd>只读 GET</dd></div></dl></article><section class="fusion-readonly-block"><header><strong>公开四类证据概览</strong><span>只读</span></header>${lines||'<div class="fusion-state-card" data-literature-state="empty"><strong>这篇文献暂无公开证据</strong><span>目录信息仍可只读浏览。</span></div>'}</section>`;
    qa("[data-evidence-index]").forEach(button=>button.addEventListener("click",()=>selectEvidence(Number(button.dataset.evidenceIndex))));
    renderSearchEvidence();selectEvidence(0,{focus:false});
  }
  function renderSearchEvidence() {
    const host=q("#fusion-search-results");if(!host)return;const results=state.evidence.map((row,index)=>`<button type="button" class="fusion-result${index===0?" selected":""}" role="option" aria-selected="${index===0?"true":"false"}" data-search-evidence-index="${index}"><span>${({item:"测量条目",finding:"研究结论",table:"完整表格",figure:"论文图片"})[row.type]} · 本机只读</span><b>${esc(row.value)}${row.unit?` <small>${esc(row.unit)}</small>`:""}</b><h2>${esc(row.title)}</h2><span class="fusion-result-meta">${esc(row.articleTitle||state.paper?.title||"")} · 第 ${esc(row.page)} 页</span>${row.excerpt?`<span class="fusion-result-excerpt">${esc(row.excerpt)}</span>`:""}</button>`).join("");
    const terminal=state.literatureStatus==="error"?literatureState("error","真实证据暂时无法载入"):literatureState("empty",state.paper?"当前论文暂无公开证据":"当前没有可浏览的文献");
    host.innerHTML=results||terminal;
    qa("[data-search-evidence-index]").forEach(button=>button.addEventListener("click",()=>selectEvidence(Number(button.dataset.searchEvidenceIndex))));
  }
  function selectEvidence(index,{focus=true}={}) {
    const row=state.evidence[index];if(!row){updateInspector();return false;}
    state.evidenceIndex=index;
    qa("[data-evidence-index],[data-search-evidence-index]").forEach(button=>{const active=Number(button.dataset.evidenceIndex??button.dataset.searchEvidenceIndex)===index;button.classList.toggle("selected",active);button.setAttribute("aria-selected",active?"true":"false");});
    updateInspector();
    if(focus)buttonForEvidence(index)?.focus({preventScroll:true});return true;
  }
  function buttonForEvidence(index){return state.view==="search"?q(`[data-search-evidence-index="${index}"]`):q(`[data-evidence-index="${index}"]`);}
  async function selectPaper(id) {
    const paper=state.papers.find(candidate=>candidate.id===id);if(!paper)return false;state.paper=paper;renderPaperCatalog();
    const request=++state.literatureRequest;state.evidenceIndex=0;state.literatureStatus="loading";q("#fusion-literature-content").innerHTML=literatureState("loading","正在读取真实文献证据…");
    q("#fusion-search-results").innerHTML=literatureState("loading","正在读取真实文献证据…");resetEvidenceCounts();q("#fusion-current-paper-state").textContent="载入中";q("#fusion-current-paper-count").textContent="—";
    try {const page=await readOnlyJSON(`${READ_ONLY_ROUTES.evidence}?q=&paper_ids=${encodeURIComponent(String(id))}&limit=100`);if(request!==state.literatureRequest)return false;state.evidence=(Array.isArray(page?.rows)?page.rows:[]).map(publicEvidence).filter(Boolean);state.literatureStatus="ready";renderLiterature();renderPaperCatalog();return true;}
    catch(_error){if(request!==state.literatureRequest)return false;state.evidence=[];state.literatureStatus="error";renderLiterature();renderSearchEvidence();renderPaperCatalog();return false;}
  }
  async function loadLiterature() {
    const request=++state.literatureRequest;state.literatureStatus="loading";
    try {const papers=await readOnlyJSON(READ_ONLY_ROUTES.papers);if(request!==state.literatureRequest)return false;state.papers=(Array.isArray(papers)?papers:[]).map(publicPaper).filter(Boolean);renderPaperCatalog();if(!state.papers.length){state.paper=null;state.evidence=[];state.literatureStatus="empty";renderLiterature();renderSearchEvidence();return true;}return selectPaper(state.papers[0].id);}
    catch(_error){if(request!==state.literatureRequest)return false;state.papers=[];state.paper=null;state.evidence=[];state.literatureStatus="error";renderPaperCatalog();renderLiterature();renderSearchEvidence();return false;}
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
    try {const dto=await readOnlyJSON(READ_ONLY_ROUTES.settings);if(!hydrateSettings(dto))throw new Error("fusion_settings_invalid");return true;}
    catch(_error){showSettingsStatus("桌面设置暂时不可用；本次会话继续使用当前外观。","error");return false;}
  }
  async function chooseAppearance(theme,density) {
    applyAppearance(theme,density);showSettingsStatus("正在保存桌面外观设置…","loading");
    try {await patchPreferences();return true;}catch(_error){
      try {const latest=await readOnlyJSON(READ_ONLY_ROUTES.settings);if(latest?.schema_version==="desktop-settings-v1"&&Number.isInteger(latest.revision))state.settingsRevision=latest.revision;}catch(_ignored){}
      showSettingsStatus("外观保存失败；本次会话仍保留当前选择，可稍后重试。","error");state.theme=theme;state.density=density;safeStoreAppearance();return false;
    }
  }
  function evidenceInspector() {
    const row=state.evidence[state.evidenceIndex];if(!row)return `<section class="fusion-inspector-section"><span>真实文献 · 只读</span><h2>${state.literatureStatus==="error"?"证据暂时无法载入":"尚未选择证据"}</h2><p>体验版不会打开 PDF、请求 AI 或修改文献。</p></section>`;
    return `<section class="fusion-inspector-section"><span>${esc(row.type.toUpperCase())} · 真实只读证据</span><div class="metric">${esc(row.value)}${row.unit?` <small>${esc(row.unit)}</small>`:""}</div><h2>${esc(row.title)}</h2></section><section class="fusion-inspector-section"><dl><dt>来源页</dt><dd>${esc(row.page)}</dd><dt>论文</dt><dd>${esc(row.articleTitle||state.paper?.title||"—")}</dd><dt>状态</dt><dd>只读</dd></dl>${row.excerpt?`<p>${esc(row.excerpt)}</p>`:""}</section>`;
  }
  function inspectorForView(view) {
    if(view==="search"||view==="paper") return evidenceInspector();
    if(view==="personal") return personalInspector();
    if(view==="package") return `<section class="fusion-inspector-section"><span>资料包安全</span><h2>三类数据彼此隔离</h2><p>官方资料包只读且经签名审计；私人实验留在本机；体验版不导入、不回退、不导出。</p></section>`;
    if(view==="settings") return `<section class="fusion-inspector-section"><span>设置边界</span><h2>仅外观可操作</h2><p>主题与密度保存在无敏感信息的本地缓存。提供商、密钥和数据操作均停用。</p></section>`;
    return `<section class="fusion-inspector-section"><span>Fusion GUI 体验版</span><h2>只读检查器</h2></section>`;
  }
  function updateInspector() {
    const title={paper:"论文检查器",search:"证据检查器",personal:"列检查器",package:"安全边界",settings:"设置说明"}[state.view];
    q("#fusion-inspector-title").textContent=title; q("#fusion-inspector-body").innerHTML=inspectorForView(state.view);bindColumnEditor();
  }
  const SETTINGS_LABELS={appearance:"设置：外观",language:"设置：语言",ai:"设置：AI 与密钥",data:"设置：数据",about:"设置：关于"};
  const DETAIL_TAB_LABELS={paper:"证据详情 · 后续接入",search:"图书管理员 · 官方全库 · 后续接入",personal:"列与曲线详情 · 后续接入",package:"任务详情 · 后续接入",settings:"设置说明"};
  function switchView(name,{focus=true}={}) {
    if(!VIEWS.includes(name)) return false;
    const previous=state.view; state.view=name; document.body.dataset.view=name;
    qa("[data-view-panel]").forEach(panel=>{const active=panel.dataset.viewPanel===name;panel.hidden=!active;panel.classList.toggle("active",active);});
    qa(".fusion-nav[data-view]").forEach(button=>{const active=button.dataset.view===name;button.classList.toggle("active",active);active?button.setAttribute("aria-current","page"):button.removeAttribute("aria-current");});
    qa("[data-context-view]").forEach(panel=>{const active=panel.dataset.contextView===name;panel.hidden=!active;panel.classList.toggle("active",active);});
    q("#fusion-context-title").textContent=VIEW_LABELS[name]; q("#fusion-breadcrumb").textContent=`${VIEW_LABELS[name]} / Fusion GUI 体验`;
    q("#fusion-primary-tab-label").textContent=name==="settings"?SETTINGS_LABELS[state.settingsSection]:{paper:"当前论文",search:"精确检索",personal:syntheticSheets[state.sheet].label,package:"资料包中心"}[name];
    q("#fusion-detail-tab-label").textContent=DETAIL_TAB_LABELS[name];
    q("#fusion-status-context").textContent={paper:"真实文献只读",search:"真实证据只读",personal:"合成实验数据",package:"资料包只读",settings:"仅外观可操作"}[name];
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
    const sheet=syntheticSheets[state.sheet],column=sheet.columns[state.column],value=sheet.rows[state.row]?.[state.column];
    return `<section class="fusion-inspector-section"><span>列 ${state.column+1} · ${state.reviewed?"本次会话已检查":"尚未检查"}</span><div class="metric">${esc(value)}${column.unit!=="—"?` <small>${esc(column.unit)}</small>`:""}</div><h2>${esc(column.name)}</h2><p>第 ${state.row+1} 行 · 合成数据，不来自生产数据库。</p></section><section class="fusion-inspector-section fusion-column-editor"><label>角色<select data-column-field="role"><option>实验条件</option><option>测量值</option><option>不确定度</option><option>标识符</option><option>样品信息</option><option>方法</option><option>忽略</option></select></label><label>物理意义<input data-column-field="meaning" value="${esc(column.meaning)}" maxlength="120"></label><label>单位<input data-column-field="unit" value="${esc(column.unit)}" maxlength="32"></label><small>仅修改当前会话内的合成列定义；重启即清除。</small></section><section class="fusion-inspector-section"><span>演示 AI 建议</span><p>${state.demoSuggestion?"演示建议已展示；没有调用任何模型，也没有发送表格。":"尚未展示。体验版可演示建议状态，但不会调用模型。"}</p></section>`;
  }
  function bindColumnEditor() {
    if(state.view!=="personal")return;const column=syntheticSheets[state.sheet].columns[state.column];const role=q('[data-column-field="role"]');if(role){role.value=column.role;role.addEventListener("change",()=>updateColumnDefinition("role",role.value));}for(const field of ["meaning","unit"]){const input=q(`[data-column-field="${field}"]`);input?.addEventListener("input",()=>updateColumnDefinition(field,input.value));}
  }
  function updateColumnDefinition(field,value) {
    if(!["role","meaning","unit"].includes(field))return false;const column=syntheticSheets[state.sheet].columns[state.column];column[field]=String(value).trim().slice(0,field==="meaning"?120:32);renderSheet(state.sheet,{focus:false,preserveSelection:true});return true;
  }
  function selectSettingsSection(name,{focus=false}={}) {
    if(!["appearance","language","ai","data","about"].includes(name))return false;state.settingsSection=name;qa("[data-settings-panel]").forEach(panel=>panel.hidden=panel.dataset.settingsPanel!==name);qa("[data-settings-section]").forEach(button=>{const active=button.dataset.settingsSection===name;button.classList.toggle("active",active);button.setAttribute("aria-selected",active?"true":"false");button.tabIndex=active?0:-1;});if(state.view==="settings")q("#fusion-primary-tab-label").textContent=SETTINGS_LABELS[name];if(focus)q(`[data-settings-section="${name}"]`)?.focus();return true;
  }
  function markReviewed() {state.reviewed=true;updateReviewStatus();updateInspector();return true;}
  function showDemoSuggestion() {state.demoSuggestion=true;const status=q("#fusion-demo-suggestion-status");if(status)status.textContent="演示建议已显示 · 未调用模型";const context=q("#fusion-ai-demo-context-state");if(context)context.textContent="演示建议 · 零模型";updateInspector();return true;}
  function updateReviewStatus() {const label=state.reviewed?"✓ 本次会话已检查":"尚未检查";const toolbar=q("#fusion-reviewed-state"),context=q("#fusion-review-context-state");if(toolbar)toolbar.textContent=label;if(context)context.textContent=state.reviewed?"已检查":"尚未检查";}
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
    readAppearance();renderSheet("hardness");switchView("paper",{focus:false});void Promise.all([loadSettings(),loadLiterature()]);
    qa(".fusion-nav[data-view]").forEach(button=>{button.addEventListener("click",()=>switchView(button.dataset.view));button.addEventListener("keydown",event=>handleRovingTabs(event,".fusion-nav[data-view]",next=>{switchView(next.dataset.view,{focus:false});next.focus();}));});
    qa("[data-theme-choice]").forEach(button=>button.addEventListener("click",()=>void chooseAppearance(button.dataset.themeChoice,state.density)));
    qa("[data-density-choice]").forEach(button=>button.addEventListener("click",()=>void chooseAppearance(state.theme,button.dataset.densityChoice)));
    qa("[data-open-drawer]").forEach(button=>{const kind=button.dataset.openDrawer;button.setAttribute("aria-controls",kind==="context"?"fusion-context":"fusion-inspector");button.setAttribute("aria-expanded","false");button.addEventListener("click",()=>openDrawer(kind,button));});
    qa("[data-close-drawer]").forEach(button=>button.addEventListener("click",()=>closeDrawers()));q("[data-close-all-drawers]")?.addEventListener("click",()=>closeDrawers());
    q("#fusion-command")?.addEventListener("click",event=>openCommandPalette(event.currentTarget));qa("[data-command-view]").forEach(button=>{button.addEventListener("keydown",handlePaletteKey);button.addEventListener("click",()=>{const view=button.dataset.commandView;closeCommandPalette(false);switchView(view);});});
    qa("[data-settings-section]").forEach(button=>{button.addEventListener("click",()=>selectSettingsSection(button.dataset.settingsSection));button.addEventListener("keydown",event=>handleRovingTabs(event,"[data-settings-section]",next=>selectSettingsSection(next.dataset.settingsSection,{focus:true})));});selectSettingsSection("appearance");
    qa("[data-sheet]").forEach(button=>{button.addEventListener("click",()=>renderSheet(button.dataset.sheet,{focus:state.view==="personal"}));button.addEventListener("keydown",event=>handleRovingTabs(event,".fusion-sheet-tabs [data-sheet]",next=>renderSheet(next.dataset.sheet,{focus:true})));});
    qa(".fusion-tabs [role='tab']").forEach(button=>button.addEventListener("keydown",event=>handleRovingTabs(event,".fusion-tabs [role='tab']",next=>next.focus())));
    q("#fusion-data-grid")?.addEventListener("keydown",handleGridKey);
    q("#fusion-mark-reviewed")?.addEventListener("click",markReviewed);q("#fusion-demo-suggestion")?.addEventListener("click",showDemoSuggestion);updateReviewStatus();
    document.addEventListener("keydown",handleShortcut);globalThis.addEventListener?.("resize",syncDrawerAccessibility);syncDrawerAccessibility();
  }
  globalThis.AutoResearchFusion=Object.freeze({initialize,switchView,renderSheet,selectCell,selectPaper,selectEvidence,loadLiterature,loadSettings,hydrateSettings,chooseAppearance,openDrawer,closeDrawers,openCommandPalette,closeCommandPalette,applyAppearance,updateColumnDefinition,selectSettingsSection,markReviewed,showDemoSuggestion,state,syntheticSheets});
  if(document.readyState==="loading") document.addEventListener("DOMContentLoaded",initialize,{once:true});else initialize();
})();
