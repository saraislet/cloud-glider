#!/usr/bin/env python3
"""Read-only trial inventory, evidence capture and conservative cleanup receipts.

Requires boto3 and existing read permissions. Never requests cleanup or deletes.
"""
import argparse
import base64
import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
METRICS = {'CPUUtilization': 'Average', 'CPUCreditUsage': 'Sum',
           'CPUCreditBalance': 'Minimum', 'CPUSurplusCreditBalance': 'Maximum',
           'CPUSurplusCreditsCharged': 'Sum'}


def now():
    return datetime.now(timezone.utc).isoformat()


def stamp(value):
    result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if result.tzinfo is None:
        raise ValueError('Timestamps must include a timezone')
    return result


def private_save(path, value):
    """Exclusive, owner-only writes: retries preserve every prior observation."""
    path = Path(path).absolute()
    if (ROOT / '.artifacts').is_symlink():
        raise ValueError('Private artifact directory cannot be a symlink')
    root = (ROOT / '.artifacts').resolve()
    if not path.resolve().is_relative_to(root):
        raise ValueError('Private output must be inside this checkout .artifacts')
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    parent = path.parent
    while parent != root.parent:
        if parent.is_symlink():
            raise ValueError('Symlink in private output path')
        parent.chmod(0o700)
        parent = parent.parent
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as stream:
        json.dump(value, stream, indent=2, default=str)
        stream.write('\n')


def validate_scope(scope):
    required = ('account', 'region', 'role_arn', 'task', 'trial', 'kind',
                'started_at', 'ownership_reviewed_at', 'no_other_operator_work',
                'stacks', 'retained_control_plane', 'coverage')
    if any(key not in scope for key in required):
        raise ValueError('Missing scope fields: ' + ', '.join(required))
    if not re.fullmatch(r'[0-9]{12}', scope['account']) or not scope['role_arn'].startswith(
            f"arn:aws:iam::{scope['account']}:role/"):
        raise ValueError('Expected account and exact IAM task role ARN required')
    if scope['kind'] not in ('smoke', 'propagation'):
        raise ValueError('Unknown trial kind')
    if scope['no_other_operator_work'] is not True:
        raise ValueError('Another operator may be active; inventory only after review')
    age = (datetime.now(timezone.utc) - stamp(scope['ownership_reviewed_at'])).total_seconds()
    if not 0 <= age <= 900:
        raise ValueError('Ownership review older than 15 minutes or in future')
    if stamp(scope['started_at']) > datetime.now(timezone.utc):
        raise ValueError('Trial start is in the future')
    if scope['kind'] == 'smoke' and not (scope.get('approved_image_id') and scope.get('daemon_sha256')):
        raise ValueError('Smoke scope requires approved_image_id and daemon_sha256')
    if scope['kind'] == 'propagation' and not all(scope.get(k) for k in
            ('launch_template_id', 'launch_template_version', 'source_commit', 'daemon_sha256')):
        raise ValueError('Propagation scope requires exact template version and artifact pins')
    if scope.get('launch_template_version') and not str(scope['launch_template_version']).isdigit():
        raise ValueError('Numeric launch-template version required')
    if not scope['task'] or not scope['trial'] or not scope['coverage']:
        raise ValueError('Explicit task, trial and coverage declaration required')
    for arn in scope['stacks']:
        prefix = f"arn:aws:cloudformation:{scope['region']}:{scope['account']}:stack/"
        if not arn.startswith(prefix) or not re.fullmatch(r'[^/]+/[^/]+', arn[len(prefix):]):
            raise ValueError('Exact stack ARN in expected account/Region required')
    if scope['kind'] == 'propagation' and not (scope.get('table') and scope.get('generation_table') and scope.get('tags')):
        raise ValueError('Propagation requires exact control/generation tables and cycle tags')
    if not scope['stacks'] and not scope.get('tags'):
        raise ValueError('Trial needs exact stack identities or trial-specific tags')
    if scope['kind'] == 'propagation' and (not str(scope['trial']).isdigit() or int(scope['trial']) < 1):
        raise ValueError('Propagation cycle must be a positive numeric request ID')
    if scope['kind'] == 'propagation' and scope.get('tags', {}).get('bootstrap-request-id') != scope['trial']:
        raise ValueError('Use exact bootstrap-request-id trial tag for propagation')
    if scope.get('tags') and not any(k in scope['tags'] for k in ('bootstrap-request-id', 'cycle-id', 'cycle_id', 'request_id', 'trial')):
        raise ValueError('Tags must contain a trial/cycle identifier; project alone is unsafe')


