"""Operator bootstrap and resumable chain cleanup. Embedded in cfn/bootstrap.yaml by the renderer."""
import hashlib
import json
import os
import re
import time
from datetime import datetime, timezone
from urllib.parse import quote



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

def fingerprint(item):
    item = {k: v for k, v in item.items() if k not in OPERATOR_FIELDS | {'propagation_enabled', 'updated_at', 'updated_by'}}
    return hashlib.sha256(json.dumps(item, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def key(pk, sk='GLOBAL'):
    return {'PK': {'S': pk}, 'SK': {'S': sk}}


def snapshot(ddb, table):
    keys = [key('CONTROL'), key('CURRENT'), key('HOLD', 'ACTIVE'), key('BOOTSTRAP', 'REQUEST')]
    result = ddb.transact_get_items(TransactItems=[{'Get': {'TableName': table, 'Key': k}} for k in keys])
    return [entry.get('Item', {}) for entry in result['Responses']]


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def parameters(control, env, request_id):
    result = json.loads(env['GENERATION_PARAMETERS'])
    fields = {
        'BootstrapVersion': 'desired_bootstrap_version', 'TemplateVersion': 'desired_template_version',
        'TemplateBucket': 'template_s3_bucket', 'TemplateKey': 'template_s3_key',
        'TemplateS3VersionId': 'template_s3_version_id', 'TemplateSha256': 'template_sha256',
        'TemplateBuildId': 'template_build_id', 'AgentArtifactBucket': 'agent_artifact_bucket',
        'AgentArtifactKey': 'agent_artifact_key', 'AgentArtifactVersionId': 'agent_artifact_version_id',
        'AgentArtifactSha256': 'agent_artifact_sha256',
    }
    result.update({target: control[source]['S'] for target, source in fields.items()})
    if control.get('propagation_backend') == {'S': 'ec2'}:
        result['LaunchTemplateId'] = control['launch_template_id']['S']
        result['LaunchTemplateVersion'] = control['launch_template_version']['S']
    result['GenerationTableName'] = env['GENERATION_TABLE']
    result.update(Generation='000000', PredecessorStackId='NONE', HandoffToken='OPERATOR_BOOTSTRAP', RequestId=request_id)
    return result


def describe(cfn, name):
    try:
        return cfn.describe_stacks(StackName=name)['Stacks'][0]
    except Exception as exc:
        error = getattr(exc, 'response', {}).get('Error', {})
        if error.get('Code') == 'ValidationError' and 'does not exist' in error.get('Message', ''):
            return None
        raise


def verify_existing(stack, params, env, request_id):
    require(stack['StackStatus'] in ('CREATE_IN_PROGRESS', 'CREATE_COMPLETE'), 'Bootstrap stack failed; inspect manually')
    require(stack.get('RoleARN') == env['SERVICE_ROLE_ARN'], 'Existing stack role mismatch')
    require({p['ParameterKey']: p['ParameterValue'] for p in stack['Parameters']} == params,
            'Existing stack parameters mismatch')
    tags = {t['Key']: t['Value'] for t in stack.get('Tags', [])}
    require(tags.get('bootstrap-request-id') == request_id, 'Existing stack belongs to another request')


def guard(control, current, hold, request, event_request, env, status):
    require(request.get('request_id') == event_request.get('request_id'), 'Request identity changed')
    require(request.get('status') == {'S': status}, 'Request canceled or already handled')
    require('propagation_enabled' not in control, 'Legacy CONTROL requires migration')
    require(control.get('generation_table_name') == {'S': env['GENERATION_TABLE']}, 'Generation table requires migration')
    require(control.get('cleanup_requested') != {'BOOL': True}, 'A cleanup command blocks bootstrap')
    require(request.get('schema_version') == {'S': '2'}, 'Lifecycle schema requires migration')
    require(type(request.get('propagation_enabled', {}).get('BOOL')) is bool, 'Invalid propagation control')
    require(request.get('cleanup_requested') == {'BOOL': False}
            and request.get('cleanup_status', {}).get('S') in ('IDLE', 'COMPLETE'), 'Cleanup blocks bootstrap')
    require(not hold, 'Emergency hold prevents bootstrap')
    require(current.get('status') == {'S': 'UNINITIALIZED'}
            and not any(field in current for field in ('generation', 'stack_id', 'instance_id')),
            'CURRENT is not uninitialized')
    require(fingerprint(control) == request['control_sha256']['S'], 'Approved control changed; inspect manually')
    require(request.get('bootstrap_requested') == {'BOOL': True}, 'Bootstrap request is disabled')
    require(control.get('environment') == {'S': env['ENVIRONMENT']}, 'Environment mismatch')
    require(control.get('approved_region') == {'S': 'us-west-2'}, 'Region mismatch')
    require(control.get('approved_architecture') == {'S': 'arm64'}, 'Architecture mismatch')
    require(control.get('approved_instance_types') == {'L': [{'S': 't4g.micro'}]}, 'Instance type mismatch')
    require(control.get('max_live_generations') == {'N': '3'}, 'Live generation limit mismatch')
    require(int(control['max_generation']['N']) >= 0, 'Invalid generation limit')


def verify_artifacts(s3, control, env):
    for fields in [('template_s3_bucket', 'template_s3_key', 'template_s3_version_id', 'template_sha256'),
                   ('agent_artifact_bucket', 'agent_artifact_key', 'agent_artifact_version_id', 'agent_artifact_sha256')]:
        bucket, path, version, digest = [control[field]['S'] for field in fields]
        require(bucket == env['ARTIFACT_BUCKET'] and path.startswith('generation/'), 'Artifact location not approved')
        require(version and version != 'null' and re.fullmatch('[0-9a-f]{64}', digest), 'Invalid artifact identity')
        obj = s3.get_object(Bucket=bucket, Key=path, VersionId=version)
        require(obj.get('VersionId') == version, 'Artifact version mismatch')
        require(hashlib.sha256(obj['Body'].read()).hexdigest() == digest, 'Artifact digest mismatch')


def verify_ec2_template(ec2, control, params):
    require(control.get('approved_account_id') == {'S': params['OperationalAlertsTopicArn'].split(':')[4]}, 'Approved account mismatch')
    template_id = control['launch_template_id']['S']
    version = control['launch_template_version']['S']
    require(re.fullmatch('lt-[0-9a-f]{17}', template_id) and re.fullmatch('[1-9][0-9]*', version), 'Invalid launch template pin')
    require(control.get('concurrency_model') == {'S': 'EC2_DRY_RUN_THEN_RETIRE'}, 'EC2 continuation model mismatch')
    versions = ec2.describe_launch_template_versions(LaunchTemplateId=template_id, Versions=[version])['LaunchTemplateVersions']
    require(len(versions) == 1 and versions[0]['LaunchTemplateId'] == template_id
            and str(versions[0]['VersionNumber']) == version, 'Launch template identity mismatch')
    data = versions[0]['LaunchTemplateData']
    digest = hashlib.sha256(json.dumps(data, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    require(control.get('launch_template_sha256') == {'S': digest}, 'Launch template digest mismatch')
    require(data.get('ImageId') == params['ApprovedImageId'] and data.get('InstanceType') == 't4g.micro'
        and data.get('IamInstanceProfile') == {'Name': params['AgentInstanceProfileName']}
        and data.get('MetadataOptions') == {'HttpEndpoint': 'enabled', 'HttpTokens': 'required',
            'HttpPutResponseHopLimit': 1, 'InstanceMetadataTags': 'enabled'}, 'Launch template configuration mismatch')
    networks = data.get('NetworkInterfaces', [])
    require(len(networks) == 1 and networks[0].get('SubnetId') == params['SubnetId']
        and networks[0].get('Groups') == [params['SecurityGroupId']]
        and networks[0].get('AssociatePublicIpAddress') is True
        and networks[0].get('DeleteOnTermination') is True, 'Launch template network mismatch')
    # Static bootstrap pins must agree with the versioned seed and artifact.
    import base64
    userdata = base64.b64decode(data['UserData']).decode()
    raw = json.loads(userdata.split("<<'JSON'", 1)[1].split('JSON', 1)[0])
    require(raw.get('propagation_backend') == 'ec2' and raw.get('generation_table_name') == params['GenerationTableName'], 'Launch template backend mismatch')
    for field, param in {'template_sha256': 'TemplateSha256', 'template_s3_version_id': 'TemplateS3VersionId',
            'agent_artifact_sha256': 'AgentArtifactSha256', 'agent_artifact_version_id': 'AgentArtifactVersionId',
            'bootstrap_version': 'BootstrapVersion', 'template_version': 'TemplateVersion'}.items():
        require(raw.get(field) == params[param], 'Launch template artifact mismatch: ' + field)


def finish(ddb, table, request_id, stack_id):
    ddb.update_item(TableName=table, Key=key('BOOTSTRAP', 'REQUEST'),
        UpdateExpression='SET #s = :submitted, stack_id = :stack',
        ConditionExpression='request_id = :id AND #s = :creating',
        ExpressionAttributeNames={'#s': 'status'},
        ExpressionAttributeValues={':id': {'S': request_id}, ':creating': {'S': 'CREATING'},
            ':submitted': {'S': 'SUBMITTED'}, ':stack': {'S': stack_id}})
    print(json.dumps({'action': 'BOOTSTRAP_SUBMITTED', 'request_id': request_id, 'stack_id': stack_id}))


def record_bootstrap_submission(ddb, table, request_id, stack_id):
    ddb.put_item(TableName=table, Item={**key('GEN#000000', 'RESOURCE#' + stack_id),
        'request_id': {'S': request_id}, 'stack_id': {'S': stack_id}},
        ConditionExpression='attribute_not_exists(PK) OR request_id = :id',
        ExpressionAttributeValues={':id': {'S': request_id}})


def reconcile_submitted_bootstrap(ddb, cfn, table, control, request, env, name):
    marker = read_item(ddb, table, 'LOCK', 'PROVISIONING')
    if not marker:
        return
    request_id = request['request_id']['S']
    require(marker.get('request_id') == {'S': request_id}
            and marker.get('token') == {'S': 'bootstrap-' + request_id}
            and marker.get('stack_name') == {'S': name},
            'Submitted bootstrap marker is ambiguous; operator inspection required')
    stack_id = request.get('stack_id', {}).get('S')
    require(stack_id, 'Submitted bootstrap lacks its exact stack ID; inspect manually')
    stack = describe(cfn, stack_id)
    require(stack is not None and stack.get('StackId') == stack_id,
            'Submitted bootstrap stack cannot be verified; inspect manually')
    verify_existing(stack, parameters(control, env, request_id), env, request_id)
    verify_cleanup_stack(stack, env, request_id)
    record_bootstrap_submission(ddb, env['GENERATION_TABLE'], request_id, stack_id)
    release_submission(ddb, table, request_id)


def process(event_request, ddb, cfn, s3, ec2, env):
    table, name = env['STATE_TABLE'], 'cloud-glider-' + env['ENVIRONMENT'] + '-gen-000000'
    control, current, hold, request = snapshot(ddb, table)
    require(request.get('request_id') == event_request.get('request_id'), 'Stale bootstrap event')
    status = request.get('status', {}).get('S')
    if status == 'SUBMITTED':
        reconcile_submitted_bootstrap(ddb, cfn, table, control, request, env, name)
        return
    if status == 'CANCELLED':
        return
    request_id = request['request_id']['S']
    params = parameters(control, env, request_id)
    require(re.fullmatch('[1-9][0-9]{0,17}', request_id), 'Invalid request id')
    existing = describe(cfn, name)
    if status == 'CREATING':
        # Never resubmit a claimed request, even if the stack is absent: an API call may still be in flight.
        require(existing is not None, 'Ambiguous bootstrap submission; operator inspection required')
        verify_existing(existing, params, env, request_id)
        record_bootstrap_submission(ddb, env['GENERATION_TABLE'], request_id, existing['StackId'])
        finish(ddb, table, request_id, existing['StackId'])
        release_submission(ddb, table, request_id)
        return
    guard(control, current, hold, request, event_request, env, 'READY')
    require(existing is None, 'Existing bootstrap stack requires operator inspection')
    for page in ec2.get_paginator('describe_instances').paginate(Filters=[
        {'Name': 'tag:project', 'Values': ['cloud-glider']},
        {'Name': 'tag:environment', 'Values': [env['ENVIRONMENT']]},
        {'Name': 'instance-state-name', 'Values': ['pending', 'running', 'stopping', 'stopped', 'shutting-down']} ]):
        require(not any(r['Instances'] for r in page['Reservations']), 'An existing generation prevents bootstrap')
    image = ec2.describe_images(ImageIds=[params['ApprovedImageId']])['Images']
    require(len(image) == 1 and image[0]['Architecture'] == 'arm64' and image[0]['State'] == 'available'
            and image[0]['RootDeviceType'] == 'ebs' and image[0]['RootDeviceName'] == params['RootDeviceName'],
            'Approved AMI is not ready or root device differs')
    verify_artifacts(s3, control, env)
    if control.get('propagation_backend') == {'S': 'ec2'}:
        verify_ec2_template(ec2, control, params)
    # A durable claim prevents duplicate execution and automatic rebootstrap after deletion.
    ddb.update_item(TableName=table, Key=key('BOOTSTRAP', 'REQUEST'),
        UpdateExpression='SET #s = :creating',
        ConditionExpression='request_id = :id AND #s = :requested AND control_sha256 = :digest AND bootstrap_requested = :enabled AND cleanup_requested = :no AND cleanup_status IN (:idle, :complete)',
        ExpressionAttributeNames={'#s': 'status'}, ExpressionAttributeValues={
            ':id': {'S': request_id}, ':requested': {'S': 'READY'}, ':enabled': {'BOOL': True}, ':creating': {'S': 'CREATING'},
            ':digest': request['control_sha256'], ':no': {'BOOL': False}, ':idle': {'S': 'IDLE'}, ':complete': {'S': 'COMPLETE'}})
    claim_submission(ddb, table, request_id, name)
    fresh = snapshot(ddb, table)
    guard(*fresh, event_request, env, 'CREATING')
    require(fingerprint(fresh[0]) == fingerprint(control), 'Approved control changed before provisioning')
    url = 'https://' + env['ARTIFACT_BUCKET'] + '.s3.us-west-2.amazonaws.com/'
    url += quote(params['TemplateKey'], safe='/') + '?versionId=' + quote(params['TemplateS3VersionId'], safe='')
    result = cfn.create_stack(StackName=name, TemplateURL=url,
        Parameters=[{'ParameterKey': k, 'ParameterValue': v} for k, v in params.items()],
        RoleARN=env['SERVICE_ROLE_ARN'], ClientRequestToken='bootstrap-' + request_id,
        Tags=[{'Key': k, 'Value': v} for k, v in {
            'project': 'cloud-glider', 'environment': env['ENVIRONMENT'], 'generation': '000000',
            'owner': params['Owner'], 'purpose': 'generation-stack', 'bootstrap-request-id': request_id}.items()],
        OnFailure='DO_NOTHING')
    record_bootstrap_submission(ddb, env['GENERATION_TABLE'], request_id, result['StackId'])
    finish(ddb, table, request_id, result['StackId'])
    release_submission(ddb, table, request_id)


def is_bootstrap_trigger(record):
    change = record.get('dynamodb', {})
    old, new = change.get('OldImage', {}), change.get('NewImage', {})
    return (record.get('eventName') == 'MODIFY'
            and new.get('PK') == {'S': 'BOOTSTRAP'} and new.get('SK') == {'S': 'REQUEST'}
            and old.get('bootstrap_requested') == {'BOOL': False}
            and new.get('bootstrap_requested') == {'BOOL': True}
            and new.get('status') == {'S': 'READY'})



def lifecycle_check(table, request_id):
    return {'ConditionCheck': {'TableName': table, 'Key': key('BOOTSTRAP', 'REQUEST'),
        'ConditionExpression': 'request_id = :id AND cleanup_requested = :no AND cleanup_status IN (:idle, :complete)',
        'ExpressionAttributeValues': {':id': {'S': request_id}, ':no': {'BOOL': False},
            ':idle': {'S': 'IDLE'}, ':complete': {'S': 'COMPLETE'}}}}


def claim_submission(ddb, table, request_id, name):
    ddb.transact_write_items(TransactItems=[lifecycle_check(table, request_id),
        {'ConditionCheck': {'TableName': table, 'Key': key('CONTROL'),
            'ConditionExpression': 'cleanup_requested = :no', 'ExpressionAttributeValues': {':no': {'BOOL': False}}}},
        {'ConditionCheck': {'TableName': table, 'Key': key('HOLD', 'ACTIVE'),
                           'ConditionExpression': 'attribute_not_exists(PK)'}},
        {'Put': {'TableName': table, 'Item': {**key('LOCK', 'PROVISIONING'),
            'request_id': {'S': request_id}, 'token': {'S': 'bootstrap-' + request_id},
            'stack_name': {'S': name}}, 'ConditionExpression': 'attribute_not_exists(PK)'}}])


def release_submission(ddb, table, request_id):
    ddb.delete_item(TableName=table, Key=key('LOCK', 'PROVISIONING'),
        ConditionExpression='request_id = :id AND #token = :token',
        ExpressionAttributeNames={'#token': 'token'},
        ExpressionAttributeValues={':id': {'S': request_id}, ':token': {'S': 'bootstrap-' + request_id}})


def read_item(ddb, table, pk, sk):
    return ddb.get_item(TableName=table, Key=key(pk, sk), ConsistentRead=True).get('Item', {})


def cleanup_update(ddb, table, request_id, assignments, *, expected=None):
    names = {f'#f{i}': field for i, field in enumerate(assignments)}
    values = {f':v{i}': value for i, value in enumerate(assignments.values())}
    values[':id'] = {'S': request_id}
    condition = 'request_id = :id'
    if expected is not None:
        names['#cs'] = 'cleanup_status'
        values[':expected'] = {'S': expected}
        condition += ' AND #cs = :expected'
    ddb.update_item(TableName=table, Key=key('BOOTSTRAP', 'REQUEST'),
        UpdateExpression='SET ' + ', '.join(f'#f{i} = :v{i}' for i in range(len(assignments))),
        ConditionExpression=condition, ExpressionAttributeNames=names, ExpressionAttributeValues=values)


def begin_cleanup(ddb, table, request):
    require(request.get('schema_version') == {'S': '2'}, 'Migrate legacy lifecycle before cleanup')
    request_id = request['request_id']['S']
    require(re.fullmatch('[1-9][0-9]{0,17}', request_id), 'Invalid cleanup cycle')
    # One atomic write closes both launch gates. No state is erased here.
    cleanup_update(ddb, table, request_id, {
        'propagation_enabled': {'BOOL': False}, 'bootstrap_requested': {'BOOL': False},
        'cleanup_requested': {'BOOL': True}, 'cleanup_status': {'S': 'QUIESCING'},
        'cleanup_started_at': {'N': str(int(time.time()))}, 'cleanup_error': {'S': ''},
        'cleanup_target_request_id': {'S': request_id}, 'cleanup_stack_ids': {'L': []},
        'cleanup_volume_ids': {'L': []}, 'cleanup_instance_ids': {'L': []}}, expected=request['cleanup_status']['S'])


def cleanup_guard(ddb, table, request_id):
    request = read_item(ddb, table, 'BOOTSTRAP', 'REQUEST')
    require(request.get('request_id') == {'S': request_id}, 'Stale cleanup invocation')
    require(request.get('cleanup_status', {}).get('S') in ('QUIESCING', 'DELETING', 'VERIFYING'),
            'Cleanup is not active')
    require(request.get('cleanup_requested') == {'BOOL': True}
            and request.get('propagation_enabled') == {'BOOL': False}
            and request.get('bootstrap_requested') == {'BOOL': False}, 'Cleanup gates changed')
    require(not read_item(ddb, table, 'LOCK', 'PROVISIONING'), 'Ambiguous provisioning marker; inspect before cleanup')
    return request


def generation_stacks(cfn, env, request_id):
    prefix = 'cloud-glider-' + env['ENVIRONMENT'] + '-gen-'
    stacks = []
    for page in cfn.get_paginator('list_stacks').paginate():
        for summary in page['StackSummaries']:
            if not summary['StackName'].startswith(prefix) or summary['StackStatus'] == 'DELETE_COMPLETE':
                continue
            stack = describe(cfn, summary['StackId'])
            if not stack or stack['StackStatus'] == 'DELETE_COMPLETE':
                continue
            verify_cleanup_stack(stack, env, request_id)
            stacks.append(stack)
    return stacks


def verify_cleanup_stack(stack, env, request_id):
    prefix = 'cloud-glider-' + env['ENVIRONMENT'] + '-gen-'
    tags = {entry['Key']: entry['Value'] for entry in stack.get('Tags', [])}
    params = {entry['ParameterKey']: entry['ParameterValue'] for entry in stack.get('Parameters', [])}
    require(re.fullmatch(re.escape(prefix) + '[0-9]{6}', stack['StackName']), 'Unexpected generation stack name')
    require(stack.get('RoleARN') == env['SERVICE_ROLE_ARN'], 'Cleanup stack role mismatch')
    require(all(tags.get(k) == v for k, v in {
        'project': 'cloud-glider', 'environment': env['ENVIRONMENT'],
        'purpose': 'generation-stack', 'bootstrap-request-id': request_id}.items()), 'Cleanup stack ownership mismatch')
    require(params.get('RequestId') == request_id and params.get('Environment') == env['ENVIRONMENT'],
            'Cleanup parameter identity mismatch')


def generation_state(ddb, table, request_id):
    result = []
    for page in ddb.get_paginator('scan').paginate(TableName=table, ConsistentRead=True,
            FilterExpression='begins_with(PK, :gen)',
            ExpressionAttributeValues={':gen': {'S': 'GEN#'}}):
        for item in page.get('Items', []):
            require(item.get('request_id') == {'S': request_id}, 'Generation state belongs to a different cycle')
            result.append(item)
    return result


def inspect_instances(ec2, env):
    filters = [{'Name': 'tag:project', 'Values': ['cloud-glider']},
               {'Name': 'tag:environment', 'Values': [env['ENVIRONMENT']]},
               {'Name': 'instance-state-name', 'Values': ['pending', 'running', 'stopping', 'stopped', 'shutting-down']}]
    return [instance for page in ec2.get_paginator('describe_instances').paginate(Filters=filters)
            for reservation in page['Reservations'] for instance in reservation['Instances']]


def verify_residuals(ec2, env, volume_ids):
    require(not inspect_instances(ec2, env), 'Residual generation instances require inspection')
    filters = [{'Name': 'tag:project', 'Values': ['cloud-glider']},
               {'Name': 'tag:environment', 'Values': [env['ENVIRONMENT']]}]
    volumes = [volume for page in ec2.get_paginator('describe_volumes').paginate(Filters=filters)
               for volume in page['Volumes']]
    require(not volumes, 'Residual tagged EBS volumes require inspection')
    # Implicit root volumes might not inherit tags: inspect the IDs captured before deletion.
    for volume_id in volume_ids:
        try:
            found = ec2.describe_volumes(VolumeIds=[volume_id]).get('Volumes', [])
        except Exception as exc:
            if getattr(exc, 'response', {}).get('Error', {}).get('Code') == 'InvalidVolume.NotFound':
                continue
            raise
        require(not found, 'Residual generation root volume requires inspection')
    require(not ec2.describe_addresses(Filters=filters).get('Addresses'), 'Residual elastic IPs require inspection')
    for page in ec2.get_paginator('describe_snapshots').paginate(Filters=filters, OwnerIds=['self']):
        require(not page['Snapshots'], 'Residual snapshots require inspection')


def exact_condition(item):
    names = {f'#f{i}': field for i, field in enumerate(item)}
    values = {f':v{i}': value for i, value in enumerate(item.values())}
    return {'ConditionExpression': ' AND '.join(f'#f{i} = :v{i}' for i in range(len(item))),
            'ExpressionAttributeNames': names, 'ExpressionAttributeValues': values}


def complete_cleanup(ddb, table, control, current, request, env):
    request_id = request['request_id']['S']
    states = generation_state(ddb, env['GENERATION_TABLE'], request_id)
    # Delete obsolete state conditionally while the durable cleanup gate remains closed.
    for item in states:
        ddb.transact_write_items(TransactItems=[
            {'ConditionCheck': {'TableName': table, 'Key': key('BOOTSTRAP', 'REQUEST'), **exact_condition(request)}},
            {'Delete': {'TableName': env['GENERATION_TABLE'], 'Key': {'PK': item['PK'], 'SK': item['SK']}, **exact_condition(item)}}])
    next_id = str(int(request_id) + 1)
    require(re.fullmatch('[1-9][0-9]{0,17}', next_id), 'Cycle counter exhausted')
    ready = {**request, 'request_id': {'S': next_id}, 'status': {'S': 'READY'},
        'bootstrap_requested': {'BOOL': False}, 'propagation_enabled': {'BOOL': False},
        'cleanup_requested': {'BOOL': False}, 'cleanup_status': {'S': 'COMPLETE'},
        'cleanup_completed_at': {'N': str(int(time.time()))}, 'control_sha256': {'S': fingerprint(control)}}
    for field in ('cleanup_retry_token', 'cleanup_retry_at', 'cleanup_retry_sequence'):
        ready.pop(field, None)
    ready.pop('stack_id', None)
    ready.pop('requested_at', None)
    ready['prepared_at'] = {'N': str(int(time.time()))}
    ddb.transact_write_items(TransactItems=[
        {'ConditionCheck': {'TableName': table, 'Key': key('CONTROL'),
            **exact_condition({k: v for k, v in control.items() if k not in OPERATOR_FIELDS | {'updated_at', 'updated_by'}})}},
        {'ConditionCheck': {'TableName': table, 'Key': key('LOCK', 'PROVISIONING'), 'ConditionExpression': 'attribute_not_exists(PK)'}},
        {'Put': {'TableName': table, 'Item': {**key('CURRENT'), 'status': {'S': 'UNINITIALIZED'},
            'environment': {'S': env['ENVIRONMENT']}}, **exact_condition(current)}},
        {'Delete': {'TableName': table, 'Key': key('LOCK', 'PROPAGATION')}},
        {'Put': {'TableName': table, 'Item': ready, **exact_condition(request)}}])
    print(json.dumps({'action': 'CLEANUP_COMPLETE', 'request_id': request_id, 'next_request_id': next_id}))


def cleanup_step(event_request, ddb, cfn, ec2, env):
    table = env['STATE_TABLE']
    control, current, hold, request = snapshot(ddb, table)
    if request.get('request_id') != event_request.get('request_id'):
        return  # Old stream records cannot operate on a newer chain.
    status = request.get('cleanup_status', {}).get('S')
    if status in ('IDLE', 'COMPLETE'):
        if request.get('cleanup_requested') != {'BOOL': True}:
            return
        begin_cleanup(ddb, table, request)
        request = read_item(ddb, table, 'BOOTSTRAP', 'REQUEST')
    elif status == 'NEEDS_ATTENTION':
        return
    request_id = request['request_id']['S']
    try:
        require('propagation_enabled' not in control, 'Legacy CONTROL requires migration')
        require(control.get('environment') == {'S': env['ENVIRONMENT']}, 'Cleanup environment mismatch')
        require(control.get('generation_table_name') == {'S': env['GENERATION_TABLE']}, 'Generation table requires migration')
        if read_item(ddb, table, 'LOCK', 'PROVISIONING'):
            require(int(time.time()) - int(request['cleanup_started_at']['N']) < 180,
                    'Provisioning marker did not settle; inspect before cleanup')
            return
        require(int(time.time()) - int(request['cleanup_started_at']['N']) < 1800, 'Cleanup exceeded 30 minutes; inspect')
        request = cleanup_guard(ddb, table, request_id)
        stacks = generation_stacks(cfn, env, request_id)
        if any(stack['StackStatus'].endswith('IN_PROGRESS') and stack['StackStatus'] not in
               ('REVIEW_IN_PROGRESS', 'DELETE_IN_PROGRESS') for stack in stacks):
            return  # Scheduled reconciliation waits for active provisioning to settle.
        # Verify the complete inventory before deleting any stack.
        require(current.get('status', {}).get('S') in ('UNINITIALIZED', 'CURRENT'), 'Missing or invalid CURRENT')
        states = generation_state(ddb, env['GENERATION_TABLE'], request_id)
        known_ids = {item['stack_id']['S'] for item in states if 'stack_id' in item}
        known_ids.update(entry['S'] for entry in request.get('cleanup_stack_ids', {}).get('L', []))
        if 'stack_id' in request:
            known_ids.add(request['stack_id']['S'])
        if 'stack_id' in current:
            known_ids.add(current['stack_id']['S'])
        listed_ids = {stack['StackId'] for stack in stacks}
        for stack_id in known_ids - listed_ids:
            stack = describe(cfn, stack_id)
            if stack and stack['StackStatus'] != 'DELETE_COMPLETE':
                # Exact-ID discovery must pass the same ownership checks as list discovery.
                verify_cleanup_stack(stack, env, request_id)
                stacks.append(stack)
        if any(stack['StackStatus'].endswith('IN_PROGRESS') and stack['StackStatus'] not in
               ('REVIEW_IN_PROGRESS', 'DELETE_IN_PROGRESS') for stack in stacks):
            return
        if current.get('status') != {'S': 'UNINITIALIZED'}:
            require(current.get('request_id') == {'S': request_id}, 'CURRENT belongs to another cycle')
        require(not any(stack['StackStatus'] == 'DELETE_FAILED' for stack in stacks),
                'CloudFormation DELETE_FAILED; inspect stack events')
        stacks.sort(key=lambda stack: stack['StackName'], reverse=True)
        instances = inspect_instances(ec2, env)
        ids = {stack['StackId'] for stack in stacks}
        previous_ids = known_ids
        volumes = {entry['S'] for entry in request.get('cleanup_volume_ids', {}).get('L', [])}
        direct_ids = {entry['S'] for entry in request.get('cleanup_instance_ids', {}).get('L', [])}
        resources = {item['instance_id']['S']: item for item in states if item.get('SK', {}).get('S', '').startswith('RESOURCE#') and 'instance_id' in item}
        direct_ids.update(resources)
        for instance in instances:
            tags = {entry['Key']: entry['Value'] for entry in instance.get('Tags', [])}
            require(tags.get('bootstrap-request-id') == request_id, 'Foreign cycle requires inspection')
            if tags.get('aws:cloudformation:stack-id') not in ids | previous_ids:
                record = resources.get(instance['InstanceId'])
                require(control.get('propagation_backend') == {'S': 'ec2'} and record is not None
                    and tags.get('propagation-backend') == 'ec2'
                    and tags.get('aws:ec2launchtemplate:id') == control['launch_template_id']['S']
                    and tags.get('aws:ec2launchtemplate:version') == control['launch_template_version']['S']
                    and record.get('client_token') == {'S': instance.get('ClientToken', '')}
                    and record.get('launch_template_id') == control['launch_template_id']
                    and record.get('launch_template_version') == control['launch_template_version'],
                    'Unrecorded or mismatched EC2 successor requires inspection')
                direct_ids.add(instance['InstanceId'])
            volumes.update(mapping['Ebs']['VolumeId'] for mapping in instance.get('BlockDeviceMappings', []) if 'Ebs' in mapping)
        # Validate every recorded direct resource, including already terminated
        # predecessors, before mutating any instance or stack.
        for instance_id in sorted(direct_ids):
            record = resources.get(instance_id)
            require(record is not None and re.fullmatch('i-[0-9a-f]{17}', instance_id), 'Missing exact EC2 inventory')
            exact = [i for r in ec2.describe_instances(InstanceIds=[instance_id]).get('Reservations', []) for i in r.get('Instances', [])]
            require(len(exact) == 1 and exact[0]['InstanceId'] == instance_id, 'Recorded EC2 deletion is ambiguous')
            tags = {t['Key']: t['Value'] for t in exact[0].get('Tags', [])}
            require(tags.get('bootstrap-request-id') == request_id and tags.get('propagation-backend') == 'ec2'
                and tags.get('aws:ec2launchtemplate:id') == control['launch_template_id']['S']
                and tags.get('aws:ec2launchtemplate:version') == control['launch_template_version']['S']
                and record.get('client_token') == {'S': exact[0].get('ClientToken', '')}, 'Exact EC2 ownership changed')
        cleanup_update(ddb, table, request_id, {
            'cleanup_status': {'S': 'DELETING' if stacks or direct_ids else 'VERIFYING'},
            'cleanup_instance_ids': {'L': [{'S': value} for value in sorted(direct_ids)]},
            'cleanup_stack_ids': {'L': [{'S': value} for value in sorted(ids | previous_ids)]},
            'cleanup_volume_ids': {'L': [{'S': value} for value in sorted(volumes)]}}, expected=request['cleanup_status']['S'])
        direct_pending = False
        for instance_id in sorted(direct_ids):
            cleanup_guard(ddb, table, request_id)
            exact = [i for r in ec2.describe_instances(InstanceIds=[instance_id]).get('Reservations', []) for i in r.get('Instances', [])]
            require(len(exact) == 1 and exact[0]['InstanceId'] == instance_id, 'Recorded EC2 deletion is ambiguous')
            tags = {t['Key']: t['Value'] for t in exact[0].get('Tags', [])}
            record = resources.get(instance_id)
            require(record is not None and tags.get('bootstrap-request-id') == request_id
                and tags.get('propagation-backend') == 'ec2'
                and tags.get('aws:ec2launchtemplate:id') == control['launch_template_id']['S']
                and tags.get('aws:ec2launchtemplate:version') == control['launch_template_version']['S']
                and record.get('client_token') == {'S': exact[0].get('ClientToken', '')}, 'Exact EC2 ownership changed')
            if exact[0]['State']['Name'] != 'terminated':
                direct_pending = True
                if exact[0]['State']['Name'] != 'shutting-down':
                    cleanup_guard(ddb, table, request_id)
                    ec2.terminate_instances(InstanceIds=[instance_id])
        for stack in stacks:
            if stack['StackStatus'] != 'DELETE_IN_PROGRESS':
                cleanup_guard(ddb, table, request_id)
                cfn.delete_stack(StackName=stack['StackId'], RoleARN=env['SERVICE_ROLE_ARN'],
                    ClientRequestToken='cleanup-' + request_id + '-' + hashlib.sha256(stack['StackId'].encode()).hexdigest()[:16])
        if stacks or direct_pending:
            return
        # Check recorded exact IDs as well as the paginated stack listing.
        for stack_id in previous_ids:
            stack = describe(cfn, stack_id)
            require(not stack or stack['StackStatus'] == 'DELETE_COMPLETE', 'Recorded stack deletion is incomplete')
            if stack:
                for page in cfn.get_paginator('list_stack_resources').paginate(StackName=stack_id):
                    require(not any(resource.get('ResourceStatus') == 'DELETE_SKIPPED'
                                    for resource in page['StackResourceSummaries']), 'CloudFormation retained generation resources')
        if direct_ids:
            import boto3
            from botocore.config import Config
            alarms = boto3.client('cloudwatch', region_name='us-west-2', config=Config(connect_timeout=2, read_timeout=5, retries={'total_max_attempts': 1}))
            try:
                for instance_id in direct_ids:
                    record = resources.get(instance_id)
                    require(record is not None, 'Missing recorded EC2 inventory')
                    generation = record['PK']['S'].removeprefix('GEN#')
                    name = 'cloud-glider-' + env['ENVIRONMENT'] + '-gen-' + generation + '-status-check'
                    result = alarms.describe_alarms(AlarmNames=[name])
                    require(not result.get('CompositeAlarms'), 'Alarm identity is ambiguous')
                    metric = result.get('MetricAlarms', [])
                    require(len(metric) <= 1 and (not metric or (metric[0].get('Namespace') == 'AWS/EC2'
                        and metric[0].get('MetricName') == 'StatusCheckFailed'
                        and metric[0].get('Dimensions') == [{'Name': 'InstanceId', 'Value': instance_id}])), 'Alarm ownership mismatch')
                    if metric:
                        cleanup_guard(ddb, table, request_id)
                        alarms.delete_alarms(AlarmNames=[name])
            finally:
                alarms.close()
        verify_residuals(ec2, env, volumes)
        control, current, hold, request = snapshot(ddb, table)
        cleanup_guard(ddb, table, request_id)
        complete_cleanup(ddb, table, control, current, request, env)
    except Exception as exc:
        # Keep all gates closed and retain identifiers for operator inspection.
        cleanup_update(ddb, table, request_id, {'cleanup_status': {'S': 'NEEDS_ATTENTION'},
            'cleanup_requested': {'BOOL': True}, 'propagation_enabled': {'BOOL': False},
            'bootstrap_requested': {'BOOL': False}, 'cleanup_error': {'S': str(exc)[:1000]}})
        raise


def is_cleanup_trigger(record):
    change = record.get('dynamodb', {})
    old, new = change.get('OldImage', {}), change.get('NewImage', {})
    return (record.get('eventName') == 'MODIFY' and new.get('PK') == {'S': 'BOOTSTRAP'}
            and new.get('SK') == {'S': 'REQUEST'} and new.get('cleanup_requested') == {'BOOL': True}
            and (old.get('cleanup_requested') == {'BOOL': False}
                 or (old.get('cleanup_status') == {'S': 'NEEDS_ATTENTION'}
                     and new.get('cleanup_status') == {'S': 'QUIESCING'})))



def command_update(table, control, assignments):
    require(set(assignments) <= OPERATOR_FIELDS, 'Controller cannot change approved settings')
    guard = exact_condition(control)
    names, values = guard['ExpressionAttributeNames'], guard['ExpressionAttributeValues']
    # Guard absent interface fields too, so a concurrent console edit is never overwritten.
    condition = guard['ConditionExpression']
    for i, field in enumerate(sorted(OPERATOR_FIELDS - control.keys())):
        names[f'#missing{i}'] = field
        condition += f' AND attribute_not_exists(#missing{i})'
    for i, (field, value) in enumerate(assignments.items()):
        names[f'#u{i}'] = field
        values[f':u{i}'] = value
    return {'Update': {'TableName': table, 'Key': key('CONTROL'),
        'UpdateExpression': 'SET ' + ', '.join(f'#u{i} = :u{i}' for i in range(len(assignments))),
        'ConditionExpression': condition, 'ExpressionAttributeNames': names, 'ExpressionAttributeValues': values}}


def command_transaction(ddb, transaction):
    try:
        ddb.transact_write_items(TransactItems=transaction)
        return True
    except Exception as exc:
        if (getattr(exc, 'response', {}).get('Error', {}).get('Code') == 'TransactionCanceledException'
                or str(exc) == 'conditional conflict'):
            return False  # A timer delivery rereads; never overwrite a concurrent request.
        raise


def prepared_request(control, current, request_id='1'):
    require(current.get('status') == {'S': 'UNINITIALIZED'} and not any(
        field in current for field in ('generation', 'stack_id', 'instance_id')), 'CURRENT needs inspection before bootstrap')
    return {**key('BOOTSTRAP', 'REQUEST'), 'schema_version': {'S': '2'},
        'request_id': {'S': request_id}, 'status': {'S': 'READY'},
        'bootstrap_requested': {'BOOL': False}, 'propagation_enabled': {'BOOL': False},
        'cleanup_requested': {'BOOL': False}, 'cleanup_status': {'S': 'IDLE'},
        'control_sha256': {'S': fingerprint(control)}, 'requested_by': {'S': 'lifecycle-controller'},
        'requested_at': {'N': str(int(time.time()))}}


def consume_command(ddb, env, event_control=None):
    table = env['STATE_TABLE']
    control, current, hold, request = snapshot(ddb, table)
    sequence = int(control.get('command_sequence', {'N': '0'})['N'])
    if event_control is not None and event_control.get('command_sequence', {'N': '0'}) != {'N': str(sequence)}:
        return False  # Duplicate/stale stream delivery cannot consume a later request.
    fields = ('start_requested', 'stop_requested', 'cleanup_requested')
    selected = [field for field in fields if control.get(field) == {'BOOL': True}]
    malformed = [field for field in fields if field in control and type(control[field].get('BOOL')) is not bool]
    if not selected and not malformed:
        return False
    next_sequence = str(sequence + 1)
    feedback = {field: {'BOOL': False} for field in fields}
    feedback.update(command_sequence={'N': next_sequence}, last_result_sequence={'N': next_sequence},
                    operation_updated_at={'N': str(int(time.time()))})
    active = control.get('active_command', {'S': 'NONE'})['S']
    idle_status = control.get('operation_status', {'S': 'IDLE'})

    def reject(message):
        feedback.pop('cycle_initialized', None)
        feedback.update(last_result={'S': message},
                        operation_status=idle_status if active != 'NONE' else {'S': 'REJECTED'})
        return command_transaction(ddb, [command_update(table, control, feedback)])

    if malformed:
        return reject('Request rejected: use Boolean true or false for each request switch.')
    if len(selected) != 1:
        return reject('Request rejected: choose just one switch: start, stop, or cleanup.')
    if 'propagation_enabled' in control:
        return reject('Request rejected: this installation still needs the offline lifecycle migration.')
    if control.get('environment') != {'S': env['ENVIRONMENT']}:
        return reject('Request rejected: control environment does not match this Lambda.')
    if control.get('generation_table_name') != {'S': env['GENERATION_TABLE']}:
        return reject('Request rejected: migrate the generation table configuration first.')
    action = {'start_requested': 'START', 'stop_requested': 'STOP', 'cleanup_requested': 'CLEANUP'}[selected[0]]
    original = request
    if not request:
        if control.get('cycle_initialized') == {'BOOL': True}:
            return reject('Request rejected: the internal lifecycle record is missing; inspect before restarting.')
        try:
            request = prepared_request(control, current)
        except RuntimeError as exc:
            return reject('Request rejected: ' + str(exc))
    if request.get('schema_version') != {'S': '2'}:
        return reject('Request rejected: migrate the legacy lifecycle before using these switches.')
    request_id = request.get('request_id', {}).get('S', '')
    if not re.fullmatch('[1-9][0-9]{0,17}', request_id):
        return reject('Request rejected: the internal cycle identity needs inspection.')
    cleanup_status = request.get('cleanup_status', {}).get('S')
    cleaning = request.get('cleanup_requested') == {'BOOL': True} or cleanup_status not in ('IDLE', 'COMPLETE')
    updated = dict(request)
    extra = []
    feedback['cycle_initialized'] = {'BOOL': True}
    if action == 'START':
        # A request made during cleanup is rejected rather than queued for a later launch.
        if cleaning or active == 'CLEANUP':
            return reject('Start rejected: cleanup is still running or needs attention. Start again after it finishes.')
        if hold:
            return reject('Start rejected: an emergency hold is active. Resolve and clear the hold first.')
        status = request.get('status', {}).get('S')
        if status == 'READY':
            updated.update(bootstrap_requested={'BOOL': True}, propagation_enabled={'BOOL': True})
            try:
                guard(control, current, hold, updated, updated, env, 'READY')
            except (RuntimeError, KeyError, ValueError) as exc:
                return reject('Start rejected: ' + str(exc))
            feedback.update(active_command={'S': 'START'}, active_request_id={'S': request_id},
                active_command_sequence={'N': next_sequence}, operation_status={'S': 'STARTING'},
                last_result={'S': 'Start accepted. Bootstrap and propagation are enabled.'})
        elif status == 'SUBMITTED' and current.get('status') == {'S': 'CURRENT'} and current.get('request_id') == {'S': request_id}:
            updated['propagation_enabled'] = {'BOOL': True}
            feedback.update(last_result={'S': 'Propagation enabled for the existing chain; no second bootstrap was created.'},
                            operation_status={'S': 'PROPAGATION_ENABLED'})
        else:
            return reject('Start rejected: bootstrap is already submitted or ambiguous. Inspect it or request cleanup before a new launch.')
        extra = [{'ConditionCheck': {'TableName': table, 'Key': key('HOLD', 'ACTIVE'), 'ConditionExpression': 'attribute_not_exists(PK)'}}]
    elif action == 'STOP':
        updated['propagation_enabled'] = {'BOOL': False}
        feedback.update(last_result={'S': 'Propagation stopped. Running instances remain intact.'},
                        operation_status=idle_status if active != 'NONE' else {'S': 'STOPPED'})
    else:
        updated.update(cleanup_requested={'BOOL': True}, bootstrap_requested={'BOOL': False}, propagation_enabled={'BOOL': False}, cleanup_retry_token={'S': ''})
        if cleanup_status == 'NEEDS_ATTENTION':
            updated.update(cleanup_status={'S': 'QUIESCING'}, cleanup_started_at={'N': str(int(time.time()))}, cleanup_error={'S': ''}, cleanup_retry_token={'S': ''})
        feedback.update(active_command={'S': 'CLEANUP'}, active_request_id={'S': request_id},
            active_command_sequence={'N': next_sequence}, operation_status={'S': 'QUIESCING'},
            last_result={'S': 'Cleanup accepted. Bootstrap and propagation are disabled.'})
    request_guard = exact_condition(original) if original else {'ConditionExpression': 'attribute_not_exists(PK)'}
    transaction = [command_update(table, control, feedback),
        {'Put': {'TableName': table, 'Item': updated, **request_guard}},
        {'ConditionCheck': {'TableName': table, 'Key': key('CURRENT'), **exact_condition(current)}}, *extra]
    return command_transaction(ddb, transaction)


def operation_feedback(ddb, env, action, request_id, status, message, *, finished=False):
    table = env['STATE_TABLE']
    control = read_item(ddb, table, 'CONTROL', 'GLOBAL')
    if control.get('active_command') != {'S': action} or control.get('active_request_id') != {'S': request_id}:
        return
    assignments = {'operation_status': {'S': status}, 'operation_updated_at': {'N': str(int(time.time()))}}
    # Preserve a newer rejection/stop message while updating the active operation's progress.
    if control.get('last_result_sequence') == control.get('active_command_sequence'):
        assignments['last_result'] = {'S': message}
    if finished:
        assignments.update(active_command={'S': 'NONE'}, active_request_id={'S': ''})
    command_transaction(ddb, [command_update(table, control, assignments)])


def run_operator(ddb, cfn, s3, ec2, env, event_control=None):
    consume_command(ddb, env, event_control)
    control, current, hold, request = snapshot(ddb, env['STATE_TABLE'])
    if any(control.get(field) == {'BOOL': True} for field in ('start_requested', 'stop_requested', 'cleanup_requested')):
        return
    action = control.get('active_command', {}).get('S')
    target = control.get('active_request_id', {}).get('S')
    if action not in ('START', 'CLEANUP'):
        return
    if action == 'CLEANUP' and request.get('cleanup_status') == {'S': 'COMPLETE'} and request.get('cleanup_target_request_id') == {'S': target}:
        # Reject requests entered while this operation was finishing before clearing its busy gate.
        consume_command(ddb, env)
        operation_feedback(ddb, env, action, target, 'COMPLETE', 'Cleanup complete. Ready for another start.', finished=True)
        return
    if request.get('request_id') != {'S': target}:
        operation_feedback(ddb, env, action, target, 'NEEDS_ATTENTION', 'Operation stopped: cycle identity changed; inspect the lifecycle record.', finished=True)
        return
    try:
        if action == 'START':
            process(request, ddb, cfn, s3, ec2, env)
            operation_feedback(ddb, env, action, target, 'SUBMITTED', 'Bootstrap submitted. Propagation follows its current setting.', finished=True)
        else:
            cleanup_step(request, ddb, cfn, ec2, env)
            # A concurrently edited switch must not turn into a queued start after cleanup.
            consume_command(ddb, env)
            fresh = read_item(ddb, env['STATE_TABLE'], 'BOOTSTRAP', 'REQUEST')
            status = fresh.get('cleanup_status', {}).get('S', 'NEEDS_ATTENTION')
            messages = {'QUIESCING': 'Cleanup is waiting for provisioning to settle.',
                'DELETING': 'Cleanup is deleting generation stacks.', 'VERIFYING': 'Cleanup is checking for leftover resources.',
                'COMPLETE': 'Cleanup complete. Ready for another start.',
                'NEEDS_ATTENTION': 'Cleanup needs attention: ' + fresh.get('cleanup_error', {}).get('S', 'inspect Lambda logs.')}
            operation_feedback(ddb, env, action, target, status, messages.get(status, 'Cleanup is in progress.'),
                               finished=status == 'COMPLETE')
    except Exception as exc:
        retry_bookkeeping = False
        if action == 'START':
            failed = read_item(ddb, env['STATE_TABLE'], 'BOOTSTRAP', 'REQUEST')
            retry_bookkeeping = (failed.get('request_id') == {'S': target}
                and failed.get('status') == {'S': 'SUBMITTED'}
                and bool(read_item(ddb, env['STATE_TABLE'], 'LOCK', 'PROVISIONING')))
            # The marker fences provisioning while stream retries or an operator
            # recovery verify the submitted stack. Cleanup schedules never launch bootstrap.
            if failed.get('request_id') == {'S': target} and not retry_bookkeeping:
                paused = {**failed, 'bootstrap_requested': {'BOOL': False}, 'propagation_enabled': {'BOOL': False}}
                command_transaction(ddb, [{'Put': {'TableName': env['STATE_TABLE'], 'Item': paused, **exact_condition(failed)}}])
        operation_feedback(ddb, env, action, target, 'NEEDS_ATTENTION', action.title() + ' needs attention: ' + str(exc)[:800],
                           finished=action == 'START' and not retry_bookkeeping)
        raise


def is_operator_trigger(record):
    change = record.get('dynamodb', {})
    old, new = change.get('OldImage', {}), change.get('NewImage', {})
    return (record.get('eventName') == 'MODIFY' and new.get('PK') == {'S': 'CONTROL'} and new.get('SK') == {'S': 'GLOBAL'}
            and any(old.get(field) != new.get(field) for field in ('start_requested', 'stop_requested', 'cleanup_requested')))


ACTIVE_CLEANUP = ('QUIESCING', 'DELETING', 'VERIFYING')


def cleanup_active(request, request_id=None):
    return (request.get('cleanup_requested') == {'BOOL': True}
        and request.get('cleanup_status', {}).get('S') in ACTIVE_CLEANUP
        and (request_id is None or request.get('request_id') == {'S': request_id}))


def cancel_cleanup_retry(scheduler, env, token):
    if not token:
        return
    try:
        scheduler.delete_schedule(GroupName=env['CLEANUP_SCHEDULE_GROUP'], Name=token)
    except Exception as exc:
        if getattr(exc, 'response', {}).get('Error', {}).get('Code') != 'ResourceNotFoundException':
            raise


def schedule_cleanup_retry(ddb, scheduler, env, request_id):
    request = read_item(ddb, env['STATE_TABLE'], 'BOOTSTRAP', 'REQUEST')
    if not cleanup_active(request, request_id):
        return
    token = request.get('cleanup_retry_token', {}).get('S', '')
    if not token:
        sequence = int(request.get('cleanup_retry_sequence', {'N': '0'})['N']) + 1
        token = 'cleanup-' + request_id + '-' + str(sequence)
        due = int(time.time()) + 60
        fields = {'cleanup_retry_sequence': {'N': str(sequence)},
            'cleanup_retry_token': {'S': token}, 'cleanup_retry_at': {'N': str(due)}}
        # A conditional write fences stale/duplicate invocations before creating a schedule.
        ddb.update_item(TableName=env['STATE_TABLE'], Key=key('BOOTSTRAP', 'REQUEST'),
            UpdateExpression='SET #sequence = :sequence, #token = :token, #due = :due',
            ConditionExpression=exact_condition(request)['ConditionExpression'],
            ExpressionAttributeNames={**exact_condition(request)['ExpressionAttributeNames'],
                '#sequence': 'cleanup_retry_sequence', '#token': 'cleanup_retry_token', '#due': 'cleanup_retry_at'},
            ExpressionAttributeValues={**exact_condition(request)['ExpressionAttributeValues'],
                ':sequence': fields['cleanup_retry_sequence'], ':token': fields['cleanup_retry_token'], ':due': fields['cleanup_retry_at']})
        request = {**request, **fields}
    # A fired schedule may already have auto-deleted. Never recreate a past
    # one-time schedule; bounded delivery retries retain the matching token.
    if token and int(request['cleanup_retry_at']['N']) <= int(time.time()):
        return
    target = {'Arn': env['FUNCTION_ARN'], 'RoleArn': env['CLEANUP_RETRY_ROLE_ARN'],
        'Input': json.dumps({'source': 'cloud-glider.cleanup', 'request_id': request_id, 'retry_token': token}, sort_keys=True),
        'RetryPolicy': {'MaximumRetryAttempts': 2, 'MaximumEventAgeInSeconds': 300}}
    expression = 'at(' + datetime.fromtimestamp(int(request['cleanup_retry_at']['N']), timezone.utc).strftime('%Y-%m-%dT%H:%M:%S') + ')'
    args = dict(GroupName=env['CLEANUP_SCHEDULE_GROUP'], Name=token,
        ClientToken=hashlib.sha256(token.encode()).hexdigest(), ScheduleExpression=expression,
        ScheduleExpressionTimezone='UTC', FlexibleTimeWindow={'Mode': 'OFF'},
        ActionAfterCompletion='DELETE', State='ENABLED', Target=target)
    try:
        scheduler.create_schedule(**args)
    except Exception as exc:
        if getattr(exc, 'response', {}).get('Error', {}).get('Code') != 'ConflictException':
            raise
        existing = scheduler.get_schedule(GroupName=args['GroupName'], Name=token)
        require(all(existing.get(field) == args[field] for field in
            ('ScheduleExpression', 'ScheduleExpressionTimezone', 'FlexibleTimeWindow', 'ActionAfterCompletion', 'State', 'Target')),
            'Cleanup retry schedule conflicts; inspect before resuming')
    # AWS creation cannot be atomic with DynamoDB. A raced completion gets at most
    # one harmless stale invocation; remove its schedule when observable here.
    fresh = read_item(ddb, env['STATE_TABLE'], 'BOOTSTRAP', 'REQUEST')
    if not cleanup_active(fresh, request_id) or fresh.get('cleanup_retry_token') != {'S': token}:
        cancel_cleanup_retry(scheduler, env, token)


def finish_cleanup_delivery(ddb, scheduler, env, before):
    request_id = before.get('request_id', {}).get('S')
    if not request_id:
        return
    fresh = read_item(ddb, env['STATE_TABLE'], 'BOOTSTRAP', 'REQUEST')
    old_token = before.get('cleanup_retry_token', {}).get('S', '')
    if not cleanup_active(fresh, request_id):
        cancel_cleanup_retry(scheduler, env, old_token)
        return
    try:
        schedule_cleanup_retry(ddb, scheduler, env, request_id)
        fresh = read_item(ddb, env['STATE_TABLE'], 'BOOTSTRAP', 'REQUEST')
        if old_token and fresh.get('cleanup_retry_token') != {'S': old_token}:
            cancel_cleanup_retry(scheduler, env, old_token)
    except Exception as exc:
        fresh = read_item(ddb, env['STATE_TABLE'], 'BOOTSTRAP', 'REQUEST')
        if cleanup_active(fresh, request_id):
            cleanup_update(ddb, env['STATE_TABLE'], request_id, {
                'cleanup_status': {'S': 'NEEDS_ATTENTION'}, 'cleanup_error': {'S': 'Cleanup retry scheduling failed: ' + str(exc)[:800]}})
            operation_feedback(ddb, env, 'CLEANUP', request_id, 'NEEDS_ATTENTION',
                'Cleanup needs attention: retry scheduling failed; inspect Lambda logs.')
        raise


def retry_cleanup(event, ddb, cfn, s3, ec2, scheduler, env):
    request = read_item(ddb, env['STATE_TABLE'], 'BOOTSTRAP', 'REQUEST')
    request_id, token = event.get('request_id'), event.get('retry_token')
    if not isinstance(request_id, str) or not re.fullmatch('[1-9][0-9]{0,17}', request_id):
        return
    if not cleanup_active(request, request_id) or not token or request.get('cleanup_retry_token') != {'S': token}:
        return  # Duplicate delivery, resumed operation, or another cycle: no work and no successor timer.
    before = dict(request)
    # Clear the pending token only after work succeeds. A Lambda timeout retains
    # it for AWS's bounded delivery retry; successful processing replaces it.
    control = read_item(ddb, env['STATE_TABLE'], 'CONTROL', 'GLOBAL')
    try:
        if control.get('active_command') == {'S': 'CLEANUP'} and control.get('active_request_id') == {'S': request_id}:
            run_operator(ddb, cfn, s3, ec2, env)
        else:
            cleanup_step(request, ddb, cfn, ec2, env)
        fresh = read_item(ddb, env['STATE_TABLE'], 'BOOTSTRAP', 'REQUEST')
        if cleanup_active(fresh, request_id) and fresh.get('cleanup_retry_token') == {'S': token}:
            ddb.update_item(TableName=env['STATE_TABLE'], Key=key('BOOTSTRAP', 'REQUEST'),
                UpdateExpression='SET #token = :empty',
                ConditionExpression=exact_condition(fresh)['ConditionExpression'],
                ExpressionAttributeNames={**exact_condition(fresh)['ExpressionAttributeNames'], '#token': 'cleanup_retry_token'},
                ExpressionAttributeValues={**exact_condition(fresh)['ExpressionAttributeValues'], ':empty': {'S': ''}})
    finally:
        finish_cleanup_delivery(ddb, scheduler, env, before)


def handler(event, context):
    import boto3
    from botocore.config import Config
    env = os.environ
    require(env['AWS_REGION'] == 'us-west-2', 'Unsupported Region')
    clients = [boto3.client(service, config=Config(retries={'total_max_attempts': 1}, connect_timeout=3, read_timeout=10))
               for service in ('dynamodb', 'cloudformation', 's3', 'ec2', 'scheduler')]
    ddb, cfn, s3, ec2, scheduler = clients
    if event.get('source') == 'cloud-glider.cleanup':
        retry_cleanup(event, ddb, cfn, s3, ec2, scheduler, env)
        return
    for record in event.get('Records', []):
        image = record.get('dynamodb', {}).get('NewImage', {})
        if not any(trigger(record) for trigger in (is_operator_trigger, is_bootstrap_trigger, is_cleanup_trigger)):
            continue
        require(record.get('eventSourceARN') == env['STREAM_ARN'], 'Unexpected event source')
        before = read_item(ddb, env['STATE_TABLE'], 'BOOTSTRAP', 'REQUEST')
        try:
            if is_operator_trigger(record):
                run_operator(ddb, cfn, s3, ec2, env, image)
            elif is_cleanup_trigger(record):
                cleanup_step(image, ddb, cfn, ec2, env)
            else:
                process(image, ddb, cfn, s3, ec2, env)
        finally:
            finish_cleanup_delivery(ddb, scheduler, env, before)
