# SPDX-License-Identifier: LGPL-3.0-only

"""Functions to publish documents and items."""

import os
import tempfile
import textwrap

import bottle
import markdown
from bottle import template as bottle_template
from plantuml_markdown import PlantUMLMarkdownExtension

from doorstop import common, settings
from doorstop.cli import utilities
from doorstop.common import DoorstopError
from doorstop.core.publisher_latex import (
    _generate_latex_wrapper,
    _get_compile_path,
    _lines_latex,
    _matrix_latex,
)
from doorstop.core.template import CSS, HTMLTEMPLATE, INDEX, MATRIX, get_template
from doorstop.core.types import is_item, is_tree, iter_documents, iter_items

EXTENSIONS = (
    "markdown.extensions.extra",
    "markdown.extensions.sane_lists",
    PlantUMLMarkdownExtension(
        server="http://www.plantuml.com/plantuml",
        cachedir=tempfile.gettempdir(),
        format="svg",
        classes="class1,class2",
        title="UML",
        alt="UML Diagram",
    ),
)

log = common.logger(__name__)


def publish(
    obj,
    path,
    ext=None,
    linkify=None,
    index=None,
    matrix=None,
    template=None,
    toc=True,
    **kwargs,
):
    """Publish an object to a given format.

    The function can be called in two ways:

    1. document or item-like object + output file path
    2. tree-like object + output directory path

    :param obj: (1) Item, list of Items, Document or (2) Tree
    :param path: (1) output file path or (2) output directory path
    :param ext: file extension to override output extension
    :param linkify: turn links into hyperlinks (for Markdown or HTML)
    :param index: create an index.html (for HTML)
    :param matrix: create a traceability matrix, traceability.csv

    :raises: :class:`doorstop.common.DoorstopError` for unknown file formats

    :return: output location if files created, else None

    """
    # Determine the output format
    ext = ext or os.path.splitext(path)[-1] or ".html"
    check(ext)
    if linkify is None:
        linkify = is_tree(obj) and ext in [".html", ".md", ".tex"]
    if index is None:
        index = is_tree(obj) and ext == ".html"
    if matrix is None:
        matrix = is_tree(obj)

    # Process templates.
    assets_dir, template = get_template(obj, path, ext, template)

    # Publish documents
    count = 0
    if ext == ".tex":
        compile_files = []
        compile_path = ""
    for obj2, path2 in iter_documents(obj, path, ext):
        count += 1
        # Publish wrapper files for LaTeX.
        if ext == ".tex":
            log.debug("Generating compile script for LaTeX from %s", path2)
            if count == 1:
                compile_path = _get_compile_path(path2)
            path2, file_to_compile = _generate_latex_wrapper(
                obj2, path2, assets_dir, template, matrix, count, obj, path
            )
            compile_files.append(file_to_compile)

        # Publish content to the specified path
        log.info("publishing to {}...".format(path2))
        lines = publish_lines(
            obj2, ext, linkify=linkify, template=template, toc=toc, **kwargs
        )
        common.write_lines(lines, path2, end=settings.WRITE_LINESEPERATOR)
        if obj2.copy_assets(assets_dir):
            log.info("Copied assets from %s to %s", obj.assets, assets_dir)

    if ext == ".tex":
        common.write_lines(
            compile_files,
            compile_path,
            end=settings.WRITE_LINESEPERATOR,
            executable=True,
        )
        msg = "You can now execute the file 'compile.sh' twice in the exported folder to produce the PDFs!"
        utilities.show(msg, flush=True)

    # Create index
    if index and count:
        _index(path, tree=obj if is_tree(obj) else None)

    # Create traceability matrix
    if (index or ext == ".tex") and (matrix and count):
        _matrix(
            path, tree=obj if is_tree(obj) else None, ext=ext if ext == ".tex" else None
        )

    # Return the published path
    if count:
        msg = "published to {} file{}".format(count, "s" if count > 1 else "")
        log.info(msg)
        return path
    else:
        log.warning("nothing to publish")
        return None


