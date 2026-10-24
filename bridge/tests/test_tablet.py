"""Exercise the actual Tcl extension without a machine or Tcl evaluation of shot files."""
import json
from pathlib import Path
import shutil
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import pytest

ROOT = Path(__file__).resolve().parents[2]
TCL = shutil.which('tclsh')
pytestmark = pytest.mark.skipif(not TCL, reason='Tcl runtime is required for extension validation')
PLUGIN = ROOT / 'tablet/mahlkonig_decent_sync/plugin.tcl'


def capture(tmp_path, scenario):
    result = subprocess.run([TCL, str(Path(__file__).with_name('tablet_fixture.tcl')),
        str(PLUGIN), str(tmp_path / 'outbox'), scenario], capture_output=True, text=True, check=True)
    assert not result.stderr
    return [json.loads(line) for line in result.stdout.splitlines()]


def test_real_extension_effective_target_timing_and_stop(tmp_path):
    events = capture(tmp_path, 'normal')
    assert [e['kind'] for e in events] == ['shot_started','preinfusion_complete','shot_finished']
    assert [e['data']['elapsed_ms'] for e in events] == [0,4000,28000]
    assert len({e['shot_id'] for e in events}) == 1
    p = events[0]['data']['profile']
    assert p['target'] == {'value':45,'unit':'mL'}
    assert p['effective_settings']['final_desired_shot_volume'] == '36'
    assert p['title'] == 'Quoted "café" profile'
    assert events[-1]['data']['completion_reason'] == 'target_volume'
    assert events[-1]['data']['pour_volume'] == {'value':45,'unit':'mL','source':'de1_pour_volume'}
    # Loading the code again does not delete the persistent outbox.
    subprocess.run([TCL, str(PLUGIN)], check=True)
    assert len(list((tmp_path/'outbox').glob('*.json'))) == 3


@pytest.mark.parametrize('scenario', ['manual','early_abort','malformed','scale','stale_scale','half_scale'])
def test_tablet_ineligible_and_scale_cases(tmp_path, scenario):
    events = capture(tmp_path, scenario)
    finish = [e['data'] for e in events if e['kind']=='shot_finished']
    if scenario == 'manual': assert finish[0]['completion_reason']=='manual_or_unknown'
    if scenario == 'early_abort':
        assert len(finish)==2 and 'missing_preinfusion_boundary' in finish[0]['quality_flags']
        assert len({e['shot_id'] for e in events})==2
    if scenario == 'malformed':
        assert finish[0]['pour_volume']['value'] is None
        assert 'missing_pour_volume' in finish[0]['quality_flags']
    if scenario == 'scale':
        assert finish[0]['scale_mass']=={'value':42.4,'unit':'g','source':'scale'}
        assert finish[0]['completion_reason']=='target_weight'
    if scenario in ('stale_scale','half_scale'): assert finish[0]['scale_mass'] is None


def test_async_outbox_retries_identical_event_until_matching_durable_ack(tmp_path):
    events = capture(tmp_path, 'normal')
    # Keep one item to exercise uncertain HTTP response, wrong ACK, then confirmed storage.
    files=sorted((tmp_path/'outbox').glob('*.json'))
    for path in files[1:]: path.unlink()
    seen=[]
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args): pass
        def do_GET(self):
            reply = {'product':'mahlkonig-decent-sync','server_id':'11111111-1111-1111-1111-111111111111',
                'controller_id':'classic-de1','controller_contracts':[1,2]}
            encoded=json.dumps(reply).encode()
            self.send_response(200);self.send_header('Content-Length',str(len(encoded)));self.end_headers();self.wfile.write(encoded)
        def do_POST(self):
            assert self.headers['X-Sync-Server-ID'] == '11111111-1111-1111-1111-111111111111'
            body=self.rfile.read(int(self.headers['Content-Length'])); seen.append(body)
            if len(seen)==1:
                self.close_connection=True
                return
            event=json.loads(body)
            reply={'server_id':'11111111-1111-1111-1111-111111111111','durable':True,'event_id':'wrong' if len(seen)==2 else event['event_id']}
            encoded=json.dumps(reply).encode()
            self.send_response(202);self.send_header('Content-Length',str(len(encoded)));self.end_headers();self.wfile.write(encoded)
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    script=tmp_path/'send.tcl'
    script.write_text('''source [lindex $argv 0]
package require json
package require http
set ::plugins::mahlkonig_decent_sync::outbox [lindex $argv 1]
set ::plugins::mahlkonig_decent_sync::settings(bridge_url) [lindex $argv 2]
set ::plugins::mahlkonig_decent_sync::settings(controller_id) classic-de1
set ::plugins::mahlkonig_decent_sync::settings(server_id) 11111111-1111-1111-1111-111111111111
# Accelerate retry timers only in this test, keeping the actual asynchronous HTTP path.
rename after real_after
proc after {delay args} {if {$delay == 5000} {set delay 50};uplevel 1 [list real_after $delay {*}$args]}
::plugins::mahlkonig_decent_sync::drain
real_after 1800 {set done 1}
vwait done
''')
    try:
        subprocess.run([TCL,str(script),str(PLUGIN),str(tmp_path/'outbox'),f'http://127.0.0.1:{server.server_port}'],check=True,timeout=5)
    finally:
        server.shutdown();thread.join();server.server_close()
    assert len(seen)==3 and seen[0]==seen[1]==seen[2]
    assert json.loads(seen[0])==events[0]
    assert not list((tmp_path/'outbox').glob('*.json'))


