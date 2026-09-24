"""Namespace-aware canonical forms of XML documents for comparisons in tests."""

from __future__ import annotations

import re

from lxml import etree

from qti_convert._xml import is_element, parse


def canonical(xml):
    """Expanded names, attributes without namespace declarations, collapsed text; comments and PIs included."""
    root = parse(xml)

    def text(value):
        return re.sub(r"\s+", " ", value).strip() if value and value.strip() else None

    def walk(node):
        if isinstance(node, etree._Comment):
            return f"<!--{node.text}-->"
        if isinstance(node, etree._ProcessingInstruction):
            return re.sub(r"\s+", " ", f"?{node.target} {node.text or ''}".strip()) + "?"
        if not is_element(node):
            return None
        children = []
        if text(node.text):
            children.append(text(node.text))
        for child in node:
            walked = walk(child)
            if walked is not None:
                children.append(walked)
            if text(child.tail):
                children.append(text(child.tail))
        return {"name": node.tag, "attrs": dict(node.attrib), "children": children}

    before = list(reversed(list(root.itersiblings(preceding=True))))
    return [walk(n) for n in before] + [walk(root)] + [walk(n) for n in root.itersiblings()]


def structure(xml):
    """Element structure, text and attributes, ignoring namespaces and schema locations (like xml2js in the TS tests)."""
    root = parse(xml)

    def walk(el):
        attrs = {k.rsplit("}", 1)[-1]: v for k, v in el.attrib.items() if not k.endswith("schemaLocation")}
        text = re.sub(r"\s+", " ", el.text or "").strip()
        tails = [re.sub(r"\s+", " ", c.tail or "").strip() for c in el]
        return (
            etree.QName(el).localname,
            attrs,
            " ".join(t for t in [text, *tails] if t),
            [walk(c) for c in el if is_element(c)],
        )

    return walk(root)