def _index(directory, index=INDEX, extensions=(".html",), tree=None):
    """Create an HTML index of all files in a directory.

    :param directory: directory for index
    :param index: filename for index
    :param extensions: file extensions to include
    :param tree: optional tree to determine index structure

    """
    # Get paths for the index index
    filenames = []
    for filename in os.listdir(directory):
        if filename.endswith(extensions) and filename != INDEX:
            filenames.append(os.path.join(filename))

    # Create the index
    if filenames:
        path = os.path.join(directory, index)
        log.info("creating an {}...".format(index))
        lines = _lines_index(sorted(filenames), tree=tree)
        common.write_lines(lines, path, end=settings.WRITE_LINESEPERATOR)
    else:
        log.warning("no files for {}".format(index))


def _lines_index(filenames, charset="UTF-8", tree=None):
    """Yield lines of HTML for index.html.

    :param filesnames: list of filenames to add to the index
    :param charset: character encoding for output
    :param tree: optional tree to determine index structure

    """
    yield "<!DOCTYPE html>"
    yield "<head>"
    yield (
        '<meta http-equiv="content-type" content="text/html; '
        'charset={charset}">'.format(charset=charset)
    )
    yield '<style type="text/css">'
    yield from _lines_css()
    yield "</style>"
    yield "</head>"
    yield "<body>"
    # Tree structure
    text = tree.draw() if tree else None
    if text:
        yield ""
        yield "<h3>Tree Structure:</h3>"
        yield "<pre><code>" + text + "</pre></code>"
    # Additional files
    if filenames:
        if text:
            yield ""
            yield "<hr>"
        yield ""
        yield "<h3>Published Documents:</h3>"
        yield "<p>"
        yield "<ul>"
        for filename in filenames:
            name = os.path.splitext(filename)[0]
            yield '<li> <a href="{f}">{n}</a> </li>'.format(f=filename, n=name)
        yield "</ul>"
        yield "</p>"
    # Traceability table
    documents = tree.documents if tree else None
    if documents:
        if text or filenames:
            yield ""
            yield "<hr>"
        yield ""
        # table
        yield "<h3>Item Traceability:</h3>"
        yield "<p>"
        yield "<table>"
        # header
        for document in documents:  # pylint: disable=not-an-iterable
            yield '<col width="100">'
        yield "<tr>"
        for document in documents:  # pylint: disable=not-an-iterable
            link = '<a href="{p}.html">{p}</a>'.format(p=document.prefix)
            yield ('  <th height="25" align="center"> {link} </th>'.format(link=link))
        yield "</tr>"
        # data
        for index, row in enumerate(tree.get_traceability()):
            if index % 2:
                yield '<tr class="alt">'
            else:
                yield "<tr>"
            for item in row:
                if item is None:
                    link = ""
                else:
                    link = _format_html_item_link(item)
                yield '  <td height="25" align="center"> {} </td>'.format(link)
            yield "</tr>"
        yield "</table>"
        yield "</p>"
    yield ""
    yield "</body>"
    yield "</html>"


def _lines_css():
    """Yield lines of CSS to embedded in HTML."""
    yield ""
    for line in common.read_lines(CSS):
        yield line.rstrip()
    yield ""


def _matrix(directory, tree, filename=MATRIX, ext=None):
    """Create a traceability matrix for all the items.

    :param directory: directory for matrix
    :param tree: tree to access the traceability data
    :param filename: filename for matrix
    :param ext: file extensionto use for the matrix

    """
    # Get path and format extension
    path = os.path.join(directory, filename)
    ext = ext or os.path.splitext(path)[-1] or ".csv"

    # Create the matrix
    if tree:
        log.info("creating an {}...".format(filename))
        content = _matrix_content(tree)
        if ext == ".tex":
            _matrix_latex(content, path)
        else:
            common.write_csv(content, path)
    else:
        log.warning("no data for {}".format(filename))


def _extract_prefix(document):
    if document:
        return document.prefix
    else:
        return None


def _extract_uid(item):
    if item:
        return item.uid
    else:
        return None


def _matrix_content(tree):
    """Yield rows of content for the traceability matrix."""
    yield tuple(map(_extract_prefix, tree.documents))
    for row in tree.get_traceability():
        yield tuple(map(_extract_uid, row))


def publish_lines(obj, ext=".txt", **kwargs):
    """Yield lines for a report in the specified format.

    :param obj: Item, list of Items, or Document to publish
    :param ext: file extension to specify the output format

    :raises: :class:`doorstop.common.DoorstopError` for unknown file formats

    """
    gen = check(ext)
    log.debug("yielding {} as lines of {}...".format(obj, ext))
    yield from gen(obj, **kwargs)


