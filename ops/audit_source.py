"""Validate the explicit public source inventory and resolved audit mappings."""
import argparse
import ast
import hashlib
import json
from pathlib import Path
import re
import shutil

ROOT=Path(__file__).resolve().parents[1]
CACHES={'.git','__pycache__','.pytest_cache'}
LOCAL={'HANDOFF.md','.DS_Store'}


def audit(root=ROOT):
    names=json.loads((root/'source-allowlist.json').read_text())
    if not isinstance(names,list) or len(names)!=len(set(names)):raise ValueError('Invalid source allowlist')
    actual={str(p.relative_to(root)) for p in root.rglob('*') if p.is_file()
        and not set(p.relative_to(root).parts)&CACHES and p.name not in LOCAL}
    if actual!=set(names):raise ValueError('Source inventory differs: '+str(sorted(actual^set(names))))
    test_modules={p.stem for p in (root/'bridge/tests').glob('test_*.py')}
    for name in names:
        path=root/name
        if Path(name).is_absolute() or '..' in Path(name).parts or path.is_symlink():raise ValueError('Unsafe source path: '+name)
        if path.suffix in ('.png','.svg'):continue
        body=path.read_text()
        if re.search(r'-----BEGIN (?:OPENSSH |RSA |EC )?PRIVATE KEY-----|gh[pousr]_[A-Za-z0-9]{30,}',body):
            raise ValueError('Credential material in declared source: '+name)
        if path.suffix=='.py' and path.parent.name=='tests':
            for node in ast.walk(ast.parse(body)):
                if isinstance(node,ast.ImportFrom) and (node.module or '').split('.')[0] in test_modules:
                    raise ValueError('Test-module dependency: '+name)
    for row in json.loads((root/'docs/audit/baseline-files.json').read_text()):
        if row.get('resolved') is not True or not row.get('resolution'):raise ValueError('Unresolved original file: '+row['path'])
        for target in row.get('current',[]):
            if target not in names:raise ValueError('Missing disposition target: '+target)
    for row in json.loads((root/'docs/audit/baseline-tests.json').read_text()):
        if row.get('resolved') is not True or not row.get('resolution'):raise ValueError('Unresolved original test: '+row['name'])
        for target in row.get('current',[]):
            filename,_,function=target.partition('::')
            if filename not in names or (function and function not in (root/filename).read_text()):
                raise ValueError('Missing regression replacement: '+target)
    inventory=json.loads((root/'docs/audit/current-inventory.json').read_text())
    if set(inventory)!=set(names) or any(not row.get('responsibility') for row in inventory.values()):
        raise ValueError('Current responsibility inventory differs')
    current_tests={str(path.relative_to(root))+'::'+node.name for path in (root/'bridge/tests').glob('test_*.py')
        for node in ast.walk(ast.parse(path.read_text())) if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef)) and node.name.startswith('test_')}
    mapped=json.loads((root/'docs/audit/current-tests.json').read_text())
    if set(mapped)!=current_tests or any(not row.get('behavior') or not row.get('area') for row in mapped.values()):
        raise ValueError('Current behavior/test map differs')
    return names


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--export',type=Path,help='Copy only audited files into a new external directory')
    args=parser.parse_args();names=audit()
    if args.export:
        destination=args.export.resolve()
        if destination.is_relative_to(ROOT):parser.error('Use a new external destination')
        destination.mkdir(parents=True,exist_ok=False)
        for name in names:
            target=destination/name;target.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(ROOT/name,target)
            if hashlib.sha256(target.read_bytes()).digest()!=hashlib.sha256((ROOT/name).read_bytes()).digest():
                raise ValueError('Export verification failed')
    print(json.dumps({'passed':True,'public_files':len(names),'export':str(args.export) if args.export else None}))


if __name__=='__main__':main()