def identity(session, scope):
    actual = session.client('sts').get_caller_identity()
    arn = actual['Arn']
    role = scope['role_arn']
    # Role names are account-unique; STS omits the IAM role's path.
    expected_session = f"arn:aws:sts::{scope['account']}:assumed-role/{role.rsplit('/', 1)[-1]}/"
    if actual['Account'] != scope['account'] or not (arn == role or (':role/' in role and arn.startswith(expected_session))):
        raise ValueError('Unexpected account or task role; no resource reads performed')
    if session.region_name != scope['region']:
        raise ValueError('Unexpected Region')
    return actual


def collect(session, scope, previous=None, *, settled=False):
    validate_scope(scope)
    result = {'schema': 1, 'scope': scope, 'identity': identity(session, scope),
              'started_at': now(), 'errors': [], 'instances': {}, 'volumes': {},
              'network_interfaces': {}, 'addresses': {}, 'snapshots': {}, 'nat_gateways': {},
              'vpc_endpoints': {}, 'alarms': {}, 'stacks': {}, 'state': [],
              'console': {}, 'metrics': {}}
    def read(label, fn):
        try:
            return fn()
        except Exception as exc:
            result['errors'].append({'operation': label, 'error': str(exc)})
            return None
    ec2 = session.client('ec2')
    cfn = session.client('cloudformation')
    cw = session.client('cloudwatch')
    ids = {key: set((previous or {}).get(key, {})) for key in
           ('instances', 'volumes', 'network_interfaces', 'addresses')}
    if scope.get('table'):
        for table in set([scope['table'], scope['generation_table']]):
            pages = read('state:' + table, lambda: list(session.client('dynamodb').get_paginator('scan').paginate(
                TableName=table, ConsistentRead=True)))
            result['state'].extend(item for page in pages or [] for item in page['Items'])
        for row in result['state']:
            if row.get('request_id') == {'S': scope['trial']} and row.get('instance_id', {}).get('S'):
                ids['instances'].add(row['instance_id']['S'])
    alarm_names = set(scope.get('alarms', [])) | set((previous or {}).get('alarms', {}))
    for row in result['state']:
        pk = row.get('PK', {}).get('S', '')
        if pk.startswith('GEN#') and row.get('request_id') == {'S': scope['trial']} and scope.get('tags', {}).get('environment'):
            alarm_names.add('cloud-glider-' + scope['tags']['environment'] + '-gen-' + pk[4:] + '-status-check')
    # Stack resource lists include only actual resources, not shared input parameters.
    for arn in scope['stacks']:
        details = read('stack:' + arn, lambda: cfn.describe_stacks(StackName=arn))
        result['stacks'][arn] = details
        resources = read('stack-resources:' + arn, lambda: list(
            cfn.get_paginator('list_stack_resources').paginate(StackName=arn)))
        if resources is not None:
            result.setdefault('stack_resources', {})[arn] = resources
            mapping = {'AWS::EC2::Instance': 'instances', 'AWS::EC2::Volume': 'volumes',
                       'AWS::EC2::NetworkInterface': 'network_interfaces', 'AWS::EC2::EIP': 'addresses'}
            for page in resources:
                if any(r.get('ResourceStatus') == 'DELETE_SKIPPED' for r in page['StackResourceSummaries']):
                    result['errors'].append({'operation': 'stack-resources:' + arn, 'error': 'CloudFormation retained resources'})
                for item in page['StackResourceSummaries']:
                    if item['ResourceType'] in mapping and item.get('PhysicalResourceId'):
                        ids[mapping[item['ResourceType']]].add(item['PhysicalResourceId'])
                    elif item['ResourceType'] == 'AWS::CloudWatch::Alarm' and item.get('PhysicalResourceId'):
                        alarm_names.add(item['PhysicalResourceId'])
                    elif item['ResourceType'] not in ('AWS::CloudFormation::WaitCondition',
                                                       'AWS::CloudFormation::WaitConditionHandle'):
                        result.setdefault('unsupported_resources', []).append(item)
    filters = [{'Name': 'tag:' + k, 'Values': [v]} for k, v in scope.get('tags', {}).items()]
    specs = [('instances', 'describe_instances', 'Reservations', 'InstanceId'),
             ('volumes', 'describe_volumes', 'Volumes', 'VolumeId'),
             ('network_interfaces', 'describe_network_interfaces', 'NetworkInterfaces', 'NetworkInterfaceId'),
             ('addresses', 'describe_addresses', 'Addresses', 'AllocationId')]
    if filters:
        for key, method, field, id_field in specs:
            pages = read('discover:' + key, lambda method=method: (
                list(ec2.get_paginator(method).paginate(Filters=filters)) if ec2.can_paginate(method)
                else [getattr(ec2, method)(Filters=filters)]))
            for page in pages or []:
                rows = page[field]
                if key == 'instances':
                    rows = [i for r in rows for i in r['Instances']]
                for row in rows:
                    ids[key].add(row[id_field])
    # Cycle-tagged network/storage resources outside stack/attachment inventory.
    # These reads never infer absence on denial, and never prune AMI snapshots.
    extras = [('snapshots', 'describe_snapshots', 'Snapshots', 'SnapshotId'),
              ('nat_gateways', 'describe_nat_gateways', 'NatGateways', 'NatGatewayId'),
              ('vpc_endpoints', 'describe_vpc_endpoints', 'VpcEndpoints', 'VpcEndpointId')]
    if filters:
        for key, method, field, id_field in extras:
            pages = read('discover:' + key, lambda method=method: list(ec2.get_paginator(method).paginate(
                **({'Filter': filters} if method == 'describe_nat_gateways' else {'Filters': filters}))))
            result[key] = {r[id_field]: r for page in pages or [] for r in page[field]}
    # Track exact previously seen extra resources even if their tags change.
    for key, method, field, id_field in extras:
        for rid in (previous or {}).get(key, {}):
            argument = {'snapshots': 'SnapshotIds', 'nat_gateways': 'NatGatewayIds', 'vpc_endpoints': 'VpcEndpointIds'}[key]
            code = {'snapshots': 'InvalidSnapshot.NotFound', 'nat_gateways': 'NatGatewayNotFound', 'vpc_endpoints': 'InvalidVpcEndpointId.NotFound'}[key]
            try:
                rows = getattr(ec2, method)(**{argument: [rid]})[field]
                result[key][rid] = rows[0] if len(rows) == 1 else {'ambiguous': rows}
            except Exception as exc:
                if getattr(exc, 'response', {}).get('Error', {}).get('Code') == code:
                    result[key][rid] = {'absent': True, 'checked_at': now()}
                else:
                    result[key][rid] = None
                    result['errors'].append({'operation': key + ':' + rid, 'error': str(exc)})
    for iid in sorted(ids['instances']):
        response = read('instance:' + iid, lambda: ec2.describe_instances(InstanceIds=[iid]))
        rows = [i for r in (response or {}).get('Reservations', []) for i in r['Instances']]
        result['instances'][iid] = rows[0] if len(rows) == 1 else None
        for row in rows:
            ids['volumes'].update(b['Ebs']['VolumeId'] for b in row.get('BlockDeviceMappings', []) if 'Ebs' in b)
            ids['network_interfaces'].update(n['NetworkInterfaceId'] for n in row.get('NetworkInterfaces', []))
            for nic in row.get('NetworkInterfaces', []):
                associated = read('attached-addresses:' + nic['NetworkInterfaceId'], lambda nic=nic: ec2.describe_addresses(
                    Filters=[{'Name': 'network-interface-id', 'Values': [nic['NetworkInterfaceId']]}]))
                ids['addresses'].update(a['AllocationId'] for a in (associated or {}).get('Addresses', []) if a.get('AllocationId'))
            result.setdefault('instance_status', {})[iid] = read('status:' + iid, lambda: ec2.describe_instance_status(InstanceIds=[iid], IncludeAllInstances=True))
        console = read('console:' + iid, lambda: ec2.get_console_output(InstanceId=iid, Latest=True))
        if console:
            try:
                console['decoded'] = base64.b64decode(console.get('Output', ''), validate=True).decode('utf-8', errors='replace')
            except (ValueError, UnicodeError):
                result['errors'].append({'operation': 'console-decode:' + iid, 'error': 'Invalid base64'})
            result['console'][iid] = console
        result['metrics'][iid] = {}
        for metric, stat in METRICS.items():
            data = read('metric:' + iid + ':' + metric, lambda: cw.get_metric_statistics(
                Namespace='AWS/EC2', MetricName=metric, Dimensions=[{'Name': 'InstanceId', 'Value': iid}],
                StartTime=stamp(scope['started_at']), EndTime=datetime.now(timezone.utc), Period=300, Statistics=[stat]))
            result['metrics'][iid][metric] = data
    # Absence is accepted only for the exact EC2 not-found code, never access denied.
    for key, method, field, id_field in specs[1:]:
        argument = {'volumes': 'VolumeIds', 'network_interfaces': 'NetworkInterfaceIds', 'addresses': 'AllocationIds'}[key]
        absent = {'volumes': 'InvalidVolume.NotFound', 'network_interfaces': 'InvalidNetworkInterfaceID.NotFound', 'addresses': 'InvalidAllocationID.NotFound'}[key]
        for rid in sorted(ids[key]):
            try:
                rows = getattr(ec2, method)(**{argument: [rid]})[field]
                result[key][rid] = rows[0] if len(rows) == 1 else {'ambiguous': rows}
            except Exception as exc:
                if getattr(exc, 'response', {}).get('Error', {}).get('Code') == absent:
                    result[key][rid] = {'absent': True, 'checked_at': now()}
                else:
                    result[key][rid] = None
                    result['errors'].append({'operation': key + ':' + rid, 'error': str(exc)})
    for name in sorted(alarm_names):
        data = read('alarm:' + name, lambda: cw.describe_alarms(AlarmNames=[name]))
        result['alarms'][name] = data
    if settled and scope.get('table'):
        final = []
        for table in set([scope['table'], scope['generation_table']]):
            pages = read('final-state:' + table, lambda: list(session.client('dynamodb').get_paginator('scan').paginate(
                TableName=table, ConsistentRead=True)))
            final.extend(item for page in pages or [] for item in page['Items'])
        canonical = lambda rows: sorted(json.dumps(row, sort_keys=True) for row in rows)
        if canonical(final) != canonical(result['state']):
            result['errors'].append({'operation': 'state-fence', 'error': 'State changed during cleanup verification; refresh after reconciliation'})
        result['final_state'] = final
    result['finished_at'] = now()
    return result


