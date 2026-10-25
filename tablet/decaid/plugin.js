/* Decaid 0.9.0 host adapter. Only GET requests and controller events are sent. */
function createPlugin(host) {
    'use strict';
    const VERSION='0.0.0';
    const ID='mahlkonig_decent_sync.reaplugin';
    const API='http://localhost:8080/api/v1';
    const LOGO='__MAHLKONIG_LOGO_BASE64__'; // Expanded from the attributed canonical asset by the release builder.
    let settings={},url='',closed=false,loaded=false,timer=null,serial=0,notice='Loading',display={},displayAt=0,cycle=null,lastProfile='',telemetryPoll=false;
    const channels=new Map();
    const report=message=>{notice=message;};
    const outbox=new SyncOutbox(host,()=>{if (loaded && !closed) tick(false);});
    const capture=new SyncCapture(outbox,()=>cycle,async id=>localGet('/shots/'+encodeURIComponent(id)),report);
    async function localGet(path) {
        const response=await fetch(API+path,{method:'GET'});
        if (!response.ok) throw new Error('Decaid data unavailable');
        const body=await response.text();
        if (body.length>2097152) throw new Error('Decaid response too large');
        return JSON.parse(body);
    }
    function connected(name) {return !!channels.get(name)?.handle;}
    function publishDisplay(message) {
        if (message.server_id!==outbox.state?.server_id) return;
        if (message.display) {display={...message.display,summary:message.summary};displayAt=Date.now();}
        if (message.cycle?.session_id===outbox.state.session) cycle=message.cycle;
        else cycle=null;
    }
    function serverMessage(message) {
        if (message.type==='hello') {
            try {outbox.bind(message,url);channels.get('server').verified=true;telemetryPoll=message.telemetry_poll===1;lastProfile='';notice='Connected';tick(false);}
            catch(error) {report(error.message);disconnect('server',false);}
        } else if (message.server_id!==outbox.state?.server_id) {
            report('Server identity changed; saved events retained');disconnect('server',false);
        } else if (message.type==='ack') {
            if (outbox.acknowledge(message)) publishDisplay(message);
        } else if (message.type==='status') {
            if (telemetryPoll && message.poll?.accepted===false) lastProfile='';
            publishDisplay(message);
        }
        else if (message.type==='error') {
            if (outbox.expireTelemetry(message)) {lastProfile='';displayAt=0;cycle=null;}
            else {report('Server rejected an event; saved events retained');outbox.error=notice;}
        }
    }
    function disconnect(name,retry=true) {
        const entry=channels.get(name);
        if (!entry) return;
        entry.retired=true;channels.delete(name);
        if (entry.handle) host.transport.close(entry.handle).catch(()=>{});
        if (name==='server') {displayAt=0;outbox.sent=null;cycle=null;}
        else if (name==='scale') {capture.scale=null;capture.flag('scale_connection_lost');}
        else capture.reset('controller_connection_lost');
        if (!retry) channels.set(name,{blocked:true});
    }
    async function connect(name,address,receive) {
        if (closed || channels.has(name)) return;
        const entry={serial:++serial};channels.set(name,entry);
        try {
            const opened=await host.transport.open({kind:'websocket',url:address});
            if (closed || entry.retired || channels.get(name)!==entry) {await host.transport.close(opened.handle);return;}
            entry.handle=opened.handle;
            host.transport.onEvent(entry.handle,event=>{
                if (closed || entry.retired || channels.get(name)!==entry) return;
                if (event.type==='close' || event.type==='error') {disconnect(name);report('Connection interrupted; saved events retained');return;}
                if (event.type!=='data' || event.dataType!=='text' || typeof event.data!=='string' || event.data.length>131072) return;
                try {receive(JSON.parse(event.data));} catch (_) {report('Invalid controller data; feedback paused');disconnect(name);}
            });
        } catch (_) {if (channels.get(name)===entry) channels.delete(name);report('Connection unavailable; retrying');}
    }
    async function send(text) {
        const entry=channels.get('server');
        if (!entry?.handle || !entry.verified) return false;
        try {await host.transport.send(entry.handle,{type:'text',data:text});return true;}
        catch (_) {disconnect('server');return false;}
    }
    function tick(schedule=true) {
        if (closed || !loaded || !outbox.ready) return;
        capture.expire();
        connect('server',url.replace(/^http:/,'ws:')+'/api/v1/controllers/decaid-de1/ws',serverMessage);
        connect('shots','ws://localhost:8080/ws/v1/machine/shotState',frame=>capture.shotFrame(frame));
        connect('machine','ws://localhost:8080/ws/v1/machine/snapshot',frame=>capture.machineFrame(frame));
        connect('scale','ws://localhost:8080/ws/v1/scale/snapshot',frame=>capture.scaleFrame(frame));
        const server=channels.get('server');
        if (server?.verified && outbox.state.server_id) {
            const raw=outbox.next();
            if (raw && (!outbox.sent || Date.now()-outbox.sent.at>5000)) {
                outbox.sent={raw,at:Date.now()};
                send('{"type":"event","server_id":'+JSON.stringify(outbox.state.server_id)+',"event":'+raw+'}');
            } else if (!outbox.writing && !outbox.state.queue.length && capture.profile && Date.now()-(outbox.lastHeartbeat || 0)>2000) {
                outbox.lastHeartbeat=Date.now();
                const profile=SyncFields.canonical(capture.profile);
                if (!telemetryPoll || profile!==lastProfile) {
                    if (outbox.enqueue('profile',{profile:capture.profile,measurement_ready:connected('scale') && !!capture.scale && SyncFields.fresh(capture.scale.timestamp)})) lastProfile=profile;
                }
            }
            if (schedule) send(JSON.stringify({type:'status',server_id:outbox.state.server_id,
                session_id:outbox.state.session,measurement_ready:connected('scale') && !!capture.scale && SyncFields.fresh(capture.scale.timestamp)}));
        }
    }
    function pulse() {if (closed) return;tick();timer=setTimeout(pulse,2000);}
    function status() {
        const fresh=Date.now()-displayAt<10000;
        const ready=fresh && display.sync_ready===true && connected('shots') && connected('machine') && connected('scale') &&
            !!capture.scale && SyncFields.fresh(capture.scale.timestamp) && !outbox.error;
        return {name:'Mahlkönig - Decent sync',version:VERSION,ready,summary:outbox.error || (fresh ? display.summary || notice : notice),
            server:url,saved_events:outbox.state?.queue.length || 0,profile:capture.profile?.title || null,
            grind:fresh && Number.isFinite(display.grind_position?.um) && display.grind_position.age_s+(Date.now()-displayAt)/1000<35 ? display.grind_position.um : null,
            notice:fresh ? display.notice || null : null,appearance:settings.appearance || 'auto'};
    }
    const escape=value=>String(value??'').replace(/[&<>"']/g,char=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
    function page() {
        const state=status();
        return '<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"><title>Mahlkönig - Decent sync</title><style>'+
            ':root{color-scheme:light dark}body{font:15px/1.4 system-ui;margin:auto;padding:16px;max-width:850px;background:#eff3f0;color:#192b23}header{display:flex;align-items:center;gap:22px;margin:12px 0 20px}header img{width:110px}h1{font-size:22px;line-height:1.2}.panel{background:#fff;padding:16px;border-radius:10px;margin:14px 0}small{color:#607168}.notice{border-left:4px solid #a8362a;padding-left:16px}a{color:inherit}@media(prefers-color-scheme:dark){body:not([data-theme=light]){background:#141b17;color:#eef4f0}body:not([data-theme=light]) .panel{background:#202824}}body[data-theme=dark]{background:#141b17;color:#eef4f0}body[data-theme=dark] .panel{background:#202824}'+
            '</style></head><body data-theme="'+escape(state.appearance)+'"><header><img alt="Mahlkönig" src="data:image/png;base64,'+LOGO+'"><div><h1>Mahlkönig - Decent sync</h1><small>Community integration</small></div></header>'+
            '<section class="panel"><h2 id="ready">'+(state.ready?'Ready':'Action needed')+'</h2><p id="summary">'+escape(state.summary)+'</p><p id="notice" class="notice">'+escape(state.notice?.detail || '')+'</p><p>Grind <strong id="grind">'+escape(state.grind??'—')+'</strong> µm</p><p>Saved events <span id="saved">'+state.saved_events+'</span></p></section>'+
            '<section class="panel"><h2>Settings</h2><p>Address / theme: native plugin settings · Targets / profile: <a href="'+escape(url)+'">server setup</a></p><p>Measurement: finalized scale grams<br>Scale loss: history only<br>Extraction: Decent · Feedback: grinder press</p></section>'+
            '<details class="panel"><summary>About</summary><p>Version '+VERSION+' · Decaid 0.9.0 · GPL-3.0-or-later.</p><p>Logo: Hemro Group; SVG by Oelna, rasterized. CC BY-SA 3.0. See bundled ASSET-NOTICES.md. No manufacturer endorsement is implied.</p></details>'+
            '<script>setInterval(async()=>{try{const r=await fetch("status");const s=await r.json();for(const [id,value] of Object.entries({ready:s.ready?"Ready":"Action needed",summary:s.summary,notice:s.notice?.detail||"",grind:s.grind??"—",saved:s.saved_events})){document.getElementById(id).textContent=value;}}catch(_){document.getElementById("ready").textContent="Connection unavailable";}},2000);</script></body></html>';
    }
    return {
        id:ID,version:VERSION,
        async onLoad(values) {
            settings=values || {};
            try {
                url=SyncFields.serverUrl(settings.serverAddress);
                const info=await localGet('/info');
                if (info.version!=='0.9.0') throw new Error('This plugin requires Decaid 0.9.0');
                const workflow=await localGet('/workflow');
                if (!capture.workflow) capture.setWorkflow(workflow);
                loaded=true;outbox.load();pulse();
            } catch(error) {report(error.message);}
        },
        onUnload() {
            closed=true;clearTimeout(timer);
            capture.active=null;capture.pending.clear();
            for (const name of [...channels.keys()]) disconnect(name,false);
        },
        onEvent(event) {
            if (closed) return;
            if (event.name==='storageRead' && event.payload?.key==='sync-state') outbox.read(event.payload.value);
            else if (event.name==='storageWrite') outbox.written(event.payload);
            else if (event.name==='workflowUpdated') capture.setWorkflow(event.payload);
            else if (event.name==='shotStored') capture.stored(event.payload?.id);
            else if (event.name==='shotUpdated') capture.edited(event.payload?.id);
            else if (event.name==='shutdown') this.onUnload();
        },
        __httpRequestHandler(request) {
            if (request.method!=='GET') return {status:405,body:'Read-only view'};
            if (request.endpoint==='status') return {status:200,headers:{'Content-Type':'application/json','Cache-Control':'no-store'},body:JSON.stringify(status())};
            if (request.endpoint!=='settings') return {status:404,body:'Unknown view'};
            return {status:200,headers:{'Content-Type':'text/html; charset=utf-8','Cache-Control':'no-store'},body:page()};
        }
    };
}
