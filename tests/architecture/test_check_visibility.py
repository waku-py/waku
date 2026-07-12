from __future__ import annotations

import subprocess  # noqa: S404
import sys
from pathlib import Path

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
    def test_conforming_tree_reports_no_violations(tmp_path: Path) -> None:
        pkg = _write_tree(
            tmp_path,
            {
                '__init__.py': "from pkg.leaf import Thing\n\n__all__ = ['Thing']\n",
                'leaf.py': "__all__ = ['Thing']\n\n\nclass Thing: ...\n",
                'sub/__init__.py': '',
                '_internal/__init__.py': '',
                '_internal/machinery.py': 'class Machine: ...\n',
            },
        )

        assert check_visibility(pkg) == []

    @staticmethod
    def test_leaf_with_unexported_public_name_is_ghost(tmp_path: Path) -> None:
        pkg = _write_tree(
            tmp_path,
            {
                '__init__.py': '__all__ = []\n',
                'leaf.py': "__all__ = ['Thing']\n\n\nclass Thing: ...\n",
            },
        )

        assert check_visibility(pkg) == [Violation(kind='ghost', module='pkg.leaf', name='Thing')]

    @staticmethod
    def test_bare_leaf_without_all_is_flagged(tmp_path: Path) -> None:
        pkg = _write_tree(
            tmp_path,
            {
                '__init__.py': '__all__ = []\n',
                'leaf.py': 'class Thing: ...\n',
            },
        )

        assert check_visibility(pkg) == [Violation(kind='missing_all', module='pkg.leaf')]

    @staticmethod
    def test_structural_init_is_transparent_for_facade_coverage(tmp_path: Path) -> None:
        pkg = _write_tree(
            tmp_path,
            {
                '__init__.py': "from pkg.sub.leaf import Thing\n\n__all__ = ['Thing']\n",
                'sub/__init__.py': '',
                'sub/leaf.py': "__all__ = ['Thing']\n\n\nclass Thing: ...\n",
            },
        )

        assert check_visibility(pkg) == []

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
    def test_same_symbol_in_two_facades_is_multi_home(tmp_path: Path) -> None:
        pkg = _write_tree(
            tmp_path,
            {
                '__init__.py': "from pkg.a import Thing\n\n__all__ = ['Thing']\n",
                'a/__init__.py': "from pkg.a.leaf import Thing\n\n__all__ = ['Thing']\n",
                'a/leaf.py': "__all__ = ['Thing']\n\n\nclass Thing: ...\n",
            },
        )

        assert check_visibility(pkg) == [Violation(kind='multi_home', module='pkg.a.leaf', name='Thing')]

    @staticmethod
    def test_distinct_symbols_sharing_bare_name_coexist(tmp_path: Path) -> None:
        pkg = _write_tree(
            tmp_path,
            {
                '__init__.py': '__all__ = []\n',
                'a/__init__.py': "from pkg.a.leaf import ISender\n\n__all__ = ['ISender']\n",
                'a/leaf.py': "__all__ = ['ISender']\n\n\nclass ISender: ...\n",
                'b/__init__.py': "from pkg.b.leaf import ISender\n\n__all__ = ['ISender']\n",
                'b/leaf.py': "__all__ = ['ISender']\n\n\nclass ISender: ...\n",
            },
        )

        assert check_visibility(pkg) == []

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
    def test_underscore_filename_outside_internal_is_flagged(tmp_path: Path) -> None:
        pkg = _write_tree(
            tmp_path,
            {
                '__init__.py': '__all__ = []\n',
                '_helper.py': 'class Helper: ...\n',
            },
        )

        assert check_visibility(pkg) == [Violation(kind='underscore_file', module='pkg._helper')]

    @staticmethod
    def test_clean_and_dunder_filenames_inside_internal_are_legal(tmp_path: Path) -> None:
        pkg = _write_tree(
            tmp_path,
            {
                '__init__.py': '__all__ = []\n',
                '_internal/__init__.py': '',
                '_internal/helper.py': 'class Helper: ...\n',
            },
        )

        assert check_visibility(pkg) == []


class TestUnderscoreImportTargets:
    @staticmethod
    def test_underscore_import_target_is_flagged(tmp_path: Path) -> None:
        pkg = _write_tree(
            tmp_path,
            {
                '__init__.py': '__all__ = []\n',
                'other.py': "__all__ = []\n\n_y = 1\n__version__ = '1'\n",
                'mod.py': '__all__ = []\n\nfrom pkg.other import _y\n',
            },
        )

        assert check_visibility(pkg) == [Violation(kind='underscore_import', module='pkg.mod', name='_y')]

    @staticmethod
    def test_dunder_import_target_is_exempt(tmp_path: Path) -> None:
        pkg = _write_tree(
            tmp_path,
            {
                '__init__.py': '__all__ = []\n',
                'other.py': "__all__ = []\n\n__version__ = '1'\n",
                'mod.py': '__all__ = []\n\nfrom pkg.other import __version__\n',
            },
        )

        assert check_visibility(pkg) == []

    @staticmethod
    def test_clean_import_through_internal_path_is_legal(tmp_path: Path) -> None:
        pkg = _write_tree(
            tmp_path,
            {
                '__init__.py': '__all__ = []\n',
                '_internal/__init__.py': '',
                '_internal/helper.py': 'class Thing: ...\n',
                'mod.py': '__all__ = []\n\nfrom pkg._internal.helper import Thing\n',
            },
        )

        assert check_visibility(pkg) == []


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
