#!/usr/bin/env python3

# To install `nvitop` with specific version of `nvidia-ml-py`, use:
#
#   pip install nvidia-ml-py==xx.yyy.zz nvitop
#
# or
#
#   pip install 'nvitop[cudaXX]'
#

"""Setup script for ``nvitop``."""

from __future__ import annotations

import contextlib
import re
import sys
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from typing import TYPE_CHECKING

from setuptools import find_packages, setup


if TYPE_CHECKING:
    from collections.abc import Generator
    from types import ModuleType


HERE = Path(__file__).absolute().parent


@contextlib.contextmanager
def vcs_version(name: str, path: Path | str) -> Generator[ModuleType]:
    """Context manager to update version string in a version module."""
    path = Path(path).absolute()
    assert path.is_file()
    module_spec = spec_from_file_location(name=name, location=path)
    assert module_spec is not None
    assert module_spec.loader is not None
    module = sys.modules.get(name)
    if module is None:
        module = module_from_spec(module_spec)
        sys.modules[name] = module
    module_spec.loader.exec_module(module)

    if module.__release__:
        yield module
        return

    content = None
    try:
        try:
            content = path.read_text(encoding='utf-8')
            path.write_text(
                data=re.sub(
                    r"""__version__\s*=\s*('[^']+'|"[^"]+")""",
                    f'__version__ = {module.__version__!r}',
                    string=content,
                ),
                encoding='utf-8',
            )
        except OSError:
            content = None

        yield module
    finally:
        if content is not None:
            with path.open(mode='wt', encoding='utf-8', newline='') as file:
                file.write(content)


extra_requirements = {
    'exporter': ['nvitop-exporter'],
    'prometheus': ['nvitop-exporter'],
    'lint': [
        'ruff',
        'pylint[spelling]',
        'xdoctest',
        'mypy',
        'typing-extensions',
        'pre-commit',
    ],
    'cuda10': ['nvidia-ml-py == 11.450.51'],
}

with vcs_version(name='nvitop.version', path=HERE / 'nvitop' / 'version.py') as version:
    for pynvml_major in sorted(
        {int(pynvml.partition('.')[0]) for pynvml in version.PYNVML_VERSION_CANDIDATES},
    ):
        pynvml_range = [
            pynvml
            for pynvml in version.PYNVML_VERSION_CANDIDATES
            if pynvml.startswith(f'{pynvml_major}.')
        ]
        if len(pynvml_range) == 1:
            extra_requirements[f'cuda{pynvml_major}'] = [
                f'nvidia-ml-py == {pynvml_range[0]}',
            ]
        elif len(pynvml_range) >= 2:
            extra_requirements[f'cuda{pynvml_major}'] = [
                f'nvidia-ml-py >= {pynvml_range[0]}, <= {pynvml_range[-1]}',
            ]

    # Keep a complete setup() fallback for setuptools versions that do not read PEP 621.
    # Build backends execute setup.py with __name__ != '__main__'.
    setup(
        name='nvitop',
        version=version.__version__,
        description='An interactive NVIDIA-GPU process viewer and beyond.',
        long_description=(HERE / 'README.md').read_text(encoding='utf-8'),
        long_description_content_type='text/markdown',
        python_requires='>=3.8',
        packages=find_packages(include=['nvitop', 'nvitop.*']),
        install_requires=[
            'nvidia-ml-py >= 11.450.51, < 13.611.0a0',
            'psutil >= 5.6.6',
            "colorama >= 0.4.0; platform_system == 'Windows'",
            "windows-curses >= 2.2.0; platform_system == 'Windows'",
        ],
        entry_points={
            'console_scripts': [
                'nvitop=nvitop.cli:main',
                'nvisel=nvitop.select:main',
                'tecotop=nvitop.cli:tecotop_main',
            ],
        },
        extras_require=extra_requirements,
    )
