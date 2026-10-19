"""Download a release on the installation host and verify GitHub provenance."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
KINDS = {'server':'server.tar.gz','compose':'linux-compose.tar.gz','classic':'classic.zip','decaid':'decaid.zip'}


def command(args):
    result = subprocess.run(args,check=True,capture_output=True,text=True,timeout=120)
    return result.stdout


def verify(path,repository,version,run=command):
    result = run(['gh','attestation','verify',str(path),'--repo',repository,
        '--signer-workflow',repository+'/.github/workflows/release.yml',
        '--source-ref','refs/tags/'+version,'--deny-self-hosted-runners','--format','json'])
    verified = json.loads(result)
    if not isinstance(verified,list) or not verified:raise ValueError('No verified release provenance')
    return verified


def download(version,kind,destination,*,run=command):
    if not re.fullmatch(r'\d+\.\d+\.\d+(?:-(?:alpha|beta|rc)\.\d+)?',version):raise ValueError('Explicit release version required')
    spec = json.loads((ROOT/'release-spec.json').read_text())
    repository = spec['repository']
    name = spec['slug']+'-'+version+'-'+KINDS[kind]
    destination = Path(destination).resolve()
    if destination.exists():raise ValueError('Use a new destination directory')
    destination.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.sync-release-',dir=destination.parent) as scratch:
        scratch = Path(scratch)
        info = json.loads(run(['gh','release','view',version,'--repo',repository,'--json','tagName,isDraft,assets']))
        if info.get('tagName') != version or info.get('isDraft'):raise ValueError('Release is not published under the requested tag')
        sizes = {asset['name']:asset['size'] for asset in info.get('assets',[])}
        if not 0 < sizes.get(name,0) <= 16*1024*1024 or not 0 < sizes.get('release-manifest.json',0) <= 131072:
            raise ValueError('Release assets are missing or exceed package limits')
        for asset in ('release-manifest.json',name):
            run(['gh','release','download',version,'--repo',repository,'--pattern',asset,'--dir',str(scratch)])
        manifest_path = scratch/'release-manifest.json'
        provenance = {'manifest':verify(manifest_path,repository,version,run)}
        manifest = json.loads(manifest_path.read_text())
        if (manifest.get('schema') != 1 or manifest.get('product') != spec['slug'] or manifest.get('version') != version
                or manifest.get('journal_schema') != 2 or manifest.get('controller_contracts') != [1,2]):
            raise ValueError('Release compatibility differs')
        body = (scratch/name).read_bytes()
        expected = manifest['artifacts'][name]
        digest = hashlib.sha256(body).hexdigest()
        if len(body) != expected['bytes'] or digest != expected['sha256']:
            raise ValueError('Release content differs from its verified manifest')
        provenance['artifact'] = verify(scratch/name,repository,version,run)
        (scratch/'provenance.json').write_text(json.dumps(provenance,indent=2)+'\n')
        scratch.rename(destination)
    return {'path':str(destination/name),'sha256':digest,'version':version}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--version',required=True)
    parser.add_argument('--kind',choices=KINDS,required=True)
    parser.add_argument('--destination',type=Path,required=True)
    args = parser.parse_args()
    print(json.dumps(download(args.version,args.kind,args.destination),indent=2))


if __name__ == '__main__':main()
