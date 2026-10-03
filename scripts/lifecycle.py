#!/usr/bin/env python3
"""Preview or apply a conditional operator action on the shared lifecycle record."""
import argparse
import json
import re
import subprocess
import time


def build_update(table, request, action, actor, now):
    request_id = request.get('request_id', {}).get('S', '')
    if request.get('schema_version') != {'S': '2'} or not re.fullmatch(r'[1-9][0-9]{0,17}', request_id):
        raise ValueError('Migrate legacy lifecycle state offline before using this tool')
    assignments = {'updated_by': {'S': actor}, 'updated_at': {'N': str(now)}}
    names, values = {'#status': 'status'}, {':id': {'S': request_id}, ':no': {'BOOL': False}}
    condition = 'request_id = :id'
    if action == 'cleanup':
        if request.get('cleanup_status', {}).get('S') not in ('IDLE', 'COMPLETE'):
            raise ValueError('Cleanup already active or needs inspection; use resume-cleanup after inspection')
        assignments.update(cleanup_requested={'BOOL': True}, propagation_enabled={'BOOL': False}, bootstrap_requested={'BOOL': False})
        condition += ' AND cleanup_requested = :no AND cleanup_status IN (:idle, :complete)'
        values.update({':idle': {'S': 'IDLE'}, ':complete': {'S': 'COMPLETE'}})
    elif action == 'resume-cleanup':
        if request.get('cleanup_status') != {'S': 'NEEDS_ATTENTION'}:
            raise ValueError('Only an inspected NEEDS_ATTENTION cleanup can be resumed')
        assignments.update(cleanup_requested={'BOOL': True}, cleanup_status={'S': 'QUIESCING'},
            cleanup_started_at={'N': str(now)}, cleanup_error={'S': ''}, cleanup_retry_token={'S': ''},
            propagation_enabled={'BOOL': False}, bootstrap_requested={'BOOL': False})
        condition += ' AND cleanup_status = :attention'
        values.pop(':no')
        values[':attention'] = {'S': 'NEEDS_ATTENTION'}
    else:
        condition += ' AND cleanup_requested = :no AND cleanup_status IN (:idle, :complete)'
        values.update({':idle': {'S': 'IDLE'}, ':complete': {'S': 'COMPLETE'}})
        if action == 'bootstrap':
            assignments['bootstrap_requested'] = {'BOOL': True}
            condition += ' AND #status = :ready AND bootstrap_requested = :no'
            values[':ready'] = {'S': 'READY'}
        elif action in ('enable', 'disable'):
            assignments['propagation_enabled'] = {'BOOL': action == 'enable'}
        else:
            raise ValueError('Unknown lifecycle action')
    if action != 'bootstrap':
        names.pop('#status')
    for i, (field, value) in enumerate(assignments.items()):
        names[f'#f{i}'] = field
        values[f':v{i}'] = value
    # Resume only after the operator reconciles any durable provisioning marker.
    return {'TableName': table, 'Key': {'PK': {'S': 'BOOTSTRAP'}, 'SK': {'S': 'REQUEST'}},
        'UpdateExpression': 'SET ' + ', '.join(f'#f{i} = :v{i}' for i in range(len(assignments))),
        'ConditionExpression': condition, 'ExpressionAttributeNames': names, 'ExpressionAttributeValues': values}



def build_control_request(table, control, action, actor, now):
    field = {'start': 'start_requested', 'stop': 'stop_requested', 'cleanup': 'cleanup_requested'}[action]
    request_fields = ('start_requested', 'stop_requested', 'cleanup_requested')
    if any(control.get(name) != {'BOOL': False} for name in request_fields):
        raise ValueError('A request is pending or the operator interface is not installed; refresh CONTROL/GLOBAL.')
    assignments = {field: {'BOOL': True}, 'updated_by': {'S': actor}, 'updated_at': {'N': str(now)}}
    names = {f'#f{i}': name for i, name in enumerate(assignments)}
    values = {f':v{i}': value for i, value in enumerate(assignments.values())}
    names.update({f'#r{i}': name for i, name in enumerate(request_fields)})
    values[':no'] = {'BOOL': False}
    values[':sequence'] = control['command_sequence']
    return {'Update': {'TableName': table, 'Key': {'PK': {'S': 'CONTROL'}, 'SK': {'S': 'GLOBAL'}},
        'UpdateExpression': 'SET ' + ', '.join(f'#f{i} = :v{i}' for i in range(len(assignments))),
        'ConditionExpression': 'command_sequence = :sequence AND ' + ' AND '.join(f'#r{i} = :no' for i in range(3)),
        'ExpressionAttributeNames': names, 'ExpressionAttributeValues': values}}

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['start', 'stop', 'bootstrap', 'enable', 'disable', 'cleanup', 'resume-cleanup'])
    parser.add_argument('--environment', default='sandbox')
    parser.add_argument('--region', default='us-west-2', choices=['us-west-2'])
    parser.add_argument('--profile')
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args(argv)
    common = ['--region', args.region, '--output', 'json', '--no-cli-pager']
    if args.profile:
        common += ['--profile', args.profile]
    def aws(*command):
        return json.loads(subprocess.check_output(['aws', *command, *common], text=True))
    table = 'cloud-glider-' + args.environment + '-state'
    actor = aws('sts', 'get-caller-identity')['Arn']
    if args.action in ('start', 'stop', 'cleanup'):
        control = aws('dynamodb', 'get-item', '--table-name', table, '--consistent-read',
                      '--key', json.dumps({'PK': {'S': 'CONTROL'}, 'SK': {'S': 'GLOBAL'}})).get('Item', {})
        transaction = [build_control_request(table, control, args.action, actor, int(time.time()))]
    else:
        # Advanced lower-level helpers are retained for preparation and recovery.
        request = aws('dynamodb', 'get-item', '--table-name', table, '--consistent-read',
                      '--key', json.dumps({'PK': {'S': 'BOOTSTRAP'}, 'SK': {'S': 'REQUEST'}})).get('Item', {})
        update = build_update(table, request, args.action, actor, int(time.time()))
        transaction = [{'Update': update}]
        if args.action in ('bootstrap', 'enable'):
            transaction.insert(0, {'ConditionCheck': {'TableName': table,
                'Key': {'PK': {'S': 'HOLD'}, 'SK': {'S': 'ACTIVE'}}, 'ConditionExpression': 'attribute_not_exists(PK)'}})
    if not args.apply:
        print(json.dumps({'mode': 'DRY_RUN', 'transact_items': transaction}, indent=2))
        return
    subprocess.check_call(['aws', 'dynamodb', 'transact-write-items', '--transact-items', json.dumps(transaction), *common])
    print(json.dumps({'mode': 'APPLIED', 'action': args.action, 'message': 'Refresh CONTROL/GLOBAL for the result.'}))


if __name__ == '__main__':
    try:
        main()
    except (ValueError, subprocess.CalledProcessError) as exc:
        raise SystemExit(str(exc)) from None
