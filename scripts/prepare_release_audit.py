#!/usr/bin/env python3
"""Prepare an operator release-audit transaction locally; never contacts AWS."""
import argparse
import json
import re
from pathlib import Path


def prepare(item, environment):
    if not re.fullmatch(r'[a-z][a-z0-9-]{1,15}', environment):
        raise ValueError('Invalid environment')
    if item.get('PK') != {'S': 'AUDIT#RELEASE'} or not item.get('SK', {}).get('S'):
        raise ValueError('Release audit requires AUDIT#RELEASE and a nonempty string SK')
    if item.get('environment') not in (None, {'S': environment}):
        raise ValueError('Release environment mismatch')
    return {'TransactItems': [{'Put': {
        'TableName': f'cloud-glider-{environment}-audit', 'Item': item,
        'ConditionExpression': 'attribute_not_exists(PK)',
    }}]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--item', type=Path, required=True, help='Complete DynamoDB wire-format release item')
    parser.add_argument('--environment', default='sandbox')
    args = parser.parse_args()
    try:
        result = prepare(json.loads(args.item.read_text()), args.environment)
    except (ValueError, TypeError, OSError) as exc:
        parser.error(str(exc))
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
