"""Actual Tcl complete-package updates against disposable HTTP and directories."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
import release

ROOT = Path(__file__).resolve().parents[2]
TCL = shutil.which('tclsh')
pytestmark = pytest.mark.skipif(not TCL, reason='Tcl runtime is required for updater validation')
IDENTITY = '11111111-1111-1111-1111-111111111111'


def exercise(tmp_path, scenario, bad_field=None):
    model = json.loads((ROOT/'release-spec.json').read_text())
    source = release.classic_package(ROOT,'1.0.0',model)
    installed = tmp_path/'plugins'/model['classic_id']
    installed.mkdir(parents=True)
    for name,body in source.items():(installed/name).write_bytes(body)
    (tmp_path/'settings.tdb').write_bytes(b'original settings, keep exactly\n')
    (tmp_path/'history').mkdir()
    (tmp_path/'history/previous.shot').write_bytes(b'original shot history\n')
    version = {'current':'1.0.0','older':'0.9.0'}.get(scenario,'1.1.0-beta.1')
    candidate = release.classic_package(ROOT,version,model)
    candidate['product.tcl'] += b'\nset ::download_executed 1\n'
    if scenario == 'wrong_code':candidate['product.tcl'] = candidate['product.tcl'].replace(version.encode(),b'1.1.0-beta.2',1)
    if scenario == 'incomplete':candidate['capture.tcl'] += b'\n{'
    manifest = {'schema':2,'plugin':model['classic_id'],'version':version,'server_release':version,
        'server_id':IDENTITY,'adapter_version':'classic-0.5','controller_contract':2,'bootstrap_api':1,
        'files':{name:{'bytes':len(body),'sha256':hashlib.sha256(body).hexdigest()} for name,body in candidate.items()}}
    if bad_field:
        key,value = bad_field
        if key.startswith('file.'):
            manifest['files']['capture.tcl'][key[5:]] = value
        elif key == 'filename':manifest['files'][value] = manifest['files'].pop('capture.tcl')
        else:manifest[key] = value
    if scenario == 'incompatible':manifest['server_release'] = '0.9.0'
    paths = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_GET(self):
            paths.append(self.path)
            if self.path == '/api/v1/identity':
                body = json.dumps({'product':'wrong' if scenario == 'wrong_health' else model['slug'],
                    'server_id':IDENTITY,'version':version}).encode()
            elif self.path == '/api/v1/updates/classic':
                if scenario == 'manifest_redirect':
                    self.send_response(302);self.send_header('Location','/unexpected-source');self.end_headers();return
                body = json.dumps(manifest).encode()
            elif self.path == '/api/v1/status':
                count = paths.count('/api/v1/status')
                busy = scenario == 'remote_busy' or (scenario == 'busy_after_download' and count == 2)
                body = json.dumps({'update_idle_reason':'unfinished_controller_record' if busy else None}).encode()
            elif self.path.startswith('/api/v1/updates/classic/files/'):
                if scenario == 'payload_redirect':
                    self.send_response(302);self.send_header('Location','/unexpected-source');self.end_headers();return
                name = self.path.rsplit('/',1)[1]
                body = candidate[name]
                if name == 'product.tcl':
                    if scenario == 'corrupt':body = body.replace(b'Community',b'Unexpected',1)
                    if scenario == 'truncated':body = body[:-10]
                    if scenario == 'oversized':body += b'x'*10000
            else:
                self.send_response(404);self.end_headers();return
            self.send_response(200);self.send_header('Content-Length',str(len(body)));self.end_headers()
            try:self.wfile.write(body)
            except (BrokenPipeError,ConnectionResetError):pass
    server = ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread = threading.Thread(target=lambda:server.serve_forever(poll_interval=.01),daemon=True);thread.start()
    try:
        result = subprocess.run([TCL,str(Path(__file__).with_name('tablet_updater_fixture.tcl')),
            str(installed/'plugin.tcl'),str(tmp_path),f'http://127.0.0.1:{server.server_port}',scenario],
            capture_output=True,text=True,timeout=16)
        assert result.returncode == 0, result.stderr or result.stdout
        assert not result.stderr
        summary = json.loads(result.stdout)
    finally:
        server.shutdown();thread.join();server.server_close()
    assert summary['preserved'] and '/unexpected-source' not in paths
    if scenario in {'local_busy','pending_scale','active_cycle','stale_ack','local_steam','saved_events'}:
        assert paths == ['/api/v1/identity','/api/v1/updates/classic']
    if scenario == 'install':
        selected = installed/'generations'/(installed/'active-generation').read_text()
        assert {p.name:p.read_bytes() for p in selected.iterdir()} == candidate
        assert (installed/'previous-generation').read_text() == 'root'
        assert {name:(installed/name).read_bytes() for name in source} == source
        loaded = subprocess.run([TCL],input=f'source {{{installed / "plugin.tcl"}}}\nputs $::plugins::mahlkonig_decent_sync::version\n',
            capture_output=True,text=True,check=True)
        assert not loaded.stderr and loaded.stdout.strip() == version
        # Code rollback changes only the pointer; the data directory is not restored.
        (installed/'active-generation').unlink()
        reverted = subprocess.run([TCL],input=f'source {{{installed / "plugin.tcl"}}}\nputs $::plugins::mahlkonig_decent_sync::version\n',
            capture_output=True,text=True,check=True)
        assert not reverted.stderr and reverted.stdout.strip() == '1.0.0'
    return summary


@pytest.mark.parametrize('scenario',['install','current','older','corrupt','truncated','oversized',
    'wrong_code','incomplete','incompatible','wrong_health','manifest_redirect','payload_redirect',
    'local_busy','pending_scale','active_cycle','stale_ack','local_steam','saved_events',
    'remote_busy','busy_after_download','outbox_after_download','rename_failure','source_changed','server_changed'])
def test_async_updater_preserves_capture_and_files(tmp_path,scenario):
    exercise(tmp_path,scenario)


@pytest.mark.parametrize('field',[('schema',1),('plugin','other'),('filename','../plugin.tcl'),
    ('filename','http://other.local/plugin.tcl'),('file.sha256','not-a-digest'),('file.bytes',0),
    ('file.bytes',2097153),('version','1.2.3;exit'),('controller_contract',1),('adapter_version','other'),
    ('server_id','another-server'),('bootstrap_api',2)])
def test_untrusted_manifest_cannot_choose_code_or_destination(tmp_path,field):
    exercise(tmp_path,'bad_manifest',field)