def evaluate(before, after):
    gaps = []
    scope = after['scope']
    # Review timestamp is intentionally refreshed, but resource/task identity is immutable.
    stable = lambda s: {k: v for k, v in s.items() if k != 'ownership_reviewed_at'}
    if stable(before['scope']) != stable(scope):
        gaps.append('Scope changed since inventory')
    if before.get('errors') or after.get('errors'):
        gaps.append('AWS/evidence reads failed; inspect errors')
    if scope['kind'] == 'propagation':
        keys = [(r.get('PK', {}).get('S'), r.get('SK', {}).get('S')) for r in after.get('state', [])]
        if len(keys) != len(set(keys)):
            gaps.append('Ambiguous duplicate state keys across tables')
    if not before.get('instances'):
        gaps.append('No pre-cleanup instances captured')
    if before.get('unsupported_resources') or after.get('unsupported_resources'):
        gaps.append('Unsupported stack resource types require separate verification')
    for key in ('instances', 'volumes', 'network_interfaces', 'addresses', 'snapshots', 'nat_gateways', 'vpc_endpoints', 'alarms'):
        if not set(before.get(key, {})) <= set(after.get(key, {})):
            gaps.append('Incomplete exact readback coverage: ' + key)
    for arn in scope['stacks']:
        original = (before.get('stacks', {}).get(arn) or {}).get('Stacks', [])
        if len(original) != 1 or original[0].get('StackId') != arn:
            gaps.append('Pre-cleanup stack identity missing: ' + arn)
        stacks = (after.get('stacks', {}).get(arn) or {}).get('Stacks', [])
        if len(stacks) != 1 or stacks[0].get('StackId') != arn or stacks[0].get('StackStatus') != 'DELETE_COMPLETE':
            gaps.append('Exact stack deletion unverified: ' + arn)
    for iid, instance in after['instances'].items():
        if not instance or instance.get('State', {}).get('Name') != 'terminated':
            gaps.append('Termination unverified: ' + iid)
    for key in ('volumes', 'network_interfaces', 'addresses'):
        for rid, resource in after[key].items():
            if not resource or resource.get('absent') is not True:
                gaps.append('Removal unverified: ' + rid)
    for key in ('snapshots', 'nat_gateways', 'vpc_endpoints'):
        for rid, resource in after.get(key, {}).items():
            if not resource or (resource.get('State') != 'deleted' and resource.get('absent') is not True):
                gaps.append('Tagged trial residue: ' + rid)
    for name, data in after.get('alarms', {}).items():
        if data is None or data.get('MetricAlarms') or data.get('CompositeAlarms'):
            gaps.append('Alarm removal unverified: ' + name)
    for iid in before['instances']:
        for metric in METRICS:
            data = before.get('metrics', {}).get(iid, {}).get(metric)
            if not data or not data.get('Datapoints'):
                gaps.append('Missing metric samples: ' + iid + ':' + metric)
        if scope['kind'] == 'smoke':
            text = before.get('console', {}).get(iid, {}).get('decoded', '')
            results = []
            for line in text.splitlines():
                try:
                    record = json.loads(line)
                    if isinstance(record, dict): results.append(record)
                except ValueError:
                    pass
            if not any(r.get('result') == 'CLOUD_GLIDER_AMI_SMOKE_PASS' and
                       r.get('daemon_sha256') == scope.get('daemon_sha256') for r in results) or 'CLOUD_GLIDER_AMI_SMOKE_FAIL' in text:
                gaps.append('Explicit smoke PASS without FAIL missing: ' + iid)
    if scope['kind'] == 'propagation':
        items = {(r.get('PK', {}).get('S'), r.get('SK', {}).get('S')): r for r in after['state']}
        lifecycle = items.get(('BOOTSTRAP', 'REQUEST'), {})
        control = items.get(('CONTROL', 'GLOBAL'), {})
        current = items.get(('CURRENT', 'GLOBAL'), {})
        required = [(control, 'propagation_enabled', {'BOOL': False}),
                    (lifecycle, 'propagation_enabled', {'BOOL': False}),
                    (lifecycle, 'cleanup_status', {'S': 'COMPLETE'}),
                    (lifecycle, 'status', {'S': 'READY'}),
                    (lifecycle, 'cleanup_requested', {'BOOL': False}),
                    (current, 'status', {'S': 'UNINITIALIZED'}),
                    (lifecycle, 'cleanup_target_request_id', {'S': scope['trial']})]
        if any(row.get(k) != value for row, k, value in required):
            gaps.append('Lifecycle/CONTROL/CURRENT completion or exact cleanup target unverified')
        if any(pk == 'LOCK' or (pk or '').startswith('GEN#') for pk, sk in items):
            gaps.append('Generation records or locks remain; reconcile through supported lifecycle')
        if any(control.get(k) != {'BOOL': False} for k in ('start_requested', 'stop_requested', 'cleanup_requested')):
            gaps.append('Pending or missing control requests')
        if lifecycle.get('bootstrap_requested') != {'BOOL': False} or lifecycle.get('stack_id'):
            gaps.append('Bootstrap request or seed stack reference pending')
        if any(field in current for field in ('generation', 'stack_id', 'instance_id', 'request_id')):
            gaps.append('CURRENT retains ownership fields after cleanup')
        if control.get('active_command') != {'S': 'NONE'} or control.get('operation_status') != {'S': 'COMPLETE'}:
            gaps.append('Control command reconciliation incomplete')
        if lifecycle.get('request_id') != {'S': str(int(scope['trial']) + 1)}:
            gaps.append('Unexpected lifecycle cycle after cleanup')
        evidence = before.get('evidence', {}).get('bundle', {})
        if not validate_evidence(evidence, scope, before)['validated']:
            gaps.append('Propagation timing/health/identity/failure evidence unvalidated')
    if (stamp(after['started_at']) - stamp(before['finished_at'])).total_seconds() > 86400:
        gaps.append('Inventory older than 24 hours; recapture before cleanup')
    for iid, instance in before['instances'].items():
        if not instance or instance.get('InstanceId') != iid:
            gaps.append('Pre-cleanup instance identity missing: ' + iid)
        elif scope['kind'] == 'smoke' and instance.get('ImageId') != scope.get('approved_image_id'):
            gaps.append('Smoke image identity mismatch: ' + iid)
        elif scope['kind'] == 'propagation':
            tags = {t['Key']: t['Value'] for t in instance.get('Tags', [])}
            template = instance.get('LaunchTemplate') or {
                'LaunchTemplateId': tags.get('aws:ec2launchtemplate:id'),
                'Version': tags.get('aws:ec2launchtemplate:version')}
            if (tags.get('bootstrap-request-id') != scope['trial'] or
                    template.get('LaunchTemplateId') != scope.get('launch_template_id') or
                    str(template.get('Version')) != str(scope.get('launch_template_version'))):
                gaps.append('Cycle/template identity mismatch: ' + iid)
    if scope.get('retained_exceptions'):
        gaps.append('Named retained exceptions require operator review; no full cleanup claim')
    if (stamp(after['started_at']) - stamp(before['finished_at'])).total_seconds() < 0:
        gaps.append('Verification precedes inventory')
    return {'status': 'INCOMPLETE' if gaps else 'VERIFIED', 'gaps': gaps,
            'retained_control_plane': scope['retained_control_plane'],
            'coverage': scope['coverage'], 'exceptions': scope.get('retained_exceptions', []),
            'trial_outcome': 'REVIEW_REQUIRED' if scope['kind'] == 'propagation' else ('PASS' if not gaps else 'UNVALIDATED'),
            'note': 'Cleanup verification covers declared scope only; operator observations do not establish a healthy trial. No deletion performed.'}


