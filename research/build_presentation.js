const pptxgen = require('/Users/andye/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/pptxgenjs');

const pptx = new pptxgen();
pptx.layout = 'LAYOUT_WIDE';
pptx.author = '代码逆向 Agent';
pptx.company = '代码逆向 Agent';
pptx.subject = 'libuv / Redis 代码逆向 SubAgent';
pptx.title = 'libuv / Redis 代码逆向 SubAgent 课题方案';
pptx.lang = 'zh-CN';
pptx.theme = {
  headFontFace: 'Helvetica Neue',
  bodyFontFace: 'PingFang SC',
  lang: 'zh-CN'
};
pptx.defineLayout({ name: 'WIDE_CUSTOM', width: 13.333, height: 7.5 });
pptx.layout = 'WIDE_CUSTOM';

const C = {
  ink: '14213D', muted: '516174', line: 'D9E2EC', bg: 'F7FAFC', white: 'FFFFFF',
  blue: '1E5EFF', cyan: '00A7A7', amber: 'D99000', red: 'C2413B', paleBlue: 'EAF1FF',
  paleCyan: 'E8F8F6', paleAmber: 'FFF4D6', paleRed: 'FDECEC', dark: '0B1830'
};
const W = 13.333, H = 7.5;
const margin = 0.55;
function tx(slide, text, x, y, w, h, opts={}) {
  slide.addText(text, { x, y, w, h, margin: 0, breakLine: false, fit: 'shrink',
    fontFace: opts.fontFace || 'PingFang SC', fontSize: opts.fontSize || 16,
    color: opts.color || C.ink, bold: opts.bold || false, italic: opts.italic || false,
    valign: opts.valign || 'mid', align: opts.align || 'left', paraSpaceAfterPt: opts.paraSpaceAfterPt || 0,
    bullet: opts.bullet, breakLine: false, ...opts });
}
function rect(slide, x, y, w, h, fill, line=C.line, radius=0.08) {
  slide.addShape(pptx.ShapeType.roundRect, { x, y, w, h, rectRadius: radius,
    fill: { color: fill }, line: { color: line, width: 0.8 } });
}
function line(slide, x1, y1, x2, y2, color=C.line, width=1.2, dash='solid', end='none') {
  slide.addShape(pptx.ShapeType.line, { x: x1, y: y1, w: x2-x1, h: y2-y1,
    line: { color, width, dash, endArrowType: end } });
}
function dot(slide, x, y, r, fill) {
  slide.addShape(pptx.ShapeType.ellipse, { x, y, w:r, h:r, fill:{color:fill}, line:{color:fill} });
}
function header(slide, kicker, title, page, accent=C.blue) {
  slide.background = { color: C.bg };
  tx(slide, kicker.toUpperCase(), margin, 0.28, 4.7, 0.24, { fontSize: 9, bold: true, color: accent, charSpacing: 1.5 });
  tx(slide, title, margin, 0.57, 11.4, 0.52, { fontSize: 25, bold: true, color:C.ink });
  line(slide, margin, 1.18, W-margin, 1.18, C.line, 1);
  tx(slide, String(page).padStart(2,'0'), 12.3, 0.35, 0.45, 0.25, { fontSize: 10, bold: true, color:C.muted, align:'right' });
}
function footer(slide, text='代码逆向 Agent · 研究版 Demo') {
  tx(slide, text, margin, 7.18, 8, 0.17, { fontSize: 8.5, color:C.muted });
}
function pill(slide, label, x, y, w, fill, color=C.ink) {
  rect(slide,x,y,w,0.28,fill,fill,0.14); tx(slide,label,x+0.08,y+0.02,w-0.16,0.22,{fontSize:9.5,bold:true,color,align:'center'});
}
function note(slide, source) { slide.addNotes(`[Sources]\n${source}`); }
function bulletList(slide, items, x, y, w, lineH=0.42, color=C.ink, size=14) {
  items.forEach((item, i) => {
    dot(slide, x, y+i*lineH+0.13, 0.08, C.blue);
    tx(slide, item, x+0.18, y+i*lineH, w-0.18, lineH, {fontSize:size, color});
  });
}
function stat(slide, value, label, x, y, w, color=C.blue) {
  rect(slide,x,y,w,0.96,C.white,C.line,0.06);
  tx(slide,value,x+0.13,y+0.14,w-0.26,0.34,{fontSize:24,bold:true,color});
  tx(slide,label,x+0.13,y+0.58,w-0.26,0.2,{fontSize:10,color:C.muted});
}
function arrow(slide, x1,y1,x2,y2,color=C.blue) { line(slide,x1,y1,x2,y2,color,1.5,'solid','triangle'); }

