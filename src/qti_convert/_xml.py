"""Small lxml helpers shared by the converters.

The converters think in terms of a list of child nodes (text and elements), like a DOM, while lxml stores text in
``.text`` and ``.tail``. The helpers here translate between the two, so tree edits keep the surrounding text.
"""

from __future__ import annotations

import html.entities
import re
from typing import Callable, Dict, Iterable, List, Optional, Union

from lxml import etree

XML_NAMESPACE = "http://www.w3.org/XML/1998/namespace"
XSI_NAMESPACE = "http://www.w3.org/2001/XMLSchema-instance"

Node = Union[str, etree._Element]
XmlInput = Union[str, bytes]

_PARSER = etree.XMLParser(
    resolve_entities=False, strip_cdata=False, remove_blank_text=False, huge_tree=True, no_network=True
)
_XML_DECLARATION = re.compile(r"^\s*<\?xml\s[^?]*\?>")
_ENTITY = re.compile(r"&([A-Za-z][A-Za-z0-9]*);")
_XML_ENTITIES = {"amp", "lt", "gt", "quot", "apos"}
_HTML_ENTITIES = {name.rstrip(";"): value for name, value in html.entities.html5.items() if name.endswith(";")}


def _html_entities_to_numeric(xml: str) -> str:
    """&nbsp; -> &#160;: QTI 2 exports often contain HTML entities that XML does not define."""

    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        if name in _XML_ENTITIES or name not in _HTML_ENTITIES:
            return match.group(0)
        return "".join(f"&#{ord(char)};" for char in _HTML_ENTITIES[name])

    return _ENTITY.sub(replace, xml)


def to_text(xml: XmlInput) -> str:
    if isinstance(xml, bytes):
        return xml.decode("utf-8-sig", errors="replace")
    return xml


def parse(xml: XmlInput) -> etree._Element:
    """Parses a document (str or bytes) and returns its root element.

    Leading junk and a byte order mark are ignored, and HTML named entities are accepted.
    """
    if isinstance(xml, bytes):
        start = xml.find(b"<")
        data: XmlInput = xml[start:] if start > 0 else xml
        if data.startswith(b"\xef\xbb\xbf"):
            data = data[3:]
    else:
        text = xml.lstrip("﻿")
        start = text.find("<")
        text = text[start:] if start > 0 else text
        # lxml refuses str input with an encoding declaration; the text is already decoded
        data = _XML_DECLARATION.sub("", text, count=1)
    try:
        return etree.fromstring(data, _PARSER)
    except etree.XMLSyntaxError:
        text = to_text(data) if isinstance(data, bytes) else data
        fixed = _html_entities_to_numeric(_XML_DECLARATION.sub("", text, count=1))
        if fixed == text:
            raise
        return etree.fromstring(fixed, _PARSER)


def is_element(node: object) -> bool:
    return isinstance(node, etree._Element) and isinstance(node.tag, str)


def tag_name(el: etree._Element) -> str:
    """The Clark name ({ns}local) of an element; "" for comments and processing instructions."""
    return el.tag if isinstance(el.tag, str) else ""


def local_name(el: etree._Element) -> str:
    return etree.QName(el).localname if is_element(el) else ""


def namespace(el: etree._Element) -> Optional[str]:
    return etree.QName(el).namespace if is_element(el) else None


def attr_local(name: str) -> str:
    return name.rsplit("}", 1)[-1]


def elements(el: etree._Element) -> List[etree._Element]:
    return [child for child in el if is_element(child)]


def descendants(el: etree._Element) -> List[etree._Element]:
    return [node for node in el.iterdescendants() if is_element(node)]


def iter_by_local_name(root: etree._Element, *names: str) -> List[etree._Element]:
    wanted = set(names)
    return [el for el in root.iter() if is_element(el) and local_name(el) in wanted]


def text_content(el: etree._Element) -> str:
    """All text inside an element, like DOM textContent (comments excluded)."""
    parts = [el.text or ""] if is_element(el) else []
    for child in el:
        if is_element(child):
            parts.append(text_content(child))
        parts.append(child.tail or "")
    return "".join(parts)


def nodes(el: etree._Element) -> List[Node]:
    """The child nodes of an element in document order; text (including tails) as separate strings."""
    result: List[Node] = []
    if el.text:
        result.append(el.text)
    for child in el:
        result.append(child)
        if child.tail:
            result.append(child.tail)
    return result


def append_text(parent: etree._Element, text: str) -> None:
    if not text:
        return
    if len(parent):
        last = parent[-1]
        last.tail = (last.tail or "") + text
    else:
        parent.text = (parent.text or "") + text


def append_nodes(parent: etree._Element, items: Iterable[Node]) -> None:
    """Appends text and (moved) elements; element tails are dropped as they are separate items."""
    for item in list(items):
        if isinstance(item, str):
            append_text(parent, item)
        else:
            item.tail = None
            parent.append(item)


def _insert_nodes(parent: etree._Element, index: int, items: List[Node]) -> None:
    previous = parent[index - 1] if index > 0 else None
    for item in items:
        if isinstance(item, str):
            if previous is None:
                parent.text = (parent.text or "") + item
            else:
                previous.tail = (previous.tail or "") + item
        else:
            item.tail = None
            parent.insert(index, item)
            previous = item
            index += 1


