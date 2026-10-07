#!/usr/bin/env python3
"""Prepare offline audit migration requests from local snapshots; never contacts AWS."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
from pathlib import Path

from request_bootstrap import fingerprint
from migrate_lifecycle import exact as lifecycle_condition

PARTITIONS = frozenset({'AUDIT#PROPAGATION', 'AUDIT#RELEASE'})
MAX_REQUEST_BYTES = 3_000_000  # Conservatively below DynamoDB's 4 MiB transaction limit.


def exact(item):
    condition = lifecycle_condition(item)
    if len(condition['ConditionExpression'].encode()) > 4096:
        raise ValueError('Snapshot has too many attributes for a safe conditional request')
    return condition


def key(item):
    return item['PK']['S'], item['SK']['S']


def index_items(items):
    result = {}
    for item in items:
        identity = key(item)
        if identity[0] not in PARTITIONS or not identity[1]:
            raise ValueError('Only propagation and release audit items may be migrated')
        if identity in result:
            raise ValueError('Duplicate audit key in snapshot')
        result[identity] = item
    return result


def batches(groups):
    """Keep each source/destination pair together, bounded by actions and bytes."""
    batch = []
    for group in groups:
        if len(json.dumps({'TransactItems': group}).encode()) > MAX_REQUEST_BYTES:
            raise ValueError('Item group exceeds conservative request-size limit')
        candidate = batch + group
        if len(candidate) > 100 or len(json.dumps({'TransactItems': candidate}).encode()) > MAX_REQUEST_BYTES:
            yield {'TransactItems': batch}
            batch = []
        batch.extend(group)
    if batch:
        yield {'TransactItems': batch}


def validate_offline(snapshot, environment):
    control, current, request = (snapshot[name] for name in ('control', 'current', 'request'))
    if control.get('environment') != {'S': environment}:
        raise ValueError('Snapshot environment mismatch')
    for item, identity in ((control, ('CONTROL', 'GLOBAL')), (current, ('CURRENT', 'GLOBAL')),
                           (request, ('BOOTSTRAP', 'REQUEST'))):
        if key(item) != identity:
            raise ValueError('Invalid lifecycle record identity')
    if 'propagation_enabled' in control:
        raise ValueError('Legacy lifecycle requires its separate migration first')
    if any(control.get(field) != {'BOOL': False} for field in
           ('start_requested', 'stop_requested', 'cleanup_requested')) or control.get('active_command') != {'S': 'NONE'}:
        raise ValueError('Operator commands must be idle')
    if request.get('schema_version') != {'S': '2'} or request.get('status') != {'S': 'READY'}:
        raise ValueError('Lifecycle must be READY')
    if any(request.get(field) != {'BOOL': False} for field in
           ('bootstrap_requested', 'propagation_enabled', 'cleanup_requested')):
        raise ValueError('Launch gates and cleanup must be disabled')
    if request.get('cleanup_status') not in ({'S': 'IDLE'}, {'S': 'COMPLETE'}):
        raise ValueError('Cleanup is unresolved')
    if current.get('status') != {'S': 'UNINITIALIZED'} or any(field in current for field in
           ('generation', 'instance_id', 'stack_id')):
        raise ValueError('CURRENT must have no owner')
    if snapshot['locks'] or snapshot['generation_items']:
        raise ValueError('Locks and generation inventory must be empty')
    if request.get('control_sha256') != {'S': fingerprint(control)}:
        raise ValueError('Approved-control fingerprint mismatch')
    return control, current, request


def prepare(snapshot, environment, destination=None, *, prepare_removal=False):
    if not re.fullmatch(r'[a-z][a-z0-9-]{1,15}', environment):
        raise ValueError('Invalid environment')
    control, current, request = validate_offline(snapshot, environment)
    source_table, audit_table = (f'cloud-glider-{environment}-{suffix}' for suffix in ('state', 'audit'))
    source = index_items(snapshot['audit_items'])
    target = index_items(destination or [])
    for identity, item in source.items():
        if identity in target and item != target[identity]:
            raise ValueError('Destination collision: ' + repr(identity))
    if prepare_removal and (destination is None or any(identity not in target for identity in source)):
        raise ValueError('Every source item must be verified in a fresh destination snapshot before removal')
    copies = [[{'ConditionCheck': {'TableName': source_table, 'Key': {'PK': item['PK'], 'SK': item['SK']}, **exact(item)}},
               {'Put': {'TableName': audit_table, 'Item': item,
                        'ConditionExpression': 'attribute_not_exists(PK)'}}]
              for identity, item in sorted(source.items()) if identity not in target]
    # SOURCE cleanup requests are withheld until complete destination verification.
    removals = [[
        {'ConditionCheck': {'TableName': audit_table, 'Key': {'PK': item['PK'], 'SK': item['SK']}, **exact(item)}},
        {'Delete': {'TableName': source_table, 'Key': {'PK': item['PK'], 'SK': item['SK']}, **exact(item)}},
    ] for _, item in sorted(source.items())] if prepare_removal else []
    if control.get('audit_table_name') not in (None, {'S': audit_table}):
        raise ValueError('Existing audit destination differs from designated table')
    verified = destination is not None and all(identity in target for identity in source)
    approved = copy.deepcopy(control)
    approved['audit_table_name'] = {'S': audit_table}
    refreshed = {**request, 'control_sha256': {'S': fingerprint(approved)}}
    cutover = [
        {'Put': {'TableName': source_table, 'Item': approved, **exact(control)}},
        {'Put': {'TableName': source_table, 'Item': refreshed, **exact(request)}},
        {'ConditionCheck': {'TableName': source_table, 'Key': {'PK': current['PK'], 'SK': current['SK']}, **exact(current)}},
        *[{'ConditionCheck': {'TableName': source_table, 'Key': {'PK': {'S': 'LOCK'}, 'SK': {'S': name}},
                              'ConditionExpression': 'attribute_not_exists(PK)'}} for name in ('PROPAGATION', 'PROVISIONING')],
    ]
    canonical = json.dumps([item for _, item in sorted(source.items())], sort_keys=True, separators=(',', ':'))
    return {'source_table': source_table, 'audit_table': audit_table,
            'manifest': {'item_count': len(source), 'sha256': hashlib.sha256(canonical.encode()).hexdigest(),
                         'keys': [list(identity) for identity in sorted(source)]},
            'copy_requests': list(batches(copies)), 'cutover_request': {'TransactItems': cutover} if verified else None,
            'removal_requests': list(batches(removals))}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--snapshot', type=Path, required=True)
    parser.add_argument('--environment', default='sandbox')
    parser.add_argument('--destination-snapshot', type=Path, help='JSON array of complete destination audit items')
    parser.add_argument('--prepare-removal', action='store_true')
    parser.add_argument('--writers-paused', action='store_true')
    parser.add_argument('--inventory-verified-empty', action='store_true')
    args = parser.parse_args()
    if not args.writers_paused or not args.inventory_verified_empty:
        parser.error('Attest all writers are paused and AWS generation inventory is verified empty')
    try:
        result = prepare(json.loads(args.snapshot.read_text()), args.environment,
                         json.loads(args.destination_snapshot.read_text()) if args.destination_snapshot else None,
                         prepare_removal=args.prepare_removal)
    except (ValueError, KeyError, TypeError, OSError) as exc:
        parser.error(str(exc))
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
