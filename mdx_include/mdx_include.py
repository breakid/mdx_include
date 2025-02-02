# -*- coding: utf-8 -*-
"""
Include Extension for Python-Markdown
===========================================

Includes local or remote files

See <https://github.com/neurobin/mdx_include> for documentation.

Copyright Md. Jahidul Hamid <jahidulhamid@yahoo.com>

License: [BSD](http://www.opensource.org/licenses/bsd-license.php)

"""
from __future__ import absolute_import
from __future__ import unicode_literals
import markdown
import re
import os
from codecs import open
import pkgutil
import encodings
import logging

try:
    # python 3
    from urllib.parse import urlparse
    from urllib.parse import urlunparse
    from urllib.request import build_opener
    from urllib.request import HTTPRedirectHandler
except ImportError:
    # python 2
    from urlparse import urlparse
    from urlparse import urlunparse
    from urllib2 import HTTPRedirectHandler
    from urllib2 import build_opener
from rcslice import RowSlice
from cyclic import Cyclic
from . import version

__version__ = version.__version__

MARKDOWN_MAJOR = (markdown.__version_info__ if hasattr(markdown, "__version_info__") else markdown.version_info)[0]

logging.basicConfig()
LOGGER_NAME = "mdx_include-" + __version__
log = logging.getLogger(LOGGER_NAME)


def encoding_exists(encoding):
    """Check if an encoding is available in Python"""
    false_positives = set(["aliases"])
    found = set(name for imp, name, ispkg in pkgutil.iter_modules(encodings.__path__) if not ispkg)
    found.difference_update(false_positives)
    if encoding:
        if encoding in found:
            return True
        elif encoding.replace("-", "_") in found:
            return True
    return False


def get_remote_content_list(url, encoding="utf-8"):
    """Follow redirect and return the content"""
    try:
        log.info("Downloading url: " + url)
        return "".join([build_opener(HTTPRedirectHandler).open(url).read().decode(encoding), "\n"]).splitlines(), True
    except Exception as err:
        # catching all exception, this will effectively return empty string
        log.exception("E: Failed to download: " + url)
        return [], False


def get_local_content_list(filename, encoding):
    """Return the file content with status"""
    textl = []
    stat = False
    try:
        with open(filename, "r", encoding=encoding) as f:
            textl = "".join([f.read(), "\n"]).splitlines()
            stat = True
    except Exception as e:
        log.exception("E: Could not find file: {}".format(filename))
    return textl, stat


