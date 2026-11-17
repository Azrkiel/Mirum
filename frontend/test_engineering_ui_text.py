"""Plain-text rendering helper for model/server text in the checked beam page."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def unescape(text):
    result, index = [], 0
    while index < len(text):
        if text[index] == '\\' and index + 1 < len(text):
            index += 1
        result.append(text[index])
        index += 1
    return ''.join(result)


def test_plain_escapes_markdown_constructs_so_text_cannot_render_as_markup():
    import engineering_ui
    hostile = '![x](https://tracker.invalid/p.png) [l](https://a.invalid) **b** `c` <b>x</b> # h\n- item'
    escaped = engineering_ui._plain(hostile)
    assert '![' not in escaped and '](' not in escaped and '**' not in escaped
    for position, char in enumerate(escaped):
        if char in engineering_ui._MARKDOWN_SPECIAL and char != '\\':
            assert escaped[position - 1] == '\\', (position, char)
    assert unescape(escaped) == hostile


def test_plain_handles_non_strings_and_ordinary_text():
    import engineering_ui
    assert engineering_ui._plain(None) == 'None'
    assert engineering_ui._plain('Length must be ten times width') == 'Length must be ten times width'
    assert engineering_ui._plain('a\\b') == 'a\\\\b'
