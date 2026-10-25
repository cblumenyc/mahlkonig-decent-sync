/* Exact event bytes become sendable only after Decaid acknowledges persistent storage. */
class SyncOutbox {
    constructor(host,changed) {
        this.host=host;this.changed=changed;this.state=null;this.writing=null;this.dirty=false;this.durable=null;
        this.error='Loading saved events';this.sent=null;this.ackAt=0;this.ready=false;
    }
    load() {this.host.storage({type:'read',key:'sync-state',namespace:'mahlkonig_decent_sync.reaplugin'});}
    read(value) {
        if (this.state) return;
        const valid=value=> {
            if (!value || value.schema!==1 || !Array.isArray(value.queue) || value.queue.length>1000 ||
                !Array.isArray(value.seen) || value.seen.length>512 || value.seen.some(id=>typeof id!=='string') ||
                !Number.isSafeInteger(value.revision) || value.revision<0 || !Number.isSafeInteger(value.boot) || value.boot<0 ||
                (value.server_id!==null && !/^[a-f0-9-]{36}$/.test(value.server_id)) ||
                (value.server_url!==null && typeof value.server_url!=='string')) return false;
            let total=0;
            try {
                for (const raw of value.queue) {
                    if (typeof raw!=='string' || raw.length>65536 || (total+=raw.length)>2097152) return false;
                    const event=JSON.parse(raw);
                    if (event.schema_version!==2 || typeof event.event_id!=='string' || typeof event.session_id!=='string' ||
                        !Number.isSafeInteger(event.sequence) || typeof event.kind!=='string' || !event.data) return false;
                }
            } catch (_) {return false;}
            return true;
        };
        if (value!==null && value!==undefined && !valid(value)) {
            this.error='Saved event format is unsupported; retained without changes';return;
        }
        this.state=value ? SyncFields.copy(value) : {schema:1,revision:0,boot:0,server_id:null,server_url:null,queue:[],seen:[]};
        this.state.boot++;
        this.state.session='decaid-'+Date.now()+'-'+this.state.boot;
        this.state.sequence=0;this.error='';this.persist();
    }
    persist() {
        this.dirty=true;
        if (!this.state || this.writing) return;
        this.dirty=false;this.state.revision++;
        this.writing=SyncFields.copy(this.state);
        try {this.host.storage({type:'write',key:'sync-state',namespace:'mahlkonig_decent_sync.reaplugin',data:this.writing});}
        catch (_) {this.error='Could not save events; feedback paused';}
        // A missing storageWrite acknowledgement never makes draft events sendable.
    }
    written(value) {
        if (!this.writing || value?.revision!==this.writing.revision || SyncFields.canonical(value)!==SyncFields.canonical(this.writing)) return;
        this.durable=this.writing;this.writing=null;this.ready=true;
        if (this.dirty) this.persist();
        this.changed();
    }
    bind(identity,url) {
        if (!this.ready || !identity || identity.product!=='mahlkonig-decent-sync' || identity.controller_id!=='decaid-de1' ||
            !identity.controller_contracts?.includes(2) || !/^[a-f0-9-]{36}$/.test(identity.server_id)) throw new Error('Server is incompatible or Decaid is not selected');
        if (this.state.server_id && this.state.server_id!==identity.server_id) throw new Error('Server identity changed; saved events retained');
        if (!this.state.server_id && this.state.queue.length) throw new Error('Unpaired saved events require recovery');
        if (this.state.server_id!==identity.server_id || this.state.server_url!==url) {
            this.state.server_id=identity.server_id;this.state.server_url=url;this.persist();
        }
    }
    enqueue(kind,data,shot=null,cycle=null,occurred=Date.now()) {
        if (!this.ready || !this.state.server_id) return false;
        const sequence=this.state.sequence+1;
        const event={schema_version:2,event_id:this.state.session+'-'+sequence,session_id:this.state.session,sequence,
            occurred_at:new Date(occurred).toISOString(),kind,data};
        if (shot) {event.shot_id=shot;event.cycle=cycle;}
        const raw=JSON.stringify(event);
        if (raw.length>65536 || this.state.queue.length>=1000 || this.state.queue.reduce((n,row)=>n+row.length,0)+raw.length>2097152) {
            this.error='Saved event limit reached; reconnect before making another shot';return false;
        }
        this.state.sequence=sequence;this.state.queue.push(raw);this.persist();return true;
    }
    markShot(id) {
        if (!this.ready || this.state.seen.includes(id)) return false;
        this.state.seen.push(id);this.state.seen=this.state.seen.slice(-512);this.persist();return true;
    }
    next() {
        if (!this.ready || this.writing || this.dirty || this.error || !this.durable?.server_id) return null;
        return this.durable.queue[0] || null;
    }
    expireTelemetry(message) {
        const raw=this.next();
        if (!raw || message.server_id!==this.state.server_id || message.expired_telemetry!==true || message.durable!==false) return false;
        const event=JSON.parse(raw);
        if (message.event_id!==event.event_id || event.shot_id || !['profile','status'].includes(event.kind)) return false;
        this.state.queue.shift();this.sent=null;this.persist();return true;
    }
    acknowledge(message) {
        const raw=this.next();
        if (!raw || message.server_id!==this.state.server_id || message.durable!==true || message.event_id!==JSON.parse(raw).event_id) return false;
        this.state.queue.shift();this.sent=null;this.ackAt=Date.now();this.persist();return true;
    }
}