// 1
{
  const s=pptx.addSlide(); s.background={color:C.dark};
  tx(s,'代码逆向 Agent',0.7,0.72,6.6,0.35,{fontSize:16,bold:true,color:'8FB2FF',charSpacing:1});
  tx(s,'libuv / Redis\n源码穿刺与 SubAgent 设计',0.68,1.42,8.5,1.42,{fontSize:31,bold:true,color:C.white,breakLine:true,fit:'shrink'});
  tx(s,'从异步回调链、函数指针、编译宏，到可追溯的 JSON 证据接口',0.72,3.18,7.8,0.36,{fontSize:16,color:'D7E2F5'});
  rect(s,0.72,4.28,5.1,1.28,'10244A','254C92',0.08);
  tx(s,'两条主线',0.98,4.55,1.5,0.23,{fontSize:11,bold:true,color:'8FB2FF'});
  tx(s,'01  双仓源码结构与关键调用链\n02  SubAgent 架构、Evidence IR 与接口规范',0.98,4.84,4.45,0.52,{fontSize:14,color:C.white,breakLine:true,fit:'shrink'});
  tx(s,'课题设计方案 · Demo 验证版 · 2026.08',0.72,6.88,5.4,0.2,{fontSize:10,color:'8DA3C4'});
  // abstract graph motif
  const pts=[[9.0,1.45],[10.7,1.08],[11.85,2.2],[9.75,3.1],[11.35,4.2],[8.65,4.85],[10.25,5.85],[12.0,6.15]];
  [[0,1],[1,2],[0,3],[3,4],[3,5],[5,6],[4,7],[6,7],[2,4]].forEach(([a,b])=>line(s,pts[a][0],pts[a][1],pts[b][0],pts[b][1],'3D68C7',1.4,'solid','triangle'));
  pts.forEach((p,i)=>{dot(s,p[0]-0.09,p[1]-0.09,0.18,i%3===0?'49D8D1':i%3===1?'6B96FF':'F3BB54');});
  note(s,'research/dual-target-source-audit.md; research/subagent-design.md');
}

// 2
{
  const s=pptx.addSlide(); header(s,'Scope','两项核心工作，统一到一条证据链',2); footer(s);
  rect(s,0.65,1.55,5.75,4.85,C.white,C.line,0.06); rect(s,6.93,1.55,5.75,4.85,C.white,C.line,0.06);
  pill(s,'01 · 源码剖析',0.92,1.82,1.45,C.paleBlue,C.blue);
  tx(s,'把“函数名相似”变成\n可回溯的生命周期事实',0.92,2.28,4.8,0.72,{fontSize:22,bold:true,breakLine:true});
  bulletList(s,['模块分层与固定快照指标','注册 → 调度 → 执行三段回调链','函数指针、整数 ID、宏展开、平台分支','每条关系绑定 file:line + resolution'],0.95,3.35,4.9,0.55,C.ink,14);
  pill(s,'02 · SubAgent 机构',7.2,1.82,1.72,C.paleCyan,C.cyan);
  tx(s,'让模型只负责解释，\n让工具负责事实',7.2,2.28,4.8,0.72,{fontSize:22,bold:true,breakLine:true});
  bulletList(s,['Evidence IR：节点、边、证据、配置','7 个受控工具与成对 JSON Schema','图切片、自然语言问答、引用与不确定性','Demo 可运行，生产层可替换 Clang / Graph Store'],7.23,3.35,4.9,0.55,C.ink,14);
  line(s,6.66,2.05,6.66,5.95,C.line,1);
  tx(s,'输入：源码 + 构建配置',0.9,6.65,3.5,0.22,{fontSize:11,bold:true,color:C.muted});
  tx(s,'输出：图谱 + 路径 + 可审计回答',8.7,6.65,3.8,0.22,{fontSize:11,bold:true,color:C.muted,align:'right'});
  note(s,'research/subagent-design.md §1-4; docs/libuv-redis-architecture.md §1-3');
}

