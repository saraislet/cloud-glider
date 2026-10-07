#!/usr/bin/env python3
"""Read-only release completion gate. Never starts propagation."""
import argparse
import base64
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.request_bootstrap import fingerprint
from scripts.validate_baked_ami import validate as validate_image


def require(condition, message):
    if not condition:
        raise ValueError(message)


def verify_pins(control, request, current, hold, launch, config, mapping, image_id, digest, commit):
    require(not hold, 'Emergency hold remains active')
    require(current.get('status') == {'S': 'UNINITIALIZED'}, 'CURRENT is not idle')
    require(control.get('active_command') == {'S': 'NONE'}, 'Operator command is active')
    require(all(control.get(k) == {'BOOL': False} for k in
                ('start_requested', 'stop_requested', 'cleanup_requested')), 'Operator request is pending')
    require(request.get('status') == {'S': 'READY'} and
            request.get('cleanup_status') == {'S': 'COMPLETE'}, 'Lifecycle is not cleaned and ready')
    require(all(request.get(k) == {'BOOL': False} for k in
                ('bootstrap_requested', 'propagation_enabled', 'cleanup_requested')), 'Lifecycle is active')
    require(request.get('control_sha256') == {'S': fingerprint(control)}, 'Bootstrap fingerprint differs')
    require(mapping.get('State') == 'Enabled' and mapping.get('LastProcessingResult') == 'OK',
            'Bootstrap stream listener is not healthy and enabled')
    require(launch['ImageId'] == image_id, 'Launch template AMI differs')
    for field, expected in [('daemon_artifact_sha256', digest), ('template_build_id', commit)]:
        require(control.get(field) == {'S': expected} and config.get(field) == expected,
                field + ' differs between approved release and deployed pins')
    for field in ('daemon_artifact_bucket', 'daemon_artifact_key', 'daemon_artifact_version_id',
                  'template_s3_bucket', 'template_s3_key', 'template_s3_version_id', 'template_sha256'):
        config_field = {'template_s3_bucket': 'template_bucket', 'template_s3_key': 'template_key'}.get(field, field)
        require(control.get(field, {}).get('S') and config.get(config_field) == control[field]['S'], field + ' differs')
    actual = hashlib.sha256(json.dumps(launch, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    require(control.get('launch_template_sha256') == {'S': actual}, 'Launch template digest differs')


def smoke_result(text, digest):
    require('CLOUD_GLIDER_AMI_SMOKE_FAIL' not in text, 'Guest smoke failed')
    for line in text.splitlines():
        try:
            result = json.loads(line[line.index('{'):])
        except (ValueError, json.JSONDecodeError):
            continue
        if result.get('result') == 'CLOUD_GLIDER_AMI_SMOKE_PASS':
            require(result.get('daemon_sha256') == digest, 'Smoke digest differs')
            require(result.get('ec2_contract') == 'EC2_BAKED_CONTRACT_PASS' and
                    result.get('free_bytes', 0) >= 384 * 1024 * 1024, 'Smoke contract/headroom failed')
            return result
    raise ValueError('Explicit guest smoke result is missing')


def main():
    import boto3
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--profile', required=True)
    p.add_argument('--listener-profile', help='Optional read identity for Lambda listener metadata')
    p.add_argument('--image-id', required=True)
    p.add_argument('--daemon-sha256', required=True)
    p.add_argument('--source-commit', required=True)
    p.add_argument('--smoke-stack-id', required=True)
    p.add_argument('--smoke-console', type=Path, required=True)
    p.add_argument('--receipt', type=Path, required=True)
    a = p.parse_args()
    s = boto3.Session(profile_name=a.profile, region_name='us-west-2')
    identity = s.client('sts').get_caller_identity()
    require(identity['Account'] == '482268323955', 'Wrong sandbox account')
    c, d, e = s.client('cloudformation'), s.client('dynamodb'), s.client('ec2')
    stacks = {}
    for suffix in ('', '-boundaries', '-bootstrap', '-launch-template'):
        stack = c.describe_stacks(StackName='cloud-glider-sandbox' + suffix)['Stacks'][0]
        require(stack['StackStatus'] == 'UPDATE_COMPLETE', 'Deployment stack not complete: ' + suffix)
        params = {x['ParameterKey']: x['ParameterValue'] for x in stack['Parameters']}
        image_field = 'ApprovedImageId' if suffix == '-launch-template' else 'AllowedImageId'
        require(params.get(image_field) == a.image_id, 'Stack image pin differs: ' + suffix)
        stacks[suffix] = stack
    def get(pk, sk='GLOBAL'):
        return d.get_item(TableName='cloud-glider-sandbox-state',
                          Key={'PK': {'S': pk}, 'SK': {'S': sk}}, ConsistentRead=True).get('Item', {})
    control, request = get('CONTROL'), get('BOOTSTRAP', 'REQUEST')
    current, hold = get('CURRENT'), get('HOLD', 'ACTIVE')
    out = {x['OutputKey']: x['OutputValue'] for x in stacks['-launch-template']['Outputs']}
    require(control['launch_template_id']['S'] == out['LaunchTemplateId'] and
            control['launch_template_version']['S'] == out['LaunchTemplateVersion'], 'Numeric template pin differs')
    launch = e.describe_launch_template_versions(LaunchTemplateId=out['LaunchTemplateId'],
              Versions=[out['LaunchTemplateVersion']])['LaunchTemplateVersions'][0]['LaunchTemplateData']
    config = json.loads(base64.b64decode(launch['UserData']).decode().split("<<'JSON'\n", 1)[1].split('\nJSON', 1)[0])
    listener = boto3.Session(profile_name=a.listener_profile or a.profile, region_name='us-west-2')
    require(listener.client('sts').get_caller_identity()['Account'] == identity['Account'], 'Wrong listener account')
    outputs = {x['OutputKey']: x['OutputValue'] for x in stacks['-bootstrap']['Outputs']}
    mapping = listener.client('lambda').get_event_source_mapping(UUID=outputs['BootstrapTriggerId'])
    verify_pins(control, request, current, hold, launch, config, mapping,
                a.image_id, a.daemon_sha256, a.source_commit)
    # Pagination matters: residue on later pages also blocks completion.
    require(not any(page.get('Items') for page in d.get_paginator('scan').paginate(
        TableName='cloud-glider-sandbox-generations', ConsistentRead=True)), 'Generation records remain')
    filters = [{'Name': 'tag:project', 'Values': ['cloud-glider']},
               {'Name': 'instance-state-name', 'Values': ['pending', 'running', 'stopped', 'stopping', 'shutting-down']}]
    require(not any(page['Reservations'] for page in e.get_paginator('describe_instances').paginate(
        Filters=filters)), 'Live sandbox instances remain')
    image = e.describe_images(ImageIds=[a.image_id])['Images'][0]
    snapshots = e.describe_snapshots(SnapshotIds=[image['BlockDeviceMappings'][0]['Ebs']['SnapshotId']])['Snapshots']
    metadata = validate_image(image, snapshots, identity['Account'], a.daemon_sha256)
    smoke = c.describe_stacks(StackName=a.smoke_stack_id)['Stacks'][0]
    require(smoke['StackStatus'] == 'DELETE_COMPLETE', 'Smoke stack cleanup incomplete')
    require({x['ParameterKey']: x['ParameterValue'] for x in smoke['Parameters']}.get('CandidateImageId') == a.image_id,
            'Smoke evidence belongs to another AMI')
    result = smoke_result(a.smoke_console.read_text(), a.daemon_sha256)
    # Re-read mutable controls after collection; never publish stale idle proof.
    require(control == get('CONTROL') and request == get('BOOTSTRAP', 'REQUEST') and
            current == get('CURRENT') and hold == get('HOLD', 'ACTIVE'), 'State changed during validation')
    receipt = {'status': 'SANDBOX_PROPAGATION_READY', 'verified_at': datetime.now(timezone.utc).isoformat(),
               'identity': identity['Arn'], 'image_id': a.image_id, 'source_commit': a.source_commit,
               'daemon_sha256': a.daemon_sha256, 'launch_template_version': out['LaunchTemplateVersion'],
               'max_generation': int(control['max_generation']['N']), 'metadata': metadata,
               'smoke': result, 'propagation_started': False}
    a.receipt.parent.mkdir(parents=True, exist_ok=True)
    a.receipt.write_text(json.dumps(receipt, indent=2) + '\n')
    a.receipt.chmod(0o600)
    print(json.dumps(receipt, indent=2))


if __name__ == '__main__':
    main()