def test_runtime_counters_do_not_change_profile_revision(tmp_path):
    events = capture(tmp_path, 'counters')
    profile = events[0]['data']['profile']
    assert profile['adapter_version'] == 'classic-0.5'
    assert 'espresso_count' not in profile['effective_settings']
    assert 'espresso_clock' not in profile['effective_settings']
    assert profile['effective_settings']['pressure_end'] == '6'
    assert profile['effective_settings']['maximum_flow'] == '0'


def test_plugin_bootstrap_uses_live_variable_widget_and_complete_profile_keys(tmp_path):
    result=subprocess.run([TCL,str(Path(__file__).with_name('tablet_bootstrap.tcl')),str(PLUGIN),str(tmp_path)],
        capture_output=True,text=True,check=True)
    assert not result.stderr
    assert result.stdout.strip()=='bootstrap and profile contract passed'


@pytest.mark.parametrize('scenario',['fresh','absent','lost','reconnect','at_start_only'])
def test_automatic_mode_uses_native_stops_and_latches_volume_backup(tmp_path,scenario):
    result=subprocess.run([TCL,str(Path(__file__).with_name('tablet_auto_fixture.tcl')),str(PLUGIN),str(tmp_path),scenario],
        capture_output=True,text=True,check=True)
    assert not result.stderr
    events=[json.loads(line) for line in result.stdout.splitlines()]
    start,pi,finish=[e['data'] for e in events]
    assert start['profile']['mode']=='auto'
    assert start['profile']['target']=={'value':45,'unit':'g'}
    assert start['profile']['fallback_target']=={'value':45,'unit':'mL'}
    assert start['profile']==finish['profile']
    assert start['profile']['effective_settings']['final_desired_shot_volume']=='0'
    assert finish['quality_flags']==[]
    if scenario=='fresh':
        assert finish['completion_reason']=='target_weight'
        assert finish['measurement_source']=='scale'
        assert finish['fallback_reason'] is None
    elif scenario=='at_start_only':
        assert finish['completion_reason']=='manual_or_unknown'
        assert finish['measurement_source']=='scale'
    else:
        assert finish['completion_reason']=='target_volume'
        assert finish['measurement_source']=='volume_estimate'
        assert finish['fallback_reason']==('scale_unavailable_at_start' if scenario=='absent' else 'scale_lost_during_shot')


@pytest.mark.parametrize('scenario', ['normal', 'lost', 'stale', 'timeout', 'interrupted'])
def test_final_mass_keeps_pump_stop_time_and_separate_checkpoint(tmp_path, scenario):
    result = subprocess.run([TCL, str(Path(__file__).with_name('tablet_final_mass_fixture.tcl')),
        str(PLUGIN), str(tmp_path), scenario], capture_output=True, text=True, check=True)
    assert not result.stderr
    events = [json.loads(line) for line in result.stdout.splitlines()]
    assert [e['kind'] for e in events] == ['shot_started', 'preinfusion_complete', 'shot_finished', 'shot_finalized']
    stop, final = events[-2:]
    assert stop['data']['scale_mass']['value'] == 35
    assert stop['data']['scale_mass_basis'] == 'extraction_stop'
    assert final['shot_id'] == stop['shot_id']
    assert final['sequence'] > stop['sequence']
    assert final['data']['elapsed_ms'] == stop['data']['elapsed_ms'] == 27095
    assert final['data']['scale_mass_basis'] == 'de1_final_espresso_weight'
    if scenario == 'normal':
        assert final['data']['scale_mass'] == {'value': 36.2, 'unit': 'g', 'source': 'scale'}
        assert final['data']['quality_flags'] == []
    else:
        assert final['data']['scale_mass'] is None
        assert final['data']['quality_flags']