def _lines_text(obj, indent=8, width=79, **_):
    """Yield lines for a text report.

    :param obj: Item, list of Items, or Document to publish
    :param indent: number of spaces to indent text
    :param width: maximum line length

    :return: iterator of lines of text

    """
    for item in iter_items(obj):
        level = _format_level(item.level)

        if item.heading:
            text_lines = item.text.splitlines()
            if item.header:
                text_lines.insert(0, item.header)
            text = os.linesep.join(text_lines)
            # Level and Text
            if settings.PUBLISH_HEADING_LEVELS:
                yield "{lev:<{s}}{t}".format(lev=level, s=indent, t=text)
            else:
                yield "{t}".format(t=text)

        else:
            # Level and UID
            if item.header:
                yield "{lev:<{s}}{u} {header}".format(
                    lev=level, s=indent, u=item.uid, header=item.header
                )
            else:
                yield "{lev:<{s}}{u}".format(lev=level, s=indent, u=item.uid)

            # Text
            if item.text:
                yield ""  # break before text
                for line in item.text.splitlines():
                    yield from _chunks(line, width, indent)

                    if not line:
                        yield ""  # break between paragraphs

            # Reference
            if item.ref:
                yield ""  # break before reference
                ref = _format_text_ref(item)
                yield from _chunks(ref, width, indent)

            # References
            if item.references:
                yield ""  # break before references
                ref = _format_text_references(item)
                yield from _chunks(ref, width, indent)

            # Links
            if item.links:
                yield ""  # break before links
                if settings.PUBLISH_CHILD_LINKS:
                    label = "Parent links: "
                else:
                    label = "Links: "
                slinks = label + ", ".join(str(l) for l in item.links)
                yield from _chunks(slinks, width, indent)
            if settings.PUBLISH_CHILD_LINKS:
                links = item.find_child_links()
                if links:
                    yield ""  # break before links
                    slinks = "Child links: " + ", ".join(str(l) for l in links)
                    yield from _chunks(slinks, width, indent)

            if item.document and item.document.publish:
                yield ""
                for attr in item.document.publish:
                    if not item.attribute(attr):
                        continue
                    attr_line = "{}: {}".format(attr, item.attribute(attr))
                    yield from _chunks(attr_line, width, indent)

        yield ""  # break between items


def _chunks(text, width, indent):
    """Yield wrapped lines of text."""
    yield from textwrap.wrap(
        text, width, initial_indent=" " * indent, subsequent_indent=" " * indent
    )


def _lines_markdown(obj, **kwargs):
    """Yield lines for a Markdown report.

    :param obj: Item, list of Items, or Document to publish
    :param linkify: turn links into hyperlinks (for conversion to HTML)

    :return: iterator of lines of text

    """
    linkify = kwargs.get("linkify", False)
    to_html = kwargs.get("to_html", False)
    for item in iter_items(obj):
        heading = "#" * item.depth
        level = _format_level(item.level)

        if item.heading:
            text_lines = item.text.splitlines()
            if item.header:
                text_lines.insert(0, item.header)
            # Level and Text
            if settings.PUBLISH_HEADING_LEVELS:
                standard = "{h} {lev} {t}".format(
                    h=heading, lev=level, t=text_lines[0] if text_lines else ""
                )
            else:
                standard = "{h} {t}".format(
                    h=heading, t=text_lines[0] if text_lines else ""
                )
            attr_list = _format_md_attr_list(item, True)
            yield standard + attr_list
            yield from text_lines[1:]
        else:
            uid = item.uid
            if settings.ENABLE_HEADERS:
                if item.header:
                    uid = "{h} <small>{u}</small>".format(h=item.header, u=item.uid)
                else:
                    uid = "{u}".format(u=item.uid)

            # Level and UID
            if settings.PUBLISH_BODY_LEVELS:
                standard = "{h} {lev} {u}".format(h=heading, lev=level, u=uid)
            else:
                standard = "{h} {u}".format(h=heading, u=uid)

            attr_list = _format_md_attr_list(item, True)
            yield standard + attr_list

            # Text
            if item.text:
                yield ""  # break before text
                yield from item.text.splitlines()

            # Reference
            if item.ref:
                yield ""  # break before reference
                yield _format_md_ref(item)

            # Reference
            if item.references:
                yield ""  # break before reference
                yield _format_md_references(item)

            # Parent links
            if item.links:
                yield ""  # break before links
                items2 = item.parent_items
                if settings.PUBLISH_CHILD_LINKS:
                    label = "Parent links:"
                else:
                    label = "Links:"
                links = _format_md_links(items2, linkify, to_html=to_html)
                label_links = _format_md_label_links(label, links, linkify)
                yield label_links

            # Child links
            if settings.PUBLISH_CHILD_LINKS:
                items2 = item.find_child_items()
                if items2:
                    yield ""  # break before links
                    label = "Child links:"
                    links = _format_md_links(items2, linkify, to_html=to_html)
                    label_links = _format_md_label_links(label, links, linkify)
                    yield label_links

            # Add custom publish attributes
            if item.document and item.document.publish:
                header_printed = False
                for attr in item.document.publish:
                    if not item.attribute(attr):
                        continue
                    if not header_printed:
                        header_printed = True
                        yield ""
                        yield "| Attribute | Value |"
                        yield "| --------- | ----- |"
                    yield "| {} | {} |".format(attr, item.attribute(attr))
                yield ""

        yield ""  # break between items


