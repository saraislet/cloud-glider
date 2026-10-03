#!/usr/bin/env python3
"""Prepare bootstrap_requested=false; an operator later toggles it in DynamoDB."""
import argparse
import hashlib
import json
import subprocess
import sys
import time
import uuid
import re
from datetime import datetime, timezone



OPERATOR_FIELDS = set('start_requested stop_requested cleanup_requested operation_status last_result command_sequence active_command active_request_id active_command_sequence last_result_sequence cycle_initialized operation_updated_at'.split())


def operator_defaults():
    return {
        'start_requested': {'BOOL': False}, 'stop_requested': {'BOOL': False},
        'cleanup_requested': {'BOOL': False}, 'operation_status': {'S': 'IDLE'},
        'last_result': {'S': 'Ready for a start or cleanup request.'},
        'command_sequence': {'N': '0'}, 'active_command': {'S': 'NONE'},
        'active_request_id': {'S': ''}, 'active_command_sequence': {'N': '0'},
        'last_result_sequence': {'N': '0'}, 'cycle_initialized': {'BOOL': False},
    }

def fingerprint(control):
    approved = {k: v for k, v in control.items() if k not in OPERATOR_FIELDS | {'propagation_enabled', 'updated_at', 'updated_by'}}
    return hashlib.sha256(json.dumps(approved, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def build_transaction(table, control, operator, request_id, now):
    if not re.fullmatch(r'[1-9][0-9]{0,17}', request_id):
        raise ValueError('request_id must be a positive cycle number')
    if 'propagation_enabled' in control:
        raise ValueError('Legacy CONTROL needs the reviewed offline lifecycle migration')
    request = {
        'PK': {'S': 'BOOTSTRAP'}, 'SK': {'S': 'REQUEST'}, 'status': {'S': 'READY'},
        'bootstrap_requested': {'BOOL': False},
        'propagation_enabled': {'BOOL': False}, 'cleanup_requested': {'BOOL': False},
        'cleanup_status': {'S': 'IDLE'}, 'schema_version': {'S': '2'},
        'request_id': {'S': request_id}, 'requested_by': {'S': operator},
        'requested_at': {'S': datetime.fromtimestamp(now, timezone.utc).isoformat()},
        'control_sha256': {'S': fingerprint(control)},
    }
    approved = {k: v for k, v in control.items() if k not in OPERATOR_FIELDS | {'PK', 'SK', 'propagation_enabled', 'updated_at', 'updated_by'}}
    if not approved:
        raise ValueError('CONTROL must be initialized before bootstrap')
    names = {f'#f{i}': k for i, k in enumerate(approved)}
    values = {f':v{i}': v for i, v in enumerate(approved.values())}
    audit = {**request, 'PK': {'S': 'AUDIT#PROPAGATION'}, 'SK': {'S': 'LATEST_BOOTSTRAP'},
             'action': {'S': 'PREPARE_BOOTSTRAP'}, 'actor': {'S': operator}, 'result': {'S': 'APPLIED'}}
    return [
        {'ConditionCheck': {'TableName': table, 'Key': {'PK': {'S': 'CONTROL'}, 'SK': {'S': 'GLOBAL'}},
            'ConditionExpression': ' AND '.join(f'#f{i} = :v{i}' for i in range(len(approved))),
            'ExpressionAttributeNames': names, 'ExpressionAttributeValues': values}},
        {'ConditionCheck': {'TableName': table, 'Key': {'PK': {'S': 'CURRENT'}, 'SK': {'S': 'GLOBAL'}},
            'ConditionExpression': '#s = :u AND attribute_not_exists(generation) AND attribute_not_exists(stack_id) AND attribute_not_exists(instance_id)',
            'ExpressionAttributeNames': {'#s': 'status'}, 'ExpressionAttributeValues': {':u': {'S': 'UNINITIALIZED'}}}},
        {'ConditionCheck': {'TableName': table, 'Key': {'PK': {'S': 'HOLD'}, 'SK': {'S': 'ACTIVE'}},
            'ConditionExpression': 'attribute_not_exists(PK)'}},
        {'Put': {'TableName': table, 'Item': request, 'ConditionExpression': 'attribute_not_exists(PK)'}},
        {'Put': {'TableName': table, 'Item': audit}},
    ]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--environment', default='sandbox')
    parser.add_argument('--region', default='us-west-2', choices=['us-west-2'])
    parser.add_argument('--profile')
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args(argv)
    table = 'cloud-glider-' + args.environment + '-state'
    common = ['--region', args.region, '--output', 'json']
    if args.profile:
        common += ['--profile', args.profile]
    def aws(*command):
        return json.loads(subprocess.check_output(['aws', *command, *common], text=True))
    control = aws('dynamodb', 'get-item', '--table-name', table, '--consistent-read',
                  '--key', json.dumps({'PK': {'S': 'CONTROL'}, 'SK': {'S': 'GLOBAL'}})).get('Item', {})
    if control.get('environment') != {'S': args.environment}:
        raise SystemExit('Missing or mismatched CONTROL environment')
    if 'propagation_enabled' in control:
        raise SystemExit('Legacy CONTROL requires reviewed offline migration; no changes made.')
    existing = aws('dynamodb', 'get-item', '--table-name', table, '--consistent-read',
                   '--key', json.dumps({'PK': {'S': 'BOOTSTRAP'}, 'SK': {'S': 'REQUEST'}})).get('Item', {})
    if existing:
        if (existing.get('status') == {'S': 'READY'}
                and existing.get('bootstrap_requested') == {'BOOL': False}
                and existing.get('control_sha256') == {'S': fingerprint(control)}
                and existing.get('schema_version') == {'S': '2'}
                and existing.get('cleanup_requested') == {'BOOL': False}
                and existing.get('cleanup_status', {}).get('S') in ('IDLE', 'COMPLETE')
                and re.fullmatch(r'[1-9][0-9]{0,17}', existing.get('request_id', {}).get('S', ''))):
            print(json.dumps({'mode': 'ALREADY_PREPARED', 'table': table,
                              'request_id': existing['request_id']['S'],
                              'bootstrap_requested': False,
                              'message': 'No changes made. Preparation is complete; this is not a readiness check.'}))
            return
        raise SystemExit('BOOTSTRAP/REQUEST already exists and is active, completed, legacy, or differs '
                         'from the approved control. Inspect it; no changes made. Do not delete or reset it to retry.')
    operator = aws('sts', 'get-caller-identity')['Arn']
    request_id = '1'
    transaction = build_transaction(table, control, operator, request_id, int(time.time()))
    if not args.apply:
        print(json.dumps({'mode': 'DRY_RUN', 'transact_items': transaction}, indent=2))
        return
    completed = subprocess.run(
        ['aws', 'dynamodb', 'transact-write-items', '--transact-items', json.dumps(transaction),
         '--client-request-token', str(uuid.uuid4()), *common], capture_output=True, text=True)
    if completed.returncode:
        print(completed.stderr.strip(), file=sys.stderr)
        raise SystemExit('Bootstrap preparation was not confirmed. Inspect CONTROL, CURRENT, HOLD, '
                         'and BOOTSTRAP/REQUEST before retrying. Transaction condition order: '
                         'approved control, uninitialized CURRENT, no HOLD, absent bootstrap request, '
                         'absent audit event. No automatic overwrite or retry was attempted.')
    print(json.dumps({'mode': 'APPLIED', 'request_id': request_id, 'table': table}))


if __name__ == '__main__':
    try:
        main()
    except subprocess.CalledProcessError as exc:
        raise SystemExit(f'AWS read failed (exit {exc.returncode}); bootstrap preparation stopped.') from None
