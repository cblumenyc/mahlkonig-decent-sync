"""Fresh, isolated CI installation. Uses only this job's Docker project and volume."""
import argparse
import ipaddress
import json
import os
from pathlib import Path
import socket
import subprocess
import tarfile
import tempfile
import urllib.request
import uuid


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('package',type=Path)
    args=parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='sync-install-') as temporary:
        folder=Path(temporary)
        with tarfile.open(args.package) as archive:archive.extractall(folder,filter='data')
        with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as probe:
            probe.connect(('192.0.2.1',9));address=probe.getsockname()[0]
        if not ipaddress.ip_address(address).is_private:raise ValueError('Isolated Linux runner needs a private interface')
        version=json.loads((folder/'app/release.json').read_text())['version']
        (folder/'.env').write_text(f'SYNC_VERSION={version}\nSYNC_ORIGIN=http://{address}\nSYNC_LAN_CIDR={address}/32\n')
        project='sync-check-'+uuid.uuid4().hex[:12]
        prefix=['docker','compose','-p',project,'--project-directory',str(folder),'--env-file',str(folder/'.env'),'-f',str(folder/'compose.yaml')]
        def run(*args):
            result=subprocess.run([*prefix,*args],capture_output=True,text=True,timeout=180)
            if result.returncode:
                print(result.stdout[-16000:]);print(result.stderr[-16000:])
                result.check_returncode()
            return result.stdout.strip()
        def get(path):
            with urllib.request.build_opener(urllib.request.ProxyHandler({})).open('http://'+address+path,timeout=5) as response:return json.load(response)
        try:
            run('build');run('up','-d','--wait','--wait-timeout','90')
            first=get('/api/v1/identity');setup=get('/api/v1/setup')
            assert not setup['feedback_enabled'] and not setup['complete']
            assert first['starts_extraction'] is False and first['version']==version
            privileges=json.loads(run('exec','-T','--user','10001:10001','sync','python','-c',
                "import json;from pathlib import Path;print(json.dumps(dict(line.split(':',1) for line in Path('/proc/1/status').read_text().splitlines())))"))
            assert privileges['Uid'].split()==['10001']*4 and privileges['Gid'].split()==['10001']*4
            assert int(privileges['CapEff'],16)==1024 and privileges['NoNewPrivs'].strip()=='1'
            run('restart','sync');run('up','-d','--wait','--wait-timeout','90')
            assert get('/api/v1/identity')['server_id']==first['server_id']
            run('exec','-T','--user','10001:10001','sync','python','/app/backup.py','/data/bridge.sqlite3')
            check="""from pathlib import Path
from backup import restore
from store import Store
source=sorted(Path('/data/backups').glob('coffee-*.sqlite3'))[-1]
restore(source,'/data/recovery-check.sqlite3')
with Store('/data/bridge.sqlite3').db as a,Store('/data/recovery-check.sqlite3').db as b:
 assert a.execute(\"select value from kv where key='installation_id'\").fetchone()==b.execute(\"select value from kv where key='installation_id'\").fetchone()
"""
            run('exec','-T','--user','10001:10001','sync','python','-c',check)
            print(json.dumps({'passed':True,'architecture':os.uname().machine,
                'checks':['clean-package-install','nonroot','health','feedback-disabled','restart-identity','backup-restore']}))
        except Exception:
            try:print(run('logs','--no-color','--tail','80'))
            except subprocess.SubprocessError:pass
            raise
        finally:
            try:run('down','--volumes','--remove-orphans')
            except subprocess.SubprocessError:pass


if __name__=='__main__':main()