def _format_level(level):
    """Convert a level to a string and keep zeros if not a top level."""
    text = str(level)
    if text.endswith(".0") and len(text) > 3:
        text = text[:-2]
    return text


def _format_md_attr_list(item, linkify):
    """Create a Markdown attribute list for a heading."""
    return " {{#{u} }}".format(u=item.uid) if linkify else ""


def _format_text_ref(item):
    """Format an external reference in text."""
    if settings.CHECK_REF:
        path, line = item.find_ref()
        path = path.replace("\\", "/")  # always use unix-style paths
        if line:
            return "Reference: {p} (line {line})".format(p=path, line=line)
        else:
            return "Reference: {p}".format(p=path)
    else:
        return "Reference: '{r}'".format(r=item.ref)


def _format_text_references(item):
    """Format an external reference in text."""
    if settings.CHECK_REF:
        ref = item.find_references()
        text_refs = []
        for ref_item in ref:
            path, line = ref_item
            path = path.replace("\\", "/")  # always use unix-style paths
            if line:
                text_refs.append("{p} (line {line})".format(p=path, line=line))
            else:
                text_refs.append("{p}".format(p=path))
        return "Reference: {}".format(", ".join(ref for ref in text_refs))
    else:
        references = item.references
        text_refs = []
        for ref_item in references:
            path = ref_item["path"]
            path = path.replace("\\", "/")  # always use unix-style paths
            text_refs.append("'{p}'".format(p=path))
        return "Reference: {}".format(", ".join(text_ref for text_ref in text_refs))


def _format_md_ref(item):
    """Format an external reference in Markdown."""
    if settings.CHECK_REF:
        path, line = item.find_ref()
        path = path.replace("\\", "/")  # always use unix-style paths
        if line:
            return "> `{p}` (line {line})".format(p=path, line=line)
        else:
            return "> `{p}`".format(p=path)
    else:
        return "> '{r}'".format(r=item.ref)


def _format_md_references(item):
    """Format an external reference in Markdown."""
    if settings.CHECK_REF:
        references = item.find_references()
        text_refs = []
        for ref_item in references:
            path, line = ref_item
            path = path.replace("\\", "/")  # always use unix-style paths

            if line:
                text_refs.append("> `{p}` (line {line})".format(p=path, line=line))
            else:
                text_refs.append("> `{p}`".format(p=path))

        return "\n".join(ref for ref in text_refs)
    else:
        references = item.references
        text_refs = []
        for ref_item in references:
            path = ref_item["path"]
            path = path.replace("\\", "/")  # always use unix-style paths
            text_refs.append("> '{r}'".format(r=path))
        return "\n".join(ref for ref in text_refs)


def _format_md_links(items, linkify, to_html=False):
    """Format a list of linked items in Markdown."""
    links = []
    for item in items:
        if to_html:
            link = _format_html_item_link(item, linkify=linkify)
        else:
            link = _format_md_item_link(item, linkify=linkify)
        links.append(link)
    return ", ".join(links)