// 3
{
  const s=pptx.addSlide(); header(s,'Evidence status','libuv 与 Redis：平行目标，证据等级不混淆',3); footer(s);
  const y=1.62; stat(s,'e43e3d8','libuv 固定 commit',0.72,y,2.3,C.blue); stat(s,'475','编译单元',3.2,y,2.3,C.blue); stat(s,'pending','Redis 官方快照',5.68,y,2.3,C.amber); stat(s,'synthetic','当前 Redis fixture',8.16,y,2.3,C.amber); stat(s,'1.0','JSON 契约版本',10.64,y,2.0,C.cyan);
  rect(s,0.72,3.05,5.55,2.7,C.paleBlue,C.paleBlue,0.06);
  tx(s,'已验证 · repository_snapshot',0.98,3.34,4.7,0.26,{fontSize:15,bold:true,color:C.blue});
  bulletList(s,['104 个 src C/H 文件','1,571 个函数 · 3,093 条关系','1,827 个宏 / 条件 · 21 个回调字段','黄金链：uv_run → uv__io_poll → uv__io_cb → uv__stream_io → uv__read'],1.0,3.83,4.8,0.45,C.ink,12.5);
  rect(s,6.55,3.05,6.05,2.7,C.paleAmber,C.paleAmber,0.06);
  tx(s,'待补 · pending_snapshot',6.82,3.34,4.8,0.26,{fontSize:15,bold:true,color:C.amber});
  bulletList(s,['不能伪造 Redis 行号或 commit','当前 fixture 只验证 ae 领域规则闭环','下载授权后固定 commit，再补五条黄金链','Redis 作为被分析仓库 ≠ 部署层队列依赖'],6.84,3.83,5.2,0.45,C.ink,12.5);
  tx(s,'证据规则：observed 必须回到源码；inferred 必须说明推断；unresolved 不得升级为事实。',0.75,6.35,11.8,0.28,{fontSize:13,bold:true,color:C.ink,align:'center'});
  note(s,'research/dual-target-source-audit.md §1; validation-report.json; research/collect_source_metrics.py');
}

// 4
{
  const s=pptx.addSlide(); header(s,'Libuv structure','libuv：从公共 ABI 到平台 poll 后端',4); footer(s);
  const layers=[['公共 API / ABI','include/uv.h · callback typedef · UV_*_FIELDS',C.paleBlue,C.blue],['公共实现','uv-common.c · timer.c · threadpool.c',C.paleCyan,C.cyan],['Unix 核心','unix/core.c · loop.c · stream.c',C.paleBlue,C.blue],['平台后端','linux.c · kqueue.c · posix-poll.c · win/*',C.paleAmber,C.amber]];
  layers.forEach((l,i)=>{const y=1.58+i*1.05; rect(s,0.8,y,6.2,0.78,l[2],l[2],0.05); tx(s,l[0],1.05,y+0.12,1.75,0.22,{fontSize:14,bold:true,color:l[3]}); tx(s,l[1],2.8,y+0.12,3.85,0.42,{fontSize:12,color:C.ink,breakLine:true}); if(i<layers.length-1) arrow(s,3.9,y+0.8,3.9,y+1.02,C.line);});
  rect(s,7.55,1.58,5.05,4.98,C.white,C.line,0.06);
  tx(s,'快照指标',7.88,1.88,2.0,0.24,{fontSize:15,bold:true,color:C.ink});
  const metrics=[['函数','1,571'],['关系','3,093'],['直接调用','3,006'],['宏 / 条件','1,827'],['回调字段','21'],['Unix poll 后端','6']];
  metrics.forEach((m,i)=>{const yy=2.35+i*0.58; line(s,7.88,yy+0.42,12.25,yy+0.42,C.line,0.7); tx(s,m[0],7.9,yy,2.6,0.22,{fontSize:12,color:C.muted}); tx(s,m[1],10.75,yy,1.5,0.22,{fontSize:16,bold:true,color:C.blue,align:'right'});});
  tx(s,'关键判断：uv__io_poll 不是“一个函数”，而是配置维度下的候选实现集合。',7.88,5.98,4.25,0.4,{fontSize:12.5,bold:true,color:C.ink,breakLine:true});
  note(s,'research/libuv-code-reverse-research.md §3; validation-report.json');
}

