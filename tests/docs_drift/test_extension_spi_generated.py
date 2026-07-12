from __future__ import annotations

from pathlib import Path

from gen_spi_docs import generate_spi_page

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_committed_spi_page_matches_generated() -> None:
    committed = (REPO_ROOT / 'docs' / 'reference' / 'extension-spi.md').read_text(encoding='utf-8')

    assert committed == generate_spi_page(REPO_ROOT / 'src' / 'waku')
