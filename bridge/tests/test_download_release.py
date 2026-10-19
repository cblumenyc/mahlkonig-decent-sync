"""Release verification rejects mismatched provenance, tags, hashes and schemas."""
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

ROOT=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location('download_release',ROOT/'ops/download_release.py')
download=importlib.util.module_from_spec(spec);spec.loader.exec_module(download)


@pytest.mark.parametrize('fault',[None,'draft','tag','hash','schema','provenance','oversized'])
def test_download_requires_signed_matching_release(tmp_path,fault):
    version='0.1.0-beta.1';name='mahlkonig-decent-sync-'+version+'-server.tar.gz'
    body=b'verified package'
    manifest={'schema':1,'product':'mahlkonig-decent-sync','version':version,'journal_schema':2,
        'controller_contracts':[1,2],'artifacts':{name:{'sha256':hashlib.sha256(body).hexdigest(),'bytes':len(body)}}}
    if fault=='hash':body+=b'changed'
    if fault=='schema':manifest['journal_schema']=3
    calls=[]
    def run(args):
        calls.append(args)
        if args[1:3]==['release','view']:
            return json.dumps({'tagName':'wrong' if fault=='tag' else version,'isDraft':fault=='draft',
                'assets':[{'name':name,'size':20*1024*1024 if fault=='oversized' else len(body)},
                          {'name':'release-manifest.json','size':1000}]})
        if args[1:3]==['release','download']:
            asset=args[args.index('--pattern')+1];folder=Path(args[args.index('--dir')+1])
            (folder/asset).write_bytes(body if asset==name else json.dumps(manifest).encode());return ''
        assert args[1:3]==['attestation','verify']
        assert args[args.index('--source-ref')+1]=='refs/tags/'+version
        assert '--deny-self-hosted-runners' in args
        assert args[args.index('--signer-workflow')+1].endswith('/.github/workflows/release.yml')
        return '[]' if fault=='provenance' else '[{"verified":true}]'
    destination=tmp_path/'release'
    if fault:
        with pytest.raises(ValueError):download.download(version,'server',destination,run=run)
        assert not destination.exists()
    else:
        result=download.download(version,'server',destination,run=run)
        assert Path(result['path']).read_bytes()==body
        assert (destination/'provenance.json').exists()
        assert len([a for a in calls if a[1:3]==['attestation','verify']])==2
