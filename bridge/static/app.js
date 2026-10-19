'use strict';
const $ = id => document.getElementById(id);
let current = null, busy = false, activeId = null, requestId = null, serverOffline = false;
const show = (id, visible) => $(id).classList.toggle('hidden', !visible);
const text = (id, value) => { $(id).textContent = value; };
const number = value => Number.isFinite(value) ? Number(value.toFixed(1)).toString() : '—';
async function api(path, body) {
  const controller=new AbortController(),deadline=setTimeout(()=>controller.abort(),5000);
  try {
    const response = await fetch('/api/v1/' + path, {method:body === undefined ? 'GET' : 'POST',
      signal:controller.signal, headers:{'Content-Type':'application/json'},
      body:body === undefined ? undefined : JSON.stringify(body)});
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || 'Request failed');
    return data;
  } finally {clearTimeout(deadline);}
}
const labels = {adjusted:'Burrs adjusted', within_tolerance:'Target updated · within tolerance', movement_pending:'Burr movement unconfirmed', held:'Setting held', recorded:'History only',
  unchanged:'Target unchanged', validating:'Checking shot', delivering:'Verifying on grinder', verified:'Recorded', rejected:'Not sent', uncertain:'Check grinder'};
function render(data) {
  current = data;
  const ready = !serverOffline && data.automation?.sync_ready === true;
  text('connection', serverOffline ? 'Server offline' : ready ? '● READY' : data.automation ? ({automatic_feedback_disabled:'Feedback disabled',tablet_connection_unavailable:'Tablet offline',grinder_connection_unavailable:'Grinder offline'}[data.automation.sync_warning] || 'Action needed') : 'Manual input');
  $('connection').classList.toggle('connected', ready);
  const r = data.recipe, a = data.active;
  text('dose', number(r.dose_g) + ' g'); text('yield', number(r.yield_g) + ' g'); text('target-time', number(r.time_s) + ' s');
  text('physical', !serverOffline && data.sensor_valid ? number(data.physical_um) + ' µm' : '—');
  text('target-gap', 'Target ' + number(r.target_um) + ' µm');
  text('workflow-note', data.automation ? 'Preinfusion end → extraction stop' :
    'First drip → extraction stop · Target '+number(r.yield_g)+' g');
  const notice = data.error || (!data.grinder_connected ? 'Grinder offline · Check Device setup' : '');
  text('notice', notice); show('notice', !!notice);
  const phase = a?.phase || 'idle';
  const headings = {idle:'Ready to grind', armed:'Arm feedback', brewing:'Enter result',
    validating:'Validating', finishing:'Sending', attention:'Check cycle', aborting:'Cancelling'};
  const instructions = {idle:'Grind → arm feedback → extract.', armed:'Press grinder once · Time from first drip',
    brewing:'Actual weight · Time from first drip', validating:'Saved · Verifying grind',
    finishing:'Awaiting native receipt',
    attention:a?.message || 'Check grinder before clearing', aborting:'Feedback cancelled'};
  text('shot-heading', headings[phase] || phase); text('shot-instruction', instructions[phase]);
  text('grind-label', a?.grind_id ? 'Grind #' + a.grind_id + ' · ' + number(a.dose_g) + ' g dose' : 'No active grind');
  const step = phase === 'idle' ? 1 : phase === 'armed' ? 2 : 3;
  for(let i=1;i<=3;i++) { $('step-'+i).classList.toggle('done', i<step); $('step-'+i).classList.toggle('current', i===step); }
  if (a?.cycle_id !== activeId) {
    activeId = a?.cycle_id; requestId = null; $('weight').value = ''; $('seconds').value = ''; $('record-only').checked = false; text('form-error', '');
  }
  const canSend = phase === 'brewing' && !!a?.grind_uuid && data.grinder_connected && !serverOffline;
  show('record-only-wrap', canSend && !a.adjustment_eligible);
  $('submit').disabled = !canSend || busy || (!a.adjustment_eligible && !$('record-only').checked);
  show('cancel', !!a && ['armed','brewing','attention'].includes(phase));
  text('cancel', phase === 'attention' ? 'Clear checked cycle' : 'Cancel wait');
  const latest = data.shots[0];
  if(latest) {
    text('outcome-title', labels[latest.outcome || latest.state] || latest.state);
    text('outcome-message', latest.message || 'Checking grinder');
    const change = latest.target_after_um != null ? number(latest.target_before_um) + ' → ' + number(latest.target_after_um) + ' µm target' : '';
    text('outcome-detail', change); show('outcome-detail', !!change);
  }
  const tbody = $('history'); tbody.replaceChildren();
  const owned = new Set(data.shots.map(s=>s.grind_id));
  const rows = data.shots.map(s=>({id:s.grind_id, weight:s.input.weight_g, time:s.input.duration_ms/1000,
    gap:s.target_after_um, result:labels[s.outcome || s.state] || s.state}));
  for (const b of data.native_history) if(!owned.has(b.grind_id)) rows.push({id:b.grind_id, weight:b.brew_mass/1000,
    time:b.brew_duration/1000, gap:null, result:'Earlier grinder record'});
  for (const row of rows.sort((a,b)=>b.id-a.id).slice(0,30)) {
    const tr=document.createElement('tr');
    for(const value of ['#'+row.id,number(Number(row.weight))+' g',number(row.time)+' s',row.gap == null ? '—' : number(row.gap)+' µm',row.result]) {
      const td=document.createElement('td'); td.textContent=value; tr.append(td);
    } tbody.append(tr);
  }
  if(!rows.length) {const tr=document.createElement('tr'),td=document.createElement('td');td.colSpan=5;td.textContent='No results';tr.append(td);tbody.append(tr);}
  text('updated', data.last_native_read ? 'Grinder read ' + new Date(data.last_native_read*1000).toLocaleTimeString([], {hour:'2-digit',minute:'2-digit'}) : 'Reading grinder…');
  renderAutomation(data.automation);
}
const words = value => (value || '—').replaceAll('_', ' ');
let renderedProfile = null, renderedSlots = null;
function renderAutomation(auto) {
  show('automation', !!auto); show('controller-journal', !!auto);
  show('auto-shot', !!auto); show('shot-form', !auto);
  if (!auto) return;
  const modes = {observe:'Observation only',xenia_one_press:'One arming press'};
  const operating = (auto.supervised_test ? 'Supervised test · ' : !auto.enabled ? 'Feedback disabled · ' : 'Automatic sync · ') + modes[auto.feedback_mode]
    + (auto.validation?.daily_use ? ' · Validation incomplete' : '');
  text('mode-label','MAHLKÖNIG E64 WS · '+operating.toUpperCase());text('footer-mode',operating+' · Native target verification');
  text('profile-title',auto.profile?.title || 'No profile');
  text('controller-connection',serverOffline ? 'STATUS UNAVAILABLE' : auto.event_connected ? 'TABLET ONLINE' : 'TABLET OFFLINE');
  $('controller-connection').classList.toggle('connected',!serverOffline && auto.event_connected && auto.sync_ready);
  text('automation-summary',auto.summary);
  text('validation-notice',auto.validation?.daily_use ? 'Validation pending: '+auto.validation.outstanding_checks.join('; ')+'.' : '');
  show('validation-notice',!!auto.validation?.daily_use);
  const t = auto.targets;
  const effective = t.profile?.target;
  text('target-check', t.confirmed ? 'Verified · Slot '+t.recipe_index+' · '+number(t.yield_g)+' g / '+number(t.target_time_s)+' s' :
    t.reason === 'set_grinder_targets' ? 'Set grinder slot '+t.recipe_index+' to '+number(t.yield_g)+' g / '+number(t.target_time_s)+' s · Current: '+number(t.native?.recipe_brew_weight/1000)+' g / '+number(t.native?.recipe_brew_time/1000)+' s.' :
    auto.profile ? 'Profile setup required' : 'Enable tablet plugin');
  show('target-check',t.confirmed || t.reason==='set_grinder_targets');
  text('measurement-mode',auto.profile?.mode === 'auto' ? 'Scale preferred · '+number(auto.profile.fallback_target.value)+' mL automatic backup · uncalibrated estimate' : auto.profile?.mode === 'scale' ? 'Finalized scale grams' : 'Pour volume × '+number(t.grams_per_ml || 1)+' g/mL · uncalibrated');
  text('time-basis','After preinfusion');text('yield-basis',auto.profile?.mode === 'volume' ? 'Grinder target · estimated grams' : 'Grinder target · grams');
  text('machine-state',serverOffline ? '—' : auto.machine.reachable ? words(auto.machine.state)+' / '+words(auto.machine.substate) : 'Tablet unreachable · feedback paused');
  text('wake-state',words(auto.wake.state));
  if (effective) text('automation-summary',auto.summary+' · Target '+number(effective.value)+' '+effective.unit+'.');
  const slots=JSON.stringify(auto.recipes);
  if (slots !== renderedSlots) {
    const select=$('recipe-slot'),chosen=select.value;select.replaceChildren();
    for (const recipe of auto.recipes) {const option=document.createElement('option');option.value=recipe.index;option.textContent='Slot '+recipe.index+' · '+number(recipe.dose_g)+' g dose';select.append(option);}
    select.value=t.recipe_index || chosen || auto.recipes[0]?.index || '';renderedSlots=slots;
  }
  if (auto.profile?.id !== renderedProfile) {
    renderedProfile=auto.profile?.id;$('suitable').checked=false;$('sync-time').value=t.target_time_s || 24;
    $('profile-setup').open=t.reason==='profile_setup_required';text('commission-error','');
  }
  $('commission').disabled=!auto.profile || !!current.active || auto.machine.state==='Espresso';
  const latest=auto.shots[0],delivery=latest?.delivery;
  const phase=current.active?.phase || 'idle';
  text('shot-heading',phase==='idle' ? (!serverOffline && auto.sync_ready ? 'Ready to grind' : 'Action needed') : phase==='armed' && auto.feedback_mode==='xenia_one_press' ? 'Arm feedback' :
    phase==='armed' || phase==='brewing' ? 'Extract on Decent' : phase==='attention' ? 'Check cycle' : 'Verifying result');
  text('shot-instruction',phase==='attention' ? current.active.message : auto.summary);
  if (latest?.feedback_status==='awaiting_arm') text('shot-heading','Press grinder to send');
  if (current.maintenance) text('shot-heading','Updating');
  text('step-two-text',auto.feedback_mode==='xenia_one_press' ? (auto.event_contract===2 ? 'Arm before or after' : 'Arm once') : 'Prepare puck');text('step-three-text','Start on Decent');
  text('auto-shot-result', latest ? (delivery?.message || (latest.feedback_status==='awaiting_final_mass' ? 'Final weight pending' : latest.feedback_status==='history_only' ? 'History only: '+words(latest.reason) : words(latest.feedback_status))) : 'No captured shot');
  const body=$('controller-history');body.replaceChildren();
  for (const shot of auto.shots) {
    const m=shot.measurements || {},raw=shot.reported_measurements || {},d=shot.delivery;
    const outcome=d ? (d.state==='verified' ? 'Recorded by grinder · '+(labels[d.outcome] || words(d.outcome)) : d.exposed_at ? 'Sent · '+words(d.state) : 'Saved · '+words(d.state)) :
      shot.feedback_status==='history_only' ? 'History only · '+words(shot.reason) : 'Saved · '+words(shot.feedback_status);
    const tr=document.createElement('tr');
    const values=[new Date(shot.created*1000).toLocaleTimeString([], {hour:'2-digit',minute:'2-digit'})+(shot.binding ? ' / #'+shot.binding.grind_id : ''),
      m.pour_volume_ml==null ? '—' : number(m.pour_volume_ml)+' mL',m.estimated_mass_g==null ? '—' : number(m.estimated_mass_g)+' g · uncalibrated',
      m.scale_mass_g==null ? '—' : number(m.scale_mass_g)+' g'+(m.scale_mass_basis==='finalized' ? ' · final' : ' · at stop'),m.duration_ms==null ? '—' : number(m.duration_ms/1000)+' s',
      outcome+(m.selected_source ? ' · '+(m.selected_source==='scale' ? 'Scale measurement' : 'Volume estimate') : '')+(m.fallback_reason ? ' · '+words(m.fallback_reason) : '')];
    for(const value of values) {const td=document.createElement('td');td.textContent=value;tr.append(td);}body.append(tr);
  }
  if (!auto.shots.length) {const tr=document.createElement('tr'),td=document.createElement('td');td.colSpan=6;td.textContent='No captures';tr.append(td);body.append(tr);}
}
async function refresh() {try {const [status,setup]=await Promise.all([api('status'),api('setup')]);serverOffline=false;render(status);renderSetup(setup);} catch(error) {serverOffline=true;if(current)render(current);if(!$('dashboard').classList.contains('hidden')) {text('notice', 'Server unavailable · Retrying');show('notice',true);$('submit').disabled=true;}}}
$('shot-form').addEventListener('submit', async event=>{
  event.preventDefault(); if(busy || !current?.active) return; busy=true;$('submit').disabled=true;text('form-error','');
  // Keep the same ID after an uncertain HTTP response; retries cannot double-submit.
  requestId ||= crypto.randomUUID ? crypto.randomUUID() : Date.now()+'-'+Math.random().toString(36).slice(2);
  const data={source:'manual',shot_id:requestId,grind_uuid:current.active.grind_uuid,
    weight_g:Number($('weight').value),duration_ms:Math.round(Number($('seconds').value)*1000),
    timing_basis:'first_drip',valid:true,record_only:!current.active.adjustment_eligible && $('record-only').checked};
  try {await api('shots',data);await refresh();}catch(e){text('form-error',e.message);}finally {busy=false;if(current)render(current);}
});
$('record-only').addEventListener('change',()=>{if(current)render(current);});
$('cancel').addEventListener('click',async()=>{try{await api('cancel',{});await refresh();}catch(e){text('form-error',e.message);}});
$('refresh').addEventListener('click',async()=>{try{await api('refresh',{});await refresh();}catch(e){text('notice',e.message);show('notice',true);}});
$('commission-form').addEventListener('submit',async event=>{
  event.preventDefault();text('commission-error','');const auto=current?.automation;if(!auto?.profile)return;
  try {await api('controllers/'+encodeURIComponent(auto.controller_id)+'/profiles/'+encodeURIComponent(auto.profile.id)+'/commission',{
    revision:auto.profile.revision,recipe_index:Number($('recipe-slot').value),target_time_s:Number($('sync-time').value),
    grams_per_ml:1,suitable_for_feedback:$('suitable').checked});await refresh();}
  catch(error){text('commission-error',error.message);}
});


