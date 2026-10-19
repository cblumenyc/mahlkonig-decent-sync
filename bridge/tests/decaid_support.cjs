'use strict';
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const root=path.resolve(__dirname,'../../tablet/decaid');
const source=['fields.js','outbox.js','capture.js','plugin.js'].map(name=>fs.readFileSync(path.join(root,name),'utf8')).join('\n');
function runtime(extra={}) {
    let now=extra.startTime ?? Date.parse('2026-10-19T12:00:00Z');
    class Clock extends Date {constructor(...args){super(...(args.length?args:[now]));}static now(){return now;}}
    const context=vm.createContext({Date:Clock,setTimeout:()=>1,clearTimeout:()=>{},...extra});
    vm.runInContext(source+'\nthis.exports={SyncFields,SyncOutbox,SyncCapture,createPlugin};',context);
    return {...context.exports,advance:ms=>{now+=ms;},now:()=>now,iso:()=>new Clock().toISOString()};
}
const identity={product:'mahlkonig-decent-sync',server_id:'11111111-1111-1111-1111-111111111111',controller_id:'decaid-de1',controller_contracts:[1,2]};
const workflow={id:'workflow-1',name:'Espresso',profile:{beverageType:'espresso',steps:[{name:'Infuse',seconds:4},{name:'Pour'}]},context:{targetYield:36,targetWaterVolume:80}};
function journal(r,initial=null) {
    const writes=[];
    const box=new r.SyncOutbox({storage:command=>{if(command.type==='write')writes.push(JSON.parse(JSON.stringify(command.data)));}},()=>{});
    box.read(initial);
    const flush=()=>{while(writes.length)box.written(writes.shift());};
    flush();box.bind(identity,'http://sync.local');flush();return {box,flush,writes};
}
function shot(r,options={}) {
    const {box,flush}=journal(r);
    const p=r.SyncFields.profile(options.workflow || workflow);
    let record;
    const offer=options.offer || {cycle_id:'cycle-1',grind_uuid:'native-1',session_id:box.state.session,profile_revision:p.revision,expires_at:r.now()/1000+290};
    const capture=new r.SyncCapture(box,()=>offer,async()=>record,()=>{});
    capture.setWorkflow(options.workflow || workflow);
    const frame=(state,extra={})=>({event:'state',timestamp:r.iso(),shotId:'live-1',state,scaleConnected:true,scaleLost:false,...extra});
    const machine=substate=>capture.machineFrame({timestamp:r.iso(),state:{state:'espresso',substate}});
    capture.shotFrame(frame('idle',{shotId:null}));
    capture.scaleFrame({timestamp:r.iso(),weight:0});
    const start=r.iso();
    capture.shotFrame(frame('preheating'));
    machine('preinfusion');r.advance(4000);machine('pouring');
    r.advance(24000);
    capture.scaleFrame({timestamp:r.iso(),weight:34});
    if(options.stale)capture.shotFrame(frame('pouring',{timestamp:new Date(r.now()-3000).toISOString()}));
    if(options.scaleLost)capture.shotFrame(frame('pouring',{scaleLost:true}));
    capture.shotFrame(frame('stopping',{event:'decision',decision:{kind:'stop',reason:options.manual?'apiStop':'targetWeight'}}));
    machine('pouringDone');flush();
    record={id:'live-1',timestamp:start,createdAt:r.iso(),updatedAt:r.iso(),workflow,
        stopReason:options.manual?'apiStop':'targetWeight',annotations:{actualYield:36.2},
        measurements:[{machine:{timestamp:r.iso()},scale:{timestamp:r.iso(),weight:36.2},volume:80}]};
    return {box,flush,capture,record,frame,machine,events:()=>JSON.parse(JSON.stringify(box.state.queue)).map(JSON.parse)};
}


module.exports={runtime,journal,shot,identity,workflow};
