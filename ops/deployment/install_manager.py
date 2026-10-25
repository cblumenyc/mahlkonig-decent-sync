"""Administrator-reviewed manager update for an existing managed installation.

This is deliberately separate from application releases. New hosts use Compose.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import shutil
import subprocess

HELPER = Path('/usr/local/sbin/coffee-sync-deploy')
POLICY = Path('/etc/coffee-bridge/deployment.json')
STATE = Path('/var/lib/coffee-sync-deploy')
APP = Path('/opt/coffee-bridge/app')


def run(*args, check=True):
    return subprocess.run(args, check=check, stdin=subprocess.DEVNULL, capture_output=True,
        env={'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LANG': 'C.UTF-8'}, cwd='/')


def install(path, body, mode):
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise ValueError('Unexpected administrator file type')
    temporary = path.with_name(path.name + '.staged')
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, mode)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(body)
        stream.flush()
        os.fsync(stream.fileno())
    os.chown(temporary, 0, 0)
    temporary.chmod(mode)
    os.replace(temporary, path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--operator', required=True)
    parser.add_argument('--expected-helper-sha256', required=True)
    parser.add_argument('--helper-only', action='store_true', help='Preserve existing operator policy and sudo rules')
    args = parser.parse_args()
    if os.geteuid() != 0:
        parser.error('Administrator installation required')
    os.umask(0o077)
    if not re.fullmatch(r'[a-z_][a-z0-9_-]{0,31}', args.operator):
        parser.error('Invalid operator account')
    operator = pwd.getpwnam(args.operator)
    if operator.pw_uid == 0:
        parser.error('Choose an ordinary operator account')
    source = Path(__file__).with_name('deploy.py').read_bytes()
    if hashlib.sha256(source).hexdigest() != args.expected_helper_sha256:
        parser.error('Reviewed helper digest differs')
    current = json.loads((STATE/'current.json').read_text())
    if current.get('legacy') or not APP.is_symlink() or str(APP.resolve()) != current['path']:
        parser.error('An existing contract-2 managed installation is required')
    if run('/usr/bin/systemctl', 'is-active', '--quiet', 'coffee-sync-deploy-worker.service', check=False).returncode == 0:
        parser.error('Wait for the current deployment to finish')
    for path in (HELPER.parent, POLICY.parent, STATE):
        if path.is_symlink() or path.stat().st_uid != 0 or path.stat().st_mode & 0o022:
            parser.error('Administrator directory ownership or permissions differ')
    if args.helper_only:
        policy = json.loads(POLICY.read_text())
        if policy.get('operator') != args.operator:
            parser.error('Existing operator differs')
        previous = STATE/('manager-' + hashlib.sha256(HELPER.read_bytes()).hexdigest() + '.py')
        if not previous.exists():
            shutil.copyfile(HELPER, previous);previous.chmod(0o600)
        install(HELPER, source, 0o755)
        print('Manager updated. Existing access policy and application data retained.')
        return
    inbox = Path(operator.pw_dir)/'mahlkonig-decent-sync-inbox'
    if inbox.is_symlink():
        parser.error('Inbox cannot be a symlink')
    inbox.mkdir(mode=0o700, exist_ok=True)
    os.chown(inbox, operator.pw_uid, operator.pw_gid)
    inbox.chmod(0o700)
    policy = (args.operator + ' ALL=(root) NOPASSWD: ' + str(HELPER) + '\n').encode()
    check = STATE/'sudoers-check'
    install(check, policy, 0o440)
    run('/usr/sbin/visudo', '-cf', str(check))
    previous = STATE/('manager-' + hashlib.sha256(HELPER.read_bytes()).hexdigest() + '.py')
    if not previous.exists():
        shutil.copyfile(HELPER, previous)
        previous.chmod(0o600)
    install(POLICY, (json.dumps({'operator': args.operator, 'inbox': str(inbox)})+'\n').encode(), 0o600)
    install(HELPER, source, 0o755)
    install(Path('/etc/sudoers.d/coffee-sync-deploy'), policy, 0o440)
    run('/usr/sbin/visudo', '-cf', '/etc/sudoers')
    print('Manager installed. Application, journal and approved feedback settings were retained.')


if __name__ == '__main__':
    main()
