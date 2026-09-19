"""Synthetic PDFs reproduce publisher block grouping without private papers."""
from pathlib import Path

import fitz
import pytest

from auto_research.evidence.visual_evidence import _generic_specs


def _paper(path: Path, lines: str) -> None:
    with fitz.open() as doc:
        page = doc.new_page()
        page.insert_text((60, 90), lines, fontsize=10, lineheight=1.2)
        for y in (132, 152, 182):
            page.draw_line((60, y), (350, y))
        for x in (60, 205, 350):
            page.draw_line((x, 132), (x, 182))
        page.insert_text((65, 146), 'Material', fontsize=9)
        page.insert_text((210, 146), 'Hardness (GPa)', fontsize=9)
        page.insert_text((65, 171), 'Synthetic A', fontsize=9)
        page.insert_text((210, 171), '3.2', fontsize=9)
        doc.save(path)


def test_table_caption_inside_prose_block_is_discovered_with_precise_crop(tmp_path):
    path = tmp_path / 'merged.pdf'
    _paper(path, 'The measurements were recorded.\nTable 2. Measured hardness of specimens.')
    with fitz.open(path) as doc:
        blocks = doc[0].get_text('blocks')
        assert any('recorded.\nTable 2.' in block[4] for block in blocks)
    specs = _generic_specs(path)
    assert len(specs) == 1
    table = specs[0]
    assert (table['asset_type'], table['number'], table['page']) == ('table', 2, 1)
    assert table['caption'] == 'Measured hardness of specimens.'
    assert table['bbox'][1] > 80  # Excludes the preceding prose baseline.
    assert table['bbox'][3] >= 182  # Retains the full source table.
    assert 'recorded' in table['source_context']
    assert table['materials'] == []  # Discovery does not invent parsed values.


@pytest.mark.parametrize('body', [
    'The measurements were recorded.\nTable 2 shows the hardness results.',
    'The measurements in Table 2. Measured hardness were recorded.',
])
def test_prose_table_references_do_not_become_captions(tmp_path, body):
    path = tmp_path / 'reference.pdf'
    _paper(path, body)
    assert _generic_specs(path) == []


def test_label_and_caption_wrapped_inside_block_exclude_table_cells(tmp_path):
    path = tmp_path / 'wrapped.pdf'
    _paper(path, 'The measurements were recorded.\nTable 2.\nMeasured hardness of specimens.')
    table, = _generic_specs(path)
    assert table['caption'] == 'Measured hardness of specimens.'
    assert table['bbox'][3] >= 182
