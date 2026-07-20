from __future__ import annotations

import subprocess  # noqa: S404
import sys
from pathlib import Path

import pytest
from check_visibility import Violation, check_visibility

REPO_ROOT = Path(__file__).resolve().parents[2]
CHECKER_SCRIPT = REPO_ROOT / 'scripts' / 'check_visibility.py'


def _write_tree(root: Path, files: dict[str, str]) -> Path:
    pkg_root = root / 'pkg'
    for rel, content in files.items():
        path = pkg_root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding='utf-8')
    return pkg_root


class TestFourStateTotality:
    @staticmethod
    @pytest.mark.parametrize(
        ('files', 'expected'),
        [
            pytest.param(
                {
                    '__init__.py': "from pkg.leaf import Thing\n\n__all__ = ['Thing']\n",
                    'leaf.py': "__all__ = ['Thing']\n\n\nclass Thing: ...\n",
                    'sub/__init__.py': '',
                    '_internal/__init__.py': '',
                    '_internal/machinery.py': 'class Machine: ...\n',
                },
                [],
                id='conforming_tree_reports_no_violations',
            ),
            pytest.param(
                {
                    '__init__.py': '__all__ = []\n',
                    'leaf.py': "__all__ = ['Thing']\n\n\nclass Thing: ...\n",
                },
                [Violation(kind='ghost', module='pkg.leaf', name='Thing')],
                id='leaf_with_unexported_public_name_is_ghost',
            ),
            pytest.param(
                {
                    '__init__.py': '__all__ = []\n',
                    'leaf.py': 'class Thing: ...\n',
                },
                [Violation(kind='missing_all', module='pkg.leaf')],
                id='bare_leaf_without_all_is_flagged',
            ),
            pytest.param(
                {
                    '__init__.py': "from pkg.sub.leaf import Thing\n\n__all__ = ['Thing']\n",
                    'sub/__init__.py': '',
                    'sub/leaf.py': "__all__ = ['Thing']\n\n\nclass Thing: ...\n",
                },
                [],
                id='structural_init_is_transparent_for_facade_coverage',
            ),
        ],
    )
    def test_four_state_classification(files: dict[str, str], expected: list[Violation], tmp_path: Path) -> None:
        pkg = _write_tree(tmp_path, files)

        assert check_visibility(pkg) == expected

    @staticmethod
    def test_init_with_imports_but_no_all_is_flagged(tmp_path: Path) -> None:
        pkg = _write_tree(
            tmp_path,
            {
                '__init__.py': '__all__ = []\n',
                'sub/__init__.py': 'from pkg.sub.leaf import Thing\n',
                'sub/leaf.py': "__all__ = ['Thing']\n\n\nclass Thing: ...\n",
            },
        )

        violations = check_visibility(pkg)

        assert Violation(kind='missing_all', module='pkg.sub') in violations


class TestSingleHome:
    @staticmethod
    @pytest.mark.parametrize(
        ('files', 'expected'),
        [
            pytest.param(
                {
                    '__init__.py': "from pkg.a import Thing\n\n__all__ = ['Thing']\n",
                    'a/__init__.py': "from pkg.a.leaf import Thing\n\n__all__ = ['Thing']\n",
                    'a/leaf.py': "__all__ = ['Thing']\n\n\nclass Thing: ...\n",
                },
                [Violation(kind='multi_home', module='pkg.a.leaf', name='Thing')],
                id='same_symbol_in_two_facades_is_multi_home',
            ),
            pytest.param(
                {
                    '__init__.py': '__all__ = []\n',
                    'a/__init__.py': "from pkg.a.leaf import ISender\n\n__all__ = ['ISender']\n",
                    'a/leaf.py': "__all__ = ['ISender']\n\n\nclass ISender: ...\n",
                    'b/__init__.py': "from pkg.b.leaf import ISender\n\n__all__ = ['ISender']\n",
                    'b/leaf.py': "__all__ = ['ISender']\n\n\nclass ISender: ...\n",
                },
                [],
                id='distinct_symbols_sharing_bare_name_coexist',
            ),
        ],
    )
    def test_single_home_enforcement(files: dict[str, str], expected: list[Violation], tmp_path: Path) -> None:
        pkg = _write_tree(tmp_path, files)

        assert check_visibility(pkg) == expected

    @staticmethod
    def test_allowlisted_pair_may_live_in_two_facades(tmp_path: Path) -> None:
        pkg = _write_tree(
            tmp_path,
            {
                '__init__.py': "from pkg.a.leaf import Thing\n\n__all__ = ['Thing']\n",
                'a/__init__.py': "from pkg.a.leaf import Thing\n\n__all__ = ['Thing']\n",
                'a/leaf.py': "__all__ = ['Thing']\n\n\nclass Thing: ...\n",
            },
        )

        violations = check_visibility(pkg, dual_home_allowlist=frozenset({('pkg.a.leaf', 'Thing')}))

        assert violations == []


class TestUnderscoreFilenames:
    @staticmethod
    @pytest.mark.parametrize(
        ('files', 'expected'),
        [
            pytest.param(
                {
                    '__init__.py': '__all__ = []\n',
                    '_helper.py': 'class Helper: ...\n',
                },
                [Violation(kind='underscore_file', module='pkg._helper')],
                id='underscore_filename_outside_internal_is_flagged',
            ),
            pytest.param(
                {
                    '__init__.py': '__all__ = []\n',
                    '_internal/__init__.py': '',
                    '_internal/helper.py': 'class Helper: ...\n',
                },
                [],
                id='clean_and_dunder_filenames_inside_internal_are_legal',
            ),
        ],
    )
    def test_underscore_filename_rules(files: dict[str, str], expected: list[Violation], tmp_path: Path) -> None:
        pkg = _write_tree(tmp_path, files)

        assert check_visibility(pkg) == expected


