"""Require recorded physical acceptance for these exact community release artifacts."""
import argparse
import json
from pathlib import Path
import re

CHECKS={'late_press','invalid_shot_recovery','mid_shot_scale_loss','wifi_recovery',
    'three_consecutive_cycles','tablet_appearance_logo_touch','readiness_banners','plugin_update',
    'reboot','backup_restore','upgrade_rollback','interrupted_migration'}


def validate(manifest, evidence):
    if evidence.get('schema')!=1:raise ValueError('Unsupported acceptance evidence')
    rows=[row for row in evidence.get('releases',[]) if row.get('version')==manifest['version']]
    if len(rows)!=1:raise ValueError('No unique hardware acceptance record for this version')
    release=rows[0]
    if release.get('artifacts')!=manifest['artifacts']:raise ValueError('Acceptance does not cover these exact package bytes')
    for kind in ('classic','decaid'):
        app=release.get('controllers',{}).get(kind,{})
        if not all(isinstance(app.get(key),str) and app[key].strip() for key in ('app_version','tablet_model','tablet_os','grinder_firmware','observed_at','reviewer')):
            raise ValueError('Missing tested hardware versions or observer')
        if app.get('grinder_model')!='E64 WS' or (kind=='decaid' and app['app_version']!='0.9.0'):
            raise ValueError('Unsupported hardware/controller claim')
        if set(app.get('checks',{}))!=CHECKS or not all(value is True for value in app['checks'].values()):
            raise ValueError('Physical acceptance remains incomplete for '+kind)
        if not re.fullmatch('[a-f0-9]{64}',app.get('private_evidence_sha256','')):
            raise ValueError('Missing private observation evidence digest')
    return True


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('manifest',type=Path)
    parser.add_argument('--evidence',type=Path,default=Path(__file__).resolve().parents[1]/'docs/acceptance.json')
    args=parser.parse_args()
    validate(json.loads(args.manifest.read_text()),json.loads(args.evidence.read_text()))
    print('Recorded hardware acceptance matches every artifact.')


if __name__=='__main__':main()
