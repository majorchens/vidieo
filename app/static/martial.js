/* Job-oriented martial asset workspace. Existing Work OS sessions and tasks remain authoritative. */
(() => {
  const nav = [
    ['martial:overview','首页'],[null,'我的工作'],
    ['martial:tasks','我的任务'],['martial:revisions','我的返修'],['works','我的作品'],
    [null,'万象武境'],['martial:arts','功法管理'],['martial:masters','功法老师'],['martial:assets','素材中心']
  ];
  const emptyAssetFilter=()=>({category:'',project_id:'',art_id:'',master_id:'',move_id:'',type:'',usage:'',status:'',creator:'',model:'',provider:'',date_from:'',date_to:'',q:'',tags:''});
  const M = { overview:null, overviewAt:0, overviewPromise:null, cacheEpoch:0, assetCache:new Map(), workspaceCache:null, assetRequestVersion:0, detail:null, quote:{}, previewQuote:{}, task:null, previewUrl:null, prepareTimer:null, mediaTimer:null, filter:'all', assetFilter:emptyAssetFilter(), assetProjects:[], tab:'production', artWizard:{step:1, mode:'single', moves:[]}, assetDetail:null };
  const OVERVIEW_TTL=30000,ASSET_TTL=20000;
  function invalidateCache(){M.cacheEpoch++;M.overview=null;M.overviewAt=0;M.overviewPromise=null;M.assetCache.clear();M.workspaceCache=null}
  const e = esc;
  const a = (id) => assetUrl({id},true);
  const martialUpload = async file => {if(file.size>40_000_000)throw new Error('武学素材超过 40MB 上限');return upload(file)};
  const uploadMotionFile = (moveId,file,form) => new Promise((resolve,reject) => {
    if(!/\.(mp4|mov)$/i.test(file.name)){reject(new Error('请选择 MP4 或 MOV 视频'));return}
    const xhr=new XMLHttpRequest(),progress=form.querySelector('.mw-upload-progress');
    progress.hidden=false;progress.textContent='正在上传真人动作…';
    xhr.open('POST',BASE+`/api/martial/moves/${encodeURIComponent(moveId)}/motion-file`);
    xhr.withCredentials=true;
    xhr.timeout=15*60*1000;
    xhr.setRequestHeader('Content-Type',/\.mov$/i.test(file.name)?'video/quicktime':'video/mp4');
    xhr.setRequestHeader('X-File-Name',encodeURIComponent(file.name));
    xhr.setRequestHeader('X-CSRF-Token',state.csrf);
    xhr.upload.onprogress=event=>{if(event.lengthComputable)progress.textContent=`正在上传真人动作：${Math.round(event.loaded/event.total*100)}%`};
    xhr.onerror=()=>reject(new Error('网络中断，上传未完成；可重新选择原文件上传'));
    xhr.ontimeout=()=>reject(new Error('上传超时，视频未确认保存'));
    xhr.onload=()=>{let result;try{result=JSON.parse(xhr.responseText)}catch{result={error:'服务响应不可解析'}}if(xhr.status>=200&&xhr.status<300){invalidateCache();resolve(result)}else reject(new Error(result.error||`上传失败 ${xhr.status}`))};
    xhr.send(file);
  });
  const words = {not_started:'待开始',queued:'等待提交',dispatching:'提交中',submitted:'生成中',running:'生成中',download_pending:'下载中',technical_check:'技术检查',succeeded:'完成',failed:'需要返修',unknown_submission:'提交状态未知，暂停重试',needs_review:'待核对',ready_for_approval:'已提交负责人',returned:'已退回修改',active:'已确认',confirmed:'已确认',confirmed_with_missing_fields:'已确认 · 源资料有空栏',locked:'已锁定',draft:'草稿',complete:'已准备',pass:'通过',fail:'需要返修',unverified:'未核实'};
  const pill = s => `<span class="mw-pill ${['failed','unknown_submission'].includes(s)?'mw-danger':''}">${e(words[s]||s||'未开始')}</span>`;
  const val = x => x === null || x === undefined || x === '' ? '未登记' : e(x);
  const panel = (title,body,extra='') => `<article class="mw-panel ${extra}"><h3>${e(title)}</h3>${body}</article>`;
  const button = (label,action,id='',cls='outline') => `<button type="button" class="${cls}" data-martial-action="${e(action)}" data-id="${e(id)}">${e(label)}</button>`;
  const link = (label,view) => `<button class="mw-link" data-view="${e(view)}">${e(label)} →</button>`;
  const plainLink = (label,view) => `<button class="mw-link" data-view="${e(view)}">${e(label)}</button>`;
  const field = (label,name,value='',area=false) => `<label>${e(label)}${area?`<textarea name="${e(name)}">${e(value)}</textarea>`:`<input name="${e(name)}" value="${e(value)}">`}</label>`;
  const video = (id,cls='') => id?`<video class="${cls}" controls preload="metadata" src="${a(id)}"></video>`:'<div class="mw-empty-media">尚无视频</div>';
  const image = id => id?`<img src="${a(id)}" alt="老师参考">`:'<div class="mw-empty-media">尚未上传</div>';
  const seconds = value => {const n=Number(value);return Number.isFinite(n)?`${String(Math.floor(n/60)).padStart(2,'0')}:${(n%60).toFixed(2).padStart(5,'0')}`:'--:--.--'};
  const employeeText=x=>String(x||'').replaceAll('AI Production Package','AI 准备内容').replaceAll('Motion Lock','标准动作').replaceAll('Candidate','视频候选');
  const block = x => Array.isArray(x)?x.length?`<ul>${x.map(y=>`<li>${e(typeof y==='string'?y:typeof y==='object'?Object.values(y).join(' · '):String(y))}</li>`).join('')}</ul>`:'<p class="muted">无</p>':x&&typeof x==='object'?`<ul>${Object.entries(x).map(([k,v])=>`<li>${e({camera:'画面',duration:'时长',ratio:'比例',resolution:'清晰度',model:'模型'}[k]||k)}：${e(Array.isArray(v)?v.join('；'):String(v))}</li>`).join('')}</ul>`:`<p>${e(x||'待补')}</p>`;
  function revisionBlock(key,values){
    if(key==='target_range'&&Array.isArray(values))return values.length?`<ul>${values.map(x=>`<li>${seconds(x.start)}–${seconds(x.end)}：${e(x.issue||'待说明问题')}${x.severity?`（${e({minor:'轻微',major:'明显',critical:'严重'}[x.severity]||x.severity)}）`:''}</li>`).join('')}</ul>`:'<p class="muted">无</p>';
    if(key==='do_not_change'&&Array.isArray(values))return block(values.map(value=>String(value).replace(/^真人 Motion REF \S+ \/ (V\d+)（视频资产 \S+；工作区间 ([^）]+)）/,'真人标准动作 $1（参考片段 $2）').replace(/（视觉资产 [^）]+）/g,'')));
    return block(values);
  }
  const isManager = () => ['founder','manager'].includes(state.user.role);
  const canEdit = () => !!state.user.martial_specialist || isManager();
  const videoKinds=[['teaching','讲解演示'],['practice','跟教练跟练']];
  const videoLabel=type=>videoKinds.find(([key])=>key===type)?.[1]||type;
  const finishedVideoTypes=m=>new Set(m.final_types||[]);
  const hasMedia = m => Object.keys(m.media_counts||{}).some(k=>k.endsWith(':succeeded'));
  const isGenerating = m => Object.keys(m.media_counts||{}).some(k=>['queued','dispatching','submitted','running','download_pending','technical_check'].some(s=>k.endsWith(':'+s)));
  function moveStatus(m){
    if(videoKinds.every(([type])=>finishedVideoTypes(m).has(type)))return 'done';
    if(m.unresolved_revision)return 'revision';
    if(isGenerating(m))return 'generating';
    if(hasMedia(m))return 'qc';
    const facts=(m.effective_version||m.version)?.payload||{};
    if(!facts.chinese_action||!facts.english_action)return 'facts';
    if(!m.motion_locked)return 'motion';
    if(!m.package_ready)return 'ai';
    return 'generate';
  }
  const statusLabels={facts:'待完善动作标准',motion:'待上传真人动作',ai:'等待 AI 准备',generate:'待生成数字老师',generating:'生成中',qc:'待动作验收',revision:'需要返修',done:'已完成'};
  const statusFilters=[['all','全部'],['facts','待完善动作标准'],['motion','待真人动作'],['ai','待 AI 准备'],['generate','待生成'],['generating','生成中'],['qc','待验收'],['revision','返修'],['done','已完成']];
  function countStatus(items,key){return items.filter(m=>moveStatus(m)===key).length}
  function artMoves(id){return (M.overview?.moves||[]).filter(m=>m.martial_art_id===id)}
  function masterName(id){return master(id)?.name||'待绑定老师'}
  function reviewPanel(arts,moves,artId=null){
    if(!isManager())return '';
    const groups=arts.map(x=>({art:x,items:moves.filter(m=>m.martial_art_id===x.id&&m.version?.status==='ready_for_approval')})).filter(x=>x.items.length&&(!artId||x.art.id===artId));
    if(!groups.length)return panel('待确认武术事实','<p class="muted">目前没有员工已提交的招式。</p>');
    return panel('待确认武术事实',groups.map(({art:ar,items})=>{
      const eligible=items.filter(x=>x.version?.batch_eligible);
      return `<section class="mw-review-group"><div class="row between"><strong>${e(ar.chinese_name)} · 已提交 ${items.length} 招 · 可批量 ${eligible.length} 招</strong>${eligible.length?button('批量确认 '+eligible.length+' 招','batch-approve',ar.id,'outline'):''}</div>${items.map(m=>`<div class="mw-review-row"><span><b>${e(moveName(m))}</b> · ${e(m.version?.submitted_by_name||'员工')}已核对 · ${e(m.version?.submission_note||'已提交')} ${m.version?.review_flags?.length?`<small>疑问 / 冲突：${e(m.version.review_flags.join('；'))}</small>`:''}</span>${link('查看差异','martial:move:'+m.id)}${button('确认','approve-move',m.id)}${button('退回修改','reject-move',m.id)}</div>`).join('')}</section>`;
    }).join(''));
  }

  async function overview(){
    if(M.overview&&Date.now()-M.overviewAt<OVERVIEW_TTL)return M.overview;
    if(M.overviewPromise)return M.overviewPromise;
    const epoch=M.cacheEpoch;
    const pending=api('/api/martial/overview').then(data=>{if(epoch===M.cacheEpoch){M.overview=data;M.overviewAt=Date.now()}return data}).finally(()=>{if(M.overviewPromise===pending)M.overviewPromise=null});
    M.overviewPromise=pending;return pending;
  }
  async function cachedAssets(path){
    const entry=M.assetCache.get(path);
    if(entry&&Date.now()-entry.at<ASSET_TTL)return entry.data||entry.promise;
    const epoch=M.cacheEpoch;
    const promise=api(path).then(data=>{if(epoch===M.cacheEpoch)M.assetCache.set(path,{at:Date.now(),data});return data}).catch(error=>{if(M.assetCache.get(path)?.promise===promise)M.assetCache.delete(path);throw error});
    M.assetCache.set(path,{at:Date.now(),promise});return promise;
  }
  async function cachedWorkspace(){
    const entry=M.workspaceCache;if(entry&&Date.now()-entry.at<OVERVIEW_TTL)return entry.data||entry.promise;
    const epoch=M.cacheEpoch,promise=api('/api/workspace').then(data=>{if(epoch===M.cacheEpoch)M.workspaceCache={at:Date.now(),data};return data}).catch(error=>{if(M.workspaceCache?.promise===promise)M.workspaceCache=null;throw error});
    M.workspaceCache={at:Date.now(),promise};return promise;
  }
  function art(id){return M.overview.arts.find(x=>x.id===id)}
  function master(id){return M.overview.masters.find(x=>x.id===id)}
  function moveName(x){return (x.effective_version||x.version)?.payload?.chinese_name||`第 ${x.ordinal} 招（待登记）`}
  function artCard(x){
    const moves=artMoves(x.id),locked=moves.filter(m=>m.motion_locked).length,candidates=moves.filter(hasMedia).length;
    const finished=moves.reduce((count,move)=>count+videoKinds.filter(([type])=>finishedVideoTypes(move).has(type)).length,0);
    return `<article class="mw-art-card"><div class="row between"><span class="eyebrow">${e(x.category||'功法')}</span>${pill(x.status)}</div><h3>${e(x.chinese_name)}</h3><p>${e(x.english_name||'英文名称待登记')}</p><div class="mw-small-grid"><span>老师<strong>${e(masterName(x.master_id))}</strong></span><span>招式<strong>${moves.length}</strong></span><span>真人动作<strong>${locked} / ${moves.length}</strong></span><span>两类视频成片<strong>${finished} / ${moves.length*2}</strong></span><span>待验收<strong>${countStatus(moves,'qc')}</strong></span><span>返修<strong>${countStatus(moves,'revision')}</strong></span></div>${link('进入功法','martial:art:'+x.id)}</article>`;
  }
  const nextAction=x=>{const status=moveStatus(x),teacherId=art(x.martial_art_id)?.master_id;if(status==='ai'&&(!teacherId||!master(teacherId)?.production_ready))return '补充功法老师素材';return ({facts:'编辑动作标准',motion:'上传真人动作',ai:'让 AI 准备',generate:'生成数字老师',generating:'查看生成进度',qc:'开始验收',revision:'查看返修要求',done:'查看作品'})[status]||'继续'};
  function moveRow(x){return `<button class="mw-move-row" data-view="martial:move:${e(x.id)}"><b>${String(x.display_order||x.ordinal).padStart(2,'0')}</b><span><strong>${e(art(x.martial_art_id)?.chinese_name||'')} / ${e(x.business_label||moveName(x))}</strong><small>${e(statusLabels[moveStatus(x)])}</small></span><i>${e(nextAction(x))} →</i></button>`}
  function layout(title,kicker,html,actions=''){if(M.previewUrl){URL.revokeObjectURL(M.previewUrl);M.previewUrl=null}page(title,kicker);$('#top-actions').innerHTML=actions||button('刷新','refresh');$('#content').innerHTML=`<div class="mw-shell">${html}</div>`}
  function pendingAssetGrid(){const grid=document.querySelector('.mw-asset-grid');if(grid){grid.setAttribute('aria-busy','true');grid.innerHTML='<div class="mw-asset-pending" role="status">正在更新素材…</div>'}}

  async function listPage(kind){
    const version=state.routeVersion,assetRequest=kind==='assets'?++M.assetRequestVersion:null;
    if(kind==='assets'){
      let context={arts:[],masters:[],moves:[]};
      if(state.user.martial_specialist||isManager()){
        try{context=await overview()}catch{M.overview=null}
      }else M.overview=null;
      if(!routeIsCurrent(version)||assetRequest!==M.assetRequestVersion)return;
      return assetPage(context,assetRequest,version);
    }
    const d=await overview(),moves=d.moves||[];
    if(!routeIsCurrent(version))return;
    if(kind==='overview'||kind==='today'){
      const focus=moves.filter(m=>moveStatus(m)!=='done').sort((a,b)=>['revision','qc','generating','generate','ai','facts','motion'].indexOf(moveStatus(a))-['revision','qc','generating','generate','ai','facts','motion'].indexOf(moveStatus(b))).slice(0,6);
      const stats=[['待完善动作标准',countStatus(moves,'facts')],['待上传真人动作',countStatus(moves,'motion')],['待生成数字老师',countStatus(moves,'ai')+countStatus(moves,'generate')],['生成中',countStatus(moves,'generating')],['待动作验收',countStatus(moves,'qc')],['需要返修',countStatus(moves,'revision')],['已完成',countStatus(moves,'done')]];
      layout('首页','万象武境 / 今日工作',`<div class="mw-page-head"><div><span class="eyebrow">万象武境</span><h2>今天做什么</h2><p>选一项工作，打开后按页面提示继续。</p></div>${link('查看功法','martial:arts')}</div>${panel('下一步工作',focus.map(moveRow).join('')||'<p class="muted">当前没有待处理的招式。</p>')}<div class="mw-stats">${stats.map(([label,n])=>`<div><span>${label}</span><strong>${n}</strong></div>`).join('')}</div><div class="section-title"><h2>全部功法</h2>${link('查看全部','martial:arts')}</div><div class="mw-card-grid">${d.arts.map(artCard).join('')}</div>${reviewPanel(d.arts,moves)}`);return;
    }
    if(kind==='arts'){layout('功法管理','万象武境 / 功法管理',`<div class="mw-page-head"><div><h2>功法管理</h2><p>选择功法后查看全部招式和生产状态。</p></div></div><div class="mw-card-grid">${d.arts.map(artCard).join('')}</div>`,canEdit()?link('＋ 新增功法','martial:new-art'):'');return}
    if(kind==='masters'){
      layout('功法老师','万象武境 / 功法老师',`<div class="mw-page-head"><div><h2>功法老师</h2><p>角色主形象与人物资料在这里管理。</p></div></div><div class="mw-card-grid">${d.masters.map(m=>`<article class="mw-art-card mw-master-card"><div class="mw-master-thumb">${image(m.primary_visual_asset_id||m.portrait_asset_id||m.portrait)}</div><span class="eyebrow">${e(m.species||'老师')} · V${e(m.current_version||1)}</span><h3>${e(m.name)}</h3><p>${e(d.arts.filter(x=>x.master_id===m.id).map(x=>x.chinese_name).join('、')||'尚未绑定功法')}</p><div class="mw-small-grid"><span>生产状态<strong>${m.production_ready?'可用于生产':'待上传主形象'}</strong></span><span>声音<strong>${m.voice_id?'已登记':'待登记'}</strong></span></div>${link('查看老师','martial:master:'+m.id)}</article>`).join('')}</div>`,canEdit()?link('＋ 新增老师','martial:new-master'):'');return;
    }
    if(kind==='tasks'||kind==='revisions'||kind==='done'){
      const selected=kind==='tasks'?moves.filter(m=>moveStatus(m)!=='done'):kind==='revisions'?moves.filter(m=>moveStatus(m)==='revision'):moves.filter(m=>moveStatus(m)==='done');
      const title={tasks:'我的任务',revisions:'我的返修',done:'我的作品'}[kind];
      layout(title,'我的工作',`<div class="mw-page-head"><div><h2>${title}</h2><p>${kind==='tasks'?'按生产阶段查看需要继续的招式。':kind==='revisions'?'专业验收提出的问题在招式页处理。':'已定版的武术资产。'}</p></div></div>${selected.length?panel(title,selected.map(moveRow).join('')):'<div class="empty">当前没有相关招式。</div>'}`);return;
    }
    if(kind==='unmapped'&&isManager()){
      const pending=await api('/api/martial/unmapped-assets');
      layout('未归属素材','万象武境 / 技术记录',`<p class="mw-filter-note">待确定归属 ${e(pending.count)} 张。</p>${pending.assets.filter(x=>!x.ignored).map(x=>`<article class="mw-panel"><div class="mw-visual-grid"><div>${image(x.asset_id)}</div><div><p>${e(x.source_path)}</p>${d.masters.map(m=>button('归到 '+m.name,'assign-unmapped',x.id+':'+m.id)).join(' ')}${button('忽略','ignore-unmapped',x.id)}</div></div></article>`).join('')||'<div class="empty">没有待归属素材。</div>'}`);return;
    }
    return listPage('arts');
  }

  const ASSET_CATEGORIES=['全部','角色','真人动作','AI图片','AI视频','教学视频','演练视频','Voice','OS语音','BGM','音效','Prompt','文档','场景','道具','图片','视频','音频','其他'];
  const projectLabel=id=>({wuxiang:'万象武境',diaojianghu:'钓江湖',dingting:'助听器'}[id]||id||'未分类项目');
  const registryPreviewUrl=url=>url?.startsWith('/api/')?BASE+url:url||'';
  const unavailableLegacyMedia=x=>['legacy_ai_center','legacy_ai_job'].includes(x.source_system)&&['image','video','audio','character','motion_reference'].includes(x.type)&&!x.preview_url;
  const directlyReusable=x=>x.source_system==='work_os';
  const assetTypeLabel=x=>x.category||({image:'图片',video:'视频',audio:'音频',document:'文档',prompt:'Prompt',motion_reference:'真人动作',character:'角色'}[x.type]||'其他');
  const assetPreview=x=>{const url=registryPreviewUrl(x.preview_url),thumb=registryPreviewUrl(x.thumbnail_url),type=x.type||'';if(['image','character','scene','prop'].includes(type)||['character','scene','prop'].includes(x.subtype))return thumb||url?`<img loading="lazy" src="${e(thumb||url)}" alt="${e(x.name||'素材预览')}">`:'<span>图片</span>';if(['video','motion_reference','digital_teacher_video'].includes(type)||['video','motion_reference','digital_teacher_video'].includes(x.subtype))return thumb?`<img loading="lazy" src="${e(thumb)}" alt="${e(x.name||'视频封面')}">`:url?`<video muted playsinline preload="metadata" src="${e(url)}"></video>`:'<span>视频</span>';if(type==='audio')return '<span>♫ 音频</span>';if(type==='prompt')return '<span>✦ Prompt</span>';return '<span>▤ 文档</span>'};
  const assetCard=x=>`<article class="mw-asset-card" role="button" tabindex="0" aria-label="查看 ${e(x.name||'未命名素材')}" data-martial-action="asset-open" data-id="${e(x.asset_id)}"><div class="mw-asset-preview">${assetPreview(x)}${x.duration_ms?`<span class="mw-duration">${seconds(Number(x.duration_ms)/1000)}</span>`:''}</div><strong>${e(x.name||'未命名素材')}</strong><small>${e(assetTypeLabel(x))} · ${e(projectLabel(x.project_name||x.project||x.project_id))}</small><small>${e((x.created_at||'').slice(0,10))} ${x.status?' · '+e(words[x.status]||x.status):''}</small>${unavailableLegacyMedia(x)?'<small class="mw-asset-unavailable">仅登记来源，暂不可预览/用于生成</small>':''}</article>`;
  async function assetPage(d,request,version){
    const f=M.assetFilter,params=new URLSearchParams(Object.entries(f).filter(([,value])=>value!=null&&String(value).trim()));
    const path='/api/asset-center'+(params.size?'?'+params:'');
    let result;try{result=await cachedAssets(path)}catch(err){if(routeIsCurrent(version)&&request===M.assetRequestVersion)layout('素材中心','万象武境 / 素材中心',`<div class="mw-blocked">素材库暂不可用：${e(err.message)}</div>`);return}
    if(!routeIsCurrent(version)||request!==M.assetRequestVersion)return;
    const assets=result.assets||[],facets=result.facets||{};
    const workspace=await cachedWorkspace().catch(()=>({projects:[]}));
    if(!routeIsCurrent(version)||request!==M.assetRequestVersion)return;
    const projects=new Map((workspace.projects||[]).map(x=>[x.id,{id:x.id,name:x.name||projectLabel(x.id)}]));
    if(state.user.martial_specialist)projects.set('wuxiang',{id:'wuxiang',name:'万象武境'});
    M.assetProjects=[...projects.values()];
    const choices=(key)=>[...new Set([...assets.map(x=>x[key]).filter(Boolean),...(f[key]?[f[key]]:[])])].sort();
    const options=(items,key,label,name='name')=>`<option value="">${label}</option>${items.map(x=>{const id=typeof x==='string'?x:x.id||x.key||x.value;let title=typeof x==='string'?x:x[name]||x.chinese_name||x.label||id;if(key==='project_id')title={wuxiang:'万象武境',diaojianghu:'钓江湖',dingting:'助听器'}[id]||id;if(key==='move_id'&&typeof x==='object')title=x.business_label||moveName(x);if(key==='type')title=assetTypeLabel({type:id});if(key==='status')title=words[id]||id;if(key==='creator')title=id===state.user.id?'我':(typeof x==='object'?x.name||'其他成员':'其他成员');return `<option value="${e(id)}" ${f[key]===id?'selected':''}>${e(title)}</option>`}).join('')}`;
    const categories=`<div class="mw-asset-categories" role="tablist" aria-label="素材分类">${ASSET_CATEGORIES.map(label=>{const key=label==='全部'?'':label;return `<button type="button" role="tab" aria-selected="${f.category===key}" class="${f.category===key?'active':''}" data-martial-action="asset-category" data-id="${e(key)}">${label}</button>`}).join('')}</div>`;
    const cards=assets.map(assetCard).join('')||'<div class="empty">没有符合条件的素材。</div>';
    layout('素材中心','公司素材 / 数字资产库',`<div class="mw-page-head"><div><h2>素材中心</h2><p>图片、视频、声音和创作资料都在这里，可以直接用于下一项工作。</p></div><strong class="mw-asset-total">${assets.length} / ${result.total} 件</strong></div>${categories}<form class="mw-asset-filters mw-asset-search" data-martial-filter="assets"><label class="mw-search-wide">搜索<input name="q" value="${e(f.q)}" placeholder="名称、标签、项目、老师或招式"></label><label>项目<select name="project_id">${options(M.assetProjects, 'project_id','全部项目')}</select></label>${d.arts.length?`<label>功法<select name="art_id">${options(d.arts,'art_id','全部功法','chinese_name')}</select></label><label>老师<select name="master_id">${options(d.masters,'master_id','全部老师')}</select></label><label>招式<select name="move_id">${options(d.moves,'move_id','全部招式','business_label')}</select></label>`:''}<label>类型<select name="type">${options(choices('type'),'type','全部类型')}</select></label><label>用途<select name="usage">${options(choices('usage'),'usage','全部用途')}</select></label><label>状态<select name="status">${options(choices('status'),'status','全部状态')}</select></label><label>创建人<select name="creator">${options(choices('creator'),'creator','全部创建人')}</select></label>${isManager()?`<label>AI 模型<select name="model">${options(choices('model'),'model','全部模型')}</select></label><label>生成平台<select name="provider">${options(choices('provider'),'provider','全部平台')}</select></label>`:''}<label>标签<input name="tags" value="${e(f.tags)}" placeholder="标签"></label><label>开始日期<input name="date_from" type="date" value="${e(f.date_from)}"></label><label>结束日期<input name="date_to" type="date" value="${e(f.date_to)}"></label><button class="outline" type="submit">查找素材</button></form><div class="mw-asset-grid">${cards}</div><div class="mw-asset-more">${result.next_offset!=null?button('加载更多素材','asset-next',result.next_offset):''}</div><dialog class="mw-dialog" id="mw-asset-dialog"></dialog>`);
  }
  async function openAsset(id){
    const [response,managerData]=await Promise.all([
      api('/api/asset-center/'+encodeURIComponent(id)),
      isManager()?api('/api/manager').catch(()=>null):Promise.resolve(null)
    ]);
    const x=response.asset||response;M.assetDetail=x;
    const dialog=document.querySelector('#mw-asset-dialog');if(!dialog)return;
    const url=registryPreviewUrl(x.preview_url),type=x.type||'',preview=['video','motion_reference','digital_teacher_video'].includes(type)||['video','motion_reference','digital_teacher_video'].includes(x.subtype)?url?`<video controls preload="metadata" src="${e(url)}"></video>`:'<p>当前素材没有可播放预览。</p>':type==='audio'?url?`<audio controls src="${e(url)}"></audio>`:'<p>暂无音频试听。</p>':['image','character','scene','prop'].includes(type)?assetPreview(x):`<div class="mw-document-preview"><strong>${e(x.name||'资料')}</strong><p>${e(x.text||x.summary||x.description||x.prompt||'可在项目中查看原始资料。')}</p></div>`;
    const links=[['关联任务',x.task_name||x.task_ref],['关联角色',x.master_name||x.character_name],['关联功法',x.art_name],['关联招式',x.move_name]];
    const unavailable=unavailableLegacyMedia(x),canReference=directlyReusable(x);
    const sourceLink=x.source_url?`<a class="outline mw-download" href="${e(x.source_url)}" target="_blank" rel="noopener">去原 AI 后台查看（需登录）</a>`:'';
    const oldOwner=x.metadata?.visibility==='private'&&x.source_system.startsWith('legacy_ai')?x.metadata?.owner_username:null;
    const identityMap=oldOwner&&isManager()?`<details class="mw-collapsible"><summary>旧账号权限映射（管理员）</summary><p class="muted">核对旧 AI 中心账号归属后，再指定可在本项目查看此账号私有素材的员工；员工还需登录旧 AI 中心。</p><form class="mw-mm-link-form" data-martial-form="legacy-identity"><input type="hidden" name="legacy_username" value="${e(oldOwner)}"><input type="hidden" name="project_id" value="${e(x.project_id||'')}"><label>Work OS 员工<select name="work_user_id" required><option value="">请选择已核对员工</option>${(managerData?.users||[]).filter(u=>u.role==='employee'&&u.active).map(u=>`<option value="${e(u.id)}">${e(u.display_name||u.username)}</option>`).join('')}</select></label><button class="outline" type="submit">确认账号映射</button></form></details>`:'';
    const reuseActions=[...(!x.source_system.startsWith('legacy_ai')?[['new_task','用于新任务']]:[]),...(x.type==='prompt'||canReference&&['document','text','analysis','concept','script'].includes(x.type)?[['ai_creation','用于 AI 创作']]:[]),...(canReference&&canEdit()&&['image','character'].includes(x.type)?[['character_reference','设为角色参考']]:[]),...(canReference&&canEdit()&&['video','motion_reference'].includes(x.type)?[['video_reference','设为视频参考']]:[]),['save_project','保存到项目']];
    dialog.innerHTML=`<div class="row between"><div><small>${e(assetTypeLabel(x))}</small><h3>${e(x.name||'未命名素材')}</h3></div>${button('关闭','asset-close')}</div><div class="mw-asset-detail"><div class="mw-asset-detail-preview">${preview}</div>${unavailable?'<p class="mw-filter-note">仅登记来源，暂不可预览/用于生成。可保存到项目，或去原 AI 后台查看。</p>':x.source_system.startsWith('legacy_ai')&&x.preview_url?'<p class="mw-filter-note">可预览旧素材；当前尚不能直接作为生成参考。</p>':''}<div class="mw-definition-grid">${[['项目',projectLabel(x.project_name||x.project||x.project_id)],['用途',x.usage],['状态',words[x.status]||x.status],['来源',({legacy_ai_center:'旧 AI 素材库',legacy_ai_job:'旧 AI 生成记录',legacy_ai_prompt:'旧 AI 提示词',work_os:'当前工作台',work_os_ai_studio:'AI 创作'}[x.source_system]||x.source_system)],['创建时间',x.created_at?.slice(0,19)],['版本',x.version],...links].filter(([,value])=>value).map(([label,value])=>`<div><b>${label}</b><span>${e(value)}</span></div>`).join('')}</div>${(x.tags||[]).length?`<p>标签：${(x.tags||[]).map(e).join('、')}</p>`:''}<div class="mw-asset-targets"><label>用于项目<select id="mw-asset-project">${M.assetProjects.map(project=>`<option value="${e(project.id)}" ${project.id===(x.project_id||x.project)?'selected':''}>${e(project.name)}</option>`).join('')}</select></label>${M.overview?.masters?.length?`<label>关联老师<select id="mw-asset-master"><option value="">选择老师</option>${M.overview.masters.map(m=>`<option value="${e(m.id)}" ${m.id===x.master_id?'selected':''}>${e(m.name)}</option>`).join('')}</select></label>`:''}${M.overview?.moves?.length?`<label>关联招式<select id="mw-asset-move"><option value="">选择招式</option>${M.overview.moves.map(m=>`<option value="${e(m.id)}" ${m.id===x.move_id?'selected':''}>${e(m.business_label||moveName(m))}</option>`).join('')}</select></label>`:''}</div><div class="mw-reuse-actions">${reuseActions.map(([action,label])=>button(label,'asset-reuse',action)).join('')}${url?`<a class="outline mw-download" href="${e(registryPreviewUrl(x.download_url)||url)}" download>下载</a>`:''}${sourceLink}</div>${isManager()?`<details><summary>高级信息</summary><div class="mw-definition-grid">${[['来源编号',x.original_id],['素材编号',x.asset_id],['文件指向',x.file_ref],['生成平台',x.provider],['模型',x.model],['生成提示词',x.prompt],['提示词来源',x.prompt_ref],['任务来源',x.task_ref]].filter(([,value])=>value).map(([label,value])=>`<div><b>${label}</b><span>${e(value)}</span></div>`).join('')}</div></details>`:''}</div>`;
    if(identityMap)dialog.querySelector('.mw-asset-detail')?.insertAdjacentHTML('beforeend',identityMap);
    dialog.showModal();
  }

  async function newArtPage(){
    const version=state.routeVersion,d=await overview();if(!routeIsCurrent(version))return;if(!canEdit())throw new Error('当前账号不能新增功法');
    M.artWizard={step:1,mode:'single',createdArtId:null,createdMasterId:null};
    const row=()=>`<tr class="mw-batch-row"><td><input name="order" type="number" min="1" placeholder="序号" aria-label="招式序号"></td><td><input name="chinese_name" placeholder="中文名" aria-label="招式中文名"></td><td><input name="english_name" placeholder="英文名" aria-label="招式英文名"></td><td><textarea name="chinese_action" placeholder="中文动作" aria-label="中文动作"></textarea></td><td><textarea name="english_action" placeholder="英文动作" aria-label="英文动作"></textarea></td><td><textarea name="chinese_coaching" placeholder="教学提示" aria-label="教学提示"></textarea></td><td>${button('移除','wizard-remove-row')}</td></tr>`;
    layout('新增功法','万象武境 / 功法管理 / 新增',`<form class="mw-form mw-panel mw-editor mw-wizard" data-martial-form="create-art"><div class="mw-wizard-steps">${['基本信息','选择老师','添加招式','确认创建'].map((x,i)=>`<span data-wizard-indicator="${i+1}" class="${i===0?'active':''}">${i+1} ${x}</span>`).join('')}</div><section data-wizard-step="1"><h3>功法基本信息</h3><div class="form-grid"><label>中文名称<input name="chinese_name" required></label>${field('英文名称','english_name')}<label>功法类型<input name="category" required placeholder="例如：掌法"></label>${field('卷 / 分类位置','volume','第一卷')}${field('简介','description','',true)}${field('风格特点（每行一项）','style_traits','',true)}<label>排序<input name="order" type="number" min="0" value="${d.arts.length+1}"></label></div></section><section data-wizard-step="2" hidden><h3>选择功法老师</h3><label>老师<select name="master_id"><option value="">暂不绑定</option>${d.masters.map(m=>`<option value="${e(m.id)}">${e(m.name)}</option>`).join('')}<option value="__new__">＋ 新增老师</option></select></label><div class="mw-wizard-new-master" hidden><div class="form-grid"><label>老师中文名<input name="teacher_chinese_name"></label><label>英文名<input name="teacher_english_name"></label><label>角色类型 / 物种<input name="teacher_species"></label><label>主形象<input type="file" name="teacher_portrait" accept=".png,.jpg,.jpeg,.webp"></label><label>其他角度图片<input type="file" name="teacher_other_angles" accept=".png,.jpg,.jpeg,.webp" multiple></label><label>声音样本<input type="file" name="teacher_voice_preview" accept=".mp3,.wav"></label>${field('人物资料','teacher_profile','',true)}${field('教学风格','teacher_teaching_style','',true)}</div></div><p class="muted">新老师创建后会自动绑定这套新功法；图片和声音可之后继续补充。</p></section><section data-wizard-step="3" hidden><h3>添加招式</h3><div class="mw-choice-row">${[['single','单条新增'],['batch','批量新增'],['import','Excel / CSV 导入'],['copy','从现有功法复制']].map(([key,label])=>`<label><input type="radio" name="move_mode" value="${key}" ${key==='single'?'checked':''}> ${label}</label>`).join('')}</div><div data-move-mode="single"><div class="form-grid">${field('中文名','single_chinese_name')}${field('英文名','single_english_name')}${field('中文动作','single_chinese_action','',true)}${field('英文动作','single_english_action','',true)}${field('中文教学提示','single_chinese_coaching','',true)}${field('英文教学提示','single_english_coaching','',true)}</div></div><div data-move-mode="batch" hidden><div class="mw-table-scroll"><table class="mw-table mw-batch-table"><thead><tr><th>序号</th><th>中文名</th><th>英文名</th><th>中文动作</th><th>英文动作</th><th>教学提示</th><th></th></tr></thead><tbody>${row()}${row()}</tbody></table></div>${button('＋ 增加一行','wizard-add-row')}</div><div data-move-mode="import" hidden><label>招式文件（CSV / XLSX）<input type="file" name="moves_file" accept=".csv,.xlsx"></label><p class="muted">请包含“序号、中文名、英文名、中文动作、英文动作、教学提示”列。</p></div><div data-move-mode="copy" hidden><label>来源功法<select name="source_art_id"><option value="">选择已有功法</option>${d.arts.map(ar=>`<option value="${e(ar.id)}">${e(ar.chinese_name)}</option>`).join('')}</select></label><p class="muted">复制后可在新功法中逐条修改，不影响来源功法。</p></div></section><section data-wizard-step="4" hidden><h3>确认创建</h3><div class="mw-wizard-summary"></div><p class="muted">确认后直接进入新功法。新招式将一并添加，不需要重新初始化。</p></section><div class="mw-wizard-actions">${button('上一步','wizard-back')} ${button('下一步','wizard-next','','primary')}<button class="primary" type="submit" id="mw-wizard-submit" hidden>创建功法并进入</button></div></form>`,link('返回功法管理','martial:arts'));
    updateWizard();
  }
  function updateWizard(){const root=document.querySelector('.mw-wizard');if(!root)return;const step=M.artWizard.step;root.querySelectorAll('[data-wizard-step]').forEach(x=>x.hidden=Number(x.dataset.wizardStep)!==step);root.querySelectorAll('[data-wizard-indicator]').forEach(x=>x.classList.toggle('active',Number(x.dataset.wizardIndicator)===step));root.querySelector('[data-martial-action="wizard-back"]').hidden=step===1;root.querySelector('[data-martial-action="wizard-next"]').hidden=step===4;root.querySelector('#mw-wizard-submit').hidden=step!==4;if(step===4){const d=fields(root),mode=d.move_mode,basic=root.querySelector('[data-wizard-step="1"]'),artName=basic.querySelector('[name="chinese_name"]').value,category=basic.querySelector('[name="category"]').value;root.querySelector('.mw-wizard-summary').innerHTML=`<p><strong>${e(artName)}</strong> · ${e(category)}</p><p>老师：${e(d.master_id==='__new__'?d.teacher_chinese_name||'新老师':M.overview.masters.find(x=>x.id===d.master_id)?.name||'暂不绑定')}</p><p>招式添加：${e({single:'单条新增',batch:'批量新增',import:'文件导入',copy:'从已有功法复制'}[mode])}${mode==='batch'?' · '+root.querySelectorAll('.mw-batch-row').length+' 条':''}</p>`}}
  function wizardMode(){const root=document.querySelector('.mw-wizard')||document.querySelector('[data-martial-form="add-moves"]');if(!root)return;const mode=root.elements.move_mode.value;root.querySelectorAll('[data-move-mode]').forEach(x=>x.hidden=x.dataset.moveMode!==mode);const teacher=root.querySelector('.mw-wizard-new-master');if(teacher)teacher.hidden=root.elements.master_id.value!=='__new__'}

  async function newMovePage(artId){
    const version=state.routeVersion,d=await overview();if(!routeIsCurrent(version))return;const ar=d.arts.find(x=>x.id===artId);if(!ar)throw new Error('功法不存在');if(!canEdit())throw new Error('当前账号不能新增招式');
    const row=`<tr class="mw-batch-row"><td><input name="order" type="number" min="1" placeholder="序号" aria-label="招式序号"></td><td><input name="chinese_name" placeholder="中文名" aria-label="招式中文名"></td><td><input name="english_name" placeholder="英文名" aria-label="招式英文名"></td><td><textarea name="chinese_action" placeholder="中文动作" aria-label="中文动作"></textarea></td><td><textarea name="english_action" placeholder="英文动作" aria-label="英文动作"></textarea></td><td><textarea name="chinese_coaching" placeholder="教学提示" aria-label="教学提示"></textarea></td><td>${button('移除','wizard-remove-row')}</td></tr>`;
    layout('添加招式',`万象武境 / ${ar.chinese_name} / 添加招式`, `<form class="mw-form mw-panel mw-editor" data-martial-form="add-moves" data-id="${e(artId)}"><h3>${e(ar.chinese_name)} · 添加招式</h3><div class="mw-choice-row">${[['single','单条新增'],['batch','批量新增'],['import','Excel / CSV 导入'],['copy','从现有功法复制']].map(([key,label])=>`<label><input type="radio" name="move_mode" value="${key}" ${key==='single'?'checked':''}> ${label}</label>`).join('')}</div><div data-move-mode="single"><div class="form-grid"><label>序号<input name="single_order" type="number" min="1" value="${artMoves(artId).length+1}"></label>${field('中文名','single_chinese_name')}${field('英文名','single_english_name')}${field('中文动作','single_chinese_action','',true)}${field('英文动作','single_english_action','',true)}${field('中文教学提示','single_chinese_coaching','',true)}${field('英文教学提示','single_english_coaching','',true)}</div></div><div data-move-mode="batch" hidden><div class="mw-table-scroll"><table class="mw-table mw-batch-table"><thead><tr><th>序号</th><th>中文名</th><th>英文名</th><th>中文动作</th><th>英文动作</th><th>教学提示</th><th></th></tr></thead><tbody>${row}${row}</tbody></table></div>${button('＋ 增加一行','wizard-add-row')}</div><div data-move-mode="import" hidden><label>招式文件（CSV / XLSX）<input type="file" name="moves_file" accept=".csv,.xlsx"></label></div><div data-move-mode="copy" hidden><label>来源功法<select name="source_art_id"><option value="">选择已有功法</option>${d.arts.filter(x=>x.id!==artId).map(x=>`<option value="${e(x.id)}">${e(x.chinese_name)}</option>`).join('')}</select></label></div><button class="primary" type="submit">添加招式</button></form>`,link('返回功法','martial:art:'+artId));
  }

  async function newMasterPage(){
    const version=state.routeVersion,d=await overview();if(!routeIsCurrent(version))return;if(!canEdit())throw new Error('当前账号不能新增老师');
    const fileField=(label,name,multiple=false)=>`<label>${e(label)}<input type="file" name="${e(name)}" accept=".png,.jpg,.jpeg,.webp" ${multiple?'multiple':''}></label>`;
    layout('新增老师','万象武境 / 功法老师 / 新增',`<form class="mw-form mw-panel mw-editor" data-martial-form="create-master"><h3>基础信息</h3><div class="form-grid">${field('中文名','chinese_name')}${field('英文名','english_name')}${field('物种','species')}<label>对应功法<select name="martial_art_ids" multiple size="${Math.min(6,Math.max(2,d.arts.length))}">${d.arts.map(x=>`<option value="${e(x.id)}">${e(x.chinese_name)}</option>`).join('')}</select><small>可多选；保存后可以调整功法绑定。</small></label></div><h3>视觉素材</h3><p class="muted">上传至少一张有效主形象即可参与生产，其余视角可以后补。</p><div class="form-grid">${fileField('主形象','portrait')}${fileField('正面','front_view')}${fileField('侧面','side_view')}${fileField('背面','back_view')}${fileField('其他角度','other_angle',true)}${fileField('三视图 / 转身参考','turnaround')}${fileField('服装','costume')}<label>数字人模型（GLB）<input type="file" name="digital_model" accept=".glb"></label></div><h3>声音与人物</h3><div class="form-grid">${field('声音编号（已有时填写）','voice_id')}<label>声音样本<input type="file" name="voice_preview" accept=".wav,.mp3"></label>${field('人物画像','profile','',true)}${field('生平','biography','',true)}${field('性格','personality','',true)}${field('世界观身份','world_identity','',true)}${field('教学风格','teaching_style','',true)}${field('说话方式','speaking_style','',true)}${field('禁止变化项（每行一项）','forbidden_changes','',true)}</div><button class="primary">保存老师</button></form>`,link('返回老师库','martial:masters'));
  }

  async function artPage(id){
    const version=state.routeVersion;
    if(M.lastArt!==id){M.filter='all';M.lastArt=id}
    const [d,detail,mm,available,lessonDashboard]=await Promise.all([overview(),api('/api/martial/arts/'+id),
      api('/api/martial/arts/'+id+'/multimodal').catch(()=>null),cachedAssets('/api/martial/available-assets').catch(()=>({assets:[]})),
      api('/api/martial/lesson-dashboard/'+id)]);
    if(!routeIsCurrent(version))return;
    const x=detail.art;if(!x)throw new Error('功法不存在');
    const items=d.moves.filter(m=>m.martial_art_id===id).slice().sort((left,right)=>(left.display_order||left.ordinal)-(right.display_order||right.ordinal)),draft=detail.versions?.[0],p=x.draft_version&&draft?.payload?draft.payload:x;
    const status=m=>moveStatus(m),qc=m=>m.qc_failed>0?'需要返修':m.final_count>0?'PASS':hasMedia(m)?'待验收':'—';
    const rows=items.map(m=>`<tr data-mw-status="${status(m)}"><td>${String(m.display_order||m.ordinal).padStart(2,'0')}</td><td><strong>${e(m.business_label||moveName(m))}</strong><small>${e((m.effective_version||m.version)?.payload?.english_name||'')}</small></td><td>${m.motion_locked?'✓ 已上传':'○ 未上传'}</td><td>${hasMedia(m)?'✓ 已生成':isGenerating(m)?'生成中':'○ 未生成'}</td><td>${e(qc(m))}</td><td>${pill(statusLabels[status(m)])}</td><td>${link(status(m)==='motion'?'开始':'继续','martial:move:'+m.id)}</td></tr>`).join('');
    const edit=canEdit()?`<details id="mw-art-editor" class="mw-panel mw-collapsible"><summary>编辑功法 / 绑定老师</summary><form class="mw-form" data-martial-form="art" data-id="${e(id)}"><div class="form-grid">${field('中文名称','chinese_name',p.chinese_name)}${field('英文名称','english_name',p.english_name)}${field('卷','volume',p.volume)}${field('功法类型','category',p.category)}<label>功法老师<select name="master_id">${d.masters.map(m=>`<option value="${e(m.id)}" ${m.id===p.master_id?'selected':''}>${e(m.name)}</option>`).join('')}</select></label>${field('规划招式数','planned_moves',p.planned_moves??'')}${field('特点（每行一项）','style_traits',(p.style_traits||[]).join('\n'),true)}${field('简介','description',p.description||'',true)}${field('资料来源','source_ref',draft?.source_ref||x.source_ref||'员工维护')}</div><p class="muted">修改已有定版功法事实需负责人确认，当前有效版本保持不变。</p><button class="primary">保存修订</button></form></details>`:'';
    layout(x.chinese_name,`万象武境 / 功法管理 / ${x.chinese_name}`,`<div class="mw-page-head"><div><span class="eyebrow">${e(x.english_name||'')}</span><h2>${e(x.chinese_name)}</h2><p>${e(x.description||'简介待补充')}</p><div class="mw-inline-facts"><span>老师 <strong>${e(masterName(x.master_id))}</strong></span><span>招式 <strong>${items.length}</strong></span><span>特点 <strong>${e((x.style_traits||[]).join(' · ')||'待登记')}</strong></span></div></div><div class="mw-header-actions">${canEdit()?button('编辑功法','edit-art',id):''}${canEdit()?button('绑定 / 更换老师','edit-teacher',id):''}</div></div>${lessonDashboardPanel(lessonDashboard)}${artAssetsPanel(mm,id,available)}<div class="mw-panel"><div class="row between"><h3>招式</h3><span class="muted">共 ${items.length} 招</span></div><div class="mw-filter-chips">${statusFilters.map(([key,label])=>`<button type="button" class="${M.filter===key?'active':''}" data-martial-filter-status="${key}">${label}${key==='all'?' '+items.length:countStatus(items,key)?' '+countStatus(items,key):''}</button>`).join('')}</div><div class="mw-table-scroll"><table class="mw-table"><thead><tr><th>#</th><th>招式</th><th>真人动作</th><th>数字老师</th><th>验收</th><th>状态</th><th>操作</th></tr></thead><tbody>${rows||'<tr><td colspan="7">尚无招式。点击“新增招式”开始。</td></tr>'}</tbody></table></div></div>${edit}${x.draft_version&&isManager()?button('确认功法修订 V'+x.draft_version,'approve-art',id,'primary'):''}${isManager()?reviewPanel(d.arts,items,id):''}`,`${canEdit()?link('＋ 新增招式','martial:new-move:'+id):''}`);
    filterArtRows();
  }
  const lessonStatus={planning:'策划中',motion_waiting:'待真人动作',teacher_waiting:'待数字老师',art_waiting:'待教学背景',motion_processing:'动作复刻中',motion_review:'待动作验收',motion_revision:'动作返修',voice_waiting:'待教学语音',qa:'待成片验收',composition:'待合成检查',ready_for_approval:'待负责人批准',approved:'已批准',published:'已发布'};
  function lessonDashboardPanel(d){
    const keys=['teacher','motion_source','motion_review','background','instruction_voice','narrative_voice','bgm','composition','video','audiovisual_review'];
    const counts=`<div class="mw-lesson-counts">${keys.map(k=>`<span>${e(d.labels[k])}<strong>${d.counts[k]||0}/${d.total}</strong></span>`).join('')}</div>`;
    const rows=d.lessons.map(item=>`<button class="mw-lesson-row" data-view="martial:lesson:${e(item.move_id)}"><strong>${e(item.name)}</strong><span>${e(lessonStatus[item.status]||item.status)}</span><small>${item.missing.length?`待补：${e(item.missing.slice(0,3).map(x=>x.label+' · '+x.owner).join('；'))}`:'关键素材已齐'}</small></button>`).join('');
    return panel('功法教学生产看板',`<p class="muted">每一式是独立教学包；动作、美术与声音可并行制作。</p>${counts}<div class="mw-lesson-list">${rows||'<p>暂无招式</p>'}</div>`);
  }
  async function lessonPage(moveId){
    const version=state.routeVersion;
    const [d,catalog]=await Promise.all([api('/api/martial/lessons/'+moveId),api('/api/asset-center?project_id=wuxiang&limit=2000').catch(()=>({assets:[]}))]);
    if(!routeIsCurrent(version))return;
    const dep=d.dependencies,active=Object.fromEntries(d.bindings.filter(b=>b.status==='active'&&!b.shot_id).map(b=>[b.role,b]));
    M.lessonRoles=d.asset_roles;M.lessonShots=d.shots;
    const groups=[['动作与老师',['facts','teacher','motion_source','motion_mapping','motion_review','pilot_motion_ref']],['场景与教学',['shot_plan','scene','background','teacher_model','instruction_script','instruction_voice']],['声音与成片',['narrative_voice','bgm','sfx','pilot_audio','pilot_subtitle','pilot_video','composition','video','audiovisual_review']]];
    const cards=groups.map(([title,keys])=>panel(title,`<div class="mw-lesson-deps">${keys.map(key=>{const item=dep.stages.find(s=>s.key===key),binding=active[key],asset=key==='motion_source'?dep.motion_reference:key==='video'?dep.final_video:null;return `<div class="mw-lesson-dep ${item.ready?'ready':'missing'}"><div><strong>${item.ready?'✓':'○'} ${e(item.label)}</strong><small>${e(item.owner)} · ${item.ready?'已具备':'待补齐'}</small></div><span>${binding?`资产 V${binding.version} · ${e(binding.name)}`:asset?`版本 ${e(asset.version||asset.id)}`:''}</span></div>`}).join('')}</div>`)).join('');
    const roles=Object.keys(d.asset_roles),assets=(catalog.assets||[]).filter(a=>a.source_system==='work_os');
    const shotPanel=panel('教学镜头',`<p class="muted">每镜记录目的、起止动作状态、机位与时长。素材可关联整式，也可指定镜头。</p><div class="mw-lesson-list">${d.shots.map(s=>`<div class="mw-lesson-row"><strong>#${e(s.ordinal)} · V${e(s.version)}</strong><span>${e(s.duration)} 秒 · ${e(s.camera)}</span><small>${e(s.purpose)}<br>起：${e(s.start_state)}<br>止：${e(s.end_state)}<br>素材：${d.bindings.filter(b=>b.status==='active'&&b.shot_id===s.shot_id).map(b=>`${e(b.role)} V${e(b.version)} · ${e(b.name)}`).join('；')||'尚未指定'}${canEdit()?`<br>${button('修改此镜','lesson-edit-shot',s.shot_id)}`:''}</small></div>`).join('')||'<p>尚无镜头设计</p>'}</div>${canEdit()?`<div class="mw-form mw-panel"><input id="mw-shot-id" type="hidden"><div class="form-grid"><label>镜号<input id="mw-shot-ordinal" type="number" min="1" value="${d.shots.length+1}"></label><label>时长（秒）<input id="mw-shot-duration" type="number" min="0.1" max="120" step="0.1"></label><label>镜头目的<input id="mw-shot-purpose"></label><label>机位与运动<input id="mw-shot-camera"></label><label>起始动作状态<input id="mw-shot-start"></label><label>结束动作状态<input id="mw-shot-end"></label></div>${button('保存镜头','lesson-save-shot',moveId,'primary')}</div>`:''}`);
    const bind=canEdit()?panel('关联制作素材',`<p class="muted">从公司 Asset Center 选择已归档素材，或上传新素材到公司云端 AI Production Assets；每次关联生成新版本。</p><div class="mw-lesson-bind"><label>用途<select id="mw-lesson-role">${roles.map(role=>`<option value="${e(role)}">${e(({scene:'场景',background:'教学背景',teacher_model:'数字人模型',instruction_voice:'老师教学语音',narrative_voice:'OS 画外音',bgm:'BGM',sfx:'音效',subtitle:'字幕',camera:'镜头设计',prompt:'Prompt',workflow:'Workflow',lesson_output:'完整教学成片',pilot_motion_ref:'历史参考片段',pilot_audio:'历史语音',pilot_subtitle:'历史字幕',pilot_video:'历史样片（不能作为正式成片）'})[role])}</option>`).join('')}</select></label><label>范围<select id="mw-lesson-shot"><option value="">整式通用</option>${d.shots.map(s=>`<option value="${e(s.shot_id)}">镜 #${e(s.ordinal)} · ${e(s.purpose)}</option>`).join('')}</select></label><label>素材<select id="mw-lesson-asset"><option value="">选择资产</option>${assets.map(x=>`<option value="${e(x.asset_id)}" data-type="${e(x.type)}">${e(x.name)} · ${e(x.type)} · V${e(x.version)}</option>`).join('')}</select></label>${button('关联并记录版本','lesson-bind',moveId,'primary')}</div><div class="mw-lesson-upload"><label>上传新素材（图片、音频、视频或文档）<input type="file" id="mw-lesson-file" accept=".png,.jpg,.jpeg,.webp,.wav,.mp3,.mp4,.mov,.glb,.md,.txt,.json,.srt"></label>${button('上传并关联','lesson-upload',moveId)}</div>`):'';
    const previous=d.bindings.filter(x=>x.status==='superseded').map(x=>`<li>${e(x.role)}${x.shot_id?' · '+e(x.shot_id):''} V${x.version} · ${e(x.name)} · ${e(x.sha256.slice(0,12))}</li>`).join('');
    const controls=`<div class="mw-lesson-actions">${link('进入本式视频制作与动作 QC','martial:move:'+moveId)}${isManager()?button('批准教学包','lesson-approve',moveId,'outline'):''}${isManager()?button('发布教学包','lesson-publish',moveId,'outline'):''}</div>`;
    const review=isManager()&&dep.final_video&&dep.final_video.martial_qc_result==='pass'?panel('成片人工视听验收',`<p class="muted">动作专业 QC 已由武术岗位完成。请连续观看教学成片并实际听审，记录覆盖范围和问题。</p><label>验收记录<textarea id="mw-lesson-review-notes" placeholder="例如：连续观看全片，听审 00:00–00:20，动作与真人参考逐段对照……"></textarea></label><label>证据 / 审片记录路径<input id="mw-lesson-review-evidence" placeholder="请输入可追溯的审片记录路径或资产 ID"></label><div class="mw-lesson-actions">${button('通过视听验收','lesson-review-pass',moveId,'primary')}${button('退回返修','lesson-review-fail',moveId)}</div>`):'';
    const pilot=active.pilot_video?`<div class="mw-panel"><h3>历史样片</h3><p class="muted">供问题复盘；不能代替真人标准动作、动作专业验收或正式成片。</p><video controls preload="metadata" src="/api/asset-center/${e(active.pilot_video.registry_asset_id)}/media"></video></div>`:'';
    const compositionPanel=panel('合成成片',`<p class="muted">先在本式视频制作页完成动作专业验收，再把含老师、背景、教学语音的完整 MP4 关联为“完整教学成片”。锁定时系统记录所有输入版本；更换素材后需重新合成和审片。</p><p>动作来源：${dep.source_final?`已定版 ${e(dep.source_final.id)}`:'待专业 QC 与定版'} · 完整成片：${active.lesson_output?`V${e(active.lesson_output.version)} · ${e(active.lesson_output.name)}`:'待上传'}</p>${active.lesson_output?`<video controls preload="metadata" src="/api/asset-center/${e(active.lesson_output.registry_asset_id)}/media"></video>`:''}${dep.composition?`<p>合成 V${e(dep.composition.version)} · 输入校验 ${e(dep.composition.inputs_sha256.slice(0,16))} · ${dep.checks.composition?'当前有效':'素材已变化，需重新锁定'}</p>`:''}${canEdit()&&active.lesson_output?`<label>合成说明与证据<textarea id="mw-composition-notes" placeholder="说明成片使用的画面、背景、教学语音，以及剪辑文件或核对记录"></textarea></label>${button('锁定合成输入','lesson-composition',moveId,'primary')}`:''}`);
    const rulePanel=panel('正式生产规则',d.production_rules.length?`<ul>${d.production_rules.map(r=>`<li>${e(r.id)} V${e(r.version)} · ${e(r.rule)}</li>`).join('')}</ul>`:'<p class="muted">暂无经 Learning Review 验证并纳入的规则；实验结论不会自动进入正式教学。</p>');
    layout(d.move_name,`万象武境 / ${d.art_name} / ${d.move_name} / 教学包`,`<div class="mw-page-head"><div><span class="eyebrow">MartialArtsLessonPackage · V${d.lesson.current_version||0}</span><h2>${e(d.move_name)}</h2><p>${e(lessonStatus[dep.status]||dep.status)} · ${dep.missing.filter(x=>x.required_for_publish).length} 项正式发布条件待补</p></div></div><div class="mw-lesson-grid">${cards}</div>${pilot}${shotPanel}${rulePanel}${bind}${compositionPanel}${review}${panel('并行工作与交付',`<p>当前可推进：${dep.ready_work.length?dep.ready_work.map(k=>e(dep.stages.find(x=>x.key===k)?.label||k)).join('、'):'请查看缺项和现有招式生产页'}</p><p>OS、BGM 与音效各自保留节点；试片可用占位，正式批准需完成必需项目。</p>${controls}`)}<details class="mw-panel mw-collapsible"><summary>版本历史与校验和</summary><ul>${previous||'<li>暂无被取代的版本</li>'}</ul>${d.lesson.approved_sha256?`<p>已批准 Manifest SHA-256：${e(d.lesson.approved_sha256)}</p>`:''}</details>`,link('返回功法','martial:art:'+d.lesson.art_id));
    filterLessonAssets();
  }
  function filterLessonAssets(){
    const role=$('#mw-lesson-role')?.value,select=$('#mw-lesson-asset');if(!role||!select)return;
    const allowed=M.lessonRoles?.[role]||[];
    for(const option of select.options){if(!option.value)continue;option.disabled=option.hidden=!allowed.includes(option.dataset.type)}
    if(select.selectedOptions[0]?.disabled)select.value='';
  }
  function filterArtRows(){
    document.querySelectorAll('[data-mw-status]').forEach(row=>row.hidden=M.filter!=='all'&&row.dataset.mwStatus!==M.filter);
    document.querySelectorAll('[data-martial-filter-status]').forEach(button=>button.classList.toggle('active',button.dataset.martialFilterStatus===M.filter));
  }

  async function masterPage(id){
    const version=state.routeVersion;
    const [d,mm,available]=await Promise.all([api('/api/martial/masters/'+id),
      api('/api/martial/masters/'+id+'/multimodal').catch(()=>null),cachedAssets('/api/martial/available-assets').catch(()=>({assets:[]}))]);
    if(!routeIsCurrent(version))return;M.detail=d;
    const m=d.master,v=d.versions?.[0],locked=d.versions?.find(x=>x.version===m.current_version),p=v?.payload||{},live=locked?.payload||{},ready=!!d.master_status?.production_ready;
    const images=[['portrait','主形象'],['front_view','正面'],['side_view','侧面'],['back_view','背面'],['turnaround','三视图'],['costume','服装']];
    const primary=live.portrait||m.primary_visual_asset_id||m.portrait_asset_id;
    layout(m.name,`万象武境 / 功法老师 / ${m.name}`,`<div class="mw-page-head"><div class="mw-master-head-image">${image(primary)}</div><div><span class="eyebrow">${e(m.species||'功法老师')} · V${e(m.current_version||1)}</span><h2>${e(m.name)}</h2><p>${e(d.martial_arts.map(x=>x.chinese_name).join('、')||'尚未绑定功法')}</p><div class="mw-inline-facts"><span>生产状态 <strong>${ready?'可用于生产':'待上传主形象'}</strong></span><span>声音 <strong>${live.voice_id?'已登记':'待登记'}</strong></span><span>版本 <strong>V${e(m.current_version||1)}</strong></span></div></div></div>${masterAssetsPanel(mm,id,available)}<div class="mw-two">${panel('视觉素材',`<div class="mw-visual-grid">${images.map(([key,label])=>`<article><span>${label}</span>${image(live[key]||(key==='portrait'?primary:null))}</article>`).join('')}${(live.other_angles||[]).map((assetId,index)=>`<article><span>其他角度 ${index+1}</span>${image(assetId)}</article>`).join('')}</div>${!ready?'<p class="mw-filter-note">上传一张可用主形象即可开始数字老师生产。</p>':''}`)}${panel('声音与人物',`<p>数字人模型：${val(live.digital_model)} · 当前可用动作 ${d.available_actions?.length||0} 条</p>${d.available_actions?.length?`<ul>${d.available_actions.map(a=>`<li>${e(a.art_name)} · 第 ${e(a.ordinal)} 式 · ${e(a.asset_id)}</li>`).join('')}</ul>`:''}<p>声音：${val(live.voice_id)} ${live.voice_preview?`<audio controls src="${a(live.voice_preview)}"></audio>`:''}</p><div class="mw-profile-list">${[['人物画像',live.profile],['生平',live.biography],['性格',live.personality],['世界观身份',live.world_identity],['教学风格',live.teaching_style],['说话方式',live.speaking_style]].map(([label,value])=>`<div><strong>${label}</strong><span>${val(value)}</span></div>`).join('')}</div><p>禁止变化项：${(live.forbidden_changes||[]).map(e).join('、')||'未登记'}</p>`)}
    </div>${canEdit()?`<form class="mw-form mw-panel" data-martial-form="master-visual" data-id="${e(id)}"><h3>补充老师素材</h3><div class="form-grid"><label>素材位置<select name="field">${images.map(([key,label])=>`<option value="${key}">${label}</option>`).join('')}<option value="other_angle">其他角度</option><option value="voice_preview">声音试听</option><option value="digital_model">数字人模型</option></select></label><label>文件<input type="file" name="file" accept=".png,.jpg,.jpeg,.webp,.wav,.mp3,.glb" required></label></div><button class="primary">上传素材</button></form>`:''}${(d.asset_imports||[]).length?`<details class="mw-panel mw-collapsible"><summary>更多角色图片（${d.asset_imports.length}）</summary><div class="mw-visual-grid">${d.asset_imports.map(x=>`<article><span>${e(x.role||'补充视角')} · V${e(x.master_version||1)}</span>${image(x.asset_id)}</article>`).join('')}</div></details>`:''}${canEdit()?`<details class="mw-panel mw-collapsible"><summary>编辑人物资料</summary><form class="mw-form" data-martial-form="master-profile" data-id="${e(id)}"><div class="form-grid">${[['人物画像','profile'],['生平','biography'],['性格','personality'],['教学风格','teaching_style'],['说话方式','speaking_style'],['世界观身份','world_identity']].map(([n,k])=>field(n,k,p[k]||'',true)).join('')}${field('声音编号','voice_id',p.voice_id||'')}${field('禁止变化项（每行一项）','forbidden_changes',(p.forbidden_changes||[]).join('\n'),true)}${field('资料来源','source_ref',v?.source_ref||'员工维护')}</div><p class="muted">修改已有定版角色核心设计仍需负责人确认。</p><button class="primary">保存修订</button></form></details>`:''}${m.draft_version&&isManager()?button('确认角色修订 V'+m.draft_version,'approve-master',id,'primary'):''}<details class="mw-panel mw-collapsible"><summary>版本记录</summary>${d.versions?.map(x=>`<div class="mw-version"><strong>V${e(x.version)}</strong>${pill(x.status)}<small>${e(x.source_ref)}</small></div>`).join('')||'<p class="muted">暂无版本记录。</p>'}</details>`,link('返回老师库','martial:masters'));
  }

  const QC_CHECKS=[['action_order','动作顺序'],['hand_path','手部 / 翼部路径'],['footwork','脚步'],['center_of_gravity','重心'],['start_pose','起始姿态'],['end_pose','结束姿态'],['key_moments','关键动作时刻'],['teaching_suitability','教学适宜性']];
  function mediaCard(m,ref,manager,index,mode='compare'){
    const selected=!!m.selection, qc=m.qc?.find(q=>q.stage==='martial'), technical=m.technical_report||{};
    const modelName=m.model_alias==='sd2.0'?'Seedance 2.0':m.model_alias==='sd2.5'?'Seedance 2.5':m.model_alias;
    const result=technical.result||m.qc?.find(q=>q.stage==='technical')?.result||'unverified';
    const plan=M.detail?.video_plans?.[m.asset_type],usePlan=m.current_context&&plan?.id===m.video_plan_id;
    const referenceStart=usePlan?plan.source_start:ref?.start_time||0,referenceEnd=usePlan?plan.source_end:ref?.end_time||0;
    return `<article class="mw-candidate" data-ref-start="${e(referenceStart)}" data-ref-end="${e(referenceEnd)}"><div class="row between"><strong>${e(videoLabel(m.asset_type))} · 第 ${index+1} 个视频 · V${index+1} · ${e(modelName)}${m.generation_mode==='preview'?' · 5 秒样片':''}</strong>${pill(m.status)}</div>
      ${m.candidate_asset_id?`<div class="mw-compare"><div><span>真人动作 · V${e(ref?.version||'?')} · ${seconds(referenceStart)}–${seconds(referenceEnd)}</span>${video(ref?.video_asset_id,'mw-ref')}</div><div><span>数字老师 · 第 ${index+1} 个视频</span>${video(m.candidate_asset_id,'mw-generated')}</div></div><div class="mw-player-controls">${button('从片段开始同时播放','sync-start',m.id)}${button('同时播放 / 暂停','sync',m.id)}${button('按真人进度定位','seek',m.id)}</div>`:'<p class="muted">候选生成完成后自动回传。可刷新查看；提交状态未知时不会自动重提。</p>'}
      <div class="mw-meta">${e(modelName)} · ${e(m.duration)} 秒 · ${e(m.resolution)} ${manager?`· 技术检查 ${pill(result)}`:''} · 预留费用 ${m.reserved_cost==null?'待核验':'¥'+Number(m.reserved_cost).toFixed(2)} · 实际费用 ${m.actual_cost==null?'待账单核验':'¥'+Number(m.actual_cost).toFixed(2)} ${manager&&m.provider?'· Provider '+e(m.provider)+' / '+e(m.model):''}${manager&&m.provider_job_id?' · 任务 '+e(m.provider_job_id):''}</div>
      ${m.error?`<p class="mw-error">${e(m.error)}</p>`:''}${selected?`<p>已选用：${e(m.selection.reason)}</p>`:''}
      ${mode==='compare'&&m.status==='succeeded'&&!selected&&state.user.martial_specialist?button('选用这个视频','select',m.id,'primary'):''}
      ${mode==='qc'&&selected&&!qc&&state.user.martial_specialist?`<form class="mw-form" data-martial-form="qc" data-id="${e(m.id)}"><h4>动作验收</h4><p class="muted">逐项对照真人动作。确认适合教学后可通过；有明显问题请选择需要返修并标记时间段。</p><div class="mw-qc-checks">${QC_CHECKS.map(([key,label])=>`<label>${label}<select name="check_${key}" required><option value="unsure">待核对</option><option value="pass">符合</option><option value="fail">需要修改</option></select></label>`).join('')}</div><h4>问题时间段</h4><div class="mw-issue-list"></div>${button('添加问题区间','add-issue',m.id)}<p class="muted">可在视频中定位后点击添加；时间和问题可以修改。</p>${field('真人动作对照结论','reference_comparison','',true)}${field('验收意见 / 返修要求','findings','',true)}<label>验收结论<select name="verdict" required><option value="">请选择</option><option value="pass">通过</option><option value="revision_required">需要返修</option></select></label><label><input type="checkbox" name="major_dispute"> 存在重大质量争议，需要负责人验收</label><button class="primary">保存验收结果</button></form>`:''}
      ${(mode==='qc'||mode==='history')&&qc?`<div class="mw-qc">动作验收：${pill(qc.result)}<p>${e(qc.findings)}</p><small>对照：${e(qc.reference_comparison)}</small>${qc.major_dispute?'<p class="mw-error">已标记重大质量争议。</p>':''}${qc.checks?`<div class="mw-qc-checks">${QC_CHECKS.map(([key,label])=>`<span>${label}：${e({pass:'符合',fail:'需要修改',unsure:'待核对'}[(qc.checks||{})[key]]||'未登记')}</span>`).join('')}</div>`:''}${(qc.issue_ranges||[]).length?`<ul>${qc.issue_ranges.map(x=>`<li>${seconds(x.start)}–${seconds(x.end)} ${e(x.issue)}</li>`).join('')}</ul>`:''}</div>`:''}
      ${(mode==='qc'||mode==='history')&&m.revision_package?`<div class="mw-panel"><h4>需要返修</h4><div class="mw-two">${[['保留','keep'],['修改','fix'],['不可改变','do_not_change'],['重点时间段','target_range']].map(([label,key])=>`<div><b>${label}</b>${manager?block(m.revision_package.payload?.[key]):revisionBlock(key,m.revision_package.payload?.[key])}</div>`).join('')}</div><p class="muted">已保存返修要求；是否重新生成由你决定。</p></div>`:''}
      ${mode==='qc'&&qc?.result==='fail'&&state.user.martial_specialist?button('重新生成','revise',m.id,'primary'):''}
      ${mode==='qc'&&qc?.result==='pass'&&m.generation_mode==='preview'?'<p class="mw-filter-note">这是 5 秒试拍样片，可以用于检查画面；完整视频仍需单独制作和验收。</p>':''}
      ${mode==='qc'&&qc?.result==='pass'&&m.generation_mode!=='preview'&&!m.final_asset_id&&(manager||state.user.martial_specialist&&!m.finalization_blocker)?button(manager?'批准为正式作品':'定版为正式作品','final',m.id,'primary'):''}
      ${mode==='qc'&&qc?.result==='pass'&&!manager&&m.finalization_blocker?`<p class="mw-filter-note">${e(m.finalization_blocker)}</p>`:''}</article>`;
  }

  const MOVE_STEPS=['动作标准','真人动作','参考片段','AI准备','数字老师','动作对比','验收完成'];
  function schedulePreparationRefresh(view){
    clearTimeout(M.prepareTimer);
    const userId=state.user.id;
    const refresh=()=>{
      if(state.view!==view||state.user?.id!==userId){M.prepareTimer=null;return}
      if(document.visibilityState==='hidden'){M.prepareTimer=setTimeout(refresh,5000);return}
      M.prepareTimer=null;go(view);
    };
    M.prepareTimer=setTimeout(refresh,5000);
  }
  function scheduleMediaRefresh(view){
    clearTimeout(M.mediaTimer);
    const userId=state.user.id;
    M.mediaTimer=setTimeout(()=>{
      M.mediaTimer=null;
      if(state.view!==view||state.user?.id!==userId)return;
      if(document.visibilityState==='hidden'){scheduleMediaRefresh(view);return}
      go(view);
    },15000);
  }
  const money=value=>value==null||!Number.isFinite(Number(value))?'暂未取得':`¥${Number(value).toFixed(2)}`;
  const inProgress=status=>['queued','dispatching','submitted','running','download_pending','technical_check'].includes(status);
  const listValue=value=>{if(Array.isArray(value))return value;if(typeof value==='string'){try{const parsed=JSON.parse(value);return Array.isArray(parsed)?parsed:[]}catch{return []}}return []};
  function currentMoveStep({factsReady,draft,ref,plans,pkg,media,finals}){
    if(!factsReady)return 1;
    if(draft)return 3;
    if(!ref)return 2;
    if(videoKinds.some(([type])=>plans?.[type]?.motion_ref_id!==ref.id))return 3;
    if(!pkg||pkg.status!=='complete')return 4;
    const unfinished=videoKinds.map(([type])=>type).filter(type=>!finals.some(x=>x.asset_type===type&&x.status==='active'));
    if(unfinished.some(type=>!media.some(x=>x.asset_type===type&&x.candidate_asset_id&&x.generation_mode==='complete')))return 5;
    if(unfinished.some(type=>!media.some(x=>x.asset_type===type&&x.selection&&x.generation_mode==='complete')))return 6;
    return 7;
  }
  function moveStepper(current,maxReachable=current){
    return `<nav class="mw-stepper" aria-label="招式制作步骤">${MOVE_STEPS.map((label,index)=>{
      const n=index+1,ready=n<=maxReachable;
      return `<button type="button" class="mw-step ${n===current?'active':n<current?'done':''}" data-mw-step="${n}" ${ready?'':'disabled'} aria-current="${n===current?'step':'false'}"><span>${n}</span><b>${label}</b></button>`;
    }).join('')}</nav>`;
  }
  function videoPlanCard(type,plan,ref,moveId){
    const label=videoLabel(type),current=plan?.motion_ref_id===ref?.id;
    const start=Number(ref?.start_time)||0,end=Number(ref?.end_time??ref?.duration)||0;
    const plannedStart=current?plan.source_start:start,plannedEnd=current?plan.source_end:end;
    const plannedDuration=current?plan.target_duration:Math.max(0,end-start);
    const segmentLength=Math.round((Number(plannedEnd)-Number(plannedStart))*100)/100;
    const mismatch=current&&Math.abs(Number(plannedDuration)-segmentLength)>0.02;
    return `<article class="mw-video-track"><div class="row between"><h4>${e(label)}</h4>${current?pill('confirmed'):pill('draft')}</div><p>${type==='teaching'?'单独讲清动作要领并示范；可与跟练采用不同片段和时长。':'单独供学员跟随教练重复动作；可比讲解演示更长。'}</p><form class="mw-form" data-martial-form="video-plan" data-id="${e(moveId)}" data-video-track="${e(type)}" data-segment-length="${e(segmentLength)}" data-saved-plan="${current?'true':'false'}"><input type="hidden" name="asset_type" value="${e(type)}"><div class="form-grid"><label>参考开始（秒）<input type="number" name="source_start" min="0" step="0.01" value="${e(plannedStart)}" required></label><label>参考结束（秒）<input type="number" name="source_end" min="0.01" step="0.01" value="${e(plannedEnd)}" required></label><label>计划成片时长（秒）<input type="number" name="target_duration" min="0.01" step="0.01" value="${e(Number(plannedDuration).toFixed(2))}" required></label></div><div class="mw-definition-grid"><div><b>所选真人片段实长</b><span data-plan-length>${seconds(segmentLength)}</span></div><div><b>计划成片时长</b><span data-plan-target>${seconds(plannedDuration)}</span></div></div><p class="mw-error" data-plan-mismatch ${mismatch?'':'hidden'}>${mismatch?`已保存的成片时长 ${seconds(plannedDuration)} 与片段实长 ${seconds(segmentLength)} 不同。请核对是否有意重复或延长；原方案不会自动改写。`:''}</p><div class="mw-plan-controls">${button('此刻设为开始','plan-start')}${button('此刻设为结束','plan-end')}${button('播放所选片段','plan-play')}${button('按片段长度填写','plan-use-length')}</div><label>必须保留的内容（选填）<textarea name="brief" rows="3" maxlength="800" placeholder="例如：保留原片的动作顺序、重复次数和节奏">${e(current?plan.brief||'':'')}</textarea></label><button class="primary" type="submit">${current?'保存新版 '+label+'方案':'保存 '+label+'方案'}</button></form><small>${current?`已保存 V${e(plan.version)} · 真人片段 ${seconds(plan.source_start)}–${seconds(plan.source_end)} · 计划成片 ${seconds(plan.target_duration)}`:'请保存这一类视频的片段和计划时长，再让 AI 准备。'}第 5 步会按当前方案列出完整制作的分段、费用预留和任务预算方式。</small></article>`;
  }
  function syncVideoPlan(form,boundsChanged=false){
    const start=Number.parseFloat(form.elements.source_start.value),end=Number.parseFloat(form.elements.source_end.value);
    const previous=Number.parseFloat(form.dataset.segmentLength),targetInput=form.elements.target_duration;
    const valid=Number.isFinite(start)&&Number.isFinite(end)&&end>start;
    const length=valid?Math.round((end-start)*100)/100:null;
    if(boundsChanged&&valid&&Number.isFinite(previous)&&Math.abs(Number.parseFloat(targetInput.value)-previous)<=0.02){
      targetInput.value=length.toFixed(2);
    }
    if(valid)form.dataset.segmentLength=String(length);
    const target=Number.parseFloat(targetInput.value),mismatch=valid&&Number.isFinite(target)&&Math.abs(target-length)>0.02;
    const lengthText=form.querySelector('[data-plan-length]'),targetText=form.querySelector('[data-plan-target]'),warning=form.querySelector('[data-plan-mismatch]');
    if(lengthText)lengthText.textContent=valid?seconds(length):'请检查片段起止';
    if(targetText)targetText.textContent=Number.isFinite(target)?seconds(target):'请填写计划时长';
    if(warning){warning.hidden=!mismatch;warning.textContent=mismatch?`${form.dataset.savedPlan==='true'?'已保存的':'当前填写的'}成片时长 ${seconds(target)} 与片段实长 ${seconds(length)} 不同。请核对是否有意重复或延长；${form.dataset.savedPlan==='true'?'原方案不会自动改写。':'如需相同长度，可点击“按片段长度填写”。'}`:''}
  }
  const osKinds=[['intro','招式介绍'],['instruction','动作讲解'],['practice','跟练提示'],['breathing','呼吸提示'],['success','完成鼓励']];
  const mmAssetChoices=(available,type,selected='',none='请选择已有素材')=>`<option value="">${e(none)}</option>${(available?.assets||[]).filter(x=>x.type===type).map(x=>`<option value="${e(x.id)}" ${x.id===selected?'selected':''}>${e(x.name)}</option>`).join('')}`;
  function artAssetsPanel(mm,id,available){
    if(!mm)return panel('功法级资产','<p class="muted">资产信息暂不可用。</p>');
    const roles=[['theme_music','功法主题音乐','audio'],['training_bgm','训练 BGM','audio'],['logo','功法 Logo','image'],['main_visual','主视觉','image']];
    const cards=roles.map(([key,label])=>`<div><b>${label}</b><span>${e(mm.assets?.[key]?.name||'尚未设置')}</span></div>`).join('');
    const editor=canEdit()?`<details class="mw-collapsible"><summary>维护功法级素材</summary><p class="muted">从已有素材中选择；BGM 会默认供本功法招式使用。</p>${roles.map(([key,label,type])=>`<form class="mw-mm-link-form" data-martial-form="art-mm-asset" data-id="${e(id)}"><input type="hidden" name="role" value="${key}"><label>${label}<select name="asset_id">${mmAssetChoices(available,type,mm.assets?.[key]?.asset_id)}</select></label><button class="outline" type="submit">保存</button></form>`).join('')}<form class="mw-form" data-martial-form="art-worldview" data-id="${e(id)}"><h4>世界观说明</h4><div class="form-grid">${field('中文','zh_text',mm.worldview?.zh_text||'',true)}${field('English','en_text',mm.worldview?.en_text||'',true)}</div><button class="outline" type="submit">保存说明新版本</button></form></details>`:'';
    return panel('功法级资产',`<div class="mw-mm-inheritance">${cards}</div><p>世界观说明：${mm.worldview?.zh_text||mm.worldview?.en_text?`已保存 V${e(mm.worldview.version)}`:'待补充'}</p>${editor}`,'mw-mm-panel');
  }
  function masterAssetsPanel(mm,id,available){
    if(!mm)return panel('老师级声音资产','<p class="muted">资产信息暂不可用。</p>');
    const voice=mm.voice_persona||{},audio=mm.intro_audio,audition=mm.voice_audition,reference=mm.voice_reference;
    const auditionText=audition?.available?audition.name||'已导入':audition?'试听文件不可用':'待导入';
    const referenceText=reference?.available?reference.name||'已导入':reference?'参考文件不可用':'待导入';
    const editor=canEdit()?`<details class="mw-collapsible"><summary>维护老师 Voice Persona</summary><form class="mw-form" data-martial-form="master-voice-persona" data-id="${e(id)}"><div class="form-grid">${field('Voice ID（已定版时不随意更换）','voice_id',mm.voice_id||'')}${field('声线与表演设定','persona',voice.persona||'',true)}${field('语言','language',voice.language||'')}${field('语气','tone',voice.tone||'')}${field('语速','pace',voice.pace||'')}</div><button class="outline" type="submit">保存声音设定新版本</button></form><form class="mw-mm-link-form" data-martial-form="master-intro-audio" data-id="${e(id)}"><label>标准介绍语音<select name="asset_id">${mmAssetChoices(available,'audio',audio?.asset_id)}</select></label><button class="outline" type="submit">保存</button></form></details>`:'';
    return panel('老师级声音资产',`<div class="mw-mm-inheritance"><div><b>Voice Persona</b><span>${e(voice.persona||'待登记')}</span></div><div><b>Voice ID</b><span>${e(mm.voice_id||'待接通')}</span></div><div><b>定稿英语试听样音（MP3）</b><span>${e(auditionText)}</span>${audition?.available?`<audio controls preload="metadata" src="${e(a(audition.asset_id))}"></audio>`:''}</div><div><b>英语生成参考原始 WAV</b><span>${e(referenceText)}</span>${reference?.available?`<a href="${e(assetUrl({id:reference.asset_id}))}" download>下载参考文件</a>`:''}</div><div><b>老师标准介绍语音</b><span>${e(audio?.name||'尚未生成')}</span></div></div><p class="muted">试听台词只用于辨认声线，不是老师介绍语音；生成参考需配对应原文使用。</p>${editor}`,'mw-mm-panel');
  }
  function moveAssetSummary(mm,id,available){
    if(!mm)return panel('资产完成度','<p class="muted">资产信息暂不可用；动作生产仍可继续。</p>');
    const evidence=mm.completion?.evidence||[],has=key=>!!evidence.find(x=>x.key===key&&x.ready);
    const scripts=mm.scripts||{},osReady=osKinds.filter(([kind])=>scripts[kind]?.tts?.zh?.current||scripts[kind]?.tts?.en?.current).length;
    const bgm=mm.bgm?.asset,bgmText=bgm?.available?`${bgm.name}（${mm.bgm.source==='art'?'继承功法':'本招专用'}）`:'待添加；默认沿用功法训练 BGM';
    const inherited=mm.voice_inheritance||{},voice=inherited.voice_persona;
    const voiceText=voice?.persona?`${voice.persona}（继承${mm.master?.name||'功法老师'}）`:inherited.voice_id?`Voice ID 已登记（继承${mm.master?.name||'功法老师'}）`:'老师声线待登记';
    const metrics=[['动作',has('action_standard')],['真人参考',has('motion_reference')],['讲解演示',has('teaching_video')],['跟教练跟练',has('practice_video')]];
    const historical=videoKinds.map(([type,label])=>({label,item:mm.videos?.[type]?.historical_final})).filter(x=>x.item?.asset?.available);
    const rows=osKinds.map(([kind,label])=>{const s=scripts[kind]||{};const zh=s.tts?.zh?.current,en=s.tts?.en?.current;return `<div class="mw-mm-os-row"><strong>${label}</strong><span>文本 ${s.version?'V'+e(s.version):'待写'}</span><span>语音 ${zh||en?`${zh?'中文':''}${zh&&en?' / ':''}${en?'英文':''}`:'待生成'}</span></div>`}).join('');
    const intro=scripts.intro||{};
    return panel('资产完成度',`<div class="mw-mm-head"><strong>${e(mm.completion?.percent||0)}%</strong><span>${e(mm.completion?.ready_count||0)} / ${e(mm.completion?.total_count||0)} 项有真实资产或内容</span></div><progress max="100" value="${e(mm.completion?.percent||0)}"></progress><div class="mw-mm-metrics">${metrics.map(([label,ready])=>`<span>${label} ${ready?'✓':'○'}</span>`).join('')}<span>OS 语音 ${osReady} / 5</span></div>${historical.length?`<p class="mw-filter-note">旧方案正式视频仍保留：${historical.map(x=>`<a href="${a(x.item.asset.asset_id)}" target="_blank" rel="noopener">${e(x.label)}</a>`).join(' · ')}。它们不计入当前方案完成度。</p>`:''}<div class="mw-mm-inheritance"><div><b>老师声线</b><span>${e(voiceText)}</span></div><div><b>训练 BGM</b><span>${e(bgmText)}</span></div></div><details class="mw-collapsible"><summary>查看五类 OS 文本与语音</summary><div class="mw-mm-os-list">${rows}</div>${canEdit()?`<form class="mw-form" data-martial-form="os-script" data-id="${e(id)}"><h4>编辑 OS 文本</h4><p class="muted">文本保存新版本；旧语音保留历史，需按新版本重新制作才算完成。</p><label>用途<select name="kind">${osKinds.map(([kind,label])=>`<option value="${kind}">${label}</option>`).join('')}</select></label><div class="form-grid">${field('中文','zh_text',intro.zh_text||'',true)}${field('English','en_text',intro.en_text||'',true)}${field('APP 调用文本','app_text',intro.app_text||'',true)}</div><button class="primary" type="submit">保存 OS 文本新版本</button></form><form class="mw-mm-link-form" data-martial-form="move-tts" data-id="${e(id)}"><label>OS 类型<select name="kind">${osKinds.map(([kind,label])=>`<option value="${kind}">${label}</option>`).join('')}</select></label><label>语种<select name="language"><option value="zh">中文</option><option value="en">English</option></select></label><label>已有语音素材<select name="asset_id" required>${mmAssetChoices(available,'audio')}</select></label><button class="outline" type="submit">关联到当前 OS 文本</button></form><form class="mw-mm-link-form" data-martial-form="move-bgm" data-id="${e(id)}"><label>本招训练 BGM<select name="asset_id">${mmAssetChoices(available,'audio',mm.bgm?.move_override_asset_id,'继承功法 BGM')}</select></label><button class="outline" type="submit">保存</button></form>`:''}</details>`,'mw-mm-panel');
  }
  async function moveWorkspacePage(id){
    const version=state.routeVersion;
    clearTimeout(M.prepareTimer);M.prepareTimer=null;
    clearTimeout(M.mediaTimer);M.mediaTimer=null;
    if(M.lastMove!==id){M.lastMove=id;M.moveStepOverride=null}
    const [d,o,mm,available]=await Promise.all([api('/api/martial/moves/'+id),overview(),
      api('/api/martial/moves/'+id+'/multimodal').catch(()=>null),cachedAssets('/api/martial/available-assets').catch(()=>({assets:[]}))]);
    if(!routeIsCurrent(version))return;
    M.detail=d;M.multimodal=mm;M.quote={};M.previewQuote={};M.task=d.task||null;
    const m=d.move,v=d.effective_version||d.version,p=v?.payload||{};
    const ref=d.motions.find(x=>x.status==='locked'),draft=d.motions.find(x=>x.status==='draft');
    const plans=d.video_plans||{},plansReady=!!ref&&videoKinds.every(([type])=>plans[type]?.motion_ref_id===ref.id);
    const latestPackage=d.packages[0];
    const pkg=d.package_current&&latestPackage?.status==='complete'?latestPackage:null;
    const packagePending=d.package_pending_current&&['queued','running','dispatching'].includes(latestPackage?.status)?latestPackage:null;
    const packageUncertain=d.package_pending_current&&latestPackage?.status==='unknown_submission';
    const siblings=o.moves.filter(x=>x.martial_art_id===m.martial_art_id).sort((a,b)=>(a.display_order||a.ordinal)-(b.display_order||b.ordinal));
    const at=siblings.findIndex(x=>x.id===id),name=siblings[at]?.business_label||p.chinese_name||'待登记招式';
    const teacher=masterName(d.art.master_id),factsReady=!!m.current_version&&!!p.chinese_action&&!!p.english_action;
    const currentMedia=d.media.filter(job=>job.current_context===true&&job.video_plan_id&&job.motion_ref_id===ref?.id&&plans[job.asset_type]?.id===job.video_plan_id);
    const activeFinals=d.finals.filter(final=>final.status==='active'&&final.current===true&&currentMedia.some(job=>job.id===final.media_job_id));
    const historicalMedia=d.media.filter(job=>!currentMedia.includes(job));
    const current=currentMoveStep({factsReady,draft,ref,plans,pkg,media:currentMedia,finals:activeFinals});M.moveStepCurrent=current;
    if(M.moveStepOverride&&M.moveStepOverride>current)M.moveStepOverride=null;
    const step=M.moveStepOverride||current;
    const canProduce=canEdit(),missing=pkg?.result?.missing_inputs||[];
    const allFinished=videoKinds.every(([type])=>activeFinals.some(x=>x.asset_type===type));
    const hasRevision=videoKinds.some(([type])=>{if(activeFinals.some(final=>final.asset_type===type))return false;const latest=currentMedia.find(x=>x.asset_type===type&&x.generation_mode==='complete'&&x.candidate_asset_id);return latest?.qc?.some(q=>q.stage==='martial'&&q.result==='fail')});
    const standard=panel('动作标准',`<div class="mw-definition-grid">${[['中文动作',p.chinese_action],['英文动作',p.english_action],['中文教学提示',p.chinese_coaching],['英文教学提示',p.english_coaching],['呼吸提示',p.breathing_notes],['安全提示',p.safety_notes]].map(([label,value])=>`<div><b>${label}</b><span>${val(value)}</span></div>`).join('')}</div>${canProduce?`${!factsReady?button('编辑动作标准','open-standard',id,'primary'):''}<details class="mw-collapsible ${!factsReady?'mw-incomplete':''}" id="mw-standard-editor"><summary>编辑动作标准</summary><form class="mw-form" data-martial-form="standard" data-id="${e(id)}"><div class="form-grid">${[['中文名称','chinese_name'],['英文名称','english_name'],['中文动作','chinese_action'],['英文动作','english_action'],['中文教学提示','chinese_coaching'],['英文教学提示','english_coaching'],['呼吸提示','breathing_notes'],['安全提示','safety_notes']].map(([label,key])=>field(label,key,p[key]||'',!['chinese_name','english_name'].includes(key))).join('')}</div><button class="primary" type="submit">保存新版本</button></form><p class="muted">每次保存都会留下旧版本。若与已经完成的正式作品有重大冲突，系统会提示负责人确认。</p></details>`:''}`);
    const motion=panel('上传真人动作',`${ref?`<div class="mw-existing-reference">${video(ref.video_asset_id,'mw-main-video')}<p>现用真人动作 · V${e(ref.version)} · ${seconds(ref.duration)} · ${e(ref.width||'?')}×${e(ref.height||'?')}</p></div>`:''}<form class="mw-form" data-martial-form="motion" data-id="${e(id)}"><label>选择真人示范视频（MP4 / MOV，保留原片）<input type="file" name="file" accept=".mp4,.mov,video/mp4,video/quicktime" required></label><div class="mw-local-preview hidden"><video class="mw-preview-video" controls preload="metadata"></video><div class="mw-video-meta"></div></div><input type="hidden" name="orientation" value="正面"><button class="primary" type="submit">上传真人动作</button><p class="mw-upload-progress" role="status" hidden></p></form><p class="muted">视频以外都可稍后补充。保存后会自动进入参考片段。</p>`,'mw-media-panel');
    const rangeControls=`<div class="mw-range"><strong>选择参考片段</strong><label>开始（秒）<input name="start_time" type="number" min="0" step="0.01" value="${e(draft?.start_time??0)}"></label><label>结束（秒）<input name="end_time" type="number" min="0" step="0.01" value="${e(draft?.end_time??draft?.duration??'')}"></label>${button('设为开始','range-start')}${button('设为结束','range-end')}${button('播放选中片段','range-play')}</div><p class="muted">默认使用整段视频；只有需要截取时才调整起止时间。</p><div class="form-grid"><label>画面方向<select name="orientation"><option value="正面" ${!draft?.orientation||draft.orientation==='正面'?'selected':''}>正面</option><option value="侧面" ${draft?.orientation==='侧面'?'selected':''}>侧面</option><option value="背面" ${draft?.orientation==='背面'?'selected':''}>背面</option><option value="其他" ${draft?.orientation==='其他'?'selected':''}>其他</option></select></label>${field('起始姿态（选填）','start_pose',draft?.start_pose||'',true)}${field('结束姿态（选填）','end_pose',draft?.end_pose||'',true)}${field('备注（选填）','notes',draft?.notes||'',true)}</div><div class="mw-key-moments"><strong>关键动作（选填）</strong><div class="mw-moment-list">${(draft?.key_moments||[]).map(x=>`<div class="mw-moment-item" data-time="${e(x.time)}"><span>${seconds(x.time)} · ${e(x.label)}</span><input type="hidden" data-moment-label value="${e(x.label)}">${button('移除','remove-moment')}</div>`).join('')}</div><div class="mw-moment-input"><label>动作描述<input name="moment_label" placeholder="播放到动作发生时，例如：翻掌"></label>${button('添加关键动作','add-moment')}</div></div>`;
    const range=draft?panel('选择参考片段',`<div class="mw-local-preview"><video class="mw-preview-video" controls preload="metadata" src="${a(draft.video_asset_id)}"></video><div class="mw-video-meta">${seconds(draft.duration)} · ${e(draft.width||'?')}×${e(draft.height||'?')} · ${e(draft.frame_orientation||'方向待识别')}</div></div><form class="mw-form" data-martial-form="motion-range" data-id="${e(draft.id)}">${rangeControls}<button class="primary" type="submit">确认为标准动作</button></form>`):panel('参考片段与两类视频',ref?`${video(ref.video_asset_id,'mw-main-video mw-plan-player')}<p>已确认为标准动作 · ${seconds(ref.start_time)}—${seconds(ref.end_time)}</p><p>把同一招的讲解演示和跟教练跟练分开规划。可选择不同原片区间，各自设定目标时长。</p>${(ref.key_moments||[]).length?`<p>关键动作：${ref.key_moments.map(x=>seconds(x.time)+' '+e(x.label)).join('；')}</p>`:''}<div class="mw-video-track-grid">${videoKinds.map(([type])=>videoPlanCard(type,plans[type],ref,id)).join('')}</div>`:'<p>先上传真人动作。确认标准动作后，再分别规划讲解演示和跟教练跟练。</p>');
    const aiReady=!!ref&&plansReady&&!!d.master_status?.production_ready&&factsReady&&d.art.status==='active';
    const brief=String(pkg?.facts?.production_brief||'');
    const aiDetails=pkg?.result||{};
    const aiBlocker=!ref?'请先确认真人动作参考':!plansReady?'请先在参考片段中分别保存讲解演示和跟教练跟练方案':!d.art.master_id?'请先给功法选择老师':!d.master_status?.production_ready?'数字老师还缺少主形象，请先上传老师图片':!factsReady?'请先完善动作标准':'请先完成前面的步骤';
    const aiFix=!d.art.master_id?link('选择功法老师','martial:art:'+d.art.id):d.art.master_id?link('前往功法老师','martial:master:'+d.art.master_id):'';
    const canPrepare=canProduce&&(!!state.user.martial_specialist||!!M.task);
    const ai=panel(pkg?'AI 已帮你准备好':'AI 准备',`${pkg?`<div class="mw-ready-list">${[['动作说明','move_summary'],['讲解演示提示词','teaching_prompt'],['跟教练跟练提示词','practice_prompt'],['禁止项','negative_constraints'],['角色参考','character_constraints'],['真人动作参考','reference_mapping'],['生成设置','shot_camera_plan']].map(([label,key])=>`<span>${aiDetails[key]?'✓':'○'} ${label}</span>`).join('')}</div>${missing.length?`<p class="mw-error">还需要补充：${e(missing.join('；'))}</p>`:''}${button('查看生成内容','show-config',id)}`:packagePending?'<p>AI 正在分别准备两类视频的生成内容，完成后页面会自动进入下一步。</p>':packageUncertain?'<p class="mw-error">AI 准备的提交状态暂不明确，系统不会自动重复提交。请联系管理员核对。</p>':`<p>${aiReady?'两类视频方案、真人动作和老师素材已就绪。AI 会分别准备讲解演示与跟教练跟练。':e(aiBlocker)}</p>`}${canPrepare&&aiReady&&!packagePending&&!packageUncertain?`<label>两类视频共同遵守的要求（选填）<textarea id="mw-production-brief" rows="3" maxlength="800" placeholder="例如：动作顺序与真人参考一致，不自行增加招式">${e(brief)}</textarea></label>${button(pkg?'重新准备两类视频':'让 AI 准备两类视频','package',id,pkg?'':'primary')}`:!aiReady?aiFix:canProduce&&!M.task?'首次 AI 准备由武学岗位员工发起。':''}`);
    const videoTrack=([type,label])=>{
      const plan=plans[type],jobs=currentMedia.filter(x=>x.asset_type===type),completeJobs=jobs.filter(x=>x.generation_mode==='complete');
      const completeJob=completeJobs[0],activeJob=completeJobs.find(x=>inProgress(x.status));
      const candidate=completeJobs.find(x=>x.candidate_asset_id),preview=jobs.find(x=>x.generation_mode==='preview'&&x.candidate_asset_id);
      const final=activeFinals.find(x=>x.asset_type===type);
      const segmentLength=plan?Math.round((Number(plan.source_end)-Number(plan.source_start))*100)/100:null;
      const mismatch=plan&&Math.abs(Number(plan.target_duration)-segmentLength)>0.02;
      const progress=final?'正式视频已完成':candidate?'完整视频已回传，待动作验收':activeJob?words[activeJob.status]||'制作中':completeJob?.status==='failed'?'制作失败，可核对原因后重新发起':completeJob?.status==='unknown_submission'?'提交状态未知，待后台核对':'待制作';
      const reportedSegments=listValue(completeJob?.segment_progress),plannedSegments=listValue(completeJob?.segments);
      const segmentRows=(plannedSegments.length?plannedSegments:reportedSegments).map((segment,index)=>({...segment,...(reportedSegments.find(item=>Number(item.index)===Number(segment.index??index))||{})}));
      const segmentProgress=segmentRows.length?`<ol class="mw-segment-progress">${segmentRows.map((segment,index)=>`<li><span>第 ${e(Number.isFinite(Number(segment.index))?Number(segment.index)+1:index+1)} 段 · ${seconds(segment.source_start)}–${seconds(segment.source_end)}</span>${pill(segment.status||(completeJob.status==='succeeded'?'succeeded':'not_started'))}${segment.error?`<small class="mw-error">${e(segment.error)}</small>`:''}</li>`).join('')}</ol>`:'';
      const form=canProduce&&pkg?`<form class="mw-form mw-complete-form" data-martial-form="generate" data-video-track="${e(type)}" data-generation-mode="complete" data-has-active="${activeJob?'true':'false'}" data-has-unknown="${completeJobs.some(x=>x.status==='unknown_submission')?'true':'false'}" data-has-final="${final?'true':'false'}" data-has-candidate="${candidate?'true':'false'}" data-id="${e(id)}"><input type="hidden" name="asset_type" value="${e(type)}"><input type="hidden" name="generation_mode" value="complete"><input type="hidden" name="model" value="sd2.5"><input type="hidden" name="candidate_count" value="1"><input type="hidden" name="revision_of" value=""><div id="mw-quote-${e(type)}-complete" class="mw-quote mw-complete-quote" role="status">正在计算完整视频的分段与费用预留…</div><button class="primary" type="submit" disabled>${activeJob?'完整视频制作中':`制作${e(label)}完整视频`}</button></form>`:'<p class="mw-filter-note">请先完成当前两类视频的 AI 准备，再核对完整制作费用。</p>';
      const previewForm=canProduce&&pkg?`<details class="mw-collapsible mw-preview-options" data-preview-options="${e(type)}"><summary>可选：先做 5 秒试拍样片</summary><p class="muted">样片只用于检查角色与画面，不能作为完整作品验收。</p><form class="mw-form" data-martial-form="generate" data-video-track="${e(type)}" data-generation-mode="preview" data-id="${e(id)}"><input type="hidden" name="asset_type" value="${e(type)}"><input type="hidden" name="generation_mode" value="preview"><input type="hidden" name="model" value="sd2.5"><input type="hidden" name="candidate_count" value="1"><input type="hidden" name="revision_of" value=""><div id="mw-quote-${e(type)}-preview" class="mw-quote" role="status">打开后读取样片费用…</div><button class="outline" type="submit" disabled>生成 5 秒试拍样片</button></form>${preview?`<div class="mw-preview-result">${video(preview.candidate_asset_id,'mw-main-video')}</div>`:''}</details>`:'';
      return `<article class="mw-video-track mw-production-track"><div class="row between"><h4>${e(label)}</h4>${final?pill('active'):completeJob?pill(completeJob.status):pill('draft')}</div><div class="mw-definition-grid"><div><b>计划成片</b><span>${plan?seconds(plan.target_duration):'待规划'}</span></div><div><b>真人参考片段</b><span>${plan?`${seconds(plan.source_start)}–${seconds(plan.source_end)}（实长 ${seconds(segmentLength)}）`:'待规划'}</span></div><div><b>数字老师</b><span>${e(teacher)} · V${e(master(d.art.master_id)?.current_version||1)}</span></div><div><b>制作进度</b><span>${e(progress)}</span></div></div>${mismatch?`<p class="mw-error">计划成片 ${seconds(plan.target_duration)} 与真人片段实长 ${seconds(segmentLength)} 不同。请先回到参考片段保存正确方案。</p>`:''}${segmentProgress}${completeJob?.error?`<p class="mw-error">${e(completeJob.error)}</p>`:''}${final?`<p>正式作品：<a href="${a(final.asset_id)}" target="_blank" rel="noopener">查看${e(label)}视频</a></p>`:''}${candidate?.candidate_asset_id?`${video(candidate.candidate_asset_id,'mw-main-video')}<p>完整视频已回传。请到“动作对比”和“验收完成”核对。</p>`:''}${form}${previewForm}</article>`;
    };
    const budgetForm=isManager()&&M.task?`<details class="mw-collapsible mw-budget-editor" open><summary>负责人：本招任务预算</summary><form class="mw-form" data-martial-form="budget" data-id="${e(id)}"><div class="mw-budget-fields"><label>预算方式<select name="budget_mode"><option value="unlimited" ${M.task.budget_unlimited?'selected':''}>不设上限</option><option value="fixed" ${M.task.budget_unlimited?'':'selected'}>设置上限</option></select></label><label data-budget-cap ${M.task.budget_unlimited?'hidden':''}>上限金额（元）<input type="number" name="budget_cap" min="0" max="100" step="0.01" value="${e(M.task.budget_cap??'')}" ${M.task.budget_unlimited?'disabled':''} required></label></div><p class="muted">每段仍会显示提交前费用预留估算；实际费用以供应商账单为准。</p><button class="outline" type="submit">保存预算方式</button></form></details>`:'';
    const digital=panel('数字老师 · 制作完整视频',`<p>讲解演示与跟教练跟练按各自确认的方案制作。超过单次时长的讲解演示将按段生成并合成一条完整视频；跟练按完整片段制作。费用为提交前的预留估算，后台实际账单随后核对。</p><div id="mw-complete-overview" class="mw-production-total" role="status">正在核对两类完整视频的费用与任务余额…</div><div class="mw-video-track-grid">${videoKinds.map(videoTrack).join('')}</div><div class="mw-production-actions">${button('刷新制作进度','refresh','','outline')}</div>${budgetForm}`);
    const comparison=panel('对照真人动作',`<div class="mw-video-track-grid">${videoKinds.map(([type,label])=>{const videos=currentMedia.filter(x=>x.asset_type===type&&x.generation_mode==='complete');return `<section class="mw-video-track"><h4>${e(label)}</h4>${videos.length?videos.map(x=>mediaCard(x,d.motions.find(y=>y.id===x.motion_ref_id),isManager(),currentMedia.indexOf(x),'compare')).join(''):'<p>完整视频生成后，可在这里和真人动作对照。</p>'}</section>`}).join('')}</div>`);
    const qc=panel('分别验收两类视频',`<div class="mw-video-track-grid">${videoKinds.map(([type,label])=>{const selected=currentMedia.find(x=>x.asset_type===type&&x.generation_mode==='complete'&&x.selection),final=activeFinals.find(x=>x.asset_type===type);return `<section class="mw-video-track"><h4>${e(label)}</h4>${selected?mediaCard(selected,d.motions.find(y=>y.id===selected.motion_ref_id),isManager(),currentMedia.indexOf(selected),'qc'):'<p>请先在动作对比中选用这一类视频。</p>'}${final?`<p>正式作品：<a href="${a(final.asset_id)}" target="_blank" rel="noopener">查看${e(label)}</a></p>`:''}</section>`}).join('')}</div>`);
    const parts=[standard,motion,range,ai,digital,comparison,qc];
    const config=pkg?`<dialog id="mw-config-dialog" class="mw-dialog"><div class="row between"><h3>AI 准备的生成内容</h3>${button('关闭','close-config')}</div><div class="mw-ai-detail">${[['生成说明','move_summary'],['讲解演示 Prompt','teaching_prompt'],['跟教练跟练 Prompt','practice_prompt'],['禁止项','negative_constraints'],['使用素材','reference_mapping'],['模型建议','shot_camera_plan']].map(([label,key])=>`<section><h4>${label}</h4>${block(aiDetails[key])}</section>`).join('')}</div><p>完整制作的分段、费用预留和任务预算方式以第 5 步实时查询为准。</p>${button('复制生成内容','copy-config')}</dialog>`:'';
    const admin=isManager()?`<details class="mw-panel mw-collapsible"><summary>管理员高级信息</summary><h4>版本记录</h4>${d.versions.map(x=>`<div class="mw-version">V${e(x.version)} ${pill(x.status)} <small>${e(x.source_ref)}</small></div>`).join('')}${button('查看技术历史','audit-history',id)}<div id="mw-technical-history"></div></details>`:'';
    const currentLabel=hasRevision?'需要返修':allFinished?'已完成':current===7?'动作验收':MOVE_STEPS[current-1];
    const nextText=hasRevision?'查看对应视频的修改要求，决定何时重新生成':allFinished?'讲解演示和跟教练跟练均已形成正式作品':current===4?(packagePending?'AI 正在准备，完成后自动显示下一步':aiReady?'点击让 AI 分别准备两类视频':aiBlocker):['请核对并完善动作标准','上传这招的真人动作视频','分别保存两类视频方案','','核对讲解与跟练的分段、预计费用和预算方式后，分别开始完整制作','对照真人动作并选择视频','分别完成动作验收'][current-1];
    const maxReachable=currentMedia.some(x=>x.generation_mode==='complete'&&x.selection)?7:currentMedia.some(x=>x.generation_mode==='complete'&&x.candidate_asset_id)?6:current;
    const history=historicalMedia.length?`<details class="mw-panel mw-collapsible"><summary>历史视频与验收（${historicalMedia.length}）</summary><p class="muted">这些视频使用的是旧真人参考或旧制作方案，保留原记录；不会被算作当前两类视频的完成结果。</p>${historicalMedia.map(x=>mediaCard(x,d.motions.find(y=>y.id===x.motion_ref_id),isManager(),d.media.indexOf(x),'history')).join('')}</details>`:'';
    layout(name,`万象武境 / ${d.art.chinese_name} / ${name}`,`<div class="mw-page-head mw-move-head"><div><span class="eyebrow">${e(d.art.chinese_name)} / 第 ${String(siblings[at]?.display_order||m.ordinal).padStart(2,'0')} 式</span><h2>${e(name)}</h2><p>当前：${e(currentLabel)}</p></div><div class="mw-header-actions">${at>0?plainLink('← 上一式','martial:move:'+siblings[at-1].id):''}${at<siblings.length-1?plainLink('下一式 →','martial:move:'+siblings[at+1].id):''}</div></div>${moveAssetSummary(mm,id,available)}${moveStepper(current,maxReachable)}<div class="mw-next-work"><strong>下一步：${e(currentLabel)}</strong><span>${e(nextText)}</span></div>${parts.map((html,i)=>`<section class="mw-step-panel" data-mw-step-panel="${i+1}" ${i+1===step?'':'hidden'}>${html}</section>`).join('')}${history}${admin}${config}`,link('返回功法','martial:art:'+d.art.id));
    if(packagePending&&!pkg)schedulePreparationRefresh(state.view);
    if(canProduce&&pkg)videoKinds.forEach(([type])=>refreshQuote(id,type,'complete'));
    if(currentMedia.some(job=>inProgress(job.status)))scheduleMediaRefresh(state.view);
  }
  function selectMoveTab(){
    const step=M.moveStepOverride||M.moveStepCurrent||1;
    document.querySelectorAll('[data-mw-step-panel]').forEach(el=>el.hidden=Number(el.dataset.mwStepPanel)!==step);
    document.querySelectorAll('[data-mw-step]').forEach(el=>{const n=Number(el.dataset.mwStep);el.classList.toggle('active',n===M.moveStepCurrent);el.classList.toggle('viewing',n===step&&n!==M.moveStepCurrent);el.setAttribute('aria-current',n===M.moveStepCurrent?'step':'false')});
  }
  function refreshTotalQuote(){
    const box=$('#mw-complete-overview');if(!box)return;
    const quotes=videoKinds.map(([type])=>M.quote[type]).filter(Boolean);
    if(!quotes.length){box.textContent=M.task?.budget_unlimited?'本招不设任务预算上限；正在核对两类完整视频的费用预留…':'正在核对两类完整视频的费用与任务余额…';return}
    const priced=quotes.every(q=>q.estimated_cost!=null),total=priced?quotes.reduce((sum,q)=>sum+Number(q.estimated_cost),0):null;
    const unlimited=quotes.some(q=>q.budget_unlimited===true)||M.task?.budget_unlimited===true;
    const remaining=quotes.find(q=>q.remaining_budget!=null)?.remaining_budget;
    const budget=quotes.find(q=>q.task_budget!=null)?.task_budget;
    const ready=quotes.length===videoKinds.length;
    const count=quotes.reduce((sum,q)=>sum+(Array.isArray(q.segments)?q.segments.length:0),0);
    const booked=new Set((M.detail?.media||[]).filter(job=>job.current_context&&job.generation_mode==='complete'&&job.status!=='failed').map(job=>job.asset_type));
    const pending=videoKinds.map(([type])=>M.quote[type]&&!booked.has(type)?M.quote[type]:null).filter(Boolean);
    const pendingCost=ready&&pending.every(q=>q.estimated_cost!=null)?pending.reduce((sum,q)=>sum+Number(q.estimated_cost),0):null;
    box.innerHTML=`<div><span>两类完整视频预计预留</span><strong>${priced&&ready?money(total):'待两项预留估算完成'}</strong></div><div><span>本招任务预算</span><strong>${unlimited?'不设上限':money(budget)}</strong></div><div><span>${unlimited?'尚未发起的预留估算':'本招任务预算余额'}</span><strong>${unlimited?(pendingCost==null?'待核算':money(pendingCost)):money(remaining)}</strong></div><p>${unlimited?'本招不设任务预算上限。':''}${ready&&count?`预计 ${count} 段分别生成，合成讲解和跟练两条完整视频。`:''}${ready&&pendingCost!=null&&pending.length?` 尚未发起的 ${pending.length} 条视频预计还需预留 ${money(pendingCost)}。`:''}${!unlimited&&pendingCost!=null&&remaining!=null&&Number(remaining)<pendingCost?' 当前余额不足，请负责人调整预算。':''} 金额是提交前的预留估算，实际费用以供应商账单为准。</p>`;
  }
  async function refreshQuote(id,type,mode='complete'){
    const selector=`[data-martial-form="generate"][data-video-track="${type}"][data-generation-mode="${mode}"]`;
    const f=$(selector),box=$(`#mw-quote-${type}-${mode}`),button=f?.querySelector('button[type="submit"]');if(!f||!box||!button)return;
    const cache=mode==='complete'?M.quote:M.previewQuote;
    delete cache[type];button.disabled=true;box.textContent=mode==='complete'?'正在计算完整视频的分段与费用预留…':'正在读取 5 秒样片的费用预留…';
    if(mode==='complete')refreshTotalQuote();
    try{
      const model=f.elements.model.value,count=f.elements.candidate_count.value;
      const q=await api(`/api/martial/quote?move=${encodeURIComponent(id)}&model=${encodeURIComponent(model)}&count=${encodeURIComponent(count)}&generation_mode=${encodeURIComponent(mode)}&asset_type=${encodeURIComponent(type)}`);
      if(f!==$(selector)||f.elements.model.value!==model||f.elements.candidate_count.value!==count)return;
      cache[type]=q;
      const taskReady=['assigned','in_progress'].includes(M.task?.status),taskAssigned=M.task?.assignee_id===state.user.id;
      const missing=!!M.detail?.packages?.find(x=>x.status==='complete')?.result?.missing_inputs?.length;
      const active=mode==='complete'&&f.dataset.hasActive==='true';
      const unknown=mode==='complete'&&f.dataset.hasUnknown==='true',final=mode==='complete'&&f.dataset.hasFinal==='true';
      const reason=q.blocked?employeeText(q.block_reason||'当前制作条件未满足'):!taskReady?'当前任务状态暂不能生成':!taskAssigned?'这招由指定员工制作，当前账号不能发起生成':missing?'AI 准备仍缺少必要素材':active?'已有完整视频任务在制作中，请等待任务状态更新':unknown?'上次提交状态未知，后台核对前不能重复制作':final?'正式视频已完成；修改方案后才能制作新版本':null;
      const budgetText=q.budget_unlimited===true||M.task?.budget_unlimited===true?'本招不设任务预算上限。':`当前任务余额 ${money(q.remaining_budget)}。`;
      if(mode==='complete'){
        const segments=Array.isArray(q.segments)?q.segments:[];
        box.innerHTML=`<div class="mw-quote-head"><span>Seedance 2.5 · ${e(q.resolution||'480p')} · 预计 ${segments.length||'待定'} 段</span><strong>${q.estimated_cost==null?'费用预留待核算':`预计预留 ${money(q.estimated_cost)}`}</strong></div><ol class="mw-quote-segments">${segments.map((segment,index)=>`<li><span>第 ${e(Number.isFinite(Number(segment.index))?Number(segment.index)+1:index+1)} 段 · 真人 ${seconds(segment.source_start)}–${seconds(segment.source_end)} · 成片 ${e(segment.duration)} 秒</span><b>${money(segment.estimated_cost)}</b></li>`).join('')||'<li>分段计划待核验</li>'}</ol><small>各段生成秒数向上取整，合成后按计划裁至 ${seconds(q.target_duration)}。${budgetText}${e(reason||'可按以上分段开始完整制作。')}</small>`;
        button.textContent=active?'完整视频制作中':final?'正式视频已完成':f.dataset.hasCandidate==='true'?`再制作一版${videoLabel(type)}`:`制作${videoLabel(type)}完整视频`;
        button.disabled=!!reason||q.estimated_cost==null||!segments.length;
        refreshTotalQuote();
      }else{
        box.innerHTML=`<span>Seedance 2.5 · ${e(q.duration||5)} 秒 · 1 条试拍样片</span><strong>${q.estimated_cost==null?'费用预留待核算':`预计预留 ${money(q.estimated_cost)}`}</strong><small>${budgetText}${e(reason||'样片仅检查画面，不能替代完整视频。')}</small>`;
        button.disabled=!!reason||q.estimated_cost==null;
      }
    }catch(err){
      box.textContent=`暂不能读取${mode==='complete'?'完整视频':'样片'}报价：${err.message}`;
      button.disabled=true;
      if(mode==='complete')refreshTotalQuote();
    }
  }

  async function route(view){if(M.userId!==state.user.id){M.userId=state.user.id;invalidateCache();M.assetFilter=emptyAssetFilter();M.assetProjects=[];M.lastMove=null;M.lastArt=null}const parts=view.split(':');if(parts[1]==='art'&&parts[2])return artPage(parts[2]);if(parts[1]==='lesson'&&parts[2])return lessonPage(parts[2]);if(parts[1]==='master'&&parts[2])return masterPage(parts[2]);if(parts[1]==='move'&&parts[2])return moveWorkspacePage(parts[2]);if(parts[1]==='new-art')return newArtPage();if(parts[1]==='new-master')return newMasterPage();if(parts[1]==='new-move'&&parts[2])return newMovePage(parts[2]);return listPage(parts[1]||'overview')}
  window.martialWorkspace={nav,route,invalidateCache};

  function loadPreview(form,source,fileName=''){
    const preview=form.querySelector('.mw-local-preview'),player=preview?.querySelector('.mw-preview-video');if(!player)return;
    if(source instanceof File){if(M.previewUrl)URL.revokeObjectURL(M.previewUrl);M.previewUrl=URL.createObjectURL(source);player.src=M.previewUrl;fileName=source.name}else player.src=source;
    if(form.elements.start_time)form.elements.start_time.value='0';if(form.elements.end_time)form.elements.end_time.value='';
    form.querySelector('.mw-moment-list')?.replaceChildren();player.dataset.playRange='0';
    preview.classList.remove('hidden');preview.dataset.fileName=fileName;preview.querySelector('.mw-video-meta').textContent=`文件 ${fileName||'已登记素材'} · 正在读取实际时长和画面尺寸…`;
    player.load();
  }
  document.addEventListener('loadedmetadata',ev=>{
    const player=ev.target;if(!player.matches?.('.mw-preview-video'))return;
    const form=player.closest('[data-martial-form]'),preview=player.closest('.mw-local-preview');
    const duration=Number(player.duration);
    preview.querySelector('.mw-video-meta').textContent=`${preview.dataset.fileName||'已登记素材'} · 时长 ${Number.isFinite(duration)?seconds(duration):'读取中'} · ${player.videoWidth||'?'}×${player.videoHeight||'?'} · ${player.videoWidth>=player.videoHeight?'横屏':'竖屏'}`;
    if(form&&Number.isFinite(duration)&&duration>0){const end=form.elements.end_time;if(end&&!end.value)end.value=duration.toFixed(2)}
  },true);
  document.addEventListener('error',ev=>{
    const player=ev.target;if(player.matches?.('.mw-preview-video'))player.closest('.mw-local-preview').querySelector('.mw-video-meta').textContent='此浏览器无法解码该视频；请换 MP4/H.264 预览，服务器仍会检查原文件元数据。';
  },true);
  document.addEventListener('timeupdate',ev=>{const player=ev.target;if(player.matches?.('.mw-preview-video')&&player.dataset.playRange==='1'){const form=player.closest('.mw-panel')?.querySelector('[data-martial-form="motion-range"]')||player.closest('form'),end=Number(form?.elements.end_time?.value);if(Number.isFinite(end)&&player.currentTime>=end){player.pause();player.dataset.playRange='0'}}if(player.matches?.('.mw-plan-player')&&player.dataset.planRangeEnd&&player.currentTime>=Number(player.dataset.planRangeEnd)){player.pause();delete player.dataset.planRangeEnd}},true);
  document.addEventListener('timeupdate',ev=>{
    const player=ev.target;if(!player.matches?.('.mw-candidate .mw-ref'))return;
    const card=player.closest('.mw-candidate'),end=Number(card?.dataset.refEnd);
    if(Number.isFinite(end)&&end>0&&player.currentTime>=end){player.pause();card.querySelector('.mw-generated')?.pause()}
  },true);
  document.addEventListener('toggle',ev=>{
    const details=ev.target;
    if(details.matches?.('[data-preview-options]')&&details.open&&M.detail?.move?.id)
      refreshQuote(M.detail.move.id,details.dataset.previewOptions,'preview');
  },true);
  document.addEventListener('change',ev=>{
    if(ev.target.id==='mw-lesson-role'){filterLessonAssets();return}
    if(ev.target.matches('[name="move_mode"],[name="master_id"]')&&ev.target.closest('.mw-wizard,[data-martial-form="add-moves"]')){wizardMode();return}
    const form=ev.target.closest('[data-martial-form]');if(!form)return;
    if(form.dataset.martialForm==='video-plan'&&['source_start','source_end','target_duration'].includes(ev.target.name)){
      syncVideoPlan(form,ev.target.name!=='target_duration');return;
    }
    if(form.dataset.martialForm==='os-script'&&ev.target.name==='kind'){
      const script=M.multimodal?.scripts?.[ev.target.value]||{};
      for(const name of ['zh_text','en_text','app_text'])form.elements[name].value=script[name]||'';
      return;
    }
    if(form.dataset.martialForm==='budget'&&ev.target.name==='budget_mode'){const cap=form.querySelector('[data-budget-cap]'),unlimited=ev.target.value==='unlimited';cap.hidden=unlimited;cap.querySelector('input').disabled=unlimited;return}
    if(form.dataset.martialForm==='generate')refreshQuote(M.detail?.move?.id,form.dataset.videoTrack,form.dataset.generationMode);
    if(form.dataset.martialForm==='motion'&&ev.target.name==='file'&&ev.target.files[0])loadPreview(form,ev.target.files[0]);
    if(form.dataset.martialForm==='link-motion'&&ev.target.name==='asset_id')loadPreview(form,a(ev.target.value),ev.target.selectedOptions[0]?.textContent||'已登记素材');
  },true);
  document.addEventListener('input',ev=>{
    const form=ev.target.closest?.('[data-martial-form="video-plan"]');
    if(form&&['source_start','source_end','target_duration'].includes(ev.target.name))syncVideoPlan(form,ev.target.name!=='target_duration');
  },true);
  document.addEventListener('click',ev=>{
    const chip=ev.target.closest('[data-martial-filter-status]');
    if(chip){ev.preventDefault();M.filter=chip.dataset.martialFilterStatus;filterArtRows();return}
    const step=ev.target.closest('[data-mw-step]');
    if(step&&!step.disabled){ev.preventDefault();M.moveStepOverride=Number(step.dataset.mwStep);selectMoveTab();return}
  },true);
  document.addEventListener('keydown',ev=>{const card=ev.target.closest?.('.mw-asset-card[data-martial-action="asset-open"]');if(card&&(ev.key==='Enter'||ev.key===' ')){ev.preventDefault();card.click()}},true);
  document.addEventListener('click',async ev=>{
    const b=ev.target.closest('[data-martial-action]');if(!b)return;ev.preventDefault();ev.stopImmediatePropagation();
    const id=b.dataset.id,action=b.dataset.martialAction;
    if(b.disabled||b.getAttribute('aria-busy')==='true')return;
    b.disabled=true;b.setAttribute('aria-busy','true');b.classList.add('is-busy');
    try{let out;if(action==='refresh'){invalidateCache();go(state.view);return}
      if(action==='lesson-edit-shot'){
        const shot=M.lessonShots?.find(s=>s.shot_id===id);if(!shot)throw new Error('镜头已变化，请刷新');
        $('#mw-shot-id').value=shot.shot_id;$('#mw-shot-ordinal').value=shot.ordinal;
        $('#mw-shot-duration').value=shot.duration;$('#mw-shot-purpose').value=shot.purpose;
        $('#mw-shot-camera').value=shot.camera;$('#mw-shot-start').value=shot.start_state;$('#mw-shot-end').value=shot.end_state;
        $('#mw-shot-purpose').focus();return;
      }
      if(action==='lesson-save-shot'){
        const shot_id=$('#mw-shot-id').value,ordinal=$('#mw-shot-ordinal').value,duration=$('#mw-shot-duration').value;
        const purpose=$('#mw-shot-purpose').value,camera=$('#mw-shot-camera').value;
        const start_state=$('#mw-shot-start').value,end_state=$('#mw-shot-end').value;
        await api(`/api/martial/lessons/${id}/shot`,{shot_id,ordinal,duration,purpose,camera,start_state,end_state});
        notice('镜头设计已保存并保留版本');go(state.view);return;
      }
      if(action==='lesson-bind'){
        const role=$('#mw-lesson-role')?.value,asset_id=$('#mw-lesson-asset')?.value;
        if(!asset_id)throw new Error('请先选择素材');
        await api(`/api/martial/lessons/${id}/bind`,{role,asset_id,shot_id:$('#mw-lesson-shot')?.value||''});notice('已关联资产并保留版本');go(state.view);return;
      }
      if(action==='lesson-upload'){
        const role=$('#mw-lesson-role')?.value,file=$('#mw-lesson-file')?.files?.[0];
        if(!file)throw new Error('请先选择文件');if(file.size>40_000_000)throw new Error('单个素材请控制在 40MB 内');
        const encoded=await upload(file);
        await api(`/api/martial/lessons/${id}/upload`,{role,shot_id:$('#mw-lesson-shot')?.value||'',upload:{name:file.name,base64:encoded.base64}});
        notice('素材已保存到公司云端并关联本教学包');go(state.view);return;
      }
      if(action==='lesson-composition'){
        const notes=$('#mw-composition-notes')?.value.trim();
        if(!notes||notes.length<12)throw new Error('请记录具体合成方式和核对依据');
        await api(`/api/martial/lessons/${id}/composition`,{notes});
        notice('合成输入和成片版本已锁定');go(state.view);return;
      }
      if(action==='lesson-review-pass'||action==='lesson-review-fail'){
        const notes=$('#mw-lesson-review-notes')?.value.trim(),evidence=$('#mw-lesson-review-evidence')?.value.trim();
        if(!notes||!evidence)throw new Error('请填写实际视听范围和证据');
        await api(`/api/martial/lessons/${id}/review`,{stage:'audiovisual',verdict:action.endsWith('pass')?'pass':'fail',notes,evidence:[evidence]});
        notice('人工审片记录已保存');go(state.view);return;
      }
      if(action==='lesson-approve'||action==='lesson-publish'){
        await api(`/api/martial/lessons/${id}/${action==='lesson-approve'?'approve':'publish'}`,{});
        notice(action==='lesson-approve'?'教学包已批准并锁定版本':'教学包已发布');go(state.view);return;
      }
      if(action==='wizard-back'){M.artWizard.step=Math.max(1,M.artWizard.step-1);updateWizard();return}
      if(action==='wizard-next'){
        const root=document.querySelector('.mw-wizard'),step=M.artWizard.step;
        if(step===1){const basic=root.querySelector('[data-wizard-step="1"]');for(const name of ['chinese_name','category'])if(!basic.querySelector(`[name="${name}"]`)?.value.trim())throw new Error('请填写功法中文名称和类型')}
        if(step===2&&root.elements.master_id.value==='__new__'&&(!root.elements.teacher_chinese_name.value.trim()||!root.elements.teacher_species.value.trim()))throw new Error('新增老师需填写姓名和角色类型');
        if(step===3){const mode=root.elements.move_mode.value;if(mode==='single'&&!root.elements.single_chinese_name.value.trim())throw new Error('请填写招式中文名');if(mode==='batch'&&![...root.querySelectorAll('.mw-batch-row [name="chinese_name"]')].some(x=>x.value.trim()))throw new Error('请至少填写一条招式');if(mode==='import'&&!root.elements.moves_file.files[0])throw new Error('请选择 CSV 或 Excel 文件');if(mode==='copy'&&!root.elements.source_art_id.value)throw new Error('请选择来源功法')}
        M.artWizard.step++;updateWizard();return;
      }
      if(action==='wizard-add-row'){const table=b.closest('[data-move-mode]')?.querySelector('.mw-batch-table tbody'),row=table?.querySelector('tr');if(table&&row){const clone=row.cloneNode(true);clone.querySelectorAll('input,textarea').forEach(x=>x.value='');table.append(clone)}return}
      if(action==='wizard-remove-row'){const row=b.closest('.mw-batch-row'),table=row?.parentElement;if(table?.children.length>1)row.remove();else row?.querySelectorAll('input,textarea').forEach(x=>x.value='');return}
      if(action==='asset-category'){M.assetFilter.category=id;document.querySelectorAll('[data-martial-action="asset-category"]').forEach(chip=>{const active=chip.dataset.id===id;chip.classList.toggle('active',active);chip.setAttribute('aria-selected',String(active))});pendingAssetGrid();await listPage('assets');return}
      if(action==='asset-next'){
        const view=state.view,filterKey=new URLSearchParams(Object.entries(M.assetFilter).filter(([,value])=>value!=null&&String(value).trim())).toString();
        const params=new URLSearchParams(filterKey);
        params.set('offset',id);
        const result=await api('/api/asset-center?'+params);
        if(state.view!==view||new URLSearchParams(Object.entries(M.assetFilter).filter(([,value])=>value!=null&&String(value).trim())).toString()!==filterKey)return;
        const grid=document.querySelector('.mw-asset-grid'),more=document.querySelector('.mw-asset-more');
        if(!grid||!more)return;
        grid.querySelector('.empty')?.remove();
        grid.insertAdjacentHTML('beforeend',(result.assets||[]).map(assetCard).join(''));
        document.querySelector('.mw-asset-total').textContent=`${grid.querySelectorAll('.mw-asset-card').length} / ${result.total} 件`;
        more.innerHTML=result.next_offset!=null?button('加载更多素材','asset-next',result.next_offset):'';
        return;
      }
      if(action==='asset-open'){await openAsset(id);return}
      if(action==='asset-close'){document.querySelector('#mw-asset-dialog')?.close();return}
      if(action==='asset-reuse'){if(!M.assetDetail)throw new Error('请先选择素材');const target=M.assetDetail;const projectId=document.querySelector('#mw-asset-project')?.value||target.project||target.project_id||'wuxiang',masterId=document.querySelector('#mw-asset-master')?.value||'',moveId=document.querySelector('#mw-asset-move')?.value||'';if(id==='character_reference'&&!masterId)throw new Error('请先选择老师');if(id==='video_reference'&&!moveId)throw new Error('请先选择招式');const response=await api('/api/asset-center/'+encodeURIComponent(target.asset_id)+'/reuse',{action:id,project_id:projectId,art_id:target.art_id||null,master_id:masterId||null,move_id:moveId||null});document.querySelector('#mw-asset-dialog')?.close();if(response.task_id){notice(response.source_asset_id?'已创建新任务':'已创建新任务；原素材仅登记来源，需在原系统查看');go('task:'+response.task_id)}else if(id==='ai_creation'&&(response.source_asset_id||response.suggested_prompt)){window.aiStudio?.setContext(response.project_id,'',response.source_asset_id||'',response.suggested_prompt||'');go(target.type==='prompt'?'ai:write':'ai:analyze')}else{const message=response.status==='reference_only'?'已登记到项目；原文件尚不能直接用于生成':id==='save_project'?'已保存到项目':id==='video_reference'&&response.motion_ref_id?'已关联真人参考，请进入招式确认片段':id==='character_reference'&&response.status==='draft'?'角色参考已保存为待确认版本':id==='character_reference'?'已设为角色参考':'已登记参考关系';notice(message)}return}
      if(action==='edit-art'||action==='edit-teacher'){const editor=$('#mw-art-editor');if(editor){editor.open=true;editor.scrollIntoView({behavior:'smooth'});if(action==='edit-teacher')editor.querySelector('[name="master_id"]')?.focus()}return}
      if(action==='open-standard'){const editor=$('#mw-standard-editor');if(editor){editor.open=true;editor.scrollIntoView({behavior:'smooth'})}return}
      if(action==='open-motion'){M.moveStepOverride=2;selectMoveTab();return}
      if(action==='full-reference'){
        const ref=M.detail?.motions?.find(x=>x.status==='locked');
        if(!ref?.video_asset_id||!Number(ref.duration))throw new Error('找不到当前真人原片');
        const moments=(ref.key_moments||[]).filter(x=>x&&x.label&&x.label!=='无');
        const next=await api(`/api/martial/moves/${id}/link-motion`,{asset_id:ref.video_asset_id,start_time:0,end_time:Number(ref.duration),orientation:ref.orientation||'正面',start_pose:ref.start_pose||'',end_pose:ref.end_pose||'',notes:ref.notes||'',key_moments:moments,cover_asset_id:ref.cover_asset_id||null});
        const draft=next.motions?.find(x=>x.status==='draft');
        if(!draft)throw new Error('新版真人参考已建立，请到第 3 步完成确认');
        await api(`/api/martial/motions/${draft.id}/confirm`,{});
        notice('已将原片整段确认为新版真人参考；请按原片结构重新准备 AI 内容');go(state.view);return;
      }
      if(action==='jump-generate'){M.moveStepOverride=5;selectMoveTab();return}
      if(action==='show-config'){document.querySelector('#mw-config-dialog')?.showModal();return}
      if(action==='close-config'){document.querySelector('#mw-config-dialog')?.close();return}
      if(action==='copy-config'){const content=M.detail?.packages?.find(x=>x.status==='complete')?.result||{};const body=[['生成说明','move_summary'],['讲解演示 Prompt','teaching_prompt'],['跟教练跟练 Prompt','practice_prompt'],['禁止项','negative_constraints'],['使用素材','reference_mapping'],['模型建议','shot_camera_plan']].map(([label,key])=>`${label}\n${typeof content[key]==='string'?content[key]:JSON.stringify(content[key]||'',null,2)}`).join('\n\n');await navigator.clipboard.writeText(body);notice('两类视频生成内容已复制');return}
      if(action==='audit-history'){if(!isManager())throw new Error('仅管理者可查看技术历史');const history=await api(`/api/martial/moves/${id}/audit-history`);const target=document.querySelector('#mw-technical-history');target.innerHTML=`<pre class="mw-audit-json">${e(JSON.stringify(history,null,2))}</pre>`;return}
      if(action==='approve-art')out=await api(`/api/martial/arts/${id}/approve`,{});
      else if(action==='approve-move')out=await api(`/api/martial/moves/${id}/approve`,{});
      else if(action==='reject-move'){const reason=prompt('退回原因（员工可见）');if(!reason?.trim())return;out=await api(`/api/martial/moves/${id}/reject`,{reason:reason.trim()})}
      else if(action==='batch-approve'){const ids=(M.overview?.moves||[]).filter(x=>x.martial_art_id===id&&x.version?.status==='ready_for_approval'&&x.version.batch_eligible).map(x=>x.id);if(!ids.length)throw new Error('没有符合批量确认条件的招式');if(!confirm(`确认批量批准 ${ids.length} 条已核对且无疑问的招式？有修改或冲突的仍需逐条查看。`))return;out=await api(`/api/martial/arts/${id}/batch-approve`,{move_ids:ids})}
      else if(action==='approve-master')out=await api(`/api/martial/masters/${id}/approve`,{});
      else if(action==='confirm-motion'){if(!confirm('将这段真人动作确认为本招标准动作？'))return;out=await api(`/api/martial/motions/${id}/confirm`,{})}
      else if(action==='package'){const production_brief=$('#mw-production-brief')?.value.trim()||'';out=await api(`/api/martial/moves/${id}/package`,{production_brief,force:!!M.detail?.packages?.length})}
      else if(action==='start-task')out=await api(`/api/tasks/${id}/start`,{});
      else if(action==='select'){const reason=prompt('为什么选用这个候选？请写明动作与角色表现。');if(!reason)return;out=await api(`/api/martial/media/${id}/select`,{reason})}
      else if(action==='final')out=await api(`/api/martial/media/${id}/final`,{});
      else if(action==='revise'){const type=M.detail?.media?.find(x=>x.id===id)?.asset_type,f=$(`[data-martial-form="generate"][data-video-track="${type}"][data-generation-mode="complete"]`);if(!f)throw new Error('请先完成这一类视频的 AI 准备');f.elements.revision_of.value=id;M.moveStepOverride=5;selectMoveTab();f.scrollIntoView({behavior:'smooth'});notice(`已带入${videoLabel(type)}的返修要求，请核对时长与预计费用`);return}
      else if(['plan-start','plan-end','plan-play','plan-use-length'].includes(action)){
        const form=b.closest('[data-martial-form="video-plan"]'),player=b.closest('.mw-panel')?.querySelector('.mw-plan-player');if(!form||!player)throw new Error('请先打开真人参考视频');
        if(action==='plan-start'){form.elements.source_start.value=player.currentTime.toFixed(2);syncVideoPlan(form,true)}
        if(action==='plan-end'){form.elements.source_end.value=player.currentTime.toFixed(2);syncVideoPlan(form,true)}
        if(action==='plan-play'){const start=Number(form.elements.source_start.value),end=Number(form.elements.source_end.value);if(!Number.isFinite(start)||!Number.isFinite(end)||end<=start)throw new Error('请先设置有效片段');player.currentTime=start;player.dataset.planRangeEnd=String(end);await player.play()}
        if(action==='plan-use-length'){const start=Number.parseFloat(form.elements.source_start.value),end=Number.parseFloat(form.elements.source_end.value);if(!Number.isFinite(start)||!Number.isFinite(end)||end<=start)throw new Error('请先设置有效片段');form.elements.target_duration.value=(Math.round((end-start)*100)/100).toFixed(2);syncVideoPlan(form)}
        return;
      }
      else if(action==='assign-unmapped'){const [itemId,masterId]=id.split(':');out=await api(`/api/martial/unmapped-assets/${itemId}/assign`,{master_id:masterId})}
      else if(action==='ignore-unmapped'){out=await api(`/api/martial/unmapped-assets/${id}/ignore`,{})}
      else if(['range-start','range-end','range-play','add-moment','remove-moment'].includes(action)){
        const form=b.closest('form'),player=b.closest('.mw-panel')?.querySelector('.mw-preview-video');if(!form||!player)throw new Error('请先选择可预览的视频');
        if(action==='range-start')form.elements.start_time.value=player.currentTime.toFixed(2);
        if(action==='range-end')form.elements.end_time.value=player.currentTime.toFixed(2);
        if(action==='range-play'){const start=Number(form.elements.start_time.value),end=Number(form.elements.end_time.value);if(!Number.isFinite(start)||!Number.isFinite(end)||end<=start)throw new Error('工作区间无效');player.currentTime=start;player.dataset.playRange='1';await player.play()}
        if(action==='add-moment'){const label=form.elements.moment_label.value.trim();if(!label)throw new Error('请先填写关键动作描述');const time=Number(player.currentTime.toFixed(2));if(time<Number(form.elements.start_time.value)||time>Number(form.elements.end_time.value))throw new Error('关键点须落在工作区间内');form.querySelector('.mw-moment-list').insertAdjacentHTML('beforeend',`<div class="mw-moment-item" data-time="${time}"><span>${seconds(time)} · ${e(label)}</span><input type="hidden" data-moment-label value="${e(label)}">${button('移除','remove-moment')}</div>`);form.elements.moment_label.value=''}
        if(action==='remove-moment')b.closest('.mw-moment-item')?.remove();return;
      }
      else if(action==='add-issue'){const form=b.closest('form'),card=b.closest('.mw-candidate'),candidate=card?.querySelector('.mw-generated');if(!form||!candidate)throw new Error('请在候选播放器中定位问题');const start=Number(candidate.currentTime.toFixed(2)),end=Math.min(Number(candidate.duration)||start+1,start+1);form.querySelector('.mw-issue-list').insertAdjacentHTML('beforeend',`<div class="mw-issue-item"><label>开始秒数<input class="mw-issue-start" type="number" min="0" step="0.01" value="${start}" required></label><label>结束秒数<input class="mw-issue-end" type="number" min="0.01" step="0.01" value="${end.toFixed(2)}" required></label><label>问题<input class="mw-issue-text" required placeholder="例如：右翼路径偏高"></label><label>严重度<select class="mw-issue-severity"><option value="minor">轻微</option><option value="major">明显</option><option value="critical">严重</option></select></label>${button('移除区间','remove-issue')}</div>`);return}
      else if(action==='remove-issue'){b.closest('.mw-issue-item')?.remove();return}
      else if(action==='sync'||action==='seek'||action==='sync-start'){const card=b.closest('.mw-candidate'),left=card?.querySelector('.mw-ref'),right=card?.querySelector('.mw-generated');if(!left||!right)return;const start=Number(card.dataset.refStart)||0;if(action==='sync-start'){left.currentTime=start;right.currentTime=0;await Promise.allSettled([left.play(),right.play()])}else if(action==='seek'){right.currentTime=Math.max(0,left.currentTime-start)}else if(left.paused||right.paused){right.currentTime=Math.max(0,left.currentTime-start);await Promise.allSettled([left.play(),right.play()])}else{left.pause();right.pause()}return}
      notice('已保存');go(state.view);
    }catch(err){notice(err.message,true)}finally{b.disabled=false;b.removeAttribute('aria-busy');b.classList.remove('is-busy')}
  },true);
  function tableMoves(root){
    const rows=[...root.querySelectorAll('.mw-batch-row')].map(row=>Object.fromEntries([...row.querySelectorAll('[name]')].map(input=>[input.name,input.value.trim()]))).filter(x=>Object.values(x).some(Boolean));
    if(!rows.length||rows.some(x=>!x.chinese_name))throw new Error('批量新增需填写每条招式的中文名');
    return rows;
  }
  async function addMoves(root,artId){
    const d=fields(root),mode=d.move_mode,source_ref='工作台新增';
    if(mode==='single'){
      if(!d.single_chinese_name?.trim())throw new Error('请填写招式中文名');
      const payload={martial_art_id:artId,order:d.single_order?Number(d.single_order):undefined,source_ref};
      for(const key of ['chinese_name','english_name','chinese_action','english_action','chinese_coaching','english_coaching'])payload[key]=d['single_'+key]||'';
      return api('/api/martial/moves',payload);
    }
    if(mode==='batch')return api(`/api/martial/arts/${artId}/moves/batch`,{moves:tableMoves(root),source_ref});
    if(mode==='import'){
      const file=root.elements.moves_file.files[0];if(!file)throw new Error('请选择 CSV 或 Excel 文件');if(file.size>3_000_000)throw new Error('导入文件不能超过 3MB');
      const encoded=await upload(file);return api(`/api/martial/arts/${artId}/moves/import`,{name:file.name,base64:encoded.base64,source_ref});
    }
    if(mode==='copy'){
      if(!d.source_art_id)throw new Error('请选择来源功法');return api(`/api/martial/arts/${artId}/moves/copy`,{source_art_id:d.source_art_id,source_ref});
    }
    throw new Error('请选择添加招式的方式');
  }
  function captureCover(form){
    const player=form.querySelector('.mw-preview-video');if(!player||player.readyState<2||!player.videoWidth)return null;
    try{const canvas=document.createElement('canvas'),scale=Math.min(1,480/player.videoWidth);canvas.width=Math.round(player.videoWidth*scale);canvas.height=Math.round(player.videoHeight*scale);canvas.getContext('2d').drawImage(player,0,0,canvas.width,canvas.height);return canvas.toDataURL('image/jpeg',0.78).split(',')[1]}catch{return null}
  }
  async function captureMotionCover(form){
    const player=form.querySelector('.mw-preview-video');if(!player)return null;
    if(player.readyState<2)await new Promise(resolve=>{
      let settled=false,timer;
      const done=()=>{if(settled)return;settled=true;clearTimeout(timer);player.removeEventListener('loadeddata',done);resolve()};
      player.addEventListener('loadeddata',done,{once:true});timer=setTimeout(done,2500);
      player.preload='auto';try{player.load()}catch{done()}
    });
    return captureCover(form);
  }
  document.addEventListener('submit',async ev=>{
    const filter=ev.target.closest('[data-martial-filter="assets"]');if(filter){ev.preventDefault();ev.stopImmediatePropagation();M.assetFilter={...M.assetFilter,...fields(filter)};pendingAssetGrid();const button=filter.querySelector('button[type="submit"]');if(button){button.disabled=true;button.classList.add('is-busy')}listPage('assets').catch(err=>notice(err.message,true)).finally(()=>{if(button){button.disabled=false;button.classList.remove('is-busy')}});return}
    const f=ev.target.closest('[data-martial-form]');if(!f)return;ev.preventDefault();ev.stopImmediatePropagation();
    const kind=f.dataset.martialForm,id=f.dataset.id,b=f.querySelector('button[type="submit"]')||[...f.querySelectorAll('button')].reverse().find(x=>!x.dataset.martialAction);if(b){b.disabled=true;b.setAttribute('aria-busy','true');b.classList.add('is-busy')}
    try{let d=fields(f),path='';
      if(kind==='legacy-identity'){
        if(!d.work_user_id||!d.legacy_username||!d.project_id)throw new Error('请先核对旧账号、项目与员工');
        await api('/api/asset-center/admin/legacy-identity',{...d,active:true});
        notice('旧账号映射已登记；员工还需登录旧 AI 中心才能预览私有素材');
        document.querySelector('#mw-asset-dialog')?.close();go(state.view);return;
      }
      if(kind==='create-art'){
        if(M.artWizard.step!==4)throw new Error('请先完成前面的步骤');
        const basic=f.querySelector('[data-wizard-step="1"]'),value=name=>basic.querySelector(`[name="${name}"]`)?.value.trim()||'';
        const artData={chinese_name:value('chinese_name'),english_name:value('english_name'),category:value('category'),volume:value('volume')||'第一卷',description:value('description'),style_traits:value('style_traits').split('\n').map(x=>x.trim()).filter(Boolean),order:Number(value('order')||0),status:'active',source_ref:'工作台新增'};
        if(!artData.chinese_name||!artData.category)throw new Error('请填写功法中文名称和类型');
        let masterId=f.elements.master_id.value;
        if(masterId==='__new__'){
          if(!M.artWizard.createdMasterId){
            const chinese=f.elements.teacher_chinese_name.value.trim(),english=f.elements.teacher_english_name.value.trim();
            const created=await api('/api/martial/masters',{name:english||chinese,chinese_name:chinese,english_name:english,species:f.elements.teacher_species.value.trim(),profile:f.elements.teacher_profile.value.trim(),teaching_style:f.elements.teacher_teaching_style.value.trim(),martial_art_ids:[],source_ref:'工作台新增'});
            M.artWizard.createdMasterId=(created.master||created).id;
            if(!M.artWizard.createdMasterId)throw new Error('老师已创建，但无法确认编号；请到老师库核对');
            for(const [field,input] of [['portrait',f.elements.teacher_portrait],['other_angle',f.elements.teacher_other_angles],['voice_preview',f.elements.teacher_voice_preview]])for(const file of [...(input?.files||[])])await api(`/api/martial/masters/${M.artWizard.createdMasterId}/visual`,{field,upload:await martialUpload(file),source_ref:'工作台新增'});
          }
          masterId=M.artWizard.createdMasterId;
        }
        artData.master_id=masterId||'';
        if(!M.artWizard.createdArtId){const result=await api('/api/martial/arts',artData),created=result.art||result;M.artWizard.createdArtId=created.id;if(!created.id)throw new Error('功法已提交，但无法确认编号；请到功法管理核对')}
        try{await addMoves(f,M.artWizard.createdArtId)}catch(err){throw new Error(`功法已创建，添加招式未完成：${err.message}。可修正后在本页重试。`)}
        notice('功法和招式已创建');go('martial:art:'+M.artWizard.createdArtId);return;
      }
      if(kind==='add-moves'){const result=await addMoves(f,id);notice(`已添加 ${result.count||1} 条招式`);go('martial:art:'+id);return}
      if(kind==='create-move'){d.order=Number(d.order);d.source_ref='工作台新增';const result=await api('/api/martial/moves',d);const created=result.move||result;if(!created.id)throw new Error('招式已提交，但响应没有招式编号；请返回功法核对');notice('招式已创建');go('martial:move:'+created.id);return}
      if(kind==='create-master'){
        d.martial_art_ids=[...f.elements.martial_art_ids.selectedOptions].map(x=>x.value);d.forbidden_changes=(d.forbidden_changes||'').split('\n').map(x=>x.trim()).filter(Boolean);d.name=d.english_name||d.chinese_name;d.source_ref='工作台新增';
        const fieldsToUpload=['portrait','front_view','side_view','back_view','other_angle','turnaround','costume','digital_model','voice_preview'];for(const key of fieldsToUpload)delete d[key];
        const result=await api('/api/martial/masters',d),created=result.master||result;if(!created.id)throw new Error('老师已提交，但响应没有老师编号；请返回老师库核对');
        const failed=[];for(const key of fieldsToUpload){for(const file of [...(f.elements[key]?.files||[])]){try{await api(`/api/martial/masters/${created.id}/visual`,{field:key,upload:await martialUpload(file),source_ref:'工作台新增'})}catch(err){failed.push(`${file.name}：${err.message}`)}}}
        notice(failed.length?`老师已创建；${failed.length} 个素材上传失败，请在老师详情补传`:'老师已创建，可进入角色资料页补充更多素材',!!failed.length);go('martial:master:'+created.id);return;
      }
      if(kind==='master-visual'){const file=f.elements.file.files[0];if(!file)throw new Error('请选择素材文件');await api(`/api/martial/masters/${id}/visual`,{field:d.field,upload:await martialUpload(file),source_ref:'工作台上传'});notice('老师素材已保存');go(state.view);return}
      if(kind==='art-mm-asset'){
        await api(`/api/martial/arts/${id}/asset`,{...d,source_ref:'员工维护功法素材'});
        notice('功法级素材已保存');go(state.view);return;
      }
      if(kind==='art-worldview'){
        await api(`/api/martial/arts/${id}/worldview`,{...d,source_ref:'员工维护世界观说明'});
        notice('世界观说明新版本已保存');go(state.view);return;
      }
      if(kind==='master-voice-persona'){
        await api(`/api/martial/masters/${id}/voice-persona`,{...d,source_ref:'员工维护老师声线'});
        notice('老师声线设定新版本已保存');go(state.view);return;
      }
      if(kind==='master-intro-audio'){
        await api(`/api/martial/masters/${id}/intro-audio`,{...d,source_ref:'员工维护老师介绍语音'});
        notice('老师介绍语音已保存');go(state.view);return;
      }
      if(kind==='move-bgm'){
        await api(`/api/martial/moves/${id}/asset`,{role:'training_bgm',asset_id:d.asset_id,source_ref:'员工维护招式 BGM'});
        notice(d.asset_id?'本招专用 BGM 已保存':'已改回继承功法 BGM');go(state.view);return;
      }
      if(kind==='move-tts'){
        await api(`/api/martial/moves/${id}/tts`,{...d,source_ref:'员工关联 OS 语音'});
        notice('OS 语音已关联到当前文本版本');go(state.view);return;
      }
      if(kind==='os-script'){
        await api(`/api/martial/moves/${id}/os-script`,{...d,source_ref:'员工维护 OS 文本'});
        notice('OS 文本新版本已保存；对应语音须按新文本重新制作');go(state.view);return;
      }
      if(kind==='standard'){
        const result=await api(`/api/martial/moves/${id}/standard`,{...d,source_ref:'员工编辑动作标准'});
        notice(result.needs_review?'已保存新版本；与正式作品可能冲突，需要负责人确认':result.changed?'动作标准新版本已生效':'内容没有变化');
        M.moveStepOverride=null;go(state.view);return;
      }
      if(kind==='motion'){
        const file=f.elements.file.files[0];if(!file)throw new Error('请选择 MP4 或 MOV 视频');
        const coverPromise=captureMotionCover(f),result=await uploadMotionFile(id,file,f);
        const cover=await coverPromise,draft=(result.motions||[]).find(ref=>ref.status==='draft');
        let coverWarning=false;
        if(cover&&draft?.id){try{await api(`/api/martial/motions/${draft.id}/cover`,{cover_base64:cover})}catch{coverWarning=true}}
        notice(coverWarning?'真人动作已保存，封面暂未生成；请继续确认参考片段':'真人动作已保存，请确认参考片段');M.moveStepOverride=null;go(state.view);return;
      }
      if(kind==='motion-range'){
        d.key_moments=[...f.querySelectorAll('.mw-moment-item')].map(row=>({time:Number(row.dataset.time),label:row.querySelector('[data-moment-label]').value})).sort((left,right)=>left.time-right.time);
        delete d.moment_label;
        if(d.start_time!=='')d.start_time=Number(d.start_time);else delete d.start_time;
        if(d.end_time!=='')d.end_time=Number(d.end_time);else delete d.end_time;
        if(d.start_time!=null&&d.end_time!=null&&d.end_time<=d.start_time)throw new Error('结束时间须晚于开始时间');
        await api(`/api/martial/motions/${id}/range`,d);
        await api(`/api/martial/motions/${id}/confirm`,{});
        notice('已确认为标准动作');M.moveStepOverride=null;go(state.view);return;
      }
      if(kind==='video-plan'){
        for(const key of ['source_start','source_end','target_duration'])d[key]=Number(d[key]);
        const ref=M.detail?.motions?.find(x=>x.status==='locked');
        if(!ref)throw new Error('请先确认真人标准动作');
        if(!Number.isFinite(d.source_start)||!Number.isFinite(d.source_end)||!Number.isFinite(d.target_duration)||d.source_start<Number(ref.start_time||0)||d.source_end>Number(ref.end_time??ref.duration)||d.source_end<=d.source_start||d.target_duration<=0)throw new Error('片段须在已确认的真人参考范围内，目标时长须大于零');
        const result=await api(`/api/martial/moves/${id}/video-plan`,d);
        const bothReady=videoKinds.every(([type])=>result.video_plans?.[type]?.motion_ref_id===ref.id);
        notice(bothReady?'两类视频方案均已保存，可以分别让 AI 准备':`${videoLabel(d.asset_type)}方案已保存；请继续设置另一类视频`);
        M.moveStepOverride=bothReady?null:3;go(state.view);return;
      }
      if(kind==='art'){d.style_traits=d.style_traits.split('\n').map(x=>x.trim()).filter(Boolean);path=`/api/martial/arts/${id}/edit`}
      else if(kind==='move'||kind==='edit-move'){if(kind==='move')d.order=Number(d.order);path='/api/martial/moves'}
      else if(kind==='submit-move'){d.review_flags=[...f.querySelectorAll('input[type="checkbox"]:checked')].map(x=>x.value);delete d.flag_question;delete d.flag_conflict;path=`/api/martial/moves/${id}/submit`}
      else if(kind==='master-asset'){d.upload=await martialUpload(f.elements.file.files[0]);delete d.file;path=`/api/martial/masters/${id}/asset`}
      else if(kind==='master-attach')path=`/api/martial/masters/${id}/attach`;
      else if(kind==='master-profile'){d.forbidden_changes=d.forbidden_changes.split('\n').map(x=>x.trim()).filter(Boolean);path=`/api/martial/masters/${id}/profile`}
      else if(kind==='link-motion'){
        d.key_moments=[...f.querySelectorAll('.mw-moment-item')].map(row=>({time:Number(row.dataset.time),label:row.querySelector('[data-moment-label]').value})).sort((left,right)=>left.time-right.time);
        delete d.moment_label;d.start_time=Number(d.start_time||0);if(d.end_time)d.end_time=Number(d.end_time);else delete d.end_time;
        path=`/api/martial/moves/${id}/link-motion`;
      }
      else if(kind==='budget'){d.budget_cap=d.budget_mode==='unlimited'?'unlimited':Number(d.budget_cap);delete d.budget_mode;path=`/api/martial/moves/${id}/budget`}
      else if(kind==='generate'){const quoteCache=d.generation_mode==='preview'?M.previewQuote:M.quote,q=quoteCache[d.asset_type];if(!q||q.blocked||q.estimated_cost==null)throw new Error(q?.block_reason||'这一类视频的费用或预算尚未确认');if(!M.detail?.package_current||M.detail?.packages?.[0]?.result?.missing_inputs?.length)throw new Error('AI 还缺少必要素材或视频方案已变更');if(M.task?.status==='assigned'){await api(`/api/tasks/${M.task.id}/start`,{});M.task.status='in_progress'}d.candidate_count=Number(d.candidate_count);path=`/api/martial/moves/${id}/generate`}
      else if(kind==='qc'){
        d.major_dispute=!!f.elements.major_dispute.checked;
        d.checks=Object.fromEntries(QC_CHECKS.map(([key])=>[key,d['check_'+key]]));for(const [key] of QC_CHECKS)delete d['check_'+key];
        d.issue_ranges=[...f.querySelectorAll('.mw-issue-item')].map(row=>({start:Number(row.querySelector('.mw-issue-start').value),end:Number(row.querySelector('.mw-issue-end').value),issue:row.querySelector('.mw-issue-text').value.trim(),severity:row.querySelector('.mw-issue-severity').value}));
        if(d.issue_ranges.some(x=>!Number.isFinite(x.start)||!Number.isFinite(x.end)||x.end<=x.start||!x.issue))throw new Error('问题区间需填写有效起止时间和具体问题');
        path=`/api/martial/media/${id}/qc`;
      }
      else throw new Error('未知表单');
      await api(path,d);notice(kind==='generate'?(d.generation_mode==='complete'?'完整视频已进入后台制作；本页会更新进度':'5 秒试拍样片已进入后台制作'):kind==='budget'?'本招预算方式已保存；请重新核对完整视频的费用预留':'已保存');
      if(kind==='move')go('martial:art:'+d.martial_art_id);else go(state.view);
    }catch(err){notice(err.message,true)}finally{if(b){b.disabled=false;b.removeAttribute('aria-busy');b.classList.remove('is-busy')}}
  },true);
})();
