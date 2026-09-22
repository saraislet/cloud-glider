#!/usr/bin/env python3
"""Embed the testable bootstrap source in CloudFormation; --check rejects stale code."""
import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    path = ROOT / 'cfn/bootstrap.yaml'
    text = path.read_text()
    start = '          # BOOTSTRAP_SOURCE_START\n'
    end = '          # BOOTSTRAP_SOURCE_END\n'
    before, remaining = text.split(start)
    _, after = remaining.split(end)
    source = ''.join(('          ' + line if line else '') + '\n' for line in (ROOT / 'bootstrap/handler.py').read_text().splitlines())
    rendered = before + start + source + end + after
    if args.check:
        if text != rendered:
            raise SystemExit('Run python3 scripts/render_bootstrap_template.py')
    else:
        path.write_text(rendered)


if __name__ == '__main__':
    main()