def test_cycle_identity_frozen_before_async_delivery(tmp_path):
    events=capture(tmp_path,'cycle')
    assert all(e['schema_version']==2 for e in events)
    assert len({json.dumps(e['cycle'],sort_keys=True) for e in events})==1
    assert events[0]['cycle']['cycle_id']=='cycle-1'
    assert events[0]['cycle']['expires_at'] % 1 == .125


def test_settings_server_validation_and_essential_banner_deduplication(tmp_path):
    result=subprocess.run([TCL,str(Path(__file__).with_name('tablet_ui_fixture.tcl')),str(PLUGIN),str(tmp_path)],
        capture_output=True,text=True,check=True)
    assert not result.stderr
    assert 'passed' in result.stdout


def test_panels_appearance_status_and_existing_settings_are_preserved(tmp_path):
    result = subprocess.run([TCL, str(Path(__file__).with_name('tablet_panels_fixture.tcl')),
        str(PLUGIN), str(tmp_path)], capture_output=True, text=True, check=True)
    assert not result.stderr
    assert 'preserved outbox passed' in result.stdout


@pytest.mark.parametrize('scenario', ['flow', 'expiry', 'preferences', 'canvas'])
def test_custom_banners_preserve_native_notifications_and_queued_events(tmp_path, scenario):
    result = subprocess.run([TCL, str(Path(__file__).with_name('tablet_banner_fixture.tcl')),
        str(PLUGIN), str(tmp_path), scenario], capture_output=True, text=True, check=True)
    assert not result.stderr
    assert 'preserved native notifications and outbox passed' in result.stdout


@pytest.mark.parametrize('scenario', ['readings', 'skin', 'zoom', 'preservation'])
def test_read_only_live_grind_preserves_recipe_and_skin_editors(tmp_path, scenario):
    result = subprocess.run([TCL, str(Path(__file__).with_name('tablet_grind_fixture.tcl')),
        str(PLUGIN), str(tmp_path), scenario], capture_output=True, text=True, check=True)
    assert not result.stderr
    assert 'preserved settings/history passed' in result.stdout


@pytest.mark.parametrize("reply_mode", ["reconnect", "rollback"])
def test_classic_polls_without_growing_outbox_and_refreshes_profile_after_reconnect(tmp_path,reply_mode):
    requests=[]
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_GET(self):
            requests.append(dict(self.headers))
            session=self.headers.get('X-Sync-Session-ID')
            reply={'server_id':'11111111-1111-1111-1111-111111111111',
                'poll':{'session_id':session,'accepted':len(requests)<3},'cycle':None}
            if reply_mode=='rollback' and len(requests)==3:reply={'controller_id':'classic-de1'}
            if self.path.endswith('/identity'):
                reply={'product':'mahlkonig-decent-sync','server_id':'11111111-1111-1111-1111-111111111111',
                    'controller_id':'classic-de1','controller_contracts':[1,2]}
            body=json.dumps(reply).encode();self.send_response(200)
            self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    script=tmp_path/'poll.tcl'
    script.write_text('''source [lindex $argv 0]
package require json
package require http
namespace eval ::plugins::mahlkonig_decent_sync {
    set session fixture-session
    set settings(bridge_url) [lindex $::argv 1]
    set settings(controller_id) classic-de1
    set settings(server_id) 11111111-1111-1111-1111-111111111111
    set verified_server "$settings(bridge_url)|$settings(server_id)"
    set last_profile original
    set sequence 12
    set outbox [file join [file dirname [info script]] outbox]
    file mkdir $outbox
}
proc pulse {} {
    ::plugins::mahlkonig_decent_sync::poll_status
    if {$::requests_left > 1} {incr ::requests_left -1;after 40 pulse} else {after 100 {set done 1}}
}
set requests_left 3
pulse
vwait done
if {$::plugins::mahlkonig_decent_sync::sequence != 12} {error {poll created durable events}}
if {$::plugins::mahlkonig_decent_sync::last_profile ne ""} {error {reconnect did not request profile}}
if {$::plugins::mahlkonig_decent_sync::sync_ready} {error {reconnect retained stale readiness}}
puts passed
''')
    try:
        result=subprocess.run([TCL,str(script),str(PLUGIN),f'http://127.0.0.1:{server.server_port}'],
            capture_output=True,text=True,check=True,timeout=5)
        assert not result.stderr and result.stdout.strip()=='passed'
    finally:server.shutdown();thread.join();server.server_close()
    polls=[row for row in requests if 'X-Sync-Session-ID' in row]
    assert len(polls)==3
    assert all(row['X-Sync-Server-ID']=='11111111-1111-1111-1111-111111111111' for row in polls)