// 5
{
  const s=pptx.addSlide(); header(s,'Libuv chain','TCP 读取链：注册、调度、分发、执行四种语义',5); footer(s);
  const nodes=[['uv_read_start','API',0.78,2.7,C.blue],['uv__read_start','写入\nread_cb',3.02,2.7,C.cyan],['uv__io_poll','平台 poll',5.35,2.7,C.blue],['uv__io_cb','bits / switch',7.7,2.7,C.amber],['uv__stream_io','handler',10.05,2.7,C.blue],['uv__read','用户回调前',10.05,4.55,C.cyan]];
  nodes.forEach(([name,sub,x,y,col])=>{rect(s,x,y,1.72,0.84,C.white,col,0.06);tx(s,name,x+0.08,y+0.12,1.56,0.2,{fontSize:12,bold:true,color:col,align:'center'});tx(s,sub,x+0.08,y+0.46,1.56,0.2,{fontSize:10,color:C.muted,align:'center',breakLine:true});});
  arrow(s,2.5,3.12,3.02,3.12,C.blue); arrow(s,4.74,3.12,5.35,3.12,C.blue); arrow(s,7.07,3.12,7.7,3.12,C.blue); arrow(s,9.42,3.12,10.05,3.12,C.blue); arrow(s,10.91,3.55,10.91,4.55,C.cyan);
  pill(s,'direct',1.65,3.82,0.78,C.paleBlue,C.blue); pill(s,'writes',3.68,3.82,0.82,C.paleCyan,C.cyan); pill(s,'scheduled_by',5.55,3.82,1.25,C.paleBlue,C.blue); pill(s,'dispatches',7.98,3.82,1.02,C.paleAmber,C.amber); pill(s,'invokes',10.18,5.58,0.92,C.paleCyan,C.cyan);
  rect(s,0.88,5.85,8.22,0.74,C.dark,C.dark,0.05); tx(s,'uv__io_t.bits & 15  →  uv__io_cb_t  →  switch  →  uv__stream_io',1.15,6.07,7.7,0.24,{fontFace:'Menlo',fontSize:12,color:C.white});
  tx(s,'证据位置：uv-common.c:1068 · unix/core.c:906 · unix/stream.c:1198',9.38,6.02,3.2,0.42,{fontSize:10.5,color:C.muted,align:'right',breakLine:true});
  note(s,'research/libuv-code-reverse-research.md §3.3-3.4; validation-report.json signals');
}

// 6
{
  const s=pptx.addSlide(); header(s,'Libuv lifecycle','三类生命周期链：监听、线程池、Timer / Async',6); footer(s);
  const cols=[['监听','uv_listen → tcp->connection_cb\n→ uv__server_io → connection_cb',C.blue,C.paleBlue],['线程池','uv_queue_work → worker work_cb\n→ loop wq → after_work_cb',C.cyan,C.paleCyan],['Timer / Async','uv_timer_start → heap → timer phase\nuv_async_send → loop-side callback',C.amber,C.paleAmber]];
  cols.forEach((c,i)=>{const x=0.72+i*4.18; rect(s,x,1.7,3.65,3.55,C.white,C.line,0.06); rect(s,x,1.7,3.65,0.13,c[2],c[2],0.06); tx(s,c[0],x+0.25,2.05,2.9,0.3,{fontSize:18,bold:true,color:c[2]}); tx(s,c[1],x+0.25,2.68,3.05,0.76,{fontSize:14,color:C.ink,breakLine:true}); line(s,x+0.25,3.78,x+3.35,3.78,C.line,1); const tags=i===0?['registers_callback','scheduled_by','invokes_callback']:i===1?['worker context','queue handoff','loop context']:['heap schedule','phase','wakeup']; tags.forEach((t,j)=>pill(s,t,x+0.25,4.12+j*0.36,2.15,j===1?C.paleCyan:C.paleBlue,j===1?C.cyan:C.blue));});
  tx(s,'关键语义：同名 callback 不等于同一对象；执行上下文（worker / loop / child）必须进 IR。',0.8,5.92,11.8,0.32,{fontSize:14,bold:true,color:C.ink,align:'center'});
  note(s,'research/libuv-code-reverse-research.md §3.5-3.8; research/subagent-design.md §5.2');
}

