#!/usr/bin/env python3
"""Capture read-only acceptance evidence. Never submits a brew or changes a recipe."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import urllib.request


def snapshot(url):
    with urllib.request.urlopen(url.rstrip('/')+'/api/v1/status', timeout=10) as response:
        return json.load(response)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--url',required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--shot-id',help='Annotate this exact real controller shot, otherwise capture status only')
    p.add_argument('--presses',type=int,choices=(0,1))
    for flag in ('real-shot','prompt-cleared','next-grind-ready','native-adjustment-reviewed'):
        p.add_argument('--'+flag,action='store_true')
    a=p.parse_args();status=snapshot(a.url)
    report={'captured_at':datetime.now(timezone.utc).isoformat(),'status':status}
    if a.shot_id:
        shots=[s for s in status.get('automation',{}).get('shots',[]) if s['shot_id']==a.shot_id]
        if len(shots)!=1:p.error('Named shot is not in the current controller journal')
        if shots[0].get('delivery',{}).get('state')!='verified':p.error('Shot lacks native confirmation')
        report['operator_observations']={'shot_id':a.shot_id,'grinder_presses':a.presses,
            **{k:getattr(a,k) for k in ('real_shot','prompt_cleared','next_grind_ready','native_adjustment_reviewed')}}
    a.output.parent.mkdir(parents=True,exist_ok=True)
    # Existing evidence is immutable; choose another filename to take another snapshot.
    with a.output.open('x') as f:json.dump(report,f,indent=2);f.write('\n')
    print(a.output)

if __name__=='__main__':main()