def preflight(capture):
    gaps = []
    if capture['errors']:
        gaps.append('Inventory reads failed')
    if any(not r or r.get('State', {}).get('Name') != 'terminated' for r in capture['instances'].values()):
        gaps.append('Existing trial instances; inspect before starting')
    for key in ('volumes', 'network_interfaces', 'addresses', 'snapshots', 'nat_gateways', 'vpc_endpoints'):
        if any(not r or (r.get('absent') is not True and r.get('State') != 'deleted') for r in capture[key].values()):
            gaps.append('Existing trial residue: ' + key)
    if any(data is None or data.get('MetricAlarms') or data.get('CompositeAlarms') for data in capture.get('alarms', {}).values()):
        gaps.append('Existing trial alarms')
    if capture['scope']['kind'] == 'propagation':
        items = {(r.get('PK', {}).get('S'), r.get('SK', {}).get('S')): r for r in capture['state']}
        control = items.get(('CONTROL', 'GLOBAL'), {})
        lifecycle = items.get(('BOOTSTRAP', 'REQUEST'), {})
        if control.get('propagation_enabled') != {'BOOL': False} or lifecycle.get('propagation_enabled') != {'BOOL': False}:
            gaps.append('Propagation enabled or unknown')
        if lifecycle.get('request_id') != {'S': capture['scope']['trial']} or lifecycle.get('status') != {'S': 'READY'}:
            gaps.append('Cycle identity or readiness mismatch')
        if lifecycle.get('cleanup_status', {}).get('S') not in ('IDLE', 'COMPLETE'):
            gaps.append('Cleanup unresolved')
        if any(pk == 'LOCK' or (pk or '').startswith('GEN#') or pk == 'HOLD' for pk, sk in items):
            gaps.append('Existing generation, lock or HOLD requires inspection')
        if control.get('active_command', {}).get('S') != 'NONE':
            gaps.append('Another control operation is active')
        if items.get(('CURRENT', 'GLOBAL'), {}).get('status') != {'S': 'UNINITIALIZED'}:
            gaps.append('CURRENT not reconciled')
        if any(control.get(k) != {'BOOL': False} for k in ('start_requested', 'stop_requested', 'cleanup_requested')):
            gaps.append('Pending control command')
        if lifecycle.get('bootstrap_requested') != {'BOOL': False} or lifecycle.get('cleanup_requested') != {'BOOL': False}:
            gaps.append('Pending lifecycle request')
    return {'status': 'INCOMPLETE' if gaps else 'VERIFIED', 'gaps': gaps,
            'note': 'Read-only preflight is not authorization to start a trial.'}


