"""Package behavior from a clean source copy; generated files never become inputs."""
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import shutil
import tarfile
import zipfile

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('release_builder', ROOT/'ops/release.py')
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


def checkout(tmp_path):
    root = tmp_path/'source'
    model = json.loads((ROOT/'release-spec.json').read_text())
    for parent, names in [('bridge', model['server_files']),
            ('tablet/'+model['classic_id'], model['classic_files']),
            ('tablet/decaid', model['decaid_files']),
            ('ops/container',model['container_files']),
            ('', ['release-spec.json', 'ops/deployment/deploy_smoke.py', *model['shared_files']])]:
        for name in names:
            target = root/parent/name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT/parent/name, target)
    return root, model


def fingerprints(root):
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob('*') if p.is_file()}


def test_packages_are_reproducible_allowlisted_and_do_not_mutate_source(tmp_path):
    root, model = checkout(tmp_path)
    for name in ['bridge/private.py', 'bridge/config.json', 'tablet/'+model['classic_id']+'/settings.tdb',
                 'tablet/'+model['classic_id']+'/outbox/queued.json']:
        path = root/name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('MUST NOT SHIP')
    original = fingerprints(root)
    for name in ('first','second'):
        release.build(root, '1.2.3-beta.1', tmp_path/name)
    assert fingerprints(root) == original
    assert fingerprints(tmp_path/'first') == fingerprints(tmp_path/'second')
    output = tmp_path/'first'
    manifest = json.loads((output/'release-manifest.json').read_text())
    assert manifest['controller_contracts'] == [1,2]
    for name, entry in manifest['artifacts'].items():
        data = (output/name).read_bytes()
        assert entry == {'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
        if name.endswith('-server.tar.gz'):
            with tarfile.open(fileobj=io.BytesIO(data)) as archive:
                assert set(archive.getnames()) == {'app/'+p for p in model['server_files']} | {
                    'app/release.json', 'app/product.json', 'tests/deploy_smoke.py', 'manifest.json',
                    'app/LICENSE','app/ASSET-NOTICES.md','app/static/mahlkonig.svg','app/updates/classic.json'} | {
                    'app/updates/classic/'+p for p in release.classic_package(root,'1.2.3-beta.1',model)}
                assert all(b'MUST NOT SHIP' not in archive.extractfile(m).read() for m in archive)
        elif name.endswith('-linux-compose.tar.gz'):
            with tarfile.open(fileobj=io.BytesIO(data)) as archive:
                names=set(archive.getnames())
                assert set(model['container_files']) <= names
                assert all(name.startswith('app/') or name in model['container_files'] for name in names)
                assert b'1.2.3-beta.1' in archive.extractfile('environment.example').read()
                assert all(b'MUST NOT SHIP' not in archive.extractfile(m).read() for m in archive)
        elif name.endswith('-classic.zip'):
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                assert set(archive.namelist()) == {model['classic_id']+'/'+p for p in release.classic_package(root,'1.2.3-beta.1',model)}
                assert all(b'MUST NOT SHIP' not in archive.read(p) for p in archive.namelist())
                assert b'variable version 1.2.3-beta.1' in archive.read(model['classic_id']+'/product.tcl')
        else:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                expected=release.decaid_package(root,'1.2.3-beta.1',model)
                assert set(archive.namelist()) == {model['decaid_id']+'/'+p for p in expected}
                manifest=json.loads(archive.read(model['decaid_id']+'/manifest.json'))
                assert manifest['version']=='1.2.3-beta.1' and manifest['name']==model['name']
                code=archive.read(model['decaid_id']+'/plugin.js')
                assert b'__MAHLKONIG_LOGO_BASE64__' not in code and b'MUST NOT SHIP' not in code
    with pytest.raises(FileExistsError):
        release.build(root, '1.2.3-beta.1', output)


def test_missing_or_linked_input_and_implicit_destination_are_rejected(tmp_path):
    root, model = checkout(tmp_path)
    with pytest.raises(ValueError):release.build(root, 'yesterday', tmp_path/'bad')
    with pytest.raises(ValueError):release.build(root, '1.2.3', root/'dist')
    path = root/'bridge'/model['server_files'][0]
    path.unlink()
    with pytest.raises(ValueError):release.build(root, '1.2.3', tmp_path/'missing')
    secret = tmp_path/'secret';secret.write_text('private')
    path.symlink_to(secret)
    with pytest.raises(ValueError):release.build(root, '1.2.3', tmp_path/'linked')
