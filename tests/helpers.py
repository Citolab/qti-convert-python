from __future__ import annotations

from lxml import etree

from qti_convert._xml import is_element, parse

XML_LANG = "{http://www.w3.org/XML/1998/namespace}lang"


def load(xml):
    """Parses XML and drops the element namespaces, so tests can use plain XPath (//choiceInteraction)."""
    root = parse(xml)
    for el in root.iter():
        if is_element(el):
            el.tag = etree.QName(el).localname
    return root


def xpath(xml_or_root, path):
    root = load(xml_or_root) if isinstance(xml_or_root, (str, bytes)) else xml_or_root
    return root.xpath(path)


def text(el) -> str:
    return "".join(el.itertext())


def codes(warnings):
    return [w.code for w in warnings]
