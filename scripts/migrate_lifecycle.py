#!/usr/bin/env python3
"""Offline, dry-run-first migration from legacy control to shared lifecycle schema."""
import argparse
import json
import subprocess
import time
import uuid

from request_bootstrap import build_transaction as prepare, operator_defaults


def exact(item):
    if not item:
        return {'ConditionExpression': 'attribute_not_exists(PK)'}
    return {'ConditionExpression': ' AND '.join(f'#f{i} = :v{i}' for i in range(len(item))),
        'ExpressionAttributeNames': {f'#f{i}': field for i, field in enumerate(item)},
        'ExpressionAttributeValues': {f':v{i}': value for i, value in enumerate(item.values())}}


def build_transaction(table, control, current, request, states, actor, now):
    if control.get('propagation_enabled') != {'BOOL': False}:
        raise ValueError('Disable legacy propagation before offline migration')
    if request.get('schema_version') == {'S': '2'}:
        raise ValueError('Lifecycle already migrated; do not reset its cycle counter')
    if len(states) > 92:
        raise ValueError('Too many generation records for a single reviewed migration')
    migrated = {key: value for key, value in control.items() if key != 'propagation_enabled'}
    migrated['audit_table_name'] = {'S': 'cloud-glider-' + control['environment']['S'] + '-audit'}
    migrated['generation_table_name'] = {'S': 'cloud-glider-' + control['environment']['S'] + '-generations'}
    migrated.update(operator_defaults())
    migrated['cycle_initialized'] = {'BOOL': True}
    lifecycle = prepare(table, migrated, actor, '1', now)[3]['Put']['Item']
    reset_current = {key: value for key, value in current.items()
                     if key not in ('generation', 'stack_id', 'instance_id', 'request_id', 'handoff_token')}
    reset_current['status'] = {'S': 'UNINITIALIZED'}
    audit = {'PK': {'S': 'AUDIT#PROPAGATION'}, 'SK': {'S': 'LATEST_MIGRATION'},
        'action': {'S': 'OFFLINE_LIFECYCLE_MIGRATION'}, 'actor': {'S': actor},
        'previous_control': {'M': control}, 'previous_current': {'M': current},
        'previous_request': {'M': request}, 'previous_generation_records': {'L': [{'M': item} for item in states]}}
    # HOLD is intentionally neither deleted nor cleared.
    return [
        {'ConditionCheck': {'TableName': table, 'Key': {'PK': {'S': 'LOCK'}, 'SK': {'S': 'PROPAGATION'}},
                           'ConditionExpression': 'attribute_not_exists(PK)'}},
        {'ConditionCheck': {'TableName': table, 'Key': {'PK': {'S': 'LOCK'}, 'SK': {'S': 'PROVISIONING'}},
                           'ConditionExpression': 'attribute_not_exists(PK)'}},
        {'Put': {'TableName': table, 'Item': migrated, **exact(control)}},
        {'Put': {'TableName': table, 'Item': reset_current, **exact(current)}},
        {'Put': {'TableName': table, 'Item': lifecycle, **exact(request)}},
        *[{'Delete': {'TableName': table, 'Key': {'PK': item['PK'], 'SK': item['SK']}, **exact(item)}} for item in states],
        {'Put': {'TableName': migrated['audit_table_name']['S'], 'Item': audit, 'ConditionExpression': 'attribute_not_exists(PK)'}}]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--environment', default='sandbox')
    parser.add_argument('--region', default='us-west-2', choices=['us-west-2'])
    parser.add_argument('--profile')
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--operator-path-paused', action='store_true',
                        help='Attest that old bootstrap delivery and operator provisioning paths are paused')
    args = parser.parse_args(argv)
    if not args.operator_path_paused:
        raise ValueError('Pause the old bootstrap event source and all operator provisioning paths first')
    common = ['--region', args.region, '--output', 'json', '--no-cli-pager']
    if args.profile:
        common += ['--profile', args.profile]
    def aws(*command, allow_missing=False):
        result = subprocess.run(['aws', *command, *common], text=True, capture_output=True)
        if result.returncode:
            if allow_missing and 'does not exist' in result.stderr:
                return {}
            raise ValueError('AWS inventory or transaction failed: ' + result.stderr.strip())
        return json.loads(result.stdout) if result.stdout.strip() else {}
    table = 'cloud-glider-' + args.environment + '-state'
    prefix = 'cloud-glider-' + args.environment + '-gen-'
    stacks = aws('cloudformation', 'list-stacks').get('StackSummaries', [])
    if any(stack['StackName'].startswith(prefix) and stack['StackStatus'] != 'DELETE_COMPLETE' for stack in stacks):
        raise ValueError('Delete and verify all legacy generation stacks through the operator CloudFormation path first')
    reservations = aws('ec2', 'describe-instances', '--filters',
        'Name=tag:project,Values=cloud-glider', 'Name=tag:environment,Values=' + args.environment).get('Reservations', [])
    if any(instance['State']['Name'] != 'terminated' for reservation in reservations for instance in reservation['Instances']):
        raise ValueError('Legacy generation instances still exist')
    def get(pk, sk):
        return aws('dynamodb', 'get-item', '--table-name', table, '--consistent-read',
                   '--key', json.dumps({'PK': {'S': pk}, 'SK': {'S': sk}})).get('Item', {})
    control, current, request = get('CONTROL', 'GLOBAL'), get('CURRENT', 'GLOBAL'), get('BOOTSTRAP', 'REQUEST')
    if control.get('environment') != {'S': args.environment} or not current:
        raise ValueError('Missing or mismatched CONTROL/CURRENT')
    states = aws('dynamodb', 'scan', '--table-name', table, '--consistent-read',
        '--filter-expression', 'begins_with(PK, :gen)',
        '--expression-attribute-values', json.dumps({':gen': {'S': 'GEN#'}})).get('Items', [])
    known_ids = {item['stack_id']['S'] for item in [current, request, *states] if 'stack_id' in item}
    for stack_id in known_ids:
        result = aws('cloudformation', 'describe-stacks', '--stack-name', stack_id, allow_missing=True)
        if any(stack['StackStatus'] != 'DELETE_COMPLETE' for stack in result.get('Stacks', [])):
            raise ValueError('Recorded generation stack is still live; migration stopped')
    generation_table = 'cloud-glider-' + args.environment + '-generations'
    if aws('dynamodb', 'scan', '--table-name', generation_table, '--consistent-read', '--select', 'COUNT').get('Count', 0):
        raise ValueError('The separate generation table must be empty before offline migration')
    actor = aws('sts', 'get-caller-identity')['Arn']
    transaction = build_transaction(table, control, current, request, states, actor, int(time.time()))
    if not args.apply:
        print(json.dumps({'mode': 'DRY_RUN', 'transact_items': transaction}, indent=2))
        return
    aws('dynamodb', 'transact-write-items', '--transact-items', json.dumps(transaction), '--client-request-token', str(uuid.uuid4()))
    print(json.dumps({'mode': 'APPLIED', 'schema_version': '2', 'request_id': '1', 'propagation_enabled': False}))


if __name__ == '__main__':
    try:
        main()
    except (ValueError, subprocess.CalledProcessError) as exc:
        raise SystemExit(str(exc)) from None