// 7
{
  const s=pptx.addSlide(); header(s,'Redis structure','Redis：应用层事件驱动模型，当前真实快照待补',7,C.amber); footer(s);
  rect(s,0.72,1.58,5.9,4.95,C.white,C.line,0.06); tx(s,'预期穿刺地图',1.0,1.9,2.5,0.26,{fontSize:16,bold:true,color:C.amber});
  const rows=[['ae 事件库','aeCreateFileEvent · aeProcessEvents · backend'],['网络 / 协议','accept → readQueryFromClient → RESP parser'],['命令分发','processCommand → command table → call'],['持久化 / BIO','AOF / RDB / background jobs'],['模块','RedisModule_OnLoad → command registration']];
  rows.forEach((r,i)=>{const y=2.42+i*0.72; dot(s,1.03,y+0.08,0.1,C.amber); tx(s,r[0],1.25,y,1.55,0.24,{fontSize:12.5,bold:true,color:C.ink}); tx(s,r[1],2.88,y,3.3,0.36,{fontSize:11.5,color:C.muted,breakLine:true});});
  rect(s,6.95,1.58,5.62,4.95,C.paleAmber,C.paleAmber,0.06); pill(s,'PENDING SNAPSHOT',7.25,1.9,1.72,C.amber,C.white); tx(s,'当前不能声称\nRedis 官方源码已验证',7.25,2.48,4.6,0.64,{fontSize:22,bold:true,color:C.ink,breakLine:true});
  bulletList(s,['本地只有 synthetic_validation fixture','需固定官方 commit 后补真实 file:line','黄金问题：AE、命令、serverCron、AOF/BIO、模块','未发现 include / link / adapter 证据时，不推断 Redis 依赖 libuv'],7.28,3.62,4.72,0.52,C.ink,13);
  tx(s,'证据纪律不是缺口，而是系统能力的一部分。',7.25,5.82,4.7,0.25,{fontSize:13,bold:true,color:C.amber});
  note(s,'research/dual-target-source-audit.md §4; research/subagent-design.md §5.3; validation-report.json');
}

// 8
{
  const s=pptx.addSlide(); header(s,'SubAgent architecture','SubAgent 分层：事实层与解释层解耦',8); footer(s);
  const boxes=[['源码快照 + 构建配置',0.8,1.65,2.35,C.paleAmber,C.amber],['AST / fallback parser',3.55,1.65,2.2,C.paleBlue,C.blue],['Domain Profiles',6.18,1.65,1.95,C.paleCyan,C.cyan],['Evidence IR',8.55,1.65,1.75,C.paleBlue,C.blue],['Graph + slices',10.72,1.65,1.8,C.paleCyan,C.cyan]];
  boxes.forEach(([t,x,y,w,fill,col],i)=>{rect(s,x,y,w,0.86,fill,col,0.06);tx(s,t,x+0.08,y+0.2,w-0.16,0.42,{fontSize:12,bold:true,color:col,align:'center',breakLine:true}); if(i<boxes.length-1) arrow(s,x+w,y+0.43,boxes[i+1][1],y+0.43,C.line);});
  rect(s,2.05,3.4,9.45,2.2,C.dark,C.dark,0.06); tx(s,'Query Agent',2.38,3.7,1.55,0.3,{fontSize:18,bold:true,color:'8FB2FF'}); tx(s,'规划 → 工具取证 → 证据校验 → 带引用回答',2.38,4.16,4.7,0.3,{fontSize:16,bold:true,color:C.white});
  const tools=['find_symbol','get_call_edges','trace_async_chain','resolve_pointer','read_slice','query_configuration','report_uncertainty'];
  tools.forEach((t,i)=>pill(s,t,2.4+(i%4)*2.1,4.82+Math.floor(i/4)*0.38,1.82,i===2?C.paleAmber:C.paleBlue,i===2?C.amber:C.blue));
  arrow(s,9.65,2.52,6.9,3.4,C.cyan); arrow(s,6.9,5.6,9.5,2.52,C.cyan);
  tx(s,'模型不直接读取数据库或任意文件；工具返回事实，模型组织语言。',1.25,6.3,10.8,0.28,{fontSize:14,bold:true,color:C.ink,align:'center'});
  note(s,'docs/libuv-redis-architecture.md §2, §8; research/subagent-design.md §6');
}

