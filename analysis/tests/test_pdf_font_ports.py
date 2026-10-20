"""Core PDF fonts must receive printable equivalents of report math labels."""
import pytest

pytest.importorskip('fpdf')
from analysis.pdf_report import _MirumPDF


def test_builtin_font_labels_keep_meaning_without_unsupported_unicode():
    pdf = _MirumPDF()
    pdf.set_font('Helvetica')
    assert pdf.normalize_text('Cl vs α — angle (°) ·') == 'Cl vs alpha - angle (°) ·'
