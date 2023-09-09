"""test BlockParser."""
import os
import pytest
import tempfile
from textwrap import dedent

from sphinx_gallery.block_parser import BlockParser
from sphinx_gallery.gen_gallery import DEFAULT_GALLERY_CONF

CXX_BODY = dedent(
    """
    int x = 3;
    return;
"""
)


@pytest.mark.parametrize(
    "comment, expected_docstring",
    [
        pytest.param(
            dedent(
                """\
                // Title
                // =====
                //
                // description
            """
            ),
            dedent(
                """\
                Title
                =====

                description
            """
            ),
            id="single-line style",
        ),
        # A simple multiline header
        pytest.param(
            dedent(
                """\
                /*
                   Title
                   =====
                 */
                """
            ),
            dedent(
                """\
                Title
                =====
                """
            ),
            id="simple multiline",
        ),
        # A multiline comment with aligned decorations on intermediate lines
        pytest.param(
            dedent(
                """\
                /*
                 * Title
                 * =====
                 */
                """
            ),
            dedent(
                """\
                Title
                =====
                """
            ),
            id="decorated-multiline",
        ),
        # A multiline comment that starts on the same line as the start symbol
        pytest.param(
            dedent(
                """\
                /* Title
                 * =====
                 *
                 */
                """
            ),
            dedent(
                """\
                Title
                =====
                """
            ),
            id="early-multiline",
        ),
    ],
)
def test_cxx_titles(comment, expected_docstring):
    doc = comment + CXX_BODY
    parser = BlockParser("*.cpp", DEFAULT_GALLERY_CONF)
    file_conf, blocks, _ = parser._split_content(doc)

    assert len(blocks) == 2
    assert blocks[0][0] == "text"
    assert blocks[0][1] == expected_docstring


@pytest.mark.parametrize(
    "filetype, title, special, expected",
    [
        pytest.param(
            "*.py",
            """# Title""",
            """# %% Single-line comment""",
            "Single-line comment\n",
            id="single-line-simple",
        ),
        pytest.param(
            "*.f90",
            """! Title""",
            """     ! %% Indented single-line comment""",
            "Indented single-line comment\n",
            id="single-line-indented",
        ),
        pytest.param(
            "*.cpp",
            """// Title""",
            (
                "     // %%\n"
                "     // First comment line\n"
                "     // Second comment line\n"
            ),
            ("First comment line\n" "Second comment line\n"),
            id="indented-separate-sentinel",
        ),
        pytest.param(
            "*.cs",
            """// Title""",
            (
                "     //%% Indented multi-line comment\n"
                "     // continued on a second line\n"
            ),
            ("Indented multi-line comment\n" "continued on a second line\n"),
            id="block-from-single-lines",
        ),
        pytest.param(
            "*.c",
            """/* Title */""",
            (
                "     /* %% Indented multi-line comment\n"
                "        continued on a second line */\n"
            ),
            ("Indented multi-line comment\n" "continued on a second line\n"),
            id="multiline-comment-short-form",
        ),
        pytest.param(
            "*.c",
            """/* Title */""",
            (
                "     /*%% * List item\n"
                "      * * Another item\n"
                "      * * Item 3\n"
                "      */"
            ),
            ("* List item\n" "* Another item\n" "* Item 3\n"),
            id="multiline-comment-short-form",
        ),
    ],
)
def test_rst_blocks(filetype, title, special, expected):
    doc = f"{title}\n{CXX_BODY}\n\n{special}\n{CXX_BODY}"
    parser = BlockParser(filetype, DEFAULT_GALLERY_CONF)
    file_conf, blocks, _ = parser._split_content(doc)

    assert len(blocks) == 4
    assert blocks[2][0] == "text"
    assert blocks[2][1] == expected


def test_cpp_file_to_rst():
    CODE = """\
// Do stuff
// --------

int main(int argc, char** argv) {
    //%% This is the start of ``main``

    // This is just a comment because of the preceding blank line
    int y = 4;

    // sphinx_gallery_start_ignore
    y = 5; // don't look: this is a secret!
    // sphinx_gallery_end_ignore
    if (y == 4) {
        return 1;
    }
    // sphinx_gallery_foobar = 14
}
"""
    with tempfile.NamedTemporaryFile("wb", suffix=".cpp", delete=False) as f:
        f.write(CODE.encode())

    try:
        parser = BlockParser(f.name, DEFAULT_GALLERY_CONF)
        file_conf, blocks, _ = parser.split_code_and_text_blocks(f.name)
    finally:
        os.remove(f.name)

    assert parser.language == "C++"
    assert file_conf == {"foobar": 14}

    assert "// This is just a comment" in blocks[3][1]
    assert "secret" in blocks[3][1]

    cleaned = parser.remove_ignore_blocks(blocks[3][1])
    assert "secret" not in cleaned
    assert "y == 4" in cleaned