def _format_md_item_link(item, linkify=True):
    """Format an item link in Markdown."""
    if linkify and is_item(item):
        if item.header:
            return "[{u} {h}]({p}.md#{u})".format(
                u=item.uid, h=item.header, p=item.document.prefix
            )
        return "[{u}]({p}.md#{u})".format(u=item.uid, p=item.document.prefix)
    else:
        return str(item.uid)  # if not `Item`, assume this is an `UnknownItem`


def _format_html_item_link(item, linkify=True):
    """Format an item link in HTML."""
    if linkify and is_item(item):
        if item.header:
            link = '<a href="{p}.html#{u}">{u} {h}</a>'.format(
                u=item.uid, h=item.header, p=item.document.prefix
            )
        else:
            link = '<a href="{p}.html#{u}">{u}</a>'.format(
                u=item.uid, p=item.document.prefix
            )
        return link
    else:
        return str(item.uid)  # if not `Item`, assume this is an `UnknownItem`


def _format_md_label_links(label, links, linkify):
    """Join a string of label and links with formatting."""
    if linkify:
        return "*{lb}* {ls}".format(lb=label, ls=links)
    else:
        return "*{lb} {ls}*".format(lb=label, ls=links)


def _table_of_contents_md(obj, linkify=None):
    toc = "### Table of Contents\n\n"

    for item in iter_items(obj):
        if item.depth == 1:
            prefix = " * "
        else:
            prefix = "    " * (item.depth - 1)
            prefix += "* "

        if item.heading:
            lines = item.text.splitlines()
            if item.header:
                heading = item.header
            else:
                heading = lines[0] if lines else ""
        elif item.header:
            heading = "{h}".format(h=item.header)
        else:
            heading = item.uid

        if settings.PUBLISH_HEADING_LEVELS:
            level = _format_level(item.level)
            lbl = "{lev} {h}".format(lev=level, h=heading)
        else:
            lbl = heading

        if linkify:
            line = "{p}[{lbl}](#{uid})\n".format(p=prefix, lbl=lbl, uid=item.uid)
        else:
            line = "{p}{lbl}\n".format(p=prefix, lbl=lbl)
        toc += line
    return toc


def _lines_html(
    obj, linkify=False, extensions=EXTENSIONS, template=HTMLTEMPLATE, toc=True
):
    """Yield lines for an HTML report.

    :param obj: Item, list of Items, or Document to publish
    :param linkify: turn links into hyperlinks

    :return: iterator of lines of text

    """
    # Determine if a full HTML document should be generated
    try:
        iter(obj)
    except TypeError:
        document = False
    else:
        document = True
    # Generate HTML

    text = "\n".join(_lines_markdown(obj, linkify=linkify, to_html=True))
    body = markdown.markdown(text, extensions=extensions)

    if toc:
        toc_md = _table_of_contents_md(obj, True)
        toc_html = markdown.markdown(toc_md, extensions=extensions)
    else:
        toc_html = ""

    if document:
        try:
            bottle.TEMPLATE_PATH.insert(
                0, os.path.join(os.path.dirname(__file__), "..", "views")
            )
            if "baseurl" not in bottle.SimpleTemplate.defaults:
                bottle.SimpleTemplate.defaults["baseurl"] = ""
            html = bottle_template(
                template, body=body, toc=toc_html, parent=obj.parent, document=obj
            )
        except Exception:
            log.error("Problem parsing the template %s", template)
            raise
        yield "\n".join(html.split(os.linesep))
    else:
        yield body


# Mapping from file extension to lines generator
FORMAT_LINES = {
    ".txt": _lines_text,
    ".md": _lines_markdown,
    ".html": _lines_html,
    ".tex": _lines_latex,
}


def check(ext):
    """Confirm an extension is supported for publish.

    :raises: :class:`doorstop.common.DoorstopError` for unknown formats

    :return: lines generator if available

    """
    exts = ", ".join(ext for ext in FORMAT_LINES)
    msg = "unknown publish format: {} (options: {})".format(ext or None, exts)
    exc = DoorstopError(msg)

    try:
        gen = FORMAT_LINES[ext]
    except KeyError:
        raise exc from None
    else:
        log.debug("found lines generator for: {}".format(ext))
        return gen
