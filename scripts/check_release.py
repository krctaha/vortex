"""Lightweight source-release guard. Reports paths/lines, never matched secrets.

Default checks tracked release content; --strict also requires an active license.
Ignored local runtime state is skipped. This is not a full security audit.
"""
from pathlib import Path
import argparse
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
IGNORED = {'.git', '.venv', 'venv', 'node_modules', '__pycache__', '.pytest_cache',
           'data', 'qa', 'logs', 'backups'}
PATTERNS = {
    'private-key': re.compile(r'-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----'),
    'telegram-token': re.compile(r'\b\d{8,12}:[A-Za-z0-9_-]{30,}\b'),
    'github-token': re.compile(r'\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})\b'),
    'openai-key': re.compile(r'\bsk-(?:proj-)?[A-Za-z0-9_-]{35,}\b'),
    'aws-access-key': re.compile(r'\bAKIA[A-Z0-9]{16}\b'),
    'operator-home-path': re.compile(r'/(?:Users|home)/[A-Za-z0-9_.-]+/'),
}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--strict', action='store_true')
    args = parser.parse_args()
    tracked = subprocess.run(['git', '-C', str(ROOT), 'ls-files', '-z'],
                             capture_output=True, text=True)
    names = [ROOT / name for name in tracked.stdout.split('\0') if name] if tracked.returncode == 0 else []
    paths = sorted(set(names) | {p for p in ROOT.rglob('*') if p.is_file()
                                 and not any(part in IGNORED for part in p.relative_to(ROOT).parts)})
    findings = []
    checked = 0
    for path in paths:
        rel = path.relative_to(ROOT)
        if not path.exists():
            continue
        if path.is_symlink():
            findings.append(f'{rel}: symlink requires review')
            continue
        if (path.name.startswith('.env') and path.name != '.env.example') or path.name in ('id_rsa', 'id_ed25519', 'authorized_keys') or path.suffix.lower() in ('.db', '.sqlite', '.sqlite3', '.pem', '.key', '.p12', '.pfx'):
            findings.append(f'{rel}: prohibited release file')
            continue
        if any(part in IGNORED for part in rel.parts):
            findings.append(f'{rel}: runtime artifact tracked in Git')
            continue
        checked += 1
        try:
            text = path.read_text(encoding='utf-8')
        except UnicodeDecodeError:
            continue
        for line_no, line in enumerate(text.splitlines(), 1):
            for label, pattern in PATTERNS.items():
                if pattern.search(line):
                    findings.append(f'{rel}:{line_no}: {label}')
    if args.strict and not (ROOT / 'LICENSE').exists():
        findings.append('LICENSE: maintainer approval required before publishing')
    for finding in findings:
        print(finding)
    print(f'Checked {checked} files; {len(findings)} release-guard findings.')
    if not args.strict:
        print('Basic guard only; independent security and rights review still recommended.')
    return bool(findings)


if __name__ == '__main__':
    sys.exit(main())