let setupLoaded=false, enrolled=null;
function renderSetup(setup) {
  text('setup-summary',setup.required_action);
  if (!setupLoaded) {
    $('setup-panel').open=!setup.complete;
    if (!setup.complete) $('dashboard').insertBefore($('device-settings'),$('automation'));
    $('controller-type').value=setup.controller_type;
    $('controller-url').value=setup.controller_url;
    $('grinder-host').value=setup.grinder_host;
    $('feedback-enabled').checked=setup.feedback_enabled;
    setupLoaded=true;
  }
}
$('inspect-certificates').addEventListener('click',async()=>{
  text('setup-error','');enrolled=null;$('save-devices').disabled=true;
  try {
    enrolled=await api('setup/certificates',{schema_version:1,host:$('grinder-host').value.trim()});
    text('certificate-fingerprints',Object.entries(enrolled.certificates).map(([port,pin])=>port+': '+pin).join('\n'));
    $('confirm-certificates').checked=false;$('save-devices').disabled=false;
  } catch(error) {text('setup-error',error.message);}
});
$('grinder-host').addEventListener('input',()=>{enrolled=null;$('save-devices').disabled=true;text('certificate-fingerprints','');});
$('devices-form').addEventListener('submit',async event=>{
  event.preventDefault();text('setup-error','');
  if (!enrolled || enrolled.host!==$('grinder-host').value.trim()) return;
  const data={schema_version:1,controller_type:$('controller-type').value,controller_url:$('controller-url').value.trim(),grinder_host:enrolled.host,certificates:enrolled.certificates};
  if ($('owner-password').value) data.owner_password=$('owner-password').value;
  try {await api('setup/devices',data);$('owner-password').value='';setupLoaded=false;await refresh();}
  catch(error){text('setup-error',error.message);}
});
$('feedback-form').addEventListener('submit',async event=>{
  event.preventDefault();text('feedback-error','');
  try {await api('setup/feedback',{schema_version:1,enabled:$('feedback-enabled').checked,acknowledge_beta:$('acknowledge-beta').checked});await refresh();}
  catch(error){text('feedback-error',error.message);}
});
const appearance=$('appearance'),dark=matchMedia('(prefers-color-scheme: dark)');
try {appearance.value=localStorage.getItem('mahlkonig-decent-sync.appearance') || 'auto';} catch (_) {}
function applyAppearance() {document.documentElement.dataset.theme=appearance.value==='auto' ? (dark.matches ? 'dark':'light') : appearance.value;}
appearance.addEventListener('change',()=>{try {localStorage.setItem('mahlkonig-decent-sync.appearance',appearance.value);} catch (_) {}applyAppearance();});
dark.addEventListener('change',applyAppearance);applyAppearance();
refresh();setInterval(refresh,2000);
