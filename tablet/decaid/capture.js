/* Live identity and boundaries precede finalized records. Imports never arm feedback. */
class SyncCapture {
    constructor(outbox,offer,fetchRecord,notify) {
        this.outbox=outbox;this.offer=offer;this.fetchRecord=fetchRecord;this.notify=notify;
        this.workflow=null;this.profile=null;this.active=null;this.pending=new Map();
        this.baseline=false;this.machine=null;this.scale=null;this.lastFrameTime=0;
    }
    setWorkflow(workflow) {
        const next=SyncFields.profile(workflow);
        if (this.active && (!next || next.revision!==this.active.profile.revision)) this.flag('workflow_changed');
        this.workflow=SyncFields.copy(workflow);this.profile=next;
    }
    flag(reason) {if (this.active && !this.active.flags.includes(reason)) this.active.flags.push(reason);}
    reset(reason) {
        if (this.active) {
            this.flag(reason);this.active.reason=reason;
            this.finish(this.active.stopAt || Date.now(),false);
        }
        this.baseline=false;this.machine=null;this.scale=null;this.lastFrameTime=0;
    }
    scaleFrame(frame) {
        if (SyncFields.fresh(frame?.timestamp) && Number.isFinite(frame.weight)) this.scale=SyncFields.copy(frame);
    }
    shotFrame(frame) {
        // The first frame of every connection is a reconnect snapshot, even if it is preheating.
        if (!this.baseline) {this.baseline=true;this.lastFrameTime=SyncFields.stamp(frame?.timestamp) || 0;return;}
        const stamp=SyncFields.stamp(frame?.timestamp);
        if (!SyncFields.fresh(frame?.timestamp) || stamp<this.lastFrameTime) {this.flag('stale_shot_frame');return;}
        this.lastFrameTime=stamp;
        if (!this.active && frame.event==='state' && frame.state==='preheating' &&
            /^[A-Za-z0-9_.:-]{1,120}$/.test(frame.shotId || '') && this.profile && this.outbox.state?.server_id) {
            if (!this.outbox.markShot(frame.shotId)) return;
            const offered=this.offer();
            const cycle=offered && offered.session_id===this.outbox.state.session &&
                offered.profile_revision===this.profile.revision && offered.expires_at*1000>stamp ? SyncFields.copy(offered) : null;
            this.active={id:frame.shotId,start:stamp,profile:SyncFields.copy(this.profile),cycle,
                pi:null,stopAt:null,reason:null,flags:[],finished:false};
            if (frame.scaleConnected!==true || !this.scale || !SyncFields.fresh(this.scale.timestamp)) this.flag('scale_unavailable_at_start');
            this.emit('shot_started',0,{measurement_source:'scale'},stamp);
        }
        const shot=this.active;
        if (!shot || shot.id!==frame.shotId) return;
        if (frame.scaleLost===true || frame.scaleConnected!==true) this.flag('scale_lost');
        const decision=frame.decision;
        if (decision && ['stop','abort','terminal'].includes(decision.kind)) {
            if (!shot.reason || shot.reason==='targetWeight') shot.reason=decision.reason;
            if (decision.kind==='abort' || decision.kind==='terminal') this.flag('shot_'+decision.reason);
        }
        if (shot.stopAt && shot.reason) this.finish(shot.stopAt,true);
        else if (frame.state==='finished' || ['abort','terminal'].includes(decision?.kind)) {
            if (!shot.stopAt) this.flag('missing_extraction_stop');
            this.finish(shot.stopAt || stamp,!!shot.stopAt);
        }
    }
    machineFrame(frame) {
        const previous=this.machine;
        if (!SyncFields.fresh(frame?.timestamp) || (previous && SyncFields.stamp(frame.timestamp)<SyncFields.stamp(previous.timestamp))) {
            this.flag('stale_machine_frame');return;
        }
        this.machine=SyncFields.copy(frame);
        const shot=this.active,stamp=SyncFields.stamp(frame.timestamp);
        if (!shot || stamp<shot.start) return;
        const current=frame.state || {},before=previous?.state || {};
        if (!shot.pi && current.state==='espresso' && current.substate==='pouring' &&
            before.state==='espresso' && ['preinfusion','preparingForShot','preheating'].includes(before.substate) &&
            SyncFields.stamp(previous.timestamp)>=shot.start) {
            shot.pi=stamp;this.emit('preinfusion_complete',stamp-shot.start,{},stamp);
        }
        if (shot.pi && !shot.stopAt && before.state==='espresso' && before.substate==='pouring' &&
            (current.state!=='espresso' || current.substate==='pouringDone')) {
            shot.stopAt=stamp;
            if (shot.reason) this.finish(stamp,true);
        }
    }
    emit(kind,elapsed,extra,at,shot=this.active) {
        return this.outbox.enqueue(kind,{profile:shot.profile,elapsed_ms:Math.max(0,Math.round(elapsed)),...extra},shot.id,shot.cycle,at);
    }
    finish(at,knownStop) {
        const shot=this.active;if (!shot || shot.finished) return;
        if (!shot.pi) this.flag('missing_preinfusion_boundary');
        if (!knownStop) this.flag('missing_extraction_stop');
        const scale=this.scale && SyncFields.fresh(this.scale.timestamp) && this.scale.weight>0 ?
            {value:this.scale.weight,unit:'g',source:'scale'} : null;
        if (!scale) this.flag('missing_stop_scale');
        shot.stopAt=at;shot.finished=true;shot.elapsed=Math.max(0,Math.round(at-shot.start));
        shot.completion=shot.reason==='targetWeight' ? 'target_weight' : 'manual_or_unknown';
        this.emit('shot_finished',shot.elapsed,{completion_reason:shot.completion,stop_reason:shot.reason || 'unknown',
            measurement_source:'scale',scale_mass:scale,scale_mass_basis:'extraction_stop',quality_flags:shot.flags.slice()},at,shot);
        this.pending.set(shot.id,shot);this.active=null;
        this.notify('Shot captured; waiting for the finalized record');
    }
    edited(id) {const shot=this.pending.get(id);if (shot && !shot.flags.includes('record_edited')) shot.flags.push('record_edited');}
    async stored(id) {
        const shot=this.pending.get(id);
        if (!shot || shot.fetching || Date.now()-shot.stopAt>20000) return;
        shot.fetching=true;
        try {
            const record=await this.fetchRecord(id);
            if (this.pending.get(id)!==shot || Date.now()-shot.stopAt>20000) return;
            const flags=shot.flags.slice();
            const matching=record?.id===id && record.createdAt && record.createdAt===record.updatedAt &&
                Math.abs(SyncFields.stamp(record.timestamp)-shot.start)<=2000 &&
                SyncFields.profile(record.workflow)?.revision===shot.profile.revision;
            if (!matching) flags.push('final_record_mismatch');
            if (record?.stopReason!==shot.reason) flags.push('stop_reason_mismatch');
            const rows=Array.isArray(record?.measurements) ? record.measurements : [];
            const lastScale=rows.map(row=>row.scale).filter(scale=>scale && Number.isFinite(scale.weight) &&
                Math.abs(SyncFields.stamp(scale.timestamp)-shot.stopAt)<=6000).pop();
            const mass=record?.annotations?.actualYield;
            if (!lastScale || !Number.isFinite(mass) || mass<=0 || mass>250) flags.push('missing_final_scale_mass');
            const volume=rows.length ? rows[rows.length-1].volume : null;
            const water=Number.isFinite(volume) && volume>=0 && volume<=1000 ? {value:volume,unit:'mL',source:'decaid_water_volume'} : null;
            this.emit('shot_finalized',shot.elapsed,{scale_mass:flags.length ? null : {value:mass,unit:'g',source:'scale'},
                scale_mass_basis:'decaid_finalized_weight',quality_flags:flags,water_volume:water,
                provenance:{live_shot_id:shot.id,record_id:record?.id || null,unedited:!!matching}},Date.now(),shot);
            this.pending.delete(id);
        } catch (_) {shot.fetching=false;this.notify('Final record unavailable; shot will remain history-only');}
    }
    expire() {
        if (this.active && Date.now()-this.active.start>290000) this.reset('incomplete_shot_lifecycle');
        for (const [id,shot] of this.pending) if (Date.now()-shot.stopAt>20000) this.pending.delete(id);
    }
}
