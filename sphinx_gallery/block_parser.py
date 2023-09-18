"""BlockParser divides source files into blocks of code and markup text."""
import ast
import codecs
from pathlib import Path
import pygments.lexers
import pygments.token
import re
from textwrap import dedent

from sphinx.errors import ExtensionError
from sphinx.util.logging import getLogger

logger = getLogger("sphinx-gallery")

# Don't just use "x in pygments.token.Comment" because it also includes preprocessor
# statements
COMMENT_TYPES = (
    pygments.token.Comment.Single,
    pygments.token.Comment,
    pygments.token.Comment.Multiline,
)


class BlockParser:
    """
    A parser that breaks a source file into blocks of code and markup text.

    Determines the source language and identifies comment blocks using pygments.

    Parameters
    ----------
    source_file : str
        A file name that has a suffix compatible with files that are subsequently parsed
    gallery_conf : dict
        Contains the configuration of Sphinx-Gallery.
    """

    def __init__(self, source_file, gallery_conf):
        source_file = Path(source_file)
        if name := gallery_conf["filetype_parsers"].get(source_file.suffix):
            self.lexer = pygments.lexers.find_lexer_class_by_name(name)()
        else:
            self.lexer = pygments.lexers.find_lexer_class_for_filename(source_file)()
        self.language = self.lexer.name

        # determine valid comment syntaxes
        comment_tests = [
            ("# comment", "#", None),
            ("// comment", "//", None),
            ("/* comment */", r"/\*", r"\*/"),
            ("% comment", "%", None),
            ("! comment", "!", None),
            ("#= comment =#", "#=", "=#"),  # Julia multiline
            ("c     comment", r"^c(?:$|     )", None),
        ]

        self.allowed_comments = []
        self.multiline_end = re.compile(chr(0))  # unmatchable regex
        for test, start, end in comment_tests:
            if next(self.lexer.get_tokens(test))[0] in COMMENT_TYPES:
                self.allowed_comments.append(start)
                if end:
                    self.multiline_end = re.compile(rf"(.*?)\s*{end}")

        if r"/\*" in self.allowed_comments:
            # Remove decorative asterisks from C-style multiline comments
            self.multiline_cleanup = re.compile(r"\s*\*\s*")
        else:
            self.multiline_cleanup = re.compile(r"\s*")

        comment_start = "(?:" + "|".join(self.allowed_comments) + ")"
        self.start_special = re.compile(f"{comment_start} ?%% ?(.*)")
        self.continue_text = re.compile(f"{comment_start} ?(.*)")

        # The pattern for in-file config comments is designed to not greedily match
        # newlines at the start and end, except for one newline at the end. This
        # ensures that the matched pattern can be removed from the code without
        # changing the block structure; i.e. empty newlines are preserved, e.g. in
        #
        #     a = 1
        #
        #     # sphinx_gallery_thumbnail_number = 2
        #
        #     b = 2
        flag_start = rf"^[\ \t]*{comment_start}\s*"

        self.infile_config_pattern = re.compile(
            flag_start + r"sphinx_gallery_([A-Za-z0-9_]+)(\s*=\s*(.+))?[\ \t]*\n?",
            re.MULTILINE,
        )

        self.start_ignore_flag = flag_start + "sphinx_gallery_start_ignore"
        self.end_ignore_flag = flag_start + "sphinx_gallery_end_ignore"
        self.ignore_block_pattern = re.compile(
            rf"{self.start_ignore_flag}(?:[\s\S]*?){self.end_ignore_flag}\n?",
            re.MULTILINE,
        )

    def split_code_and_text_blocks(self, source_file, return_node=False):
        """Return list with source file separated into code and text blocks.

        Parameters
        ----------
        source_file : str
            Path to the source file.
        return_node : bool
            Ignored; returning an ast node is not supported

        Returns
        -------
        file_conf : dict
            File-specific settings given in source file comments as:
            ``# sphinx_gallery_<name> = <value>``
        blocks : list
            (label, content, line_number)
            List where each element is a tuple with the label ('text' or 'code'),
            the corresponding content string of block and the leading line number.
        node : None
            Returning an ast node is not supported.
        """
        with codecs.open(source_file, "r", "utf-8") as fid:
            content = fid.read()
        # change from Windows format to UNIX for uniformity
        content = content.replace("\r\n", "\n")
        return self._split_content(content)

    def _get_content_lines(self, content):
        """
        Combine individual tokens into lines, using the first non-whitespace
        token (if any) as the characteristic token type for the line
        """
        current_line = []
        line_token = pygments.token.Whitespace
        for token, text in self.lexer.get_tokens(content):
            if line_token == pygments.token.Whitespace:
                line_token = token

            if "\n" in text:
                text_lines = text.split("\n")
                # first item belongs to the previous line
                current_line.append(text_lines.pop(0))
                yield line_token, "".join(current_line)
                # last item belongs to the line after this token
                current_line = [text_lines.pop()]
                # Anything left is a block of lines to add directly
                for ln in text_lines:
                    if not ln.strip():
                        line_token = pygments.token.Whitespace
                    yield line_token, ln
                if not current_line[0].strip():
                    line_token = pygments.token.Whitespace
            else:
                current_line.append(text)

    def _get_blocks(self, content):
        """
        Generate a sequence of (label, content, line_number) tuples based on the lines
        in `content`.
        """

        start_text = self.continue_text  # No special delimiter needed for first block

        def cleanup_multiline(lines):
            first_line = 1 if start_text == self.continue_text else 0
            longest = max(len(line) for line in lines)
            matched = False
            for i, line in enumerate(lines[first_line:]):
                if m := self.multiline_cleanup.match(line):
                    matched = True
                    if (n := len(m.group(0))) < len(line):
                        longest = min(longest, n)

            if matched and longest:
                for i, line in enumerate(lines[first_line:], start=first_line):
                    lines[i] = lines[i][longest:]

            return lines

        def finalize_block(mode, block):
            nonlocal start_text
            if mode == "text":
                # subsequent blocks need to have the special delimiter
                start_text = self.start_special

                # Remove leading blank lines, and end in a single newline
                first = 0
                for i, line in enumerate(block):
                    first = i
                    if line.strip():
                        break
                last = None
                for i, line in enumerate(reversed(block)):
                    last = -i or None
                    if line.strip():
                        break
                block.append("")
                text = dedent("\n".join(block[first:last]))
            else:
                text = "\n".join(block)
            return mode, text, n - len(block)

        block = []
        mode = None
        for n, (token, text) in enumerate(self._get_content_lines(content)):
            if mode == "text" and token in pygments.token.Whitespace:
                # Blank line ends current text block
                if block:
                    yield finalize_block(mode, block)
                mode, block = None, []
            elif (
                mode != "text"
                and token in COMMENT_TYPES
                and (m := start_text.search(text))
            ):
                # start of a text block; end the current block
                if block:
                    yield finalize_block(mode, block)
                mode, block = "text", []
                if (trailing_text := m.group(1)) is not None:
                    if start_text == self.continue_text:
                        # Keep any text on the first line of the title block
                        block.append(trailing_text)
                    elif trailing_text.strip():
                        # Warn about text following a "%%" delimiter
                        logger.warning(
                            f"Dropped text on same line as marker: {trailing_text!r}"
                        )
            elif mode == "text" and token in COMMENT_TYPES:
                # Continuation of a text block
                if token == pygments.token.Comment.Multiline:
                    if m := self.multiline_end.search(text):
                        block.append(m.group(1))
                        block = cleanup_multiline(block)
                    else:
                        block.append(text)
                else:
                    block.append(self.continue_text.search(text).group(1))
            elif mode != "code":
                # start of a code block
                if block:
                    yield finalize_block(mode, block)
                mode, block = "code", [text]
            else:
                # continuation of a code block
                block.append(text)

        # end of input ends final block
        if block:
            yield finalize_block(mode, block)

    def _split_content(self, content):
        file_conf = self.extract_file_config(content)
        blocks = list(self._get_blocks(content))
        return file_conf, blocks, None

    def extract_file_config(self, content):
        """Pull out the file-specific config specified in the docstring."""
        file_conf = {}
        for match in re.finditer(self.infile_config_pattern, content):
            name = match.group(1)
            value = match.group(3)
            if value is None:  # a flag rather than a config setting
                continue
            try:
                value = ast.literal_eval(value)
            except (SyntaxError, ValueError):
                logger.warning(
                    "Sphinx-gallery option %s was passed invalid value %s", name, value
                )
            else:
                file_conf[name] = value
        return file_conf

    def remove_ignore_blocks(self, code_block):
        """
        Return the content of *code_block* with ignored areas removed.

        An ignore block starts with ``?? sphinx_gallery_start_ignore`` and ends with
        ``?? sphinx_gallery_end_ignore`` where ``??`` is the active language's line
        comment marker. These lines and anything in between them will be removed, but
        surrounding empty lines are preserved.

        Parameters
        ----------
        code_block : str
            A code segment.
        """
        num_start_flags = len(re.findall(self.start_ignore_flag, code_block))
        num_end_flags = len(re.findall(self.end_ignore_flag, code_block))

        if num_start_flags != num_end_flags:
            raise ExtensionError(
                'All "sphinx_gallery_start_ignore" flags must have a matching '
                '"sphinx_gallery_end_ignore" flag!'
            )
        return re.subn(self.ignore_block_pattern, "", code_block)[0]

    def remove_config_comments(self, code_block):
        """
        Return the content of *code_block* with in-file config comments removed.

        Comment lines with the pattern ``sphinx_gallery_[option] = [val]`` after the
        line comment character are removed, but surrounding empty lines are preserved.

        Parameters
        ----------
        code_block : str
            A code segment.
        """
        parsed_code, _ = re.subn(self.infile_config_pattern, "", code_block)
        return parsed_code