class IncludeExtension(markdown.Extension):
    """Include Extension class for markdown"""

    def __init__(self, configs={}):
        self.config = {
            "base_path": [".", "Base path from where relative paths are calculated"],
            "encoding": ["utf-8", "Encoding of the files."],
            "allow_local": [True, "Allow including local files."],
            "allow_remote": [True, "Allow including remote files."],
            "truncate_on_failure": [True, "Truncate the include markdown if failed to get the content."],
            "recurs_local": [True, "Whether the inclusion is recursive for local files."],
            "recurs_remote": [False, "Whether the inclusion is recursive for remote files."],
            "syntax_left": [r"\{!", "The left mandatory part of the syntax"],
            "syntax_right": [r"!\}", "The right mandatory part of the syntax"],
            "syntax_delim": [r"\|", "Delemiter used to separate path from encoding"],
            "syntax_recurs_on": ["+", "Character to specify recurs on"],
            "syntax_recurs_off": ["-", "Character to specify recurs off"],
            "syntax_strip_indent": ["<", "Strip indentation common to all included lines"],
            "syntax_apply_indent": [">", "Character to specify apply indentation"],
            "content_cache_local": [True, "Whether to cache content for local files"],
            "content_cache_remote": [True, "Whether to cache content for remote files"],
            "content_cache_clean_local": [
                False,
                "Whether to clean content cache for local files after processing all the includes.",
            ],
            "content_cache_clean_remote": [
                False,
                "Whether to clean content cache for remote files after processing all the includes.",
            ],
            "allow_circular_inclusion": [False, "Whether to allow circular inclusion."],
            "line_slice_separator": [
                ["", ""],
                "A list of lines that will be used to separate parts specified by line slice syntax: 1-2,3-4,5 etc.",
            ],
            "recursive_relative_path": [
                False,
                "Whether include paths inside recursive files should be relative to the parent file path",
            ],
        }

        # ~ super(IncludeExtension, self).__init__(*args, **kwargs)
        # default setConfig does not preserve None when the default config value is a bool (a bug may be or design decision)
        for k, v in configs.items():
            self.setConfig(k, v)

        regex_str = "".join(
            [
                r"(?P<escape>\\)?",
                self.config["syntax_left"][0],
                r"(?P<recursive>[",
                self.config["syntax_recurs_on"][0],
                self.config["syntax_recurs_off"][0],
                r"])?(?P<strip_indent>",
                self.config["syntax_strip_indent"][0],
                r"?)?(?P<apply_indent>",
                self.config["syntax_apply_indent"][0],
                r"?)?\s*(?P<path>[^]|[]+?)(\s*\[ln:(?P<lines>[\d.,-]+)\])?\s*(",
                self.config["syntax_delim"][0],
                r"\s*(?P<encoding>.+?)\s*)?",
                self.config["syntax_right"][0],
            ]
        )
        log.debug(regex_str)
        self.compiled_re = re.compile(regex_str)

    def setConfig(self, key, value):
        """Sets the config key value pair preserving None value and validating the value type."""
        if value is None or isinstance(value, bool):
            if self.config[key][0] is None or isinstance(self.config[key][0], bool):
                pass
            else:
                raise TypeError("E: The type of the value (%s) for the key %s is not correct." % (value, key,))
        else:
            if isinstance(value, type(self.config[key][0])):
                pass
            else:
                raise TypeError("E: The type ({}) of the value ({}) does not match with the required type ({}) for the key {}.".format(type(value), value, type(self.config[key][0]), key))
        self.config[key][0] = value

    def extendMarkdown(self, *args):
        if MARKDOWN_MAJOR == 2:
            args[0].preprocessors.add("mdx_include", IncludePreprocessor(args[0], self.config, self.compiled_re), "_begin")
        else:
            args[0].preprocessors.register(IncludePreprocessor(args[0], self.config, self.compiled_re), "mdx_include", 101)


