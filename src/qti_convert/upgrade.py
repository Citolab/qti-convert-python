"""QTI 2.x -> QTI 3.0 conversion of a single item, test or stimulus.

Python port of the qti-convert upgrader, itself a port of qti30upgrader/qti2xTo30.xsl (ETS, Apache-2.0) plus the
Citolab additions, without an XSLT dependency. Deliberate fixes compared to the XSLT:

- stimulusBody, durationLT/GTE and a few QTI 2.x elements missing from the XSLT lists get their qti- name
- testFeedback content is wrapped in qti-content-body like the other feedback elements
- qti-rubric-block gets the use attribute QTI 3 requires (scoring for scorer-only rubrics, else instructions)
- elements are matched by local name, so prefixed QTI 2 elements convert correctly
- an <object> video keeps its converted children once (the XSLT copied them twice)
- inline SVG stays in the SVG namespace
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

from lxml import etree

from ._xml import (
    XSI_NAMESPACE,
    XmlInput,
    add_root_siblings,
    append_text,
    attr_local,
    is_element,
    local_name,
    namespace,
    nodes,
    parse,
    prefix_for,
    root_siblings,
    serialize_document,
    text_content,
)
from .names import QTI3_NAMESPACE, kabobize, qti2_element_name_to_qti3, qti_kabobify

MATHML_NAMESPACE = "http://www.w3.org/1998/Math/MathML"
SSML_NAMESPACE = "http://www.w3.org/2001/10/synthesis"
SVG_NAMESPACE = "http://www.w3.org/2000/svg"
_MATHML_NAMESPACES = {MATHML_NAMESPACE, "http://www.w3.org/2010/Math/MathML"}
_SSML_NAMESPACES = {SSML_NAMESPACE, "http://www.w3.org/2010/10/synthesis"}

QTI3_SCHEMA_LOCATION = f"{QTI3_NAMESPACE} https://purl.imsglobal.org/spec/qti/v3p0/schema/xsd/imsqti_asiv3p0_v1p0.xsd"
QTI3_RPTEMPLATES_URI = "https://purl.imsglobal.org/spec/qti/v3p0/rptemplates/"
_XML_MODEL = (
    'href="https://purl.imsglobal.org/spec/qti/v3p0/schema/xsd/imsqti_asiv3p0_v1p0.xsd" '
    'type="application/xml" schematypens="http://purl.oclc.org/dsdl/schematron"'
)

_ROOT_ELEMENTS = {"assessmentItem", "assessmentStimulus", "assessmentTest"}
_CONTENT_BODY_ELEMENTS = {"feedbackBlock", "modalFeedback", "rubricBlock", "templateBlock", "testFeedback"}
_SCHEMA_LOCATION = f"{{{XSI_NAMESPACE}}}schemaLocation"

Attributes = List[Tuple[str, str]]


def _kabob_attributes(el: etree._Element, exclude: Tuple[str, ...] = ()) -> Attributes:
    result: Attributes = []
    for name, value in el.attrib.items():
        if name in exclude:
            continue
        local = attr_local(name)
        if re.match(r"^(aria|data)-", local):
            result.append((name, value))
        else:
            result.append((name[: len(name) - len(local)] + kabobize(local), value))
    return result


class _Upgrader:
    def element(
        self,
        source: etree._Element,
        parent: Optional[etree._Element],
        local: str,
        uri: str,
        attributes: Attributes,
    ) -> etree._Element:
        """Creates an element; declares namespaces for the element name and prefixed attributes when needed."""
        in_scope: Dict[Optional[str], str] = dict(parent.nsmap) if parent is not None else {}
        nsmap: Dict[Optional[str], str] = {}
        if in_scope.get(None) != uri:
            nsmap[None] = uri
        for name, _ in attributes:
            if not name.startswith("{"):
                continue
            attr_uri = name[1:].split("}", 1)[0]
            if attr_uri == "http://www.w3.org/XML/1998/namespace" or attr_uri in in_scope.values():
                continue
            prefix = "xsi" if attr_uri == XSI_NAMESPACE else prefix_for(source, attr_uri)
            if prefix and prefix not in nsmap:
                nsmap[prefix] = attr_uri
        tag = f"{{{uri}}}{local}"
        el = etree.Element(tag, nsmap=nsmap) if parent is None else etree.SubElement(parent, tag, nsmap=nsmap)
        for name, value in attributes:
            el.set(name, value)
        return el

    def children(self, items, parent: etree._Element) -> None:
        for item in items:
            if isinstance(item, str):
                append_text(parent, item)
            else:
                self.node(item, parent)

    def node(self, node: etree._Element, parent: Optional[etree._Element]) -> Optional[etree._Element]:
        if isinstance(node, etree._Comment):
            copy = etree.Comment(node.text or "")
        elif isinstance(node, etree._ProcessingInstruction):
            # existing schematron associations are dropped
            if node.target == "xml-model" and "dsdl/schematron" in (node.text or ""):
                return None
            copy = etree.ProcessingInstruction(node.target, node.text)
        elif isinstance(node, etree._Entity):
            copy = etree.Entity(node.name)
        elif is_element(node):
            return self.tag(node, parent)
        else:
            return None
        if parent is not None:
            parent.append(copy)
        return copy

    def tag(self, el: etree._Element, parent: Optional[etree._Element]) -> Optional[etree._Element]:
        local = local_name(el)
        uri = namespace(el) or ""

        if local == "apipAccessibility":
            return None

        for namespaces, target in (
            (_MATHML_NAMESPACES, MATHML_NAMESPACE),
            (_SSML_NAMESPACES, SSML_NAMESPACE),
            ({SVG_NAMESPACE}, SVG_NAMESPACE),
        ):
            if uri in namespaces:
                new = self.element(el, parent, local, target, list(el.attrib.items()))
                self.children(nodes(el), new)
                return new

        media_type = el.get("type", "")
        if local == "object" and media_type.startswith("image"):
            others = [(n, v) for n, v in el.attrib.items() if n not in ("data", "type")]
            attributes = [("src", el.get("data", "")), ("alt", text_content(el))] + others
            return self.element(el, parent, "img", QTI3_NAMESPACE, attributes)
        if local == "object" and media_type.startswith("video"):
            others = [(n, v) for n, v in el.attrib.items() if n not in ("data", "type")]
            video = self.element(el, parent, "video", QTI3_NAMESPACE, [("src", el.get("data", ""))] + others)
            self.children(nodes(el), video)
            self.element(el, video, "source", QTI3_NAMESPACE, [("type", media_type), ("src", el.get("data", ""))])
            return video

        if local in _ROOT_ELEMENTS:
            attributes = [(_SCHEMA_LOCATION, QTI3_SCHEMA_LOCATION)] + _kabob_attributes(el, (_SCHEMA_LOCATION,))
            new = self.element(el, parent, qti_kabobify(local), QTI3_NAMESPACE, attributes)
            self.children(nodes(el), new)
            return new

        if local in _CONTENT_BODY_ELEMENTS:
            attributes = _kabob_attributes(el)
            if local == "rubricBlock" and el.get("use") is None:
                # use is required in QTI 3; derive it from the audience
                views = (el.get("view") or "").split()
                use = "scoring" if "scorer" in views and "candidate" not in views else "instructions"
                attributes.append(("use", use))
            new = self.element(el, parent, qti_kabobify(local), QTI3_NAMESPACE, attributes)
            items = nodes(el)
            is_stylesheet = [not isinstance(i, str) and is_element(i) and local_name(i) == "stylesheet" for i in items]
            self.children([i for i, s in zip(items, is_stylesheet) if s], new)
            body = self.element(el, new, "qti-content-body", QTI3_NAMESPACE, [])
            self.children([i for i, s in zip(items, is_stylesheet) if not s], body)
            return new

        attributes = _kabob_attributes(el)
        if local == "responseProcessing":
            attributes = [
                (n, f"{re.sub(r'^.*/', QTI3_RPTEMPLATES_URI, v, count=1)}.xml") if n == "template" else (n, v)
                for n, v in attributes
            ]
        new = self.element(el, parent, qti2_element_name_to_qti3(local) or local, QTI3_NAMESPACE, attributes)
        self.children(nodes(el), new)
        return new


def upgrade_qti2_to_qti3(qti2: XmlInput) -> str:
    """Converts QTI 2.x (item, test or stimulus) XML to QTI 3.0 XML."""
    source = parse(qti2)
    upgrader = _Upgrader()
    root = upgrader.node(source, None)
    if root is None:
        raise ValueError("The document has no convertible root element")
    before, after = root_siblings(source)
    converted = lambda siblings: [c for c in (upgrader.node(n, None) for n in siblings) if c is not None]  # noqa: E731
    add_root_siblings(
        root, [etree.ProcessingInstruction("xml-model", _XML_MODEL)] + converted(before), converted(after)
    )
    return serialize_document(root)