def validate_evidence(bundle, scope, capture):
    """Validate exact timing producer coverage; preserve health/failure observations.

    Evidence bundle contains timing_records, expected identities and observations.
    Observations are operator review, not a substitute for authoritative gates.
    """
    from verify_timing_collection import validate
    outcome = {'sha256': hashlib.sha256(json.dumps(bundle, sort_keys=True, default=str).encode()).hexdigest(), 'bundle': bundle,
               'validated': False, 'gaps': []}
    try:
        expected = bundle['expected']
        if {e['instance_id'] for e in expected} != set(capture['instances']):
            raise ValueError('Evidence does not cover exact inventoried instances')
        if any(e['request_id'] != scope['trial'] or e['source_commit'] != scope.get('source_commit') or
               e['daemon_sha256'] != scope.get('daemon_sha256') for e in expected):
            raise ValueError('Evidence belongs to another cycle')
        outcome['timing'] = validate(bundle['timing_records'], expected=expected)
        for category in ('health', 'identity', 'failure'):
            observations = bundle['observations'][category]
            if not observations or {o['instance_id'] for o in observations} != set(capture['instances']):
                raise ValueError('Missing observation coverage: ' + category)
            if any(not o.get('details') for o in observations):
                raise ValueError('Missing raw observation details: ' + category)
            for observation in observations:
                if not stamp(scope['started_at']) <= stamp(observation['observed_at']) <= datetime.now(timezone.utc):
                    raise ValueError('Observation outside trial window')
            if any(o.get('request_id') != scope['trial'] or not o.get('source') or
                   not o.get('observed_at') or o.get('reviewed_by') != scope['task'] for o in observations):
                raise ValueError('Uncorrelated operator observations: ' + category)
        outcome['validated'] = True
    except (ValueError, KeyError, TypeError) as exc:
        outcome['gaps'].append(str(exc))
    return outcome


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('action', choices=['preflight', 'capture', 'verify'])
    p.add_argument('--scope', required=True, help='Private operator-reviewed JSON scope')
    p.add_argument('--before', help='Pre-cleanup capture JSON, required for verify or incremental capture')
    p.add_argument('--output', required=True, help='New file under .artifacts; never overwritten')
    p.add_argument('--profile', required=True)
    p.add_argument('--evidence', help='Private propagation evidence bundle JSON')
    p.add_argument('--timing-log-group', help='Retrieve timing records using bundle expected identities')
    args = p.parse_args(argv)
    scope = json.loads(Path(args.scope).read_text())
    validate_scope(scope)
    before = json.loads(Path(args.before).read_text()) if args.before else None
    if args.action != 'preflight' and scope['kind'] == 'propagation' and not scope['stacks']:
        p.error('Propagation capture/verify requires exact seed/generation stack ARNs')
    if args.action == 'verify' and before is None:
        p.error('verify requires --before; STOP or post-cleanup inventory alone is insufficient')
    if before and before.get('scope') and {k:v for k,v in before['scope'].items() if k != 'ownership_reviewed_at'} != {k:v for k,v in scope.items() if k != 'ownership_reviewed_at'}:
        raise ValueError('Capture scope changed; preserve separate inventories for separate trials')
    if before and before.get('schema') != 1:
        raise ValueError('Unsupported capture schema')
    import boto3
    session = boto3.Session(profile_name=args.profile, region_name=scope['region'])
    capture = collect(session, scope, before, settled=args.action == 'verify')
    if args.evidence:
        bundle = json.loads(Path(args.evidence).read_text())
        if args.timing_log_group:
            from verify_timing_collection import retrieve
            try:
                bundle['timing_records'] = retrieve(session.client('logs'), args.timing_log_group, bundle['expected'])
                bundle['timing_log_group'] = args.timing_log_group
            except Exception as exc:
                capture['errors'].append({'operation': 'timing-retrieval', 'error': str(exc)})
                bundle['timing_records'] = []
        capture['evidence'] = validate_evidence(bundle, scope, capture)
    elif args.timing_log_group:
        p.error('--timing-log-group requires --evidence with expected identities')
    if args.action == 'preflight':
        capture['preflight'] = preflight(capture)
    output = {'capture': capture, 'captured_at': now()} if args.action == 'verify' else capture
    if args.action == 'verify':
        output['completion'] = evaluate(before, capture)
        output['inventory_sha256'] = hashlib.sha256(Path(args.before).read_bytes()).hexdigest()
    private_save(args.output, output)
    status = output.get('completion', output.get('preflight', {})).get('status', 'CAPTURED')
    if status == 'CAPTURED' and (capture['errors'] or (args.evidence and not capture['evidence']['validated'])):
        status = 'INCOMPLETE'
    print(json.dumps({'status': status, 'output': args.output, 'read_errors': len(capture['errors'])}))
    return 2 if status == 'INCOMPLETE' else 0


if __name__ == '__main__':
    raise SystemExit(main())