// 9
{
  const s=pptx.addSlide(); header(s,'Contracts','Evidence IR + HTTP / Tool JSON 规范',9); footer(s);
  rect(s,0.72,1.55,5.7,4.98,C.white,C.line,0.06); tx(s,'统一边模型',1.0,1.88,2.0,0.25,{fontSize:16,bold:true,color:C.blue});
  const fields=['edge_id / source / target','type + semantics','resolution: observed / inferred / unresolved','confidence: 0..1','evidence[]: file + line + text','configurations[]'];
  bulletList(s,fields,1.02,2.4,4.75,0.52,C.ink,13);
  rect(s,6.75,1.55,5.85,4.98,C.dark,C.dark,0.06); tx(s,'tool.invoke.schema.json',7.05,1.88,3.6,0.23,{fontFace:'Menlo',fontSize:14,bold:true,color:'8FB2FF'}); tx(s,'{\n  "tool_call_id": "tc_...",\n  "analysis_id": "an_...",\n  "tool_name": "trace_async_chain",\n  "arguments": { ... }\n}',7.05,2.42,4.8,1.65,{fontFace:'Menlo',fontSize:13,color:C.white,breakLine:true}); tx(s,'tool.result.schema.json',7.05,4.42,3.6,0.23,{fontFace:'Menlo',fontSize:14,bold:true,color:'49D8D1'}); tx(s,'ok=true  → result + evidence\nok=false → error + evidence[]',7.05,4.86,4.8,0.65,{fontFace:'Menlo',fontSize:13,color:C.white,breakLine:true});
  tx(s,'公开 API：/v1/analyses · /graph · /queries · /health',0.85,6.7,11.8,0.22,{fontSize:12,bold:true,color:C.muted,align:'center'});
  note(s,'contracts/openapi.json; contracts/tool.invoke.schema.json; contracts/tool.result.schema.json; docs/api-json-spec.md §4-7');
}

// 10
{
  const s=pptx.addSlide(); header(s,'Demo loop','Demo 验证闭环：从请求到可审计回答',10); footer(s);
  const flow=[['POST /v1/analyses','创建快照',0.85,2.0,C.blue],['CodeAnalyzer','提取节点 / 边',3.25,2.0,C.cyan],['Domain Profile','标注语义',5.65,2.0,C.amber],['Evidence IR','分页图',8.05,2.0,C.blue],['POST /v1/queries','路径 + 引用',10.45,2.0,C.cyan]];
  flow.forEach(([t,sub,x,y,col],i)=>{rect(s,x,y,1.72,1.0,C.white,col,0.06);tx(s,t,x+0.08,y+0.15,1.56,0.24,{fontSize:12,bold:true,color:col,align:'center',breakLine:true});tx(s,sub,x+0.08,y+0.62,1.56,0.18,{fontSize:9.5,color:C.muted,align:'center'});if(i<flow.length-1)arrow(s,x+1.72,y+0.5,flow[i+1][2],y+0.5,C.line);});
  rect(s,1.0,4.05,11.35,1.55,C.paleBlue,C.paleBlue,0.06); tx(s,'Demo 已验证',1.32,4.35,1.55,0.22,{fontSize:14,bold:true,color:C.blue});
  const checks=['分析资源与图 API','注册 / 调度 / 执行边','7 工具受控运行时','非法 cursor / 越界路径失败信封','查询 scope：配置、方向、边类型、跳数'];
  checks.forEach((c,i)=>{const x=3.15+(i%3)*2.8;const y=4.26+Math.floor(i/3)*0.46;dot(s,x,y+0.07,0.1,C.cyan);tx(s,c,x+0.18,y,2.45,0.25,{fontSize:11.5,color:C.ink});});
  tx(s,'局限：轻量正则 ≠ Clang AST；Redis 官方快照待授权后补齐。',1.0,6.12,11.2,0.26,{fontSize:13,bold:true,color:C.amber,align:'center'});
  note(s,'README.md §运行 / API / 验证; tests/; subagent_tools.py; validation-report.json');
}

