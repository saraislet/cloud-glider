#!/usr/bin/env python3
"""Preview or submit a one-shot bootstrap request, independently of propagation_enabled."""
import argparse
import hashlib
import json
import subprocess
import time
import uuid
from datetime import datetime, timezone


def fingerprint(control):
    approved = {k: v for k, v in control.items() if k not in ('propagation_enabled', 'updated_at', 'updated_by')}
    return hashlib.sha256(json.dumps(approved, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def build_transaction(table, control, operator, request_id, now):
    request = {
        'PK': {'S': 'BOOTSTRAP'}, 'SK': {'S': 'REQUEST'}, 'status': {'S': 'REQUESTED'},
        'request_id': {'S': request_id}, 'requested_by': {'S': operator},
        'requested_at': {'S': datetime.fromtimestamp(now, timezone.utc).isoformat()},
        'valid_until_epoch': {'N': str(now + 900)}, 'control_sha256': {'S': fingerprint(control)},
    }
    approved = {k: v for k, v in control.items() if k not in ('PK', 'SK', 'propagation_enabled', 'updated_at', 'updated_by')}
    if not approved:
        raise ValueError('CONTROL must be initialized before bootstrap')
    names = {f'#f{i}': k for i, k in enumerate(approved)}
    values = {f':v{i}': v for i, v in enumerate(approved.values())}
    audit = {**request, 'PK': {'S': 'AUDIT#PROPAGATION'}, 'SK': {'S': 'BOOTSTRAP#' + request_id},
             'action': {'S': 'REQUEST_BOOTSTRAP'}, 'actor': {'S': operator}, 'result': {'S': 'APPLIED'}}
    return [
        {'ConditionCheck': {'TableName': table, 'Key': {'PK': {'S': 'CONTROL'}, 'SK': {'S': 'GLOBAL'}},
            'ConditionExpression': ' AND '.join(f'#f{i} = :v{i}' for i in range(len(approved))),
            'ExpressionAttributeNames': names, 'ExpressionAttributeValues': values}},
        {'ConditionCheck': {'TableName': table, 'Key': {'PK': {'S': 'CURRENT'}, 'SK': {'S': 'GLOBAL'}},
            'ConditionExpression': '#s = :u AND attribute_not_exists(generation) AND attribute_not_exists(stack_id) AND attribute_not_exists(instance_id)',
            'ExpressionAttributeNames': {'#s': 'status'}, 'ExpressionAttributeValues': {':u': {'S': 'UNINITIALIZED'}}}},
        {'ConditionCheck': {'TableName': table, 'Key': {'PK': {'S': 'HOLD'}, 'SK': {'S': 'ACTIVE'}},
            'ConditionExpression': 'attribute_not_exists(PK)'}},
        *[{'Put': {'TableName': table, 'Item': item, 'ConditionExpression': 'attribute_not_exists(PK)'}}
          for item in (request, audit)],
    ]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--environment', default='sandbox')
    parser.add_argument('--region', default='us-west-2', choices=['us-west-2'])
    parser.add_argument('--profile')
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
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
    operator = aws('sts', 'get-caller-identity')['Arn']
    request_id = str(uuid.uuid4())
    transaction = build_transaction(table, control, operator, request_id, int(time.time()))
    if not args.apply:
        print(json.dumps({'mode': 'DRY_RUN', 'transact_items': transaction}, indent=2))
        return
    subprocess.run(['aws', 'dynamodb', 'transact-write-items', '--transact-items', json.dumps(transaction),
                    '--client-request-token', request_id, *common], check=True)
    print(json.dumps({'mode': 'APPLIED', 'request_id': request_id, 'table': table}))


if __name__ == '__main__':
    main()
