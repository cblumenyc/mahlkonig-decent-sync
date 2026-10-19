"""Reproducible, allowlisted release artifacts. Never changes the source checkout."""
import argparse
import base64
import gzip
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import re
import tarfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def encoded(value):
    return (json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False) + '\n').encode()


def digest(body):
    return hashlib.sha256(body).hexdigest()


def declared_files(root, names):
    result = {}
    if len(names) != len(set(names)):
        raise ValueError('Duplicate package path')
    for name in names:
        path = PurePosixPath(name)
        if path.is_absolute() or str(path) != name or any(p.startswith('.') for p in path.parts):
            raise ValueError('Unsafe package path')
        source = root / name
        if not source.is_file() or any(p.is_symlink() for p in (source, *source.parents)):
            raise ValueError('Package inputs must be regular files: ' + name)
        result[name] = source.read_bytes()
    return result


def tar_bytes(files):
    output = io.BytesIO()
    with gzip.GzipFile(fileobj=output, mode='wb', filename='', mtime=0) as gz:
        with tarfile.open(fileobj=gz, mode='w', format=tarfile.USTAR_FORMAT) as tar:
            for name, body in sorted(files.items()):
                member = tarfile.TarInfo(name)
                member.size, member.mode, member.mtime = len(body), 0o644, 0
                tar.addfile(member, io.BytesIO(body))
    return output.getvalue()


def zip_bytes(files):
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name, body in sorted(files.items()):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type, info.create_system = zipfile.ZIP_DEFLATED, 3
            info.external_attr = 0o100644 << 16
            archive.writestr(info, body)
    return output.getvalue()


def classic_package(root, version, spec):
    files = declared_files(root/'tablet'/spec['classic_id'], spec['classic_files'])
    code, count = re.subn(r'^    variable version .+$', '    variable version '+version,
                         files['product.tcl'].decode(), flags=re.M)
    if count != 1:raise ValueError('Classic version marker missing or ambiguous')
    files['product.tcl'] = code.encode()
    shared = declared_files(root, spec['shared_files'])
    files.update({name:shared[name] for name in ('LICENSE','ASSET-NOTICES.md')})
    files['mahlkonig.png'] = shared['assets/mahlkonig.png']
    return files


def decaid_package(root, version, spec):
    source = declared_files(root/'tablet/decaid',spec['decaid_files'])
    shared = declared_files(root,spec['shared_files'])
    code = b'\n'.join(source[name] for name in ('fields.js','outbox.js','capture.js','plugin.js')).decode()
    code,count = re.subn(r"const VERSION='[^']+';", "const VERSION='"+version+"';", code)
    if count != 1:raise ValueError('Decaid version marker missing or ambiguous')
    code = code.replace('__MAHLKONIG_LOGO_BASE64__',base64.b64encode(shared['assets/mahlkonig.png']).decode())
    manifest = json.loads(source['manifest.json'])
    if manifest['id'] != spec['decaid_id']:raise ValueError('Decaid identity differs from release model')
    manifest.update(version=version,name=spec['name'])
    return {'plugin.js':code.encode(),'manifest.json':encoded(manifest),'README.md':source['README.md'],
        'LICENSE':shared['LICENSE'],'ASSET-NOTICES.md':shared['ASSET-NOTICES.md']}


def build(root, version, destination):
    if not re.fullmatch(r'\d+\.\d+\.\d+(?:-(?:alpha|beta|rc)\.\d+)?', version):
        raise ValueError('Use an explicit semantic release version, optionally -beta.N')
    spec = json.loads((root / 'release-spec.json').read_text())
    metadata = {key: spec[key] for key in ('journal_schema', 'controller_contracts')}
    metadata['version'] = version
    classic = classic_package(root, version, spec)
    shared = declared_files(root, spec['shared_files'])
    files = {'app/'+name:body for name,body in declared_files(root/'bridge',spec['server_files']).items()}
    files.update({'app/'+name:shared[name] for name in ('LICENSE','ASSET-NOTICES.md')})
    files['app/static/mahlkonig.svg'] = shared['assets/mahlkonig.svg']
    files['app/release.json'] = encoded(metadata)
    files['app/product.json'] = encoded({k:v for k,v in spec.items() if not k.endswith('_files')})
    catalogue = {'schema':2,'plugin':spec['classic_id'],'version':version,'server_release':version,
        'adapter_version':spec['classic_adapter'],'controller_contract':2,'bootstrap_api':1,
        'files':{name:{'sha256':digest(body),'bytes':len(body)} for name,body in classic.items()}}
    files['app/updates/classic.json'] = encoded(catalogue)
    files.update({'app/updates/classic/'+name:body for name,body in classic.items()})
    files['tests/deploy_smoke.py'] = (root/'ops/deployment/deploy_smoke.py').read_bytes()
    manifest = {'schema':1,**metadata,'files':{name:digest(body) for name,body in files.items()}}
    files['manifest.json'] = encoded(manifest)
    prefix = spec['slug']+'-'+version
    artifacts = {prefix+'-server.tar.gz':tar_bytes(files),
        prefix+'-classic.zip':zip_bytes({spec['classic_id']+'/'+name:body for name,body in classic.items()})}
    decaid = decaid_package(root,version,spec)
    artifacts[prefix+'-decaid.zip'] = zip_bytes({spec['decaid_id']+'/'+name:body for name,body in decaid.items()})
    container = declared_files(root/'ops/container',spec['container_files'])
    container['environment.example'] = container['environment.example'].replace(b'__RELEASE_VERSION__',version.encode())
    container.update({name:body for name,body in files.items() if name.startswith('app/')})
    artifacts[prefix+'-linux-compose.tar.gz'] = tar_bytes(container)
    artifacts['release-manifest.json'] = encoded({'schema': 1, 'product': spec['slug'], **metadata,
        'artifacts': {name: {'sha256': digest(body), 'bytes': len(body)} for name, body in artifacts.items()}})
    destination = destination.resolve()
    # A release is immutable, and its output must not become a source input.
    if destination == root.resolve() or root.resolve() in destination.parents:
        raise ValueError('Choose an output directory outside the source checkout')
    destination.mkdir(parents=True, exist_ok=False)
    for name, body in artifacts.items():
        (destination / name).write_bytes(body)
    return {'version': version, 'output': str(destination), 'artifacts': sorted(artifacts)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--version', required=True)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(build(ROOT, args.version, args.output), indent=2))


if __name__ == '__main__':
    main()