// 11
{
  const s=pptx.addSlide(); header(s,'Validation','验证结果：把“通过”拆成可解释的层级',11); footer(s);
  stat(s,'36/36','单元 / 契约 / 工具测试',0.8,1.62,2.45,C.cyan); stat(s,'30/30','初始基线回归',3.55,1.62,2.45,C.cyan); stat(s,'1,571','libuv functions',6.3,1.62,2.45,C.blue); stat(s,'0','Redis 伪造行号',9.05,1.62,2.45,C.amber);
  rect(s,0.8,3.05,5.65,2.6,C.paleBlue,C.paleBlue,0.06); tx(s,'libuv · source_verified',1.08,3.35,3.5,0.25,{fontSize:16,bold:true,color:C.blue}); bulletList(s,['固定 commit：e43e3d8a…','关键链命中：uv_run → poll → io_cb → stream_io → read','宏 / callback slot / platform scope 已进入 IR'],1.1,3.88,4.9,0.52,C.ink,13);
  rect(s,6.75,3.05,5.75,2.6,C.paleAmber,C.paleAmber,0.06); tx(s,'Redis · synthetic_validation',7.03,3.35,3.9,0.25,{fontSize:16,bold:true,color:C.amber}); bulletList(s,['fixture 只证明规则可运行','官方仓库与真实 commit 尚未进入工作区','报告明确标注 pending_snapshot'],7.05,3.88,4.95,0.52,C.ink,13);
  tx(s,'验收原则：smoke test 通过 ≠ 真实仓库准确率；双仓指标必须分开统计。',0.8,6.2,11.7,0.28,{fontSize:14,bold:true,color:C.ink,align:'center'});
  note(s,'validation-report.json; tests/test_domain_profiles.py; tests/test_contracts.py; research/subagent-design.md §8');
}

// 12
{
  const s=pptx.addSlide(); s.background={color:C.dark};
  tx(s,'结论与下一步',0.72,0.65,4.2,0.32,{fontSize:15,bold:true,color:'8FB2FF',charSpacing:1});
  tx(s,'先把证据链做实，\n再让 Agent 说得像专家。',0.72,1.35,8.7,1.0,{fontSize:31,bold:true,color:C.white,breakLine:true});
  const steps=[['现在','libuv 固定快照 + Demo / JSON 契约可复现',0.8,3.15,C.cyan],['授权后','Redis 官方 commit + 五条黄金链 + 真实报告',4.35,3.15,C.amber],['生产化','Clang AST、多配置图、持久化 Graph Store、评测集',7.9,3.15,C.blue]];
  steps.forEach(([t,d,x,y,col])=>{rect(s,x,y,3.0,1.55,'10244A','254C92',0.06);pill(s,t,x+0.22,y+0.22,0.78,col,col===C.amber?C.dark:C.dark);tx(s,d,x+0.22,y+0.72,2.5,0.5,{fontSize:13,color:C.white,breakLine:true});});
  line(s,3.8,3.94,4.35,3.94,'3D68C7',1.4,'solid','triangle'); line(s,7.35,3.94,7.9,3.94,'3D68C7',1.4,'solid','triangle');
  tx(s,'交付物',0.8,5.55,1.0,0.22,{fontSize:13,bold:true,color:'8FB2FF'}); tx(s,'源码剖析报告 · 架构设计 · OpenAPI / JSON Schema · 七工具运行时 · Web Demo · 本 PPT',0.8,5.95,11.55,0.3,{fontSize:15,color:C.white});
  tx(s,'代码逆向 Agent · libuv / Redis 专项课题',0.8,6.88,6.5,0.2,{fontSize:10,color:'8DA3C4'});
  note(s,'research/README.md; research/dual-target-source-audit.md; docs/api-json-spec.md');
}

(async () => {
  await pptx.writeFile({ fileName: '/Users/andye/Documents/New project/code_reverse_agent/docs/libuv-redis代码逆向SubAgent课题方案.pptx' });
  await pptx.writeFile({ fileName: '/Users/andye/Documents/New project/libuv-redis代码逆向SubAgent课题方案.pptx' });
})();
