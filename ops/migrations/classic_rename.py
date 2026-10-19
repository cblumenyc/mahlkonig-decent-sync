"""One assisted migration from Classic decent_sync 0.5.x; run with de1app closed.

Operate on the actual mounted tablet directories, not a live application. The
external recovery directory is the transaction journal and must be retained.
Retire this tool after the documented first-beta migration window closes.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import urllib.request
import uuid
import zipfile

OLD, NEW = 'decent_sync', 'mahlkonig_decent_sync'
TCL = Path(__file__).with_name('tcl_data.tcl')
ROOT = Path(__file__).resolve().parents[2]


def tcl(mode, data):
    return subprocess.run(['tclsh',str(TCL),mode],input=data,capture_output=True,
        check=True,timeout=5).stdout


def read_settings(body):
    return {bytes.fromhex(k).decode():bytes.fromhex(v).decode()
        for k,v in (line.split('\t') for line in tcl('read',body).decode().splitlines())}


def settings_bytes(value):
    return tcl('write',''.join(k.encode().hex()+'\t'+v.encode().hex()+'\n'
        for k,v in value.items()).encode())


def digest(body):return hashlib.sha256(body).hexdigest()


def tree(folder):
    folder = Path(folder)
    if not folder.exists():return None
    if folder.is_symlink() or not folder.is_dir():raise ValueError('Expected a regular directory')
    files = {}
    for path in folder.rglob('*'):
        if path.is_symlink():raise ValueError('Migration cannot follow symlinks')
        if path.is_file():files[str(path.relative_to(folder))] = digest(path.read_bytes())
    return files


def sync_directory(path):
    fd = os.open(path,os.O_RDONLY)
    try:os.fsync(fd)
    finally:os.close(fd)


def durable_tree(folder):
    for path in Path(folder).rglob('*'):
        if path.is_file():
            with path.open('rb') as stream:os.fsync(stream.fileno())
    for path in sorted(Path(folder).rglob('*'),key=lambda p:len(p.parts),reverse=True):
        if path.is_dir():sync_directory(path)
    sync_directory(folder)


def atomic(path, body):
    path = Path(path)
    if path.is_symlink():raise ValueError('Migration cannot replace symlinks')
    temporary = path.with_name(path.name+'.migration-pending')
    fd = os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_TRUNC|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'wb') as stream:
        stream.write(body);stream.flush();os.fsync(stream.fileno())
    os.replace(temporary,path);sync_directory(path.parent)


def fetch_identity(url):
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self,*args):raise ValueError('Server redirects are not allowed')
    with urllib.request.build_opener(NoRedirect).open(url+'/api/v1/identity',timeout=5) as reply:
        body = reply.read(65537)
    if len(body)>65536:raise ValueError('Server identity response is too large')
    return json.loads(body)


def prepare(code, data, global_settings, package, package_sha256, backup, server_id, *, fetch=fetch_identity):
    code,data,global_settings,package,backup = [Path(p).absolute() for p in (code,data,global_settings,package,backup)]
    if any(p.is_symlink() or p.resolve()!=p for p in (code,data,global_settings,package)):
        raise ValueError('Use real paths without symlink components')
    if backup.exists():raise ValueError('Recovery directory exists; use resume or rollback')
    if any(backup.is_relative_to(p) or p.is_relative_to(backup) for p in (code,data)):
        raise ValueError('Recovery must be outside the plugin directories')
    if code.name!=OLD or data.name!=OLD:raise ValueError('Migration only accepts decent_sync directories')
    old_code,old_data = tree(code),tree(data)
    if old_code is None or old_data is None:raise ValueError('Existing plugin and data are required')
    source = (code/'plugin.tcl').read_text()
    if not re.search(r'variable version\s+0\.5\.\d+',source):raise ValueError('Only the current Classic 0.5.x installation is supported')
    body = package.read_bytes()
    if digest(body)!=package_sha256:raise ValueError('Package differs from the provenance-verified release')
    new_code,new_data = code.with_name(NEW),data.with_name(NEW)
    if new_code.exists() or new_data.exists():raise ValueError('A renamed plugin already exists; do not install duplicate hooks')
    settings = read_settings((data/'settings.tdb').read_bytes())
    url = settings.get('bridge_url','').rstrip('/')
    if not re.fullmatch(r'http://[A-Za-z0-9][A-Za-z0-9.-]*(?::[0-9]{1,5})?',url):
        raise ValueError('Existing server address is invalid; do not redirect queued events')
    if settings.get('server_id') not in (None,'',server_id):raise ValueError('Existing installation identity differs')
    uuid.UUID(server_id)
    identity = fetch(url)
    if (identity.get('product')!='mahlkonig-decent-sync' or identity.get('server_id')!=server_id or
            identity.get('controller_id')!=settings.get('controller_id','classic-de1') or 2 not in identity.get('controller_contracts',[])):
        raise ValueError('The existing server has not been verified for this migration')
    settings['server_id'] = server_id
    original_global = global_settings.read_bytes()
    global_values = read_settings(original_global)
    enabled = global_values.get('enabled_plugins','')
    global_values['enabled_plugins'] = tcl('disable',enabled.encode()).decode()
    disabled = settings_bytes(global_values)
    global_values['enabled_plugins'] = tcl('plugins',enabled.encode()).decode()
    final_global = settings_bytes(global_values)
    spec = json.loads((ROOT/'release-spec.json').read_text())
    expected = set(spec['classic_files']) | {'LICENSE','ASSET-NOTICES.md','mahlkonig.png'}
    with zipfile.ZipFile(package) as archive:
        names = archive.namelist()
        if set(names)!={NEW+'/'+name for name in expected} or len(names)!=len(expected):
            raise ValueError('Package does not match the complete Classic allowlist')
        if sum(m.file_size for m in archive.infolist())>4*1024*1024:raise ValueError('Package exceeds the size limit')
        payload = {name:archive.read(NEW+'/'+name) for name in expected}
    # All old files are backed up; only documented mutable data becomes runtime
    # input. Unknown files remain private evidence, never executable product code.
    backup.mkdir(mode=0o700,parents=True)
    try:
        shutil.copytree(code,backup/'original-code')
        shutil.copytree(data,backup/'original-data')
        atomic(backup/'original-settings.tdb',original_global)
        stage = backup/'new-code';stage.mkdir()
        for name,content in payload.items():atomic(stage/name,content)
        target_data = stage if code==data else backup/'new-data'
        target_data.mkdir(exist_ok=True)
        atomic(target_data/'settings.tdb',settings_bytes(settings))
        if (data/'outbox').exists():shutil.copytree(data/'outbox',target_data/'outbox')
        atomic(backup/'disabled-settings.tdb',disabled)
        atomic(backup/'new-settings.tdb',final_global)
        if tree(backup/'original-code')!=old_code or tree(backup/'original-data')!=old_data:
            raise ValueError('Recovery copy verification failed')
        durable_tree(backup)
        plan = {'schema':1,'state':'prepared','code':str(code),'data':str(data),'global':str(global_settings),
            'new_code':str(new_code),'new_data':str(new_data),'server_id':server_id,'package_sha256':package_sha256,
            'old_code':old_code,'old_data':old_data,'desired_code':tree(stage),'desired_data':tree(target_data),
            'original_global':digest(original_global),'disabled_global':digest(disabled),'desired_global':digest(final_global)}
        atomic(backup/'transaction.json',(json.dumps(plan,indent=2)+'\n').encode())
    except BaseException:
        # Retain even a partial backup for inspection; no live file has changed.
        raise
    return plan


def load(backup):
    backup=Path(backup)
    plan=json.loads((backup/'transaction.json').read_text())
    if plan.get('schema')!=1:raise ValueError('Unsupported migration journal')
    for folder,key in [('original-code','old_code'),('original-data','old_data'),('new-code','desired_code')]:
        if tree(backup/folder)!=plan[key]:raise ValueError('Recovery material changed: '+folder)
    data_stage='new-code' if plan['code']==plan['data'] else 'new-data'
    if tree(backup/data_stage)!=plan['desired_data']:raise ValueError('Staged data changed')
    for filename,key in [('original-settings.tdb','original_global'),('disabled-settings.tdb','disabled_global'),('new-settings.tdb','desired_global')]:
        if digest((backup/filename).read_bytes())!=plan[key]:raise ValueError('Recovery settings changed')
    return plan


def publish_tree(source, destination, expected):
    destination=Path(destination)
    if destination.exists():
        if tree(destination)!=expected:raise ValueError('Live files changed; refusing to overwrite them')
        return
    staging=destination.with_name('.'+destination.name+'.migration-pending')
    if staging.exists():shutil.rmtree(staging)
    shutil.copytree(source,staging)
    if tree(staging)!=expected:raise ValueError('Staged copy differs')
    durable_tree(staging)
    staging.rename(destination);sync_directory(destination.parent)


def apply(backup, *, rollback=False, checkpoint=lambda phase:None):
    backup=Path(backup);plan=load(backup)
    global_path=Path(plan['global'])
    current=digest(global_path.read_bytes())
    if current not in {plan['original_global'],plan['disabled_global'],plan['desired_global']}:
        raise ValueError('App settings changed; close the app and recover manually')
    for path,key in [('code','old_code'),('data','old_data'),('new_code','desired_code'),('new_data','desired_data')]:
        actual=tree(plan[path])
        if actual is not None and actual!=plan[key]:raise ValueError('Plugin data changed; automatic recovery would lose data')
    atomic(global_path,(backup/'disabled-settings.tdb').read_bytes());checkpoint('disabled')
    if rollback:
        publish_tree(backup/'original-code',plan['code'],plan['old_code'])
        if plan['data']!=plan['code']:publish_tree(backup/'original-data',plan['data'],plan['old_data'])
        for path in set((plan['new_code'],plan['new_data'])):
            if Path(path).exists():shutil.rmtree(path);sync_directory(Path(path).parent)
        checkpoint('restored')
        atomic(global_path,(backup/'original-settings.tdb').read_bytes())
        plan['state']='rolled-back'
    else:
        publish_tree(backup/'new-code',plan['new_code'],plan['desired_code']);checkpoint('code')
        if plan['data']!=plan['code']:publish_tree(backup/'new-data',plan['new_data'],plan['desired_data'])
        checkpoint('data')
        # Remove the retired hook source only after both new trees are verified.
        for path in set((plan['code'],plan['data'])):
            if Path(path).exists():shutil.rmtree(path);sync_directory(Path(path).parent)
        checkpoint('retired')
        atomic(global_path,(backup/'new-settings.tdb').read_bytes())
        plan['state']='complete'
    checkpoint('settings')
    atomic(backup/'transaction.json',(json.dumps(plan,indent=2)+'\n').encode())
    return plan['state']


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['prepare','resume','rollback'])
    parser.add_argument('--recovery',type=Path,required=True)
    parser.add_argument('--app-closed',action='store_true',required=True)
    for field in ('code','data','global-settings','package','package-sha256','server-id'):parser.add_argument('--'+field)
    args=parser.parse_args()
    if args.action=='prepare':
        if not all(getattr(args,f) for f in ('code','data','global_settings','package','package_sha256','server_id')):
            parser.error('Preparation requires all source paths, verified package digest and existing server identity')
        prepare(args.code,args.data,args.global_settings,args.package,args.package_sha256,args.recovery,args.server_id)
        print('Verified recovery prepared. Keep the app closed; run resume to perform the migration.')
    else:print(apply(args.recovery,rollback=args.action=='rollback'))


if __name__=='__main__':main()
