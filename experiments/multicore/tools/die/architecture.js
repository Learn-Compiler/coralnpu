/* Functional block diagram and teaching animation; never produces RTL measurements. */
function setupArchitecture() {
  'use strict';
  const $=id=>document.getElementById(id), NS='http://www.w3.org/2000/svg';
  const svg=$('die-svg'), nodes=new Map(), edges=new Map(), details=new Map();
  const colors={control:'#517daf',data:'#12897d',matrix:'#9661bf',neutral:'#899ba7'};
  function element(tag,attrs={},parent=svg){const el=document.createElementNS(NS,tag);for(const [key,value] of Object.entries(attrs))el.setAttribute(key,value);parent.append(el);return el;}
  function text(parent,x,y,value,style='block-small'){const el=element('text',{x,y,class:style},parent);el.textContent=value;return el;}
  const defs=element('defs');
  for(const [kind,color] of Object.entries(colors)){const marker=element('marker',{id:'tip-'+kind,markerWidth:7,markerHeight:7,refX:6,refY:3.5,orient:'auto-start-reverse'},defs);element('path',{d:'M0 0 7 3.5 0 7Z',fill:color},marker);}
  element('rect',{x:18,y:120,width:1564,height:1130,rx:20,fill:'#f6f9fb',stroke:'#bacdd8','stroke-dasharray':'7 5'});
  text(svg,36,143,'单 die · 功能连接图 / 内部模块抽象', 'block-caption');
  const backgrounds=element('g'), wires=element('g',{id:'architecture-wires'}), blocks=element('g'), particles=element('g',{'aria-hidden':'true'});
  function box(id,x,y,w,h,title,lines=[],kind='normal',description='',source='') {
    const g=element('g',{class:'arch-node '+kind,'data-node':id,role:'button',tabindex:0,'aria-label':'查看 '+title},['cpu','shell','vector','matrix'].includes(kind)?backgrounds:blocks);
    element('rect',{x,y,width:w,height:h,rx:8},g);
    text(g,x+12,y+23,title,'block-title');lines.forEach((line,i)=>text(g,x+12,y+44+i*18,line));
    nodes.set(id,g);details.set(id,{title,description:description||lines.join('；'),source});
    g.addEventListener('click',()=>select(id));g.addEventListener('keydown',event=>{if(event.key==='Enter'||event.key===' '){event.preventDefault();select(id);}});return g;
  }
  function edge(id,path,kind='data',label='') {
    const g=element('g',{class:'arch-edge '+kind,'data-edge':id},wires);
    element('path',{d:path,fill:'none',stroke:colors[kind],'stroke-width':2,'marker-end':'url(#tip-'+kind+')'},g);
    edges.set(id,{g,path,kind,label});return g;
  }
  function select(id){const value=details.get(id);if(!value)return;nodes.forEach((g,key)=>g.classList.toggle('selected',key===id));$('module-title').textContent=value.title;$('module-description').textContent=value.description;$('module-address').textContent=value.source;$('module-kind').textContent=id.includes('matrix')||id.includes('tile')||id.includes('pe')?'VME / ZVT · 扩展预览':'功能模块 · 对应源码';}
  box('host',35,20,1530,74,'宿主 C++ / Verilator',['装入启动镜像和输入配置；执行后独立核对结果。实际核调度由 CPU 内的 C runtime 执行。'],'host','宿主不替代 CPU 下发算子。底部播放是教学动画，真实计数与校验仍从工作台运行得到。','die/smoke_die.cc · die/complex_check.h');
  box('boot',36,161,276,75,'控制 RAM · 64 KiB',['固件 + kernel 镜像 + 栈'],'memory','CPU 从本地 RAM 取指，kernel 镜像内嵌在控制固件中。','0x00000000..0x0000ffff');
  edge('boot-load','M174 94 V154','neutral');
  box('cpu',36,264,276,680,'控制 CPU · PicoRV32',['RV32IM · 多周期 FSM'],'cpu','此图按功能拆分 PicoRV32，不能理解成五级流水线。乘除法通过 PCPI 单元完成。','die/vendor/picorv32/picorv32.v · die/Die.scala');
  box('cpu-pc',54,329,240,40,'PC / 取指请求',[],'control','维护程序计数器并发出指令读取请求。','picorv32.v: reg_pc / mem_instr');
  box('cpu-decode',54,397,240,40,'指令译码 / 立即数',[],'control','识别 RV32IM 指令并产生操作数和状态控制。','picorv32.v: decoder_trigger');
  box('cpu-gpr',54,465,240,40,'GPR · x0–x31',[],'normal','存放标量地址、循环变量、参数和运算结果。','picorv32.v: cpuregs / reg_rs1 / reg_rs2');
  box('cpu-alu',54,535,116,67,'ALU / 分支',['地址 / 比较'],'normal','执行整数、移位、分支与地址计算。','picorv32.v: alu_*');
  box('cpu-mul',184,535,110,67,'MUL / DIV',['PCPI'],'normal','当前配置启用了 fast multiply 和 divide。','ENABLE_FAST_MUL=1 · ENABLE_DIV=1');
  box('cpu-fsm',54,630,240,57,'多周期控制 / 写回',['fetch → exec → mem'],'control','PicoRV32 使用多周期状态机推进指令，不是新增流水级。','picorv32.v: cpu_state_*');
  box('cpu-mem',54,717,240,62,'统一 native 访存端口',['valid / ready · 32-bit'],'memory','取指与数据访存共用 native 接口；控制桥连接 RAM、系统 CSR 与 AXI。','DieControlFabric.cpu*');
  box('runtime',54,819,240,90,'CPU 上运行的软件',['C runtime','装载 → 分片 → 启动 → 等待'],'software','软件框表示 CPU 执行的程序，不是专用硬件队列。当前使用固定调度计划。','die/firmware/runtime.c');
  edge('cpu-fetch','M174 236 V322','control');edge('cpu-decode','M174 369 V390','control');edge('cpu-registers','M174 437 V458','control');
  edge('cpu-execute','M112 505 V528','control');edge('cpu-pcpi','M239 505 V528','control');
  edge('cpu-writeback','M112 602 V622','control');edge('cpu-mulback','M239 602 V622','control');edge('cpu-memory','M174 687 V710','control');
  edge('cpu-rf-return','M54 658 H45 V484 H49','control');
  box('fabric',350,161,1214,68,'DieControlFabric · native bus 地址译码 / AXI host 控制',['四个独立 Coral host 端口：装载镜像、参数下发、入口 PC、reset / clock gate'],'control','CPU 地址窗分别译码到四核 host AXI slave。第五个端口用于共享 SRAM。','die/Die.scala · Coral i = 0x40000000 + i × 0x100000');
  edge('cpu-fabric','M294 748 H330 V194 H343','control');
  const starts=[350,660,970,1280];
  for(let i=0;i<4;i++){
    const x=starts[i], p='c'+i+'-';
    box(p+'shell',x,264,284,680,'Coral '+i+' · 独立 CoreAxi',[],'shell','每个核拥有独立复位、程序入口和私有存储。此处四套内部模块都独立绘制。','hdl/chisel/src/coralnpu/CoreAxi.scala');
    box(p+'host',x+15,308,254,38,'AXI slave / 启动 CSR',[],'control','CPU 通过此端口装载 ITCM、DTCM 和控制寄存器。','host 0x40'+i+'00000');
    box(p+'itcm',x+15,370,119,62,'ITCM',['8 KiB'],'memory','本核私有指令存储。','host +0x00000');
    box(p+'dtcm',x+150,370,119,62,'DTCM',['32 KiB'],'memory','私有数据存储，起始区域保存启动参数。','host +0x10000');
    box(p+'fetch',x+15,458,119,42,'Fetch',[],'control','UncachedFetch 向 dispatch 提供指令。','scalar/SCore.scala · UncachedFetch');
    box(p+'lsu',x+150,458,119,42,'LSU',[],'memory','标量、向量及可选 Tile 访存经 LSU 和存储 fabric 路由。','scalar/SCore.scala · Lsu.scala');
    box(p+'dispatch',x+15,526,254,40,'Decode / Dispatch / 依赖检查',[],'control','派发标量与向量指令，并与寄存器 scoreboard、retirement buffer 配合。矩阵模式是扩展预览。','scalar/SCore.scala · DispatchV2');
    box(p+'gpr',x+15,591,119,43,'Scalar GPR',[],'normal','标量操作数与地址寄存器。','SCore: Regfile');
    box(p+'scalar',x+150,591,119,43,'ALU/M/FPU',[],'normal','标量 ALU、分支、乘除法及 FloatCore；图中将多个单元合并表示。','SCore: Alu / Bru / Mlu / Dvu / FloatCore');
    box(p+'retire',x+15,659,254,40,'Retire / CSR · mcycle / minstret',[],'neutral','归并退休、异常与 CSR 状态。真实报告从 CSR 读取周期与退休计数。','SCore: RetirementBuffer / Csr');
    box(p+'rvv',x+15,723,254,80,'RVV · VLEN 128',[],'vector','RvvCore 包含向量寄存器、调度及向量执行后端；图为功能抽象，不表示 lane 的物理个数。','coralnpu/rvv/RvvCore.scala · rvv/design/RvvCore.sv');
    box(p+'vrf',x+27,754,103,34,'Vector RF',[],'vector-inner','向量寄存器，当前 kernel 使用 e32,m1。','VLEN=128 · 当前 RTL 已接通');
    box(p+'valu',x+151,754,106,34,'ALU / MAC',[],'vector-inner','执行向量加法、乘加和比较等；一个退休向量指令按一条计数。','RVV 当前执行路径');
    box(p+'matrix',x+15,827,254,98,'VME / Zvt · 预览',[],'matrix','参考仓库已有 zvt_ctrl、zvt_mt、zvt_pe_array。四核 die 当前 enableVme 未开启；此区域为接入预览，不是实测矩阵运算。','hdl/verilog/rvv/design/Zvt/zvt.sv');
    box(p+'mctrl',x+185,834,72,25,'控制',[],'matrix-inner','矩阵 uop 调度、Tile 读写控制和 PE 启动。','Zvt/zvt_ctrl.sv');
    box(p+'tile',x+27,865,103,44,'Tile RF',[],'matrix-inner','矩阵 Tile 寄存器组保存操作数与累加结果；不是新增共享 SRAM。','Zvt/zvt_mt.sv · zvt_mt_reg.sv');
    const pe=box(p+'pe',x+151,865,106,44,'PE / MAC',[],'matrix-inner','PE array 读取 Tile 并做乘加，结果写回 Tile。下方小格只是阵列符号，不代表 PE 数量或吞吐。','Zvt/zvt_pe_array.sv · zvt_pe_block.sv');
    for(let r=0;r<2;r++)for(let c=0;c<5;c++)element('rect',{x:x+163+c*16,y:892+r*5,width:10,height:3,rx:1,class:'pe-cell'},pe);
    edge(p+'control','M'+(x+142)+' 229 V301','control');
    edge(p+'load-code',`M${x+75} 346 V363`,'control');edge(p+'load-args',`M${x+210} 346 V363`,'control');
    edge(p+'fetch-code',`M${x+75} 432 V451`,'control');edge(p+'decode',`M${x+75} 500 V519`,'control');
    edge(p+'scalar-read',`M${x+75} 566 V584`,'control');edge(p+'scalar-execute',`M${x+134} 612 H${x+143}`,'control');
    edge(p+'retire-scalar',`M${x+210} 634 V652`,'neutral');
    edge(p+'vector-issue',`M${x+15} 546 H${x+7} V738 H${x+10}`,'control');
    edge(p+'matrix-issue',`M${x+269} 546 H${x+280} V842 H${x+274}`,'matrix');
    edge(p+'local-load',`M${x+210} 432 V451`,'data');
    edge(p+'vector-load',`M${x+210} 500 V513 H${x+3} V771 H${x+20}`,'data');
    edge(p+'vector-compute',`M${x+130} 771 H${x+144}`,'data');
    edge(p+'vector-accumulate',`M${x+204} 788 V797 H${x+78} V793`,'data');
    edge(p+'tile-load',`M${x+210} 500 V513 H${x+3} V888 H${x+20}`,'matrix');
    edge(p+'matrix-compute',`M${x+130} 888 H${x+144}`,'matrix');
    edge(p+'tile-accumulate',`M${x+204} 909 V917 H${x+78} V914`,'matrix');
    edge(p+'vector-store',`M${x+27} 779 H${x+3} V513 H${x+210} V507`,'data');
    edge(p+'matrix-store',`M${x+27} 888 H${x+3} V513 H${x+210} V507`,'matrix');
    edge(p+'axi-out',`M${x+269} 490 H${x+290} V982`,'data');
    edge(p+'axi-in',`M${x+282} 980 V494 H${x+274}`,'data');
  }
  box('status',36,987,276,82,'系统状态 / 完成',['WFI · fault · finish · 周期'],'neutral','控制 CPU 轮询系统状态，Coral 结束后写 done 并进入 WFI。','0x50000000..0x5000002f');
  edge('status-poll','M174 944 V980','neutral');
  box('arbiter',350,990,1214,52,'四核 AXI master · 4 → 1 轮转仲裁',[],'data','读写分别串行化事务，共用 128-bit AXI 数据通路。','experiments/multicore/Cluster.scala');
  box('merge',350,1080,1214,42,'2 → 1 AXI 合并 · Coral 集群 + 控制 CPU 的共享 SRAM 端口',[],'data','CPU 与 Coral 访存汇入相同片上 SRAM。','die/Die.scala');
  box('sram',350,1160,1214,64,'共享 SRAM · 16 KiB',['输入 / 输出 / profile 记录 / MMIO 日志'],'memory','共享结果位于 SRAM；当前不是 DDR/HBM 模型，没有 DMA 或 cache coherence。','0x20000000..0x20003fff · profile 0x20003000 · log 0x20003ff0');
  edge('cluster-merge','M957 1042 V1073','data');edge('merge-memory','M957 1122 V1153','data');
  edge('memory-merge','M1070 1160 V1129','data');edge('merge-cluster','M1070 1080 V1049','data');
  edge('cpu-shared','M1564 195 H1574 V1100 H1570','control');
  text(svg,40,1125,'实线：当前已接通的功能模块', 'block-caption');text(svg,40,1151,'紫色虚线：VME / Zvt 接入预览', 'block-caption');text(svg,40,1189,'所有动画均为教学示意', 'block-caption');text(svg,40,1215,'不产生周期、吞吐或带宽测量', 'block-caption');
  // Animated paths are selected by semantic events, independent of RTL reports.
  const all=[0,1,2,3], ids=(cores,suffix)=>cores.map(i=>'c'+i+'-'+suffix);
  const stage=(title,description,active,paths,token='')=>({title,description,active,paths,token});
  function computeStage(cores,matrix){return stage(matrix?'矩阵 PE 乘加 / Tile 累加':'RVV 向量计算',matrix?'Tile A/B 提供操作数，PE 执行乘加并将累加结果写回 Tile。紫色路径是待接入的 VME/Zvt。':'向量寄存器提供操作数，ALU/MAC 并行处理向量元素并写回寄存器。',ids(cores,matrix?'pe':'valu').concat(ids(cores,matrix?'tile':'vrf')),ids(cores,matrix?'matrix-compute':'vector-compute').concat(ids(cores,matrix?'tile-accumulate':'vector-accumulate')),matrix?'A×B':'v');}
  function loadStage(cores,matrix){return stage('输入加载到核内',matrix?'共享 SRAM → AXI 仲裁 → LSU → Tile RF。':'共享 SRAM → AXI 仲裁 → LSU → Vector RF；DTCM 可提供本核参数。',['sram','arbiter','merge',...ids(cores,'lsu'),...ids(cores,matrix?'tile':'vrf')],['memory-merge','merge-cluster',...ids(cores,'axi-in'),...ids(cores,matrix?'tile-load':'vector-load')],matrix?'A/B':'x');}
  function storeStage(cores,matrix){return stage('结果写回共享 SRAM','各核写入各自的输出分片，经共享仲裁端口返回 SRAM。',[...ids(cores,'lsu'),'arbiter','merge','sram'],[...ids(cores,matrix?'matrix-store':'vector-store'),...ids(cores,'axi-out'),'cluster-merge','merge-memory'],'C');}
  function stages(){const scenario=$('flow-scenario').value, matrix=scenario!=='rvv', pair=scenario.startsWith('pair'), a=pair?[0,1]:all, b=[2,3];
    const sequence=[
      stage('CPU 执行控制程序','控制 CPU 从 RAM 取指、译码并读取标量寄存器。',['boot','cpu-pc','cpu-decode','cpu-gpr'],['cpu-fetch','cpu-decode','cpu-registers'],'I'),
      stage('runtime 计算分片与核组','CPU 的 ALU / 寄存器执行 C runtime，生成分片范围与启动顺序。',['runtime','cpu-alu','cpu-gpr','cpu-fsm'],['cpu-execute','cpu-writeback'],'ctl'),
      stage('向四核装载指令','CPU native bus → 控制桥 → 各核 host → 私有 ITCM。',['cpu-mem','fabric',...ids(all,'host'),...ids(all,'itcm')],['cpu-memory','cpu-fabric',...ids(all,'control'),...ids(all,'load-code')],'I'),
      stage('下发参数 / 启动核组','runtime 写入各核 DTCM 和启动 CSR；每个核具有独立 PC 与参数。',['fabric',...ids(all,'dtcm'),...ids(all,'host')],ids(all,'control').concat(ids(all,'load-args')),'arg'),
      stage('Coral 取指与分派','各核 ITCM → Fetch → Decode/Dispatch；标量路径负责地址与循环控制。',[...ids(all,'itcm'),...ids(all,'fetch'),...ids(all,'dispatch'),...ids(all,'gpr')],[...ids(all,'fetch-code'),...ids(all,'decode'),...ids(all,'scalar-read')],'I')
    ];
    if(scenario==='pair-parallel'){
      const loadA=loadStage(a,true), loadB=loadStage(b,false), compA=computeStage(a,true), compB=computeStage(b,false), storeA=storeStage(a,true),storeB=storeStage(b,false);
      sequence.push(stage('两组核并行加载','核 0/1 加载矩阵 Tile，核 2/3 加载向量。两组共享 SRAM 带宽。',loadA.active.concat(loadB.active),loadA.paths.concat(loadB.paths),'A/x'));
      sequence.push(stage('矩阵 + 向量同时计算','核 0/1 的矩阵 PE 与核 2/3 的 RVV 同时处于计算阶段。此为目标架构示意。',compA.active.concat(compB.active),compA.paths.concat(compB.paths),'MAC'));
      sequence.push(stage('两组核写回分片','矩阵与向量结果通过同一 AXI 仲裁访问共享 SRAM。',storeA.active.concat(storeB.active),storeA.paths.concat(storeB.paths),'C'));
    }else{
      sequence.push(loadStage(a,matrix));
      sequence.push(stage('执行单元接收指令',matrix?'Decode/Dispatch → VME/Zvt 控制与 Tile / PE。':'Decode/Dispatch → RVV 后端。',ids(a,'dispatch').concat(ids(a,matrix?'matrix':'rvv'),matrix?ids(a,'mctrl'):[]),ids(a,matrix?'matrix-issue':'vector-issue'),'uop'));
      sequence.push(computeStage(a,matrix),storeStage(a,matrix));
      if(scenario==='pair-serial'){
        sequence.push(stage('依赖屏障 / 启动第二组','CPU 等待核 0/1 完成，再启动核 2/3；后继读取共享区结果。',['status','runtime','cpu-mem'],['status-poll','cpu-fabric'],'done'));
        sequence.push(loadStage(b,false),computeStage(b,false),storeStage(b,false));
      }
    }
    sequence.push(stage('退休 / done / WFI / CPU 汇总','Coral 更新退休与 CSR 状态，写 done 并进入 WFI；CPU 轮询系统状态。',[...ids(all,'retire'),'status','cpu-fsm'],ids(all,'retire-scalar').concat('status-poll'),'done'));
    return sequence;
  }
  let step=0,playing=false,timer=null;
  function showStep(){const list=stages();step=Math.min(step,list.length-1);const current=list[step];nodes.forEach(g=>g.classList.remove('active'));edges.forEach(({g})=>g.classList.remove('active'));particles.replaceChildren();current.active.forEach(id=>nodes.get(id)?.classList.add('active'));
    for(const id of new Set(current.paths)){const entry=edges.get(id);if(!entry)continue;entry.g.classList.add('active');const g=element('g',{class:'flow-token '+entry.kind},particles);element('circle',{r:11,fill:colors[entry.kind],stroke:'#fff','stroke-width':2},g);const label=text(g,0,4,current.token,'token-label');label.setAttribute('text-anchor','middle');element('animateMotion',{dur:(2/Number($('flow-speed').value))+'s',repeatCount:'indefinite',path:entry.path},g);}
    $('flow-stage').textContent=(step+1)+' / '+list.length+' · '+current.title;$('flow-description').textContent=current.description;$('flow-progress').max=list.length-1;$('flow-progress').value=step;
    $('flow-next').disabled=step===list.length-1;$('flow-play').textContent=playing?'暂停演示':'播放数据流';svg.classList.toggle('is-playing',playing);if(!playing)svg.pauseAnimations();else svg.unpauseAnimations();
  }
  function schedule(){clearTimeout(timer);if(!playing)return;timer=setTimeout(()=>{if(step>=stages().length-1){playing=false;showStep();return;}step++;showStep();schedule();},2400/Number($('flow-speed').value));}
  $('flow-play').addEventListener('click',()=>{playing=!playing;if(playing&&step===stages().length-1)step=0;showStep();schedule();});
  $('flow-next').addEventListener('click',()=>{playing=false;clearTimeout(timer);step++;showStep();});
  $('flow-reset').addEventListener('click',()=>{playing=false;clearTimeout(timer);step=0;showStep();});
  $('flow-progress').addEventListener('input',()=>{playing=false;clearTimeout(timer);step=Number($('flow-progress').value);showStep();});
  $('flow-speed').addEventListener('change',()=>{showStep();schedule();});
  $('flow-scenario').addEventListener('change',()=>{playing=false;clearTimeout(timer);step=0;if($('flow-scenario').value!=='rvv')$('show-matrix').checked=true;matrixVisibility();showStep();});
  function matrixVisibility(){svg.classList.toggle('hide-matrix',!$('show-matrix').checked);$('matrix-note').textContent=$('show-matrix').checked?'矩阵扩展预览：参考上游 VME / Zvt 模块，当前四核 die 尚未启用。':'当前 RVV 视图：矩阵扩展暂时隐藏。';}
  $('show-matrix').addEventListener('change',()=>{if(!$('show-matrix').checked){$('flow-scenario').value='rvv';playing=false;clearTimeout(timer);step=0;}matrixVisibility();showStep();});
  document.querySelectorAll('[data-flow]').forEach(button=>button.addEventListener('click',()=>{$('architecture').dataset.flow=button.dataset.flow;document.querySelectorAll('.flow-tabs button').forEach(b=>b.classList.toggle('chosen',b===button));}));
  $('architecture-focus').addEventListener('change',()=>{const value=$('architecture-focus').value;svg.setAttribute('viewBox',value==='all'?'0 0 1600 1270':value==='cpu'?'20 250 310 710':`${starts[Number(value)]-5} 250 306 710`);svg.classList.toggle('focused',value!=='all');});
  $('architecture-zoom').addEventListener('change',()=>{const value=$('architecture-zoom').value;svg.style.width=value==='fit'?'100%':value+'px';svg.style.maxWidth='none';});
  if(document.body.classList.contains('arch-only'))$('expand-architecture').textContent='打开算子工作台';
  $('expand-architecture').addEventListener('click',()=>{if(document.body.classList.contains('arch-only')){window.location.href='/';return;}const expanded=document.body.classList.toggle('arch-expanded');$('expand-architecture').textContent=expanded?'返回工作台布局':'展开架构图';$('architecture').scrollIntoView({behavior:'smooth',block:'start'});});
  document.addEventListener('visibilitychange',()=>{if(document.hidden&&playing){playing=false;clearTimeout(timer);showStep();}});
  select('cpu');matrixVisibility();showStep();
}