def replace_with(el: etree._Element, items: List[Node]) -> None:
    """Replaces an element by nodes; its tail stays in place."""
    parent = el.getparent()
    if parent is None:
        raise ValueError(f"<{local_name(el)}> has no parent to be replaced in")
    index = parent.index(el)
    tail = el.tail
    parent.remove(el)
    _insert_nodes(parent, index, list(items) + ([tail] if tail else []))


def remove(el: etree._Element) -> None:
    """Removes an element, keeping the text that follows it."""
    replace_with(el, [])


def unwrap(el: etree._Element) -> None:
    replace_with(el, nodes(el))


def insert_before(el: etree._Element, new: etree._Element) -> None:
    new.tail = None
    el.addprevious(new)


def remove_leading_whitespace(el: etree._Element) -> None:
    """Removes the whitespace-only text right before an element."""
    previous = el.getprevious()
    if previous is not None:
        if previous.tail is not None and not previous.tail.strip():
            previous.tail = None
    else:
        parent = el.getparent()
        if parent is not None and parent.text is not None and not parent.text.strip():
            parent.text = None


def prefix_for(el: etree._Element, uri: str) -> Optional[str]:
    for prefix, value in el.nsmap.items():
        if value == uri and prefix is not None:
            return prefix
    return None


TagMapper = Callable[[etree._Element], str]


def rebuild(
    src: etree._Element,
    tag_for: TagMapper,
    root_nsmap: Dict[Optional[str], str],
    root_attributes: Optional[List[tuple]] = None,
    prefix_hints: Optional[Dict[str, str]] = None,
) -> etree._Element:
    """Deep-copies a tree with new element names and fresh namespace declarations.

    ``tag_for`` returns the Clark name ({ns}local) of the copy of an element. Elements that change namespace get a
    default namespace declaration, unless the source element or ``prefix_hints`` has a prefix for it.
    """
    hints = prefix_hints or {}

    def attribute_nsmap(el: etree._Element, attributes: List[tuple], in_scope: Dict[Optional[str], str]):
        extra: Dict[Optional[str], str] = {}
        for name, _ in attributes:
            if not name.startswith("{"):
                continue
            uri = name[1:].split("}", 1)[0]
            if uri == XML_NAMESPACE or uri in in_scope.values() or uri in extra.values():
                continue
            prefix = hints.get(uri) or prefix_for(el, uri) or ("xsi" if uri == XSI_NAMESPACE else None)
            if prefix and prefix not in in_scope and prefix not in extra:
                extra[prefix] = uri
        return extra

    def copy(el: etree._Element, parent: Optional[etree._Element], attributes: Optional[List[tuple]] = None):
        if not is_element(el):
            node = _copy_special(el)
            if parent is not None and node is not None:
                parent.append(node)
            return node
        tag = tag_for(el)
        uri = etree.QName(tag).namespace
        attrs = attributes if attributes is not None else list(el.attrib.items())
        if parent is None:
            nsmap: Dict[Optional[str], str] = dict(root_nsmap)
            in_scope: Dict[Optional[str], str] = {}
        else:
            nsmap = {}
            in_scope = dict(parent.nsmap)
            # a namespace that is in scope (as default or with a prefix) is reused by lxml
            if uri and uri not in in_scope.values():
                prefix = hints.get(uri) or (el.prefix if etree.QName(el).namespace == uri else None)
                nsmap[prefix if prefix and prefix not in in_scope else None] = uri
        nsmap.update(attribute_nsmap(el, attrs, {**in_scope, **nsmap}))
        new = etree.Element(tag, nsmap=nsmap) if parent is None else etree.SubElement(parent, tag, nsmap=nsmap)
        for name, value in attrs:
            new.set(name, value)
        new.text = el.text
        for child in el:
            copied = copy(child, new)
            if copied is not None:
                copied.tail = child.tail
            else:
                append_text(new, child.tail or "")
        return new

    output = copy(src, None, root_attributes)
    assert output is not None
    return output


def _copy_special(node: etree._Element):
    if isinstance(node, etree._Comment):
        return etree.Comment(node.text or "")
    if isinstance(node, etree._ProcessingInstruction):
        return etree.ProcessingInstruction(node.target, node.text)
    if isinstance(node, etree._Entity):
        return etree.Entity(node.name)
    return None


def root_siblings(root: etree._Element):
    """Comments and processing instructions before and after the root element."""
    before = list(reversed(list(root.itersiblings(preceding=True))))
    after = list(root.itersiblings())
    return before, after


def add_root_siblings(root: etree._Element, before: List[etree._Element], after: List[etree._Element]) -> None:
    for node in before:
        root.addprevious(node)
    for node in reversed(after):
        root.addnext(node)


def serialize_document(root: etree._Element, declaration: bool = True) -> str:
    """The root with the comments and processing instructions around it, one top-level node per line."""
    before, after = root_siblings(root)
    body = "\n".join(serialize(node) for node in [*before, root, *after])
    return f'<?xml version="1.0" encoding="UTF-8"?>\n{body}' if declaration else body


def serialize(el: etree._Element) -> str:
    return etree.tostring(el, encoding="unicode", with_tail=False)
