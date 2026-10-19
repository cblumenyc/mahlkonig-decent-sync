'use strict';
const {test}=require('node:test');
const assert=require('node:assert/strict');
const crypto=require('node:crypto');
const {runtime,journal,shot,identity,workflow}=require('./decaid_support.cjs');

test('canonical profile hashing matches SHA-256 and ignores workflow metadata',()=>{
    const r=runtime();
    for (const input of ['', 'abc', 'Mahlkönig ☕']) assert.equal(r.SyncFields.sha256(input),crypto.createHash('sha256').update(input).digest('hex'));
    const first=r.SyncFields.profile(workflow);
    const other=r.SyncFields.profile({...workflow,notes:'changed',context:{...workflow.context,targetDoseWeight:18}});
    assert.equal(first.revision,other.revision);
    assert.notEqual(first.revision,r.SyncFields.profile({...workflow,context:{targetYield:40}}).revision);
});

test('durable queue, wrong acknowledgements, identity replacement and restart preserve exact bytes',()=>{
    const r=runtime(),{box,flush,writes}=journal(r);
    box.enqueue('profile',{profile:r.SyncFields.profile(workflow)});
    assert.equal(box.next(),null);
    const wrong={...writes[0],revision:100};box.written(wrong);assert.equal(box.next(),null);
    flush();const raw=box.next();assert.ok(raw);
    assert.equal(box.acknowledge({server_id:'other',event_id:JSON.parse(raw).event_id,durable:true}),false);
    assert.equal(box.next(),raw);
    assert.throws(()=>box.bind({...identity,server_id:'22222222-2222-2222-2222-222222222222'},'http://other.local'));
    const restarted=journal(r,box.durable);
    assert.equal(restarted.box.next(),raw);
    assert.notEqual(restarted.box.state.session,box.state.session);
    assert.equal(restarted.box.acknowledge({server_id:identity.server_id,event_id:JSON.parse(raw).event_id,durable:true}),true);
    assert.equal(restarted.box.next(),null);restarted.flush();assert.equal(restarted.box.state.queue.length,0);
});

test('live boundaries freeze cycle and yield; final weight does not extend extraction time',async()=>{
    const r=runtime(),s=shot(r);r.advance(4000);await s.capture.stored('live-1');s.flush();
    const events=s.events();assert.deepEqual(events.map(row=>row.kind),['shot_started','preinfusion_complete','shot_finished','shot_finalized']);
    assert.deepEqual(events.map(row=>row.data.elapsed_ms),[0,4000,28000,28000]);
    assert.ok(events.every(row=>row.cycle.grind_uuid==='native-1'));
    assert.equal(events[2].data.scale_mass.value,34);assert.equal(events[3].data.scale_mass.value,36.2);
    assert.equal(events[3].data.water_volume.source,'decaid_water_volume');
    assert.equal(events[3].data.provenance.unedited,true);
    assert.equal(events[0].data.profile.target.value,36);
});

for(const fault of ['scaleLost','manual','edited','workflow','imported','late','reconnect','stale'])test('ineligible '+fault+' stays history-only',async()=>{
    const r=runtime(),s=shot(r,{scaleLost:fault==='scaleLost',manual:fault==='manual',stale:fault==='stale'});
    if(fault==='edited'){s.capture.edited('live-1');s.record.updatedAt='2026-10-19T12:01:00Z';}
    if(fault==='workflow')s.record.workflow={...workflow,context:{targetYield:40}};
    if(fault==='imported'){await s.capture.stored('imported-record');assert.equal(s.events().length,3);return;}
    if(fault==='late'){r.advance(21000);await s.capture.stored('live-1');assert.equal(s.events().length,3);return;}
    if(fault==='reconnect') {
        s.capture.reset('controller_connection_lost');
        const count=s.events().length;
        s.capture.shotFrame(s.frame('preheating',{shotId:'replayed'}));
        s.capture.shotFrame(s.frame('pouring',{shotId:'replayed'}));
        assert.equal(s.events().length,count);return;
    }
    r.advance(4000);await s.capture.stored('live-1');s.flush();
    const rows=s.events();
    if(fault==='manual')assert.equal(rows[2].data.completion_reason,'manual_or_unknown');
    else {assert.ok(rows.at(-1).data.quality_flags.length);assert.equal(rows.at(-1).data.scale_mass,null);}
});

