"""One-shot operator bootstrap. Embedded in cfn/bootstrap.yaml by the renderer."""
import hashlib
import json
import os
import re
import time
from urllib.parse import quote


def fingerprint(item):
    item = {k: v for k, v in item.items() if k not in ('propagation_enabled', 'updated_at', 'updated_by')}
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


def parameters(control, env):
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
    result.update(Generation='000000', PredecessorStackId='NONE', HandoffToken='OPERATOR_BOOTSTRAP')
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
    require(type(control.get('propagation_enabled', {}).get('BOOL')) is bool, 'Invalid propagation control')
    require(not hold, 'Emergency hold prevents bootstrap')
    require(current.get('status') == {'S': 'UNINITIALIZED'}
            and not any(field in current for field in ('generation', 'stack_id', 'instance_id')),
            'CURRENT is not uninitialized')
    require(fingerprint(control) == request['control_sha256']['S'], 'Approved control changed; inspect manually')
    require(int(request['valid_until_epoch']['N']) > int(time.time()), 'Bootstrap request expired')
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


def finish(ddb, table, request_id, stack_id):
    ddb.update_item(TableName=table, Key=key('BOOTSTRAP', 'REQUEST'),
        UpdateExpression='SET #s = :submitted, stack_id = :stack',
        ConditionExpression='request_id = :id AND #s = :creating',
        ExpressionAttributeNames={'#s': 'status'},
        ExpressionAttributeValues={':id': {'S': request_id}, ':creating': {'S': 'CREATING'},
            ':submitted': {'S': 'SUBMITTED'}, ':stack': {'S': stack_id}})
    print(json.dumps({'action': 'BOOTSTRAP_SUBMITTED', 'request_id': request_id, 'stack_id': stack_id}))


def process(event_request, ddb, cfn, s3, ec2, env):
    table, name = env['STATE_TABLE'], 'cloud-glider-' + env['ENVIRONMENT'] + '-gen-000000'
    control, current, hold, request = snapshot(ddb, table)
    require(request.get('request_id') == event_request.get('request_id'), 'Stale bootstrap event')
    status = request.get('status', {}).get('S')
    if status in ('SUBMITTED', 'CANCELLED'):
        return
    params = parameters(control, env)
    request_id = request['request_id']['S']
    require(re.fullmatch('[a-zA-Z0-9-]{1,64}', request_id), 'Invalid request id')
    existing = describe(cfn, name)
    if status == 'CREATING':
        # Never resubmit a claimed request, even if the stack is absent: an API call may still be in flight.
        require(existing is not None, 'Ambiguous bootstrap submission; operator inspection required')
        verify_existing(existing, params, env, request_id)
        finish(ddb, table, request_id, existing['StackId'])
        return
    guard(control, current, hold, request, event_request, env, 'REQUESTED')
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
    # A durable claim prevents duplicate execution and automatic rebootstrap after deletion.
    ddb.update_item(TableName=table, Key=key('BOOTSTRAP', 'REQUEST'),
        UpdateExpression='SET #s = :creating',
        ConditionExpression='request_id = :id AND #s = :requested AND control_sha256 = :digest',
        ExpressionAttributeNames={'#s': 'status'}, ExpressionAttributeValues={
            ':id': {'S': request_id}, ':requested': {'S': 'REQUESTED'}, ':creating': {'S': 'CREATING'},
            ':digest': request['control_sha256']})
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
    finish(ddb, table, request_id, result['StackId'])


def handler(event, context):
    import boto3
    from botocore.config import Config
    env = os.environ
    require(env['AWS_REGION'] == 'us-west-2', 'Unsupported Region')
    clients = [boto3.client(service, config=Config(retries={'total_max_attempts': 1}))
               for service in ('dynamodb', 'cloudformation', 's3', 'ec2')]
    for record in event.get('Records', []):
        image = record.get('dynamodb', {}).get('NewImage', {})
        if record.get('eventName') != 'INSERT' or image.get('PK') != {'S': 'BOOTSTRAP'} or image.get('SK') != {'S': 'REQUEST'}:
            continue
        require(record.get('eventSourceARN') == env['STREAM_ARN'], 'Unexpected event source')
        require(image.get('status') == {'S': 'REQUESTED'}, 'Unexpected request status')
        process(image, *clients, env)