class TestUnderscoreImportTargets:
    @staticmethod
    @pytest.mark.parametrize(
        ('files', 'expected'),
        [
            pytest.param(
                {
                    '__init__.py': '__all__ = []\n',
                    'other.py': "__all__ = []\n\n_y = 1\n__version__ = '1'\n",
                    'mod.py': '__all__ = []\n\nfrom pkg.other import _y\n',
                },
                [Violation(kind='underscore_import', module='pkg.mod', name='_y')],
                id='underscore_import_target_is_flagged',
            ),
            pytest.param(
                {
                    '__init__.py': '__all__ = []\n',
                    'other.py': "__all__ = []\n\n__version__ = '1'\n",
                    'mod.py': '__all__ = []\n\nfrom pkg.other import __version__\n',
                },
                [],
                id='dunder_import_target_is_exempt',
            ),
            pytest.param(
                {
                    '__init__.py': '__all__ = []\n',
                    '_internal/__init__.py': '',
                    '_internal/helper.py': 'class Thing: ...\n',
                    'mod.py': '__all__ = []\n\nfrom pkg._internal.helper import Thing\n',
                },
                [],
                id='clean_import_through_internal_path_is_legal',
            ),
        ],
    )
    def test_underscore_import_target_rules(files: dict[str, str], expected: list[Violation], tmp_path: Path) -> None:
        pkg = _write_tree(tmp_path, files)

        assert check_visibility(pkg) == expected


class TestTypeVarNaming:
    @staticmethod
    @pytest.mark.parametrize(
        ('files', 'expected'),
        [
            pytest.param(
                {
                    '__init__.py': '__all__ = []\n',
                    'leaf.py': "__all__ = []\n\nfrom typing import TypeVar\n\n_T = TypeVar('_T')\n",
                },
                [Violation(kind='typevar_name', module='pkg.leaf', name='_T')],
                id='bare_private_typevar_is_flagged',
            ),
            pytest.param(
                {
                    '__init__.py': '__all__ = []\n',
                    'leaf.py': (
                        '__all__ = []\n\n'
                        'from typing import TypeVar\n\n'
                        "_ValueT = TypeVar('_ValueT')\n"
                        "_ResponseT_co = TypeVar('_ResponseT_co', covariant=True)\n"
                        "_CommandT_contra = TypeVar('_CommandT_contra', contravariant=True)\n"
                    ),
                },
                [],
                id='descriptive_and_variance_suffixed_private_typevars_are_clean',
            ),
            pytest.param(
                {
                    '__init__.py': '__all__ = []\n',
                    'leaf.py': "__all__ = []\n\nfrom typing import TypeVar\n\nFooT = TypeVar('FooT')\n",
                },
                [Violation(kind='typevar_public_unimported', module='pkg.leaf', name='FooT')],
                id='public_typevar_never_imported_elsewhere_is_flagged',
            ),
            pytest.param(
                {
                    '__init__.py': '__all__ = []\n',
                    'defs.py': "__all__ = []\n\nfrom typing import TypeVar\n\nStateT = TypeVar('StateT')\n",
                    'reader.py': '__all__ = []\n\nfrom pkg.defs import StateT\n',
                },
                [],
                id='public_typevar_reused_by_import_is_clean',
            ),
        ],
    )
    def test_typevar_naming_rules(files: dict[str, str], expected: list[Violation], tmp_path: Path) -> None:
        pkg = _write_tree(tmp_path, files)

        assert check_visibility(pkg) == expected


class TestRealTree:
    @staticmethod
    def test_src_waku_has_zero_violations() -> None:
        assert check_visibility(REPO_ROOT / 'src' / 'waku') == []


class TestCli:
    @staticmethod
    def test_cli_exits_nonzero_and_prints_one_line_per_violation(tmp_path: Path) -> None:
        pkg = _write_tree(
            tmp_path,
            {
                '__init__.py': '__all__ = []\n',
                'ghost.py': "__all__ = ['Thing']\n\n\nclass Thing: ...\n",
                'bare.py': 'class Other: ...\n',
            },
        )

        result = subprocess.run(  # noqa: S603
            [sys.executable, str(CHECKER_SCRIPT), str(pkg)],
            capture_output=True,
            text=True,
            check=False,
        )

        assert result.returncode == 1
        assert len(result.stdout.strip().splitlines()) == 2

    @staticmethod
    def test_cli_exits_zero_on_conforming_tree(tmp_path: Path) -> None:
        pkg = _write_tree(
            tmp_path,
            {
                '__init__.py': "from pkg.leaf import Thing\n\n__all__ = ['Thing']\n",
                'leaf.py': "__all__ = ['Thing']\n\n\nclass Thing: ...\n",
            },
        )

        result = subprocess.run(  # noqa: S603
            [sys.executable, str(CHECKER_SCRIPT), str(pkg)],
            capture_output=True,
            text=True,
            check=False,
        )

        assert result.returncode == 0
        assert not result.stdout
