#!/usr/bin/env python3
"""Select build headers and obsolete kernel packages; always preserve the target AWS kernel."""
import re
import sys


def obsolete_packages(packages, keep):
    if not re.fullmatch(r'[0-9][a-zA-Z0-9.+-]*-aws', keep):
        raise ValueError('expected an AWS kernel release')
    names = {package.split(':', 1)[0] for package in packages}
    if not {'linux-image-' + keep, 'linux-modules-' + keep} <= names:
        raise ValueError('selected kernel image/modules are not both installed')
    result = []
    for package in packages:
        name = package.split(':', 1)[0]
        headers = name.startswith('linux-headers-') or re.fullmatch(r'linux-aws-.*-headers-.*', name)
        kernel = re.fullmatch(r'linux-(?:image|modules)(?:-extra)?-([0-9].*)', name)
        if headers or (kernel and kernel[1] != keep):
            result.append(package)
    return sorted(result)


if __name__ == '__main__':
    for package in obsolete_packages(sys.stdin.read().splitlines(), sys.argv[1]):
        print(package)
