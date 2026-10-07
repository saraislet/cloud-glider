#!/usr/bin/env python3
"""Prepare an offline DynamoDB rename transaction from reviewed snapshots; never apply it."""
import argparse
import copy
import json
import re
import uuid

from migrate_lifecycle import exact
from request_bootstrap import fingerprint

ARTIFACT_FIELDS = ('bucket', 'key', 'version_id', 'sha256')
RELEASE_FIELDS = {
    'desired_template_version', 'desired_bootstrap_version', 'template_s3_bucket',
    'template_s3_key', 'template_s3_version_id', 'template_sha256', 'template_build_id',
    'launch_template_id', 'launch_template_version', 'launch_template_sha256',
    *('daemon_artifact_' + suffix for suffix in ARTIFACT_FIELDS),
}


def build_transaction(table, snapshot, approved_control, actor, event_id):
    """Fence the saved idle state and change only release pins and artifact names."""
    control, current, request = (snapshot[k] for k in ('control', 'current', 'request'))
    for item, pk, sk in ((control, 'CONTROL', 'GLOBAL'), (current, 'CURRENT', 'GLOBAL'),
                         (request, 'BOOTSTRAP', 'REQUEST')):
        if item.get('PK') != {'S': pk} or item.get('SK') != {'S': sk}:
            raise ValueError('Snapshot identity mismatch')
    if any(control.get(k) != {'BOOL': False} for k in
           ('start_requested', 'stop_requested', 'cleanup_requested')):
        raise ValueError('Operator commands must be idle')
    if control.get('active_command') != {'S': 'NONE'}:
        raise ValueError('An operator command is active')
    if request.get('schema_version') != {'S': '2'} or request.get('status') != {'S': 'READY'}:
        raise ValueError('Lifecycle must be READY in the shared schema')
    if any(request.get(k) != {'BOOL': False} for k in
           ('bootstrap_requested', 'propagation_enabled', 'cleanup_requested')):
        raise ValueError('Launch gates and cleanup must be disabled')
    if request.get('cleanup_status', {}).get('S') not in ('IDLE', 'COMPLETE'):
        raise ValueError('Cleanup is unresolved')
    if current.get('status') != {'S': 'UNINITIALIZED'} or any(
        k in current for k in ('generation', 'stack_id', 'instance_id')):
        raise ValueError('CURRENT must have no live owner')
    if not re.fullmatch('[1-9][0-9]{0,17}', request.get('request_id', {}).get('S', '')):
        raise ValueError('Invalid cycle identity')
    if request.get('control_sha256') != {'S': fingerprint(control)}:
        raise ValueError('Existing approved-control fingerprint does not match the snapshot')
    renamed = copy.deepcopy(control)
    for suffix in ARTIFACT_FIELDS:
        old, new = 'agent_artifact_' + suffix, 'daemon_artifact_' + suffix
        if old not in renamed or new in renamed:
            raise ValueError('Expected an unmigrated artifact schema')
        renamed[new] = renamed.pop(old)
    if set(approved_control) != set(renamed):
        raise ValueError('Approved CONTROL must preserve the complete schema except the rename')
    for field in renamed:
        if field not in RELEASE_FIELDS and renamed[field] != approved_control[field]:
            raise ValueError('Cannot change non-release field: ' + field)
    for field in RELEASE_FIELDS & approved_control.keys():
        value = approved_control[field].get('S', '')
        if not value:
            raise ValueError('Release pins must be nonempty strings: ' + field)
        if field.endswith('sha256') and not re.fullmatch('[0-9a-f]{64}', value):
            raise ValueError('Invalid SHA-256: ' + field)
    if 'launch_template_version' in approved_control and not re.fullmatch(
        '[1-9][0-9]*', approved_control['launch_template_version']['S']):
        raise ValueError('Launch template version must be numeric')
    refreshed = {**request, 'control_sha256': {'S': fingerprint(approved_control)}}
    operations = [
        {'Put': {'TableName': table, 'Item': approved_control, **exact(control)}},
        {'Put': {'TableName': table, 'Item': refreshed, **exact(request)}},
        {'ConditionCheck': {'TableName': table, 'Key': {'PK': current['PK'], 'SK': current['SK']},
                            **exact(current)}},
    ]
    for lock in ('PROPAGATION', 'PROVISIONING'):
        operations.append({'ConditionCheck': {'TableName': table,
            'Key': {'PK': {'S': 'LOCK'}, 'SK': {'S': lock}},
            'ConditionExpression': 'attribute_not_exists(PK)'}})
    # Reject a concurrent partial rename instead of accepting mixed schemas.
    put = operations[0]['Put']
    for suffix in ARTIFACT_FIELDS:
        alias = '#new_' + suffix
        put['ExpressionAttributeNames'][alias] = 'daemon_artifact_' + suffix
        put['ConditionExpression'] += ' AND attribute_not_exists(' + alias + ')'
    operations.append({'Put': {'TableName': 'cloud-glider-' + approved_control['environment']['S'] + '-audit', 'Item': {
        'PK': {'S': 'AUDIT#PROPAGATION'}, 'SK': {'S': 'DAEMON_MIGRATION#' + event_id},
        'action': {'S': 'OFFLINE_DAEMON_RENAME'}, 'actor': {'S': actor},
        'previous_control': {'M': control}, 'approved_control': {'M': approved_control},
        'previous_request': {'M': request}}, 'ConditionExpression': 'attribute_not_exists(PK)'}})
    return operations


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--table-name', required=True)
    parser.add_argument('--snapshot', required=True, help='JSON with control/current/request in DynamoDB wire format')
    parser.add_argument('--approved-control', required=True, help='Reviewed complete new CONTROL item in wire format')
    parser.add_argument('--operator-id', required=True)
    parser.add_argument('--operator-path-paused', action='store_true')
    parser.add_argument('--inventory-verified-empty', action='store_true')
    args = parser.parse_args()
    if not args.operator_path_paused or not args.inventory_verified_empty:
        parser.error('Attest paused provisioning paths and verified empty AWS inventory first')
    with open(args.snapshot) as stream:
        snapshot = json.load(stream)
    with open(args.approved_control) as stream:
        approved = json.load(stream)
    print(json.dumps(build_transaction(args.table_name, snapshot, approved,
                                      args.operator_id, str(uuid.uuid4())), indent=2))


if __name__ == '__main__':
    main()
