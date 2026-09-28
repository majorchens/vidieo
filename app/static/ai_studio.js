/* Company-wide AI creation. Provider names and credentials remain in the admin view. */
(() => {
  const ROOT = '/api/ai-studio';
  const A = {overview:null, overviewAt:0, overviewPending:null, artifacts:[], artifactsAt:0,
    current:null, view:'home', selectedProject:'', selectedTask:'', selectedAsset:'', selectedPrompt:'',
    lastSpec:null, drafts:{}, historyFilters:{}};
  const labels = {write:'写内容',ideas:'找创意',image:'生成图片',video:'生成视频',
    speech:'生成语音',audio:'音乐 / 音频',analyze:'分析文件',assistant:'AI 助手'};
  const icons = {write:'✍️',ideas:'💡',image:'🎨',video:'🎬',speech:'🎙️',audio:'🎵',analyze:'📄',assistant:'💬'};
  const summaries = {write:'文案、脚本、翻译与总结',ideas:'选题、剧情、活动与灵感',
    image:'角色、场景、道具与封面',video:'画面、角色与动作视频',
    speech:'教学、跟练、对白与旁白',audio:'BGM、音效与氛围音乐',
    analyze:'理解文档与表格',assistant:'结合项目继续讨论'};
  const E = v => esc(v == null ? '' : v);
  const $ai = s => document.querySelector(s);
  const name = v => projectName[v] || v || '自由工作';
  const statusLabel = {queued:'排队中',running:'制作中',complete:'已完成',completed:'已完成',succeeded:'已完成',failed:'失败',unknown_submission:'提交待核实',disabled:'不可用'};
  const ready = x => ['PRODUCTION_READY','production_ready','ready'].includes(String(x || ''));
  const box = (message, cls='') => `<div class="ai-muted-banner ${cls}">${E(message)}</div>`;
  const badge = s => `<span class="ai-badge ${['failed','unknown_submission','disabled'].includes(s)?'bad':['queued','running'].includes(s)?'warn':''}">${E(statusLabel[s] || s || '未开始')}</span>`;
  const projectOptions = (chosen='', empty='自由工作') => `<option value="">${E(empty)}</option>${(A.overview?.projects || []).map(p => `<option value="${E(p.id)}" ${p.id===chosen?'selected':''}>${E(p.name)}</option>`).join('')}`;
  function chooseProject(chosen=''){
    return new Promise(resolve=>{
      const projects=A.overview?.projects||[];
      if(!projects.length){notice('当前没有可保存的项目',true);resolve('');return;}
      const overlay=document.createElement('div');overlay.className='ai-project-dialog';
      overlay.innerHTML=`<div class="ai-project-dialog-panel" role="dialog" aria-modal="true" aria-label="选择项目"><h3>保存到项目</h3><p>选择这份成果要归属的项目。</p><label>项目<select>${projects.map(p=>`<option value="${E(p.id)}" ${p.id===chosen?'selected':''}>${E(p.name)}</option>`).join('')}</select></label><div class="actions"><button type="button" data-pick="cancel" class="outline">取消</button><button type="button" data-pick="save" class="primary">保存</button></div></div>`;
      const finish=value=>{document.removeEventListener('keydown',onKey);overlay.remove();resolve(value);};
      const onKey=event=>{if(event.key==='Escape')finish('');};
      overlay.addEventListener('click',event=>{if(event.target===overlay||event.target.closest('[data-pick="cancel"]'))finish('');else if(event.target.closest('[data-pick="save"]'))finish(overlay.querySelector('select')?.value||'');});
      document.addEventListener('keydown',onKey);document.body.append(overlay);overlay.querySelector('select')?.focus();
    });
  }
  const feature = key => (A.overview?.capabilities || {})[key] || {};
  const available = key => ready(feature(key).status) ||
    (key==='image' && feature(key).enabled && feature(key).status==='CONFIGURED');
  const availabilityLabel = key => key==='image'&&feature(key).status==='CONFIGURED'&&feature(key).enabled
    ? '可试用 · 待费用验收' : available(key) ? '可以开始' : '暂未接通';
  const shell = (title,body) => {
    page(title,'工作台 / AI 创作');
    const back=A.view==='home'?'':`<button type="button" class="ai-back" data-ai-go="${A.view==='history'?'home':A.view==='artifact'?'history':'home'}">← 返回</button>`;
    $ai('#content').innerHTML=`<div class="ai-shell"><div class="ai-page-head">${back}<div><h2>${E(title)}</h2><p>${E(summaries[A.view] || '保存每一次创作，随时继续制作。')}</p></div></div>${body}</div>`;
  };
  const selectProject = () => `<label>项目<select name="project_id" data-ai-project>${projectOptions(A.selectedProject)}</select></label>`;
  const selectAssets = '<label class="ai-wide">从项目素材选择（最多 5 件）<select name="asset_ids" data-ai-assets multiple size="4"><option value="">请先选择项目</option></select><small>选择已登记的文本素材；也可以直接上传新文件。</small></label>';
  async function hydrateAssetSelect(){
    const select=$ai('[data-ai-project]'),list=select?.closest('form')?.querySelector('[data-ai-assets]');
    if(!select||!list||!select.value)return;
    const result=await api(`/api/projects/${encodeURIComponent(select.value)}/assets`);
    const assets=(result.assets||[]).filter(a=>['.md','.txt','.csv','.docx','.xlsx','.pdf'].includes(a.extension||a.storage_ref?.match(/\.[^.]+$/)?.[0]||''));
    list.innerHTML=assets.map(a=>`<option value="${E(a.id)}" ${a.id===A.selectedAsset?'selected':''}>${E(a.name)}</option>`).join('')||'<option value="">当前项目暂无可选文本素材</option>';
  }
  const isManager = () => ['founder','manager'].includes(state.user?.role);
  const load = async (force=false) => {
    if(!force&&A.overview&&Date.now()-A.overviewAt<60000)return A.overview;
    if(!A.overviewPending)A.overviewPending=api(`${ROOT}/overview`).then(data=>{
      A.overview=data;A.overviewAt=Date.now();return data;
    }).finally(()=>{A.overviewPending=null;});
    return A.overviewPending;
  };

  function restoreDraft(key){
    const form=$ai('[data-ai-form]'),saved=A.drafts[key];
    if(!form||!saved)return;
    for(const [name,value] of Object.entries(saved)){
      const control=form.elements.namedItem(name);
      if(!control||control.type==='file'||name==='asset_ids')continue;
      if(typeof value==='string')control.value=value;
    }
  }
  function rememberDraft(form){
    const saved={};
    for(const control of form.elements){
      if(control.name&&control.type!=='file'&&control.name!=='asset_ids'&&typeof control.value==='string')saved[control.name]=control.value;
    }
    A.drafts[form.dataset.aiForm]=saved;
  }

  async function home(){
    A.view='home';
    const token=state.routeVersion;
    shell('开始创作','<div class="ai-loading">正在确认可用的创作能力…</div>');
    await load();if(state.routeVersion!==token)return;
    const cards=Object.keys(labels).map(key => `<button class="ai-capability ${available(key)?'':'is-unavailable'}" data-ai-go="${key}"><span class="ai-icon">${icons[key]}</span><strong>${labels[key]}</strong><small>${summaries[key]}</small><span class="ai-state ${available(key)?'is-ready':''}">${availabilityLabel(key)}</span></button>`).join('');
    shell('开始创作',`<div class="ai-home-intro"><div><strong>你想做什么？</strong><p>选一种创作目标，素材和设置会跟着任务走。</p></div><button class="outline" data-ai-go="history">创作记录 →</button></div><div class="ai-capabilities">${cards}</div><div class="section-title"><h2>最近成果</h2></div><div id="ai-recent" class="ai-results"><div class="ai-loading">正在读取创作记录…</div></div>`);
    await listArtifacts({},'#ai-recent',5);
  }

  const settings = (key,extra='') => `<div class="ai-composer-settings"><h3>生成设置</h3>${selectProject()}${extra}<details class="ai-advanced"><summary>高级设置</summary>${key==='speech'?'':`<label>输出语言<select name="language"><option value="zh">中文</option><option value="en">English</option><option value="both">双语</option></select></label>`}<label>补充要求<textarea name="requirements" placeholder="语气、长度、限制等"></textarea></label>${isManager()?`<label>关联任务<input name="task_id" value="${E(A.selectedTask)}" placeholder="可选"></label>`:`<input type="hidden" name="task_id" value="${E(A.selectedTask)}">`}</details></div>`;
  const composer = (key,question,placeholder,attachments,extra='',note='') => {
    const active=available(key);
    return `<form class="ai-composer" data-ai-form="${key}"><div class="ai-composer-main"><p class="ai-kicker">创作内容 + 素材</p><h3>${E(question)}</h3><textarea class="ai-prompt" name="prompt" required maxlength="12000" placeholder="${E(placeholder)}"></textarea><div class="ai-attachments">${attachments}</div>${note?`<p class="ai-form-note">${E(note)}</p>`:''}<div class="ai-composer-result"><h3>本次结果</h3><div id="ai-current" class="ai-results"><div class="empty compact">完成后会显示在这里，并自动保存到创作记录。</div></div></div></div>${settings(key,extra)}<div class="ai-composer-footer"><span>${active?'准备好后开始创作':'暂未接通，当前不能提交生成'}</span><button class="primary" type="submit" ${active?'':'disabled'}>${E(key==='assistant'?'发送':key==='analyze'?'开始分析':labels[key])}</button></div></form>`;
  };
  const fileAttachment = (accept,label='添加文件') => `<label class="ai-attach-button">＋ ${E(label)}<input type="file" name="file" accept="${E(accept)}"></label>`;
  const projectAttachment = () => `<details class="ai-asset-drawer"><summary>＋ 项目资料</summary>${selectAssets}</details>`;
  const unavailableAttachment = text => `<span class="ai-attach-disabled" title="此参考输入尚未接通">＋ ${E(text)} · 暂未接通</span>`;
  function commonForm(key){
    const heading={write:'你想写什么？',ideas:'你想找什么创意？',assistant:'你想讨论什么？'}[key];
    const scenarios=key==='write'?['草稿','改写','扩写','翻译','总结','标题','运营内容','SOP']:
      key==='ideas'?['短视频创意','剧情','活动方案','选题','内容系列']:[];
    const extra=scenarios.length?`<label>用途<select name="scenario">${scenarios.map(x=>`<option>${E(x)}</option>`).join('')}</select></label>`:'';
    return composer(key,heading,'写下目标、受众和你希望得到的结果',fileAttachment('.txt,.md,.pdf,.docx,.csv,.xlsx')+projectAttachment(),extra);
  }
  function imageForm(){
    const purposes=['角色','场景','道具','封面','Storyboard','UI 素材'];
    const extra=`<label>用途<select name="scenario">${purposes.map(x=>`<option>${E(x)}</option>`).join('')}</select></label><label>画幅<input value="16:9 · 2560×1440" readonly></label><input type="hidden" name="width" value="2560"><input type="hidden" name="height" value="1440"><input type="hidden" name="count" value="1"><input type="hidden" name="budget_cap" value="0.172">`;
    return composer('image','描述你想生成的图片','画面主体、场景、风格、光线和用途',unavailableAttachment('参考图')+unavailableAttachment('项目图片'),extra,'目前仅开放已验证报价的单张文生图；图片参考需要单独验证。');
  }
  function videoForm(){
    const models=feature('video').models||{};
    const alias=ready(models['sd2.5'])?'sd2.5':ready(models['sd2.0'])?'sd2.0':'sd2.5';
    const cap=alias==='sd2.0'?'2.50':'3.60';
    const extra=`<div class="ai-mode-list"><strong>常用模式</strong><span class="ai-mode-active">文生视频</span><span>图生视频 · 待验证</span><span>角色参考 · 待验证</span><span>动作参考 · 待验证</span><span>多参考 · 待验证</span></div><label>时长<input name="duration" type="number" value="5" readonly></label><label>画幅<select name="ratio"><option>16:9</option></select></label><label>本次预算上限（元）<input name="budget_cap" type="number" min="0" max="100" step="0.01" value="${cap}"></label><input type="hidden" name="model_alias" value="${alias}"><input type="hidden" name="count" value="1">`;
    return composer('video','描述你想生成的视频','场景、人物、动作顺序和镜头要求',unavailableAttachment('图片')+unavailableAttachment('视频')+unavailableAttachment('角色 / 招式 / 场景'),extra,'当前公司级创作仅验证 5 秒、480p、16:9 文生视频；武学双参考在招式页面制作。');
  }
  function analyzeForm(){
    const extra=`<label>分析方式<select name="scenario">${['总结','提取','比较','找问题','生成方案'].map(x=>`<option>${E(x)}</option>`).join('')}</select></label>`;
    return composer('analyze','你希望从文件中得到什么？','例如：总结关键结论并列出需要跟进的事项',fileAttachment('.pdf,.docx,.txt,.md,.csv,.xlsx','添加待分析文件')+projectAttachment(),extra,'上传文件先留在本人工作空间，明确保存后才加入项目。');
  }
  function speechForm(){
    const extra=`<label>用途<select name="scenario"><option>功法老师 OS</option><option>招式教学</option><option>跟练提示</option><option>剧情对白</option><option>普通旁白</option></select></label><label>Voice Persona<input name="voice_persona" placeholder="继承项目或老师设定"></label><label>语言<select name="language"><option value="zh">中文</option><option value="en">English</option></select></label><details class="ai-advanced"><summary>表达方式</summary><label>情绪<input name="emotion" placeholder="例如：沉稳、鼓励"></label><label>语速<input name="speed" type="number" min="0.5" max="2" step="0.1" value="1"></label></details>`;
    return composer('speech','写下要说的话','输入教学、跟练、对白或旁白文本',unavailableAttachment('老师 Voice Persona'),extra,'语音服务最近一次检查异常；声线与角色定版尚未完成映射，因此暂不能提交。');
  }
  function audioForm(){
    const extra=`<label>用途<select name="scenario"><option>BGM</option><option>氛围音乐</option><option>音效</option><option>UI Sound</option></select></label>`;
    return composer('audio','描述你想要的声音','例如：适合晨练的舒缓训练 BGM',unavailableAttachment('参考音频'),extra,'当前账号没有已验证的音乐或音效生成模型。');
  }
  async function capability(key){
    A.view=key;
    const token=state.routeVersion;
    shell(labels[key],'<div class="ai-loading">正在准备创作空间…</div>');
    await load();if(state.routeVersion!==token)return;
    const form=key==='image'?imageForm():key==='video'?videoForm():key==='analyze'?analyzeForm():
      key==='speech'?speechForm():key==='audio'?audioForm():commonForm(key);
    shell(labels[key],`${ready(feature(key).status)?'':box(feature(key).display_status||'暂未接通；请等待管理员完成真实验证。')} ${form}`);
    restoreDraft(key);
    await hydrateAssetSelect();if(state.routeVersion!==token)return;
    if(A.selectedPrompt){const prompt=$ai('[data-ai-form] [name=prompt]');if(prompt&&!prompt.value)prompt.value=A.selectedPrompt;A.selectedPrompt='';}
  }

  const outputText = value => typeof value==='string'?value: value == null?'':typeof value.text==='string'?value.text:JSON.stringify(value,null,2);
  const mediaItems = artifact => {
    const out=artifact.output || {};
    const items=out.images || out.videos || out.media || (out.asset_id?[out]:[]);
    return Array.isArray(items)?items:[];
  };
  const safeUrl = item => {
    const path=item.download_url || item.preview_url || (item.asset_id?`/api/assets/${encodeURIComponent(item.asset_id)}/download?inline=1`:'');
    if(typeof path!=='string')return '';
    if(path.startsWith('/api/'))return BASE+path;
    if(/^https:\/\//.test(path))return path;
    return '';
  };
  function artifactCard(item){
    const output=item.output ?? item.result ?? '';
    const media=mediaItems(item);
    const ideas=Array.isArray(output?.ideas)?output.ideas:[];
    const ideaCards=ideas.length?`<div class="ai-idea-grid">${ideas.map((idea,i)=>`<div class="ai-idea"><h4>方案 ${String.fromCharCode(65+i)} · ${E(idea.title||'')}</h4><p><strong>核心创意：</strong>${E(idea.core_idea)}</p><p><strong>Hook：</strong>${E(idea.hook)}</p><p><strong>执行难度：</strong>${E(idea.difficulty)}</p><p><strong>需要素材：</strong>${E(Array.isArray(idea.required_assets)?idea.required_assets.join('、'):idea.required_assets)}</p><p><strong>适合平台：</strong>${E(Array.isArray(idea.platform)?idea.platform.join('、'):idea.platform)}</p></div>`).join('')}</div>`:'';
    const visual=media.length?`<div class="ai-gallery">${media.map((m,i)=>{const url=safeUrl(m);return `<figure>${url?item.capability==='video'?`<video controls preload="metadata" src="${E(url)}"></video>`:`<img src="${E(url)}" alt="生成图片 ${i+1}">`:'<div class="empty compact">媒体文件待回收</div>'}<figcaption>结果 ${i+1}${url?` · <a href="${E(url)}" download>下载</a>`:''}</figcaption></figure>`}).join('')}</div>`: (output?`<div class="ai-output">${E(outputText(output))}</div>${ideaCards}`:'<p class="muted">结果尚未返回，可稍后刷新。</p>');
    const complete=['complete','completed','succeeded'].includes(item.status);
    const ops=complete?`<div class="ai-actions"><button data-ai-action="view" data-id="${E(item.id)}">查看</button><button data-ai-action="save" data-id="${E(item.id)}">保存到项目</button><button data-ai-action="library" data-id="${E(item.id)}">加入素材库</button><button data-ai-action="task" data-id="${E(item.id)}">转成任务</button>${item.type==='video'?'':`<button data-ai-action="continue" data-id="${E(item.id)}">继续创作</button>`}<button data-ai-action="again" data-id="${E(item.id)}">再生成</button>${['video','image'].includes(item.type)?`<a href="${E(BASE+ROOT+'/artifacts/'+encodeURIComponent(item.id)+'/download')}" download>下载</a>`:`<button data-ai-action="download" data-id="${E(item.id)}">下载</button><button data-ai-action="copy" data-id="${E(item.id)}">复制</button>`}</div>`:`<div class="ai-actions"><button data-ai-action="refresh" data-id="${E(item.id)}">刷新状态</button></div>`;
    return `<article class="ai-result" data-ai-artifact="${E(item.id)}"><div class="ai-result-head"><h3>${E(output?.title||labels[item.capability]||'AI 成果')}</h3>${badge(item.status)}</div><div class="ai-result-meta">${E(name(item.project_id))} · ${E(item.created_at ? new Date(item.created_at).toLocaleString('zh-CN') : '')}</div>${item.error?box(item.error):''}${visual}${ops}</article>`;
  }

  async function showArtifact(id,slot='#ai-current'){
    const token=state.routeVersion;
    const r=await api(`${ROOT}/artifacts/${encodeURIComponent(id)}`); const item=r.artifact||r;
    if(state.routeVersion!==token)return item;
    A.current=item; const el=$ai(slot); if(el)el.innerHTML=artifactCard(item);
    return item;
  }
  async function listArtifacts(filters={},target='#ai-list',limit=0){
    const token=state.routeVersion;
    if(!A.artifactsAt||Date.now()-A.artifactsAt>30000){
      const r=await api(`${ROOT}/artifacts`);
      if(state.routeVersion!==token)return;
      A.artifacts=r.artifacts||r.items||[];A.artifactsAt=Date.now();
    }
    const group=status=>['queued','running','submitted','dispatching','download_pending','technical_check','unknown_submission'].includes(status)?'running':['complete','completed','succeeded'].includes(status)?'complete':['failed','disabled'].includes(status)?'failed':'running';
    const items=A.artifacts.filter(x=>(!filters.status||filters.status==='all'||group(x.status)===filters.status)&&(!filters.capability||x.capability===filters.capability)&&
      (!filters.project_id||x.project_id===filters.project_id)&&
      (!filters.date_from||x.created_at?.slice(0,10)>=filters.date_from)&&
      (!filters.date_to||x.created_at?.slice(0,10)<=filters.date_to));
    const el=$ai(target); if(el)el.innerHTML=(limit?items.slice(0,limit):items).map(artifactCard).join('')||'<div class="empty compact">暂无生成成果</div>';
  }
  async function history(){
    A.view='history';const token=state.routeVersion;
    shell('创作记录','<div class="ai-loading">正在读取创作记录…</div>');
    await load();if(state.routeVersion!==token)return;
    shell('创作记录',`<div class="ai-toolbar"><select id="ai-history-type"><option value="">全部类型</option>${Object.entries(labels).map(([k,v])=>`<option value="${k}">${v}</option>`).join('')}</select><select id="ai-history-project">${projectOptions('','全部项目')}</select><label>起始日期<input id="ai-history-from" type="date"></label><label>结束日期<input id="ai-history-to" type="date"></label><button class="outline" data-ai-action="filter">筛选</button></div>${objectTabs('ai-history',[['all','全部记录','<div id="ai-list-all" class="ai-results"></div>'],['running','进行中','<div id="ai-list-running" class="ai-results"></div>'],['complete','已完成','<div id="ai-list-complete" class="ai-results"></div>'],['failed','需处理','<div id="ai-list-failed" class="ai-results"></div>']],'all')}`);
    for(const [id,value] of Object.entries(A.historyFilters)){
      const control=$ai('#ai-history-'+id);if(control)control.value=value;
    }
    await renderHistoryStatus(state.objectTabs.get('ai-history')||'all');
  }
  function renderHistoryStatus(status){return listArtifacts({status,capability:A.historyFilters.type,project_id:A.historyFilters.project,
    date_from:A.historyFilters.from,date_to:A.historyFilters.to},'#ai-list-'+status)}

  async function admin(){
    if(!isManager())throw new Error('没有管理权限'); A.view='admin';
    const token=state.routeVersion;
    shell('AI 能力管理','<div class="ai-loading">正在读取能力与调用记录…</div>');
    const d=await api(`${ROOT}/admin`);if(state.routeVersion!==token)return;
    const rows = (items,columns) => `<div class="ai-table-scroll"><table><thead><tr>${columns.map(c=>`<th>${E(c[1])}</th>`).join('')}</tr></thead><tbody>${items.map(x=>`<tr>${columns.map(c=>`<td>${E(x[c[0]]==null?'未知':typeof x[c[0]]==='object'?JSON.stringify(x[c[0]]):x[c[0]])}</td>`).join('')}</tr>`).join('')||`<tr><td colspan="${columns.length}">暂无记录</td></tr>`}</tbody></table></div>`;
    const providers=rows(d.providers||[],[['name','Provider'],['status','状态'],['endpoint_profile','接入方式'],['secret_ref','密钥引用'],['last_check','最近检查'],['last_error','最近错误']]);
    const models=rows(d.models||[],[['model','模型'],['provider','平台'],['capability','能力'],['status','状态'],['input_modes','输入模式']]);
    const routes=`<div class="ai-table-scroll"><table><thead><tr><th>业务能力</th><th>主路由</th><th>备选</th><th>状态</th><th>控制</th></tr></thead><tbody>${(d.routes||[]).map(x=>`<tr><td>${E(x.business_model)}</td><td>${E(x.primary||'未接入')}</td><td>${E(x.secondary||'—')}</td><td>${E(x.status)}</td><td><button class="outline" data-ai-action="route-toggle" data-key="${E(x.route_key)}" data-provider="${E(x.primary||(x.route_key==='image'?'wanjie':''))}" data-enabled="${x.enabled?'1':'0'}">${x.enabled?'停用':'启用'}</button></td></tr>`).join('')}</tbody></table></div>`;
    const calls=rows(d.calls||[],[['created_at','时间'],['provider','平台'],['model','模型'],['capability','用途'],['status','状态'],['input_tokens','输入 token'],['output_tokens','输出 token'],['actual_cost','实际费用'],['error','错误']]);
    const budget=`<form class="ai-form" data-ai-budget><label>预算层级<select name="scope"><option value="project">项目</option><option value="user">员工</option><option value="task">任务</option></select></label><label>编号<input name="scope_id" required placeholder="项目 / 员工 / 任务编号"></label><label>上限（元）<input name="cap_cny" type="number" min="0" max="10000" step="0.01" required></label><button class="primary">保存预算</button></form><p class="ai-help">默认项目上限 ¥${E(d.defaults?.project_cap_cny)}、个人上限 ¥${E(d.defaults?.user_cap_cny)}；任务预算还受原任务额度限制。</p>${rows(d.budgets||[],[['scope','层级'],['scope_id','编号'],['cap_cny','上限'],['updated_at','更新时间']])}`;
    shell('AI 能力管理',objectTabs('ai-admin',[
      ['routes','平台与业务路由',`<section class="ai-admin-card"><h3>平台状态</h3>${providers}</section><section class="ai-admin-card"><h3>业务路由</h3>${routes}</section>`],
      ['models','模型状态',`<section class="ai-admin-card"><h3>模型与真实状态</h3>${models}</section>`],
      ['budgets','预算设置',`<section class="ai-admin-card"><h3>项目 / 员工 / 任务预算</h3>${budget}</section>`],
      ['calls','调用记录',`<section class="ai-admin-card"><h3>最近调用、费用和健康</h3>${calls}</section>`]
    ],'routes'));
  }

  async function route(view){
    const key=view.split(':')[1]||'home';
    if(key==='home')return home(); if(key==='history')return history(); if(key==='admin')return admin();
    if(key==='artifact'){
      A.view='artifact';
      shell('查看创作成果','<div id="ai-current" class="ai-results"><div class="ai-loading">正在读取成果…</div></div>');
      return showArtifact(view.split(':')[2]);
    }
    if(Object.hasOwn(labels,key))return capability(key); return home();
  }

  async function readFiles(form){
    const files={};
    for(const input of form.querySelectorAll('input[type=file]')){
      const file=input.files?.[0]; if(!file)continue;
      const packed=await upload(file); files[input.name]=packed;
    }
    return files;
  }
  async function run(form){
    const capability=form.dataset.aiForm; const data=Object.fromEntries(new FormData(form).entries());
    for(const key of ['file','image_file','video_file'])delete data[key];
    data.asset_ids=Array.from(form.querySelector('[data-ai-assets]')?.selectedOptions||[]).map(x=>x.value).filter(Boolean);
    if(data.project_id)A.selectedProject=data.project_id;
    const files=await readFiles(form);
    const payload={capability,...data,...files};
    if(capability==='video'&&!payload.project_id)throw new Error('视频生成必须先选择项目');
    for(const key of ['count','duration','budget_cap','width','height'])if(payload[key]!==undefined)payload[key]=Number(payload[key]);
    A.lastSpec=payload;
    const result=await api(`${ROOT}/run`,payload);
    const item=result.artifact||result;
    A.current=item;
    A.artifactsAt=0;
    const el=$ai('#ai-current'); if(el)el.innerHTML=artifactCard(item);
    notice(item.status==='failed'?'执行失败，请查看结果':'已提交，结果保存到生成记录',item.status==='failed');
  }
  async function act(action,id){
    const item=A.current?.id===id?A.current:A.artifacts.find(x=>x.id===id)||await showArtifact(id);
    if(action==='refresh'){if(A.view==='history'){A.artifactsAt=0;return renderHistoryStatus(state.objectTabs.get('ai-history')||'all')}return showArtifact(id)}
    if(action==='view')return go('ai:artifact:'+id);
    if(action==='copy'){await navigator.clipboard.writeText(outputText(item.output??item.result));return notice('已复制结果');}
    if(action==='download'){
      const detail=await api(`${ROOT}/artifacts/${encodeURIComponent(id)}`);
      const value=detail.artifact||detail;
      const blob=new Blob([outputText(value.output??value.result)],{type:'text/plain;charset=utf-8'});
      const url=URL.createObjectURL(blob);const link=document.createElement('a');link.href=url;link.download=`AI-创作-${id}.txt`;link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);return;
    }
    if(action==='save'||action==='library'){
      const project=await chooseProject(item.project_id||A.selectedProject||'');
      if(!project)return;
      await api(`${ROOT}/artifacts/${encodeURIComponent(id)}/save`,{project_id:project});
      notice(action==='library'?'已加入素材库':'已保存到项目');return showArtifact(id);
    }
    if(action==='task'){
      const title=prompt('新任务名称',item.title||labels[item.capability]||'AI 成果后续制作');if(!title)return;
      if(!item.project_id){const project=await chooseProject(A.selectedProject||'');if(!project)return;await api(`${ROOT}/artifacts/${encodeURIComponent(id)}/save`,{project_id:project});}
      await api(`${ROOT}/artifacts/${encodeURIComponent(id)}/task`,{title});
      notice('已转成任务');return showArtifact(id);
    }
    if(action==='continue'){
      const instruction=prompt('下一步如何深化这份成果？');if(!instruction)return;
      const result=await api(`${ROOT}/artifacts/${encodeURIComponent(id)}/continue`,{instruction});
      const next=result.artifact||result;
      notice('已提交继续深化');
      return showArtifact(next.id, $ai('#ai-current')?'#ai-current':'#ai-list-'+(state.objectTabs.get('ai-history')||'all'));
    }
    if(action==='again'){
      const detail=await api(`${ROOT}/artifacts/${encodeURIComponent(id)}`);
      const source=detail.artifact||detail;
      await capability(source.capability);
      const f=$ai('[data-ai-form]');
      if(f){
        for(const key of ['project_id','scenario','language','prompt','requirements','model_alias']){
          if(f.elements[key]&&source.inputs?.[key]!=null)f.elements[key].value=source.inputs[key];
        }
        if(f.elements.project_id){f.elements.project_id.dispatchEvent(new Event('change',{bubbles:true}));}
        f.elements.prompt?.focus();
      }
      notice('已载入原要求；检查后点击生成');
    }
  }

  document.addEventListener('click',async e=>{
    const historyTab=e.target.closest('[data-object-tabs="ai-history"] [data-object-tab]');
    if(historyTab){await renderHistoryStatus(historyTab.dataset.objectTab);return}
    const jump=e.target.closest('[data-ai-go]');
    if(jump){e.preventDefault();return go('ai:'+jump.dataset.aiGo);}
    const b=e.target.closest('[data-ai-action]'); if(!b)return;
    e.preventDefault(); try{b.disabled=true;
      if(b.dataset.aiAction==='route-toggle'){
        await api(`${ROOT}/admin/route`,{business_model:b.dataset.key,primary_provider:b.dataset.provider||null,enabled:b.dataset.enabled!=='1'});
        notice('路由设置已保存');return admin();
      }
      if(b.dataset.aiAction==='filter'){
        A.historyFilters={type:$ai('#ai-history-type')?.value||'',project:$ai('#ai-history-project')?.value||'',
          from:$ai('#ai-history-from')?.value||'',to:$ai('#ai-history-to')?.value||''};
        return renderHistoryStatus(state.objectTabs.get('ai-history')||'all');
      }
      return await act(b.dataset.aiAction,b.dataset.id);
    }catch(err){notice(err.message,true)}finally{b.disabled=false}
  });
  document.addEventListener('change',async e=>{
    const select=e.target.closest('[data-ai-project]');if(!select)return;
    const list=select.closest('form')?.querySelector('[data-ai-assets]');if(!list)return;
    if(!select.value){list.innerHTML='<option value="">请先选择项目</option>';return;}
    A.selectedAsset='';try{await hydrateAssetSelect()}catch(err){notice(err.message,true)}
  });
  document.addEventListener('input',e=>{
    const form=e.target.closest?.('[data-ai-form]');if(form)rememberDraft(form);
  });
  document.addEventListener('submit',async e=>{
    const budget=e.target.closest('[data-ai-budget]');
    if(budget){e.preventDefault();const data=Object.fromEntries(new FormData(budget).entries());data.cap_cny=Number(data.cap_cny);try{await api(`${ROOT}/admin/budget`,data);notice('预算已保存');return admin()}catch(err){notice(err.message,true)}return;}
    const f=e.target.closest('[data-ai-form]'); if(!f)return;
    e.preventDefault();const b=f.querySelector('button[type=submit],button:not([type])');
    try{if(b)b.disabled=true;await run(f)}catch(err){notice(err.message,true)}finally{if(b)b.disabled=false}
  });
  window.aiStudio={route,setContext:(projectId,taskId='',assetId='',prompt='')=>{A.selectedProject=projectId||'';A.selectedTask=taskId||'';A.selectedAsset=assetId||'';A.selectedPrompt=prompt||'';}};
})();
