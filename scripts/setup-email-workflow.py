#!/usr/bin/env python3
"""Update newsletter workflow YAML + wire email tool_executor to use local sendmail."""
import sys
sys.path.insert(0, 'src')

import yaml
import os
import subprocess

# ── 1. Update the newsletter no-escalation YAML ──
yaml_path = 'out-of-the-box/newsletter-no-escalation.yaml'
with open(yaml_path) as f:
    workflow = yaml.safe_load(f)

# Update recipient default in publish step
workflow['input_schema']['properties']['recipient'] = {
    'type': 'string',
    'description': 'Email recipient',
    'default': 'newsletter@evolvingsoftware.com'
}

# Update the publish step template to use newsletter@evolvingsoftware.com as default
for step in workflow['steps']:
    if step['id'] == 'step-publish':
        step['prompt'] = step['prompt'].replace(
            '{{input.recipient | default: "sebastian@evolving.software"}}',
            '{{input.recipient | default: "newsletter@evolvingsoftware.com"}}'
        )

with open(yaml_path, 'w') as f:
    yaml.dump(workflow, f, default_flow_style=False, sort_keys=False, allow_unicode=True)

print(f'✅ Updated {yaml_path}')
print(f'  Default recipient → newsletter@evolvingsoftware.com')

# ── 2. Update the newsletter WITH review YAML ──
yaml_path2 = 'out-of-the-box/newsletter-workflow.yaml'
with open(yaml_path2) as f:
    workflow2 = yaml.safe_load(f)

# Same recipient update
if 'input_schema' in workflow2 and 'properties' in workflow2['input_schema']:
    if 'recipient' not in workflow2['input_schema']['properties']:
        workflow2['input_schema']['properties']['recipient'] = {
            'type': 'string',
            'description': 'Email recipient',
            'default': 'newsletter@evolvingsoftware.com'
        }

for step in workflow2.get('steps', []):
    if step.get('id') == 'step-publish':
        if 'prompt' in step:
            step['prompt'] = step['prompt'].replace(
                '{{input.recipient | default: "sebastian@evolving.software"}}',
                '{{input.recipient | default: "newsletter@evolvingsoftware.com"}}'
            )

with open(yaml_path2, 'w') as f:
    yaml.dump(workflow2, f, default_flow_style=False, sort_keys=False, allow_unicode=True)

print(f'✅ Updated {yaml_path2}')

# ── 3. Check if sendmail works for local delivery ──
print('\n── sendmail test ──')
try:
    result = subprocess.run(
        ['/usr/sbin/sendmail', '-v', 'newsletter@evolvingsoftware.com'],
        input=b'Subject: Test\n\nThis is a test from ESAM.\n',
        capture_output=True,
        timeout=10,
    )
    print(f'sendmail exit: {result.returncode}')
    if result.stdout:
        print(f'stdout: {result.stdout.decode()[:200]}')
    if result.stderr:
        print(f'stderr: {result.stderr.decode()[:200]}')
except subprocess.TimeoutExpired:
    print('sendmail timed out — no MTA configured')
except FileNotFoundError:
    print('sendmail not found')

# ── 4. Create msmtp config template ──
msmtp_config = """# Default settings for msmtp
defaults
auth           on
tls            on
tls_trust_file /etc/ssl/cert.pem
logfile        ~/.msmtp.log

# Gmail account
account        gmail
host           smtp.gmail.com
port           587
from           newsletter@evolvingsoftware.com
user           newsletter@evolvingsoftware.com
passwordeval   security find-generic-password -s esam-smtp -w

# Default account
account default : gmail
"""

msmtp_path = os.path.expanduser('~/.msmtprc')
if not os.path.exists(msmtp_path):
    with open(msmtp_path, 'w') as f:
        f.write(msmtp_config)
    os.chmod(msmtp_path, 0o600)
    print(f'\n✅ Created {msmtp_path} (needs SMTP password in keychain)')
    print('   Run: security add-generic-password -s esam-smtp -a "newsletter@evolvingsoftware.com" -w "<app-password>"')
else:
    print(f'\nℹ️  {msmtp_path} already exists')

print('\n── SMTP setup required ──')
print('To send real email, need SMTP credentials for newsletter@evolvingsoftware.com')
print('Then run: security add-generic-password -s esam-smtp -a "newsletter@evolvingsoftware.com" -w "<password>"')
print('Credentials needed: SMTP host, port, username, app password')