class IncludePreprocessor(markdown.preprocessors.Preprocessor):
    """
    This provides an "include" function for Markdown. The syntax is {! file_path | encoding !} or
    simply {! file_path !} for default encoding from config params.
    file_path can be a remote URL.
    This is done prior to any other Markdown processing.
    All file names are relative to the location from which Markdown is being called.
    """

    def __init__(self, md, config, compiled_regex):
        md.mdx_include_content_cache_clean_local = self.mdx_include_content_cache_clean_local
        md.mdx_include_content_cache_clean_remote = self.mdx_include_content_cache_clean_remote
        md.mdx_include_get_content_cache_local = self.mdx_include_get_content_cache_local
        md.mdx_include_get_content_cache_remote = self.mdx_include_get_content_cache_remote
        super(IncludePreprocessor, self).__init__(md)
        self.compiled_re = compiled_regex
        self.base_path = config["base_path"][0]
        self.encoding = config["encoding"][0]
        self.allow_local = config["allow_local"][0]
        self.allow_remote = config["allow_remote"][0]
        self.truncate_on_failure = config["truncate_on_failure"][0]
        self.recursive_local = config["recurs_local"][0]
        self.recursive_remote = config["recurs_remote"][0]
        self.syntax_recurs_on = config["syntax_recurs_on"][0]
        self.syntax_recurs_off = config["syntax_recurs_off"][0]
        self.syntax_strip_indent = config["syntax_strip_indent"][0]
        self.syntax_apply_indent = config["syntax_apply_indent"][0]
        self.mdx_include_content_cache_local = {}  # key = file_path_or_url, value = content
        self.mdx_include_content_cache_remote = {}  # key = file_path_or_url, value = content
        self.content_cache_local = config["content_cache_local"][0]
        self.content_cache_remote = config["content_cache_remote"][0]
        self.content_cache_clean_local = config["content_cache_clean_local"][0]
        self.content_cache_clean_remote = config["content_cache_clean_remote"][0]
        self.allow_circular_inclusion = config["allow_circular_inclusion"][0]
        self.line_slice_separator = config["line_slice_separator"][0]
        self.recursive_relative_path = config["recursive_relative_path"][0]

        self.row_slice = RowSlice(self.line_slice_separator)

    def mdx_include_content_cache_clean_local(self):
        """Clean the cache dict for local files"""
        self.mdx_include_content_cache_local = {}

    def mdx_include_content_cache_clean_remote(self):
        """Clean the cache dict for remote files"""
        self.mdx_include_content_cache_remote = {}

    def mdx_include_get_content_cache_local(self):
        """Get the cache dict for local files"""
        return self.mdx_include_content_cache_local

    def mdx_include_get_content_cache_remote(self):
        """Get the cache dict for remote files"""
        return self.mdx_include_content_cache_remote

    def mdx_include_get_cyclic_safe_processed_line_list(self, textl, filename, parent):
        """Returns recursive text list if cyclic inclusion not detected,
        otherwise returns the  unmodified text list if cyclic is allowed,
        otherwise throws exception.
        """
        if not self.cyclic.is_cyclic(filename):
            textl = self.mdx_include_get_processed_lines(textl, filename)
        else:
            if self.allow_circular_inclusion:
                log.warning("Circular inclusion detected in file: " + parent + " when including " + filename + ". Including in non-recursive mode ...")
            else:
                raise RuntimeError("Circular inclusion not allowed; detected in file: " + parent + " when including " + filename + " whose parents are: " + str(self.cyclic.root[filename]))
        return textl

    def get_remote_content_list(self, filename, encoding="utf-8"):
        """Get remote content list from cache or by download"""
        if self.content_cache_remote and filename in self.mdx_include_content_cache_remote:
            textl = self.mdx_include_content_cache_remote[filename]
            stat = True
        else:
            textl, stat = get_remote_content_list(filename, encoding)

            if stat and self.content_cache_remote:
                self.mdx_include_content_cache_remote[filename] = textl

        return textl, stat

    def get_local_content_list(self, filename, encoding):
        """Get local content list from cache or by reading the file"""
        if self.content_cache_local and filename in self.mdx_include_content_cache_local:
            textl = self.mdx_include_content_cache_local[filename]
            stat = True
        else:
            textl, stat = get_local_content_list(filename, encoding)
            if stat and self.content_cache_local:
                self.mdx_include_content_cache_local[filename] = textl
        return textl, stat

    def get_recursive_content_list(self, textl, filename, parent, recursive, recurse_state):
        if recursive:
            if recurse_state != self.syntax_recurs_off:
                textl = self.mdx_include_get_cyclic_safe_processed_line_list(textl, filename, parent)
        elif recursive is None:
            # it's in a neutral position, check recursive state
            if recurse_state == self.syntax_recurs_on:
                textl = self.mdx_include_get_cyclic_safe_processed_line_list(textl, filename, parent)

        return textl

    def mdx_include_get_processed_lines(self, lines, parent):
        """Process each line and return the processed lines"""
        new_lines = []

        for line in lines:
            resll = []
            c = 0  # current offset
            ms = self.compiled_re.finditer(line)

            for m in ms:
                textl = []
                stat = True
                total_match = m.group(0)
                d = m.groupdict()
                escape = d.get("escape")
                apply_indent = d.get("apply_indent")

                if not escape:
                    filename = d.get("path")
                    filename = os.path.expanduser(filename)
                    encoding = d.get("encoding")
                    recurse_state = d.get("recursive")
                    file_lines = d.get("lines")

                    if not encoding_exists(encoding):
                        if encoding:
                            log.warning("W: Encoding (%s) not recognized . Falling back to: %s" % (encoding, self.encoding,))
                        encoding = self.encoding

                    urlo = urlparse(filename)

                    if urlo.netloc:
                        # remote url
                        if self.allow_remote:
                            filename = urlunparse(urlo).rstrip("/")

                            # push the child parent relation
                            self.cyclic.add(filename, parent)

                            # get the content split in lines handling cache
                            textl, stat = self.get_remote_content_list(filename, encoding)

                            # if slice sytax is found, slice the content, we must do it before going recursive because we don't
                            # want to be recursive on unnecessary parts of the file.
                            if file_lines:
                                textl = self.row_slice.slice(textl, file_lines)

                            # We can not cache the whole parsed content after doing all recursive includes
                            # because some files can be included in non-recursive mode. If we just put the recursive
                            # content from cache it won't work.
                            # This if statement must be outside the cache management if statement.
                            textl = self.get_recursive_content_list(textl, filename, parent, self.recursive_remote, recurse_state)
                        else:
                            # If allow_remote and allow_local both is false, then status is false
                            # so that user still have the option to truncate or not, textl is empty now.
                            stat = False
                    elif self.allow_local:
                        # local file
                        if not os.path.isabs(filename):
                            if self.recursive_relative_path and parent:
                                filename = os.path.normpath(os.path.join(os.path.dirname(parent), filename))
                            else:
                                filename = os.path.normpath(os.path.join(self.base_path, filename))

                        # push the child parent relation
                        self.cyclic.add(filename, parent)

                        # get the content split in lines handling cache
                        textl, stat = self.get_local_content_list(filename, encoding)

                        # if slice sytax is found, slice the content, we must do it before going recursive because we don't
                        # want to be recursive on unnecessary parts of the file.
                        if file_lines:
                            textl = self.row_slice.slice(textl, file_lines)

                        # We can not cache the whole parsed content after doing all recursive includes
                        # because some files can be included in non-recursive mode. If we just put the recrsive
                        # content from cache it won't work.
                        # This if statement must be outside the cache management if statement.
                        textl = self.get_recursive_content_list(textl, filename, parent, self.recursive_local, recurse_state)
                    else:
                        # If allow_remote and allow_local both is false, then status is false
                        # so that user still have the option to truncate or not, textl is empty now.
                        stat = False
                else:
                    # this one is escaped, gobble up the escape backslash
                    textl = [total_match[1:]]

                if not stat and not self.truncate_on_failure:
                    # get content failed and user wants to retain the include markdown
                    textl = [total_match]

                s, e = m.span()

                if textl:
                    # textl has at least one element
                    
                    if d.get("strip_indent"):
                        # Find the minimum number of spaces common to every line
                        num_common_spaces = min(len(item) - len(item.lstrip()) for item in textl if item.strip())

                        # Remove common spaces from each line
                        textl = [item[num_common_spaces:] for item in textl]

                    if resll:
                        resll[-1] = "".join([resll[-1], line[c:s], textl[0]])
                        resll.extend(textl[1:])
                    else:
                        if apply_indent != "":
                            resll = ["".join([line[c:s], element]) for element in textl]
                        else:
                            resll.append("".join([line[c:s], textl[0]]))
                            resll.extend(textl[1:])
                else:
                    resll.append(line[c:s])
                # set the current offset to the end offset of this match
                c = e

            # All replacements are done, copy the rest of the string
            if resll:
                resll[-1] = "".join([resll[-1], line[c:]])
            else:
                resll.append(line[c:])

            new_lines.extend(resll)
        return new_lines

    def run(self, lines):
        """Process the list of lines provided and return a modified list"""
        self.cyclic = Cyclic()
        new_lines = self.mdx_include_get_processed_lines(lines, "")

        if self.content_cache_clean_local:
            self.mdx_include_content_cache_clean_local()

        if self.content_cache_clean_remote:
            self.mdx_include_content_cache_clean_remote()

        return new_lines


def makeExtension(*args, **kwargs):  # pragma: no cover
    return IncludeExtension(kwargs)


if __name__ == "__main__":
    import doctest

    doctest.testmod()