test('host integration checks version, binds server, and sends only persisted contract-2 events',async()=>{
    let plugin;
    const writes=[],listeners=new Map(),opened=[],sent=[];
    const r=runtime({fetch:async(url,options)=>{
        assert.equal(options.method,'GET');
        assert.ok(url.startsWith('http://localhost:8080/api/v1/'));
        return {ok:true,text:async()=>JSON.stringify(url.endsWith('/info')?{version:'0.9.0'}:workflow)};
    }});
    const host={storage:command=>{
        if(command.type==='read')plugin.onEvent({name:'storageRead',payload:{key:command.key,value:null}});
        else writes.push(JSON.parse(JSON.stringify(command.data)));
    },transport:{open:async options=>{opened.push(options.url);return {handle:String(opened.length)};},
        onEvent:(handle,callback)=>listeners.set(handle,callback),send:async(handle,payload)=>{sent.push(payload.data);},close:async()=>{}}};
    plugin=r.createPlugin(host);await plugin.onLoad({serverAddress:'http://sync.local'});
    assert.equal(opened.length,0);
    const flush=async()=>{while(writes.length){plugin.onEvent({name:'storageWrite',payload:writes.shift()});await new Promise(resolve=>setImmediate(resolve));}};
    await flush();assert.equal(opened.length,4);
    listeners.get('1')({type:'data',dataType:'text',data:JSON.stringify({type:'hello',...identity})});
    assert.equal(sent.length,0);await flush();
    assert.ok(sent.length>0);
    const event=JSON.parse(sent.find(raw=>JSON.parse(raw).type==='event'));
    assert.equal(event.server_id,identity.server_id);assert.equal(event.event.schema_version,2);
    const status=JSON.parse(plugin.__httpRequestHandler({method:'GET',endpoint:'status'}).body);
    assert.equal(status.ready,false);
    plugin.onEvent({name:'shotStored',payload:{id:'import'}});
    plugin.onUnload();
    assert.ok(opened.every(url=>!url.includes('/espresso')));
});

test('corrupt storage remains untouched and missing write acknowledgement prevents delivery',()=>{
    const r=runtime();let writes=0;
    const box=new r.SyncOutbox({storage:()=>{writes++;}},()=>{});
    box.read({schema:1,revision:0,boot:0,server_id:null,server_url:null,queue:['{broken'],seen:[]});
    assert.equal(box.ready,false);assert.equal(box.state,null);assert.equal(writes,0);
    const blocked=new r.SyncOutbox({storage:()=>{throw new Error('disk full');}},()=>{});
    blocked.read(null);assert.equal(blocked.next(),null);assert.equal(blocked.ready,false);
});

test('mid-shot reconnect and reordered machine frames cannot produce valid feedback',()=>{
    const r=runtime(),{box,flush}=journal(r);
    const capture=new r.SyncCapture(box,()=>null,async()=>null,()=>{});
    capture.setWorkflow(workflow);
    capture.shotFrame({timestamp:r.iso(),state:'idle'});
    capture.scaleFrame({timestamp:r.iso(),weight:1});
    capture.shotFrame({event:'state',timestamp:r.iso(),state:'preheating',shotId:'interrupted',scaleConnected:true});
    capture.machineFrame({timestamp:r.iso(),state:{state:'espresso',substate:'preinfusion'}});
    r.advance(1000);capture.machineFrame({timestamp:r.iso(),state:{state:'espresso',substate:'pouring'}});
    capture.machineFrame({timestamp:new Date(r.now()-500).toISOString(),state:{state:'espresso',substate:'pouringDone'}});
    assert.equal(capture.active.stopAt,null);
    capture.reset('controller_connection_lost');flush();
    const finish=JSON.parse(box.state.queue.at(-1));
    assert.ok(finish.data.quality_flags.includes('controller_connection_lost'));
    assert.ok(finish.data.quality_flags.includes('stale_machine_frame'));
    assert.equal(finish.data.completion_reason,'manual_or_unknown');
    capture.shotFrame({event:'state',timestamp:r.iso(),state:'preheating',shotId:'interrupted',scaleConnected:true});
    capture.shotFrame({event:'state',timestamp:r.iso(),state:'preheating',shotId:'interrupted',scaleConnected:true});
    assert.equal(capture.active,null);
});
