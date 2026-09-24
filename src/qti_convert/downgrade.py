"""QTI 3.0 -> QTI 2.1 conversion of a single item, test or stimulus.

Best-effort: constructs without a QTI 2.1 equivalent are converted or removed and reported as warnings.

Internally the QTI 3 elements are taken out of their namespace (qti-item-body instead of {ns}qti-item-body), so the
steps below can work with plain names; the final rebuild puts everything in the QTI 2.1 namespace.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

from lxml import etree

from ._xml import (
    XML_NAMESPACE,
    XSI_NAMESPACE,
    XmlInput,
    append_nodes,
    descendants,
    elements,
    insert_before,
    is_element,
    local_name,
    namespace,
    nodes,
    parse,
    rebuild,
    remove,
    replace_with,
    root_siblings,
    serialize_document,
    tag_name,
    text_content,
    to_text,
    unwrap,
)
from .names import QTI3_NAMESPACE, QTI21_NAMESPACE, camelize, qti3_element_name_to_qti2
from .paths import dirname, is_relative_url, join_path, mime_type_from_path

QTI21_SCHEMA_LOCATION = f"{QTI21_NAMESPACE} http://www.imsglobal.org/xsd/qti/qtiv2p1/imsqti_v2p1p2.xsd"
QTI21_RPTEMPLATES_URI = "http://www.imsglobal.org/question/qti_v2p1/rptemplates/"
PCI_NAMESPACE = "http://www.imsglobal.org/xsd/portableCustomInteraction"
_SSML_NAMESPACES = {"http://www.w3.org/2001/10/synthesis", "http://www.w3.org/2010/10/synthesis"}

#: QTI 3 elements that only exist in QTI 3.0 (or 2.2) and are removed including their content.
QTI3_ONLY_REMOVE = (
    "qti-catalog-info",
    "qti-companion-materials-info",
    "qti-context-declaration",
    "qti-assessment-stimulus-ref",  # inlined when resolvable, see _inline_shared_stimuli
)

#: Elements that are only valid in QTI 2.2+ and are renamed to generic XHTML with a warning.
HTML5_TO_HTML4: Dict[str, str] = {
    "article": "div",
    "aside": "div",
    "bdi": "span",
    "figcaption": "div",
    "figure": "div",
    "footer": "div",
    "header": "div",
    "mark": "span",
    "nav": "div",
    "section": "div",
    "bdo": "span",
    "ruby": "span",
    "rb": "span",
    "rt": "span",
    "rp": "span",
}

#: Attributes added in QTI 3.0 (or 2.2) that have no QTI 2.1 equivalent, per QTI 3 element name.
QTI3_ONLY_ATTRIBUTES: Dict[str, List[str]] = {
    "qti-rubric-block": ["use"],
    "qti-outcome-declaration": ["external-scored", "variable-identifier-ref"],
    "qti-choice-interaction": ["orientation"],
}

#: Interactions that require an <object> (not an <img>) as background in QTI 2.1.
GRAPHIC_INTERACTIONS = (
    "qti-hotspot-interaction",
    "qti-select-point-interaction",
    "qti-graphic-order-interaction",
    "qti-graphic-associate-interaction",
    "qti-graphic-gap-match-interaction",
    "qti-position-object-stage",
    "qti-position-object-interaction",
    "qti-drawing-interaction",
    "qti-gap-img",
)

_REBASE_ATTRIBUTES = ("src", "data", "href", "poster")
_BLOCK_ONLY_PARENTS = {"qti-item-body", "blockquote", "qti-rubric-block"}
_XML_LANG = f"{{{XML_NAMESPACE}}}lang"
_SCHEMA_LOCATION = f"{{{XSI_NAMESPACE}}}schemaLocation"

#: Warning codes of convert_qti3_to_qti21.
WARNING_CODES = (
    "already-qti2",
    "not-qti",
    "stimulus-inlined",
    "stimulus-unresolved",
    "stimulus-standalone",
    "removed-element",
    "removed-attribute",
    "data-attributes-removed",
    "ssml-removed",
    "media-to-object",
    "img-to-object",
    "html5-element",
    "pci",
    "shared-vocabulary-classes",
    "shared-vocabulary-stylesheet",
    "accessibility-attributes-removed",
    "gap-text-to-gap-img",
)


@dataclass(frozen=True)
class Qti21Warning:
    code: str
    message: str
    file: Optional[str] = None


@dataclass
class Qti21ConversionResult:
    xml: str
    warnings: List[Qti21Warning] = field(default_factory=list)


class _Warnings:
    def __init__(self, file: Optional[str]):
        self.file = file
        self.warnings: List[Qti21Warning] = []

    def add(self, code: str, message: str) -> None:
        if not any(w.code == code and w.message == message for w in self.warnings):
            self.warnings.append(Qti21Warning(code, message, self.file))


def qti3_attribute_name_to_qti21(name: str) -> str:
    """response-identifier -> responseIdentifier; namespaced attributes (xml:lang, ...) are left untouched."""
    return name if name.startswith("{") or ":" in name else camelize(name)


def qti3_rp_template_to_qti21(template: str) -> str:
    """https://purl.imsglobal.org/spec/qti/v3p0/rptemplates/match_correct.xml -> the QTI 2.1 template URI."""
    match = re.search(r"rptemplates/([^/]+?)(\.xml)?$", template)
    return f"{QTI21_RPTEMPLATES_URI}{match.group(1)}" if match else template


def qti3_resource_type_to_qti21(resource_type: str) -> str:
    """imsqti_item_xmlv3p0 -> imsqti_item_xmlv2p1"""
    return re.sub(r"xmlv3p0$", "xmlv2p1", resource_type)


def _load(xml: XmlInput) -> etree._Element:
    """Parses QTI 3 and takes the QTI 3 elements out of their namespace."""
    root = parse(xml)
    for el in root.iter():
        if is_element(el) and namespace(el) == QTI3_NAMESPACE:
            el.tag = local_name(el)
    return root


def _find(root: etree._Element, *names: str) -> List[etree._Element]:
    wanted = set(names)
    return [el for el in root.iter() if is_element(el) and el.tag in wanted]


def _rebase_assets(scope: etree._Element, base_dir: str) -> None:
    if not base_dir:
        return
    for el in descendants(scope):
        for attr in _REBASE_ATTRIBUTES:
            value = el.get(attr)
            if value and is_relative_url(value):
                el.set(attr, join_path(base_dir, value))


def _inline_shared_stimuli(
    root: etree._Element, resolve_stimulus: Optional[Callable[[str], Optional[XmlInput]]], warnings: _Warnings
) -> None:
    """Replaces qti-assessment-stimulus-ref with the stimulus body at the start of the item body."""
    for ref in _find(root, "qti-assessment-stimulus-ref"):
        href = ref.get("href") or ""
        identifier = ref.get("identifier") or href
        stimulus_xml = resolve_stimulus(href) if href and resolve_stimulus else None
        if not stimulus_xml:
            warnings.add(
                "stimulus-unresolved", f'Shared stimulus "{identifier}" could not be resolved and was removed.'
            )
            remove(ref)
            continue
        stimulus = _load(stimulus_xml)
        base_dir = dirname(href)
        body = next(iter(_find(stimulus, "qti-stimulus-body")), None)
        item_body = next(iter(_find(root, "qti-item-body")), None)
        if item_body is not None:
            for stylesheet in _find(stimulus, "qti-stylesheet"):
                stylesheet_href = stylesheet.get("href")
                if stylesheet_href and is_relative_url(stylesheet_href):
                    stylesheet.set("href", join_path(base_dir, stylesheet_href))
                insert_before(item_body, stylesheet)
            div = etree.Element("div", {"class": "qti-shared-stimulus"})
            lang = stimulus.get(_XML_LANG)
            if lang:
                div.set(_XML_LANG, lang)
            if body is not None:
                _rebase_assets(body, base_dir)
                append_nodes(div, nodes(body))
            div.tail, item_body.text = item_body.text, None
            item_body.insert(0, div)
        remove(ref)
        warnings.add("stimulus-inlined", f'Shared stimulus "{identifier}" was inlined into the item body.')


def _object(src: str, media_type: Optional[str], attributes: Dict[str, Optional[str]], fallback) -> etree._Element:
    el = etree.Element("object", {"data": src, "type": media_type or mime_type_from_path(src)})
    for name, value in attributes.items():
        if value:
            el.set(name, value)
    append_nodes(el, [fallback] if isinstance(fallback, str) else fallback)
    return el


def _media_attributes(el: etree._Element) -> Dict[str, Optional[str]]:
    return {name: el.get(name) for name in ("width", "height", "id", "class")}


def _convert_media_to_object(root: etree._Element, warnings: _Warnings) -> None:
    for el in _find(root, "audio", "video"):
        if el.getparent() is None:
            continue
        sources = [child for child in el if is_element(child) and child.tag == "source"]
        src = el.get("src") or (sources[0].get("src") if sources else None)
        if not src:
            remove(el)
            warnings.add("media-to-object", f"<{el.tag}> without a source was removed.")
            continue
        fallback = []
        for item in nodes(el):
            if isinstance(item, str) or not (is_element(item) and item.tag in ("source", "track")):
                fallback.append(item)
        media_type = sources[0].get("type") if sources else None
        obj = _object(src, media_type, _media_attributes(el), fallback)
        # <object> is inline in QTI 2.1, so it needs a block wrapper where only block content is allowed
        parent = el.getparent()
        if parent is not None and parent.tag in _BLOCK_ONLY_PARENTS:
            wrapper = etree.Element("div")
            wrapper.append(obj)
            obj = wrapper
        replace_with(el, [obj])
        warnings.add("media-to-object", f"HTML5 <{el.tag}> was converted to <object>.")


def _convert_graphic_images_to_object(root: etree._Element, warnings: _Warnings) -> None:
    for interaction in GRAPHIC_INTERACTIONS:
        for parent in _find(root, interaction):
            for img in [child for child in parent if is_element(child) and child.tag == "img"]:
                replace_with(img, [_object(img.get("src") or "", None, _media_attributes(img), img.get("alt") or "")])
                warnings.add("img-to-object", f"<img> in {interaction} was converted to <object>, as QTI 2.1 requires.")


def _convert_image_gap_texts_to_gap_img(root: etree._Element, warnings: _Warnings) -> None:
    """A qti-gap-text with only an image becomes a gapImg: QTI 2.1 gapText can only contain text."""
    for gap_text in _find(root, "qti-gap-text"):
        children = elements(gap_text)
        if len(children) != 1 or children[0].tag != "img" or text_content(gap_text).strip():
            continue
        img = children[0]
        gap_img = etree.Element("qti-gap-img", dict(gap_text.attrib))
        gap_img.append(_object(img.get("src") or "", None, _media_attributes(img), img.get("alt") or ""))
        replace_with(gap_text, [gap_img])
        warnings.add(
            "gap-text-to-gap-img", "A gap text with only an image was converted to gapImg, as QTI 2.1 requires."
        )


def _convert_pci(root: etree._Element, warnings: _Warnings) -> None:
    """qti-portable-custom-interaction -> customInteraction wrapping the PCI markup in the PCI namespace."""
    for el in _find(root, "qti-portable-custom-interaction"):
        custom = etree.Element("customInteraction", {"responseIdentifier": el.get("response-identifier") or ""})
        pci = etree.SubElement(custom, f"{{{PCI_NAMESPACE}}}portableCustomInteraction")
        for name, value in el.attrib.items():
            if name != "response-identifier" and not name.startswith("data-"):
                pci.set(qti3_attribute_name_to_qti21(name), value)
        append_nodes(pci, nodes(el))
        # children of the PCI move into the PCI namespace (qti-interaction-markup -> pci:interactionMarkup)
        for child in descendants(pci):
            name = qti3_element_name_to_qti2(tag_name(child)) if namespace(child) is None else None
            if name:
                child.tag = f"{{{PCI_NAMESPACE}}}{name}"
        replace_with(el, [custom])
        warnings.add(
            "pci", "Portable custom interaction was wrapped in a customInteraction; check it in the target player."
        )


def _strip_ssml(root: etree._Element, warnings: _Warnings) -> None:
    for el in [el for el in root.iter() if is_element(el) and namespace(el) in _SSML_NAMESPACES]:
        unwrap(el)
        warnings.add("ssml-removed", "SSML markup is not supported in QTI 2.1 and was removed (text is kept).")


def _rename_tree(el: etree._Element, warnings: _Warnings, stats: Dict[str, int]) -> None:
    """Renames elements and attributes to QTI 2.1 and strips what 2.1 doesn't allow."""
    if namespace(el) is not None:
        # MathML, SVG, PCI markup, ...
        return
    original = tag_name(el)
    qti21_name = qti3_element_name_to_qti2(original)
    html4_name = HTML5_TO_HTML4.get(original)

    attributes = []
    for name, value in el.attrib.items():
        if name.startswith("data-"):
            stats["data"] += 1
            continue
        if name.startswith("aria-") or name in ("role", "dir"):
            warnings.add(
                "accessibility-attributes-removed",
                "aria-*, role and dir attributes are not allowed in QTI 2.1 and were removed.",
            )
            continue
        if not qti21_name:
            attributes.append((name, value))
            continue
        if name in QTI3_ONLY_ATTRIBUTES.get(original, ()):
            warnings.add("removed-attribute", f'Attribute "{name}" on {original} is not supported in QTI 2.1.')
            continue
        attributes.append((qti3_attribute_name_to_qti21(name), value))
    el.attrib.clear()
    for name, value in attributes:
        el.set(name, value)

    template = el.get("template")
    if original == "qti-response-processing" and template:
        el.set("template", qti3_rp_template_to_qti21(template))
    if qti21_name:
        el.tag = qti21_name
    elif html4_name:
        warnings.add("html5-element", f"HTML5 <{original}> was converted to <{html4_name}>.")
        el.tag = html4_name

    for child in el:
        if is_element(child):
            _rename_tree(child, warnings, stats)


def _uses_shared_vocabulary(root: etree._Element) -> bool:
    # qti-shared-stimulus marks inlined stimuli (added by this converter) and is not part of the vocabulary
    for el in root.iter():
        if is_element(el) and any(
            c.startswith("qti-") and c != "qti-shared-stimulus" for c in (el.get("class") or "").split()
        ):
            return True
    return False


def convert_qti3_to_qti21(
    xml: XmlInput,
    resolve_stimulus: Optional[Callable[[str], Optional[XmlInput]]] = None,
    file_path: Optional[str] = None,
    shared_vocabulary_stylesheet_href: Optional[str] = None,
) -> Qti21ConversionResult:
    """Converts a QTI 3.0 assessment item, test or stimulus to QTI 2.1.

    :param resolve_stimulus: returns the QTI 3 stimulus XML for the href of a qti-assessment-stimulus-ref
        (relative to the item), or None when it can't be found. Without a resolver stimulus refs are removed
        with a warning.
    :param file_path: used to tag warnings.
    :param shared_vocabulary_stylesheet_href: when set, an item that uses QTI 3 shared vocabulary classes (qti-*)
        gets a stylesheet with this href, so a QTI 2.1 player can style them. The file itself
        (``QTI3_SHARED_VOCABULARY_CSS``) is up to the caller.
    """
    warnings = _Warnings(file_path)
    try:
        root = _load(xml)
    except etree.XMLSyntaxError as error:
        warnings.add("not-qti", f"The document could not be parsed: {error}")
        return Qti21ConversionResult(to_text(xml), warnings.warnings)

    if not tag_name(root).startswith("qti-"):
        is_qti2 = re.match(r"^(assessmentItem|assessmentTest|assessmentStimulus)$", local_name(root))
        warnings.add(
            "already-qti2" if is_qti2 else "not-qti",
            f"<{local_name(root)}> is not a QTI 3 document; it was left unchanged.",
        )
        return Qti21ConversionResult(to_text(xml), warnings.warnings)
    if root.tag == "qti-assessment-stimulus":
        warnings.add(
            "stimulus-standalone", "assessmentStimulus is a QTI 2.2 construct; QTI 2.1 players will not recognise it."
        )

    _inline_shared_stimuli(root, resolve_stimulus, warnings)

    for name in QTI3_ONLY_REMOVE:
        for el in _find(root, name):
            warnings.add("removed-element", f"<{name}> is not supported in QTI 2.1 and was removed.")
            remove(el)
    for el in _find(root, "qti-content-body"):
        unwrap(el)
    _convert_pci(root, warnings)
    _convert_media_to_object(root, warnings)
    _convert_graphic_images_to_object(root, warnings)
    _convert_image_gap_texts_to_gap_img(root, warnings)
    for el in _find(root, "wbr", "track") + [s for p in _find(root, "picture") for s in p if s.tag == "source"]:
        remove(el)
    for el in _find(root, "picture"):
        unwrap(el)
    _strip_ssml(root, warnings)

    if _uses_shared_vocabulary(root):
        href = shared_vocabulary_stylesheet_href
        item_body = next(iter(_find(root, "qti-item-body")), None)
        if href and root.tag == "qti-assessment-item" and item_body is not None:
            # stylesheets come right before the item body in QTI 2.1
            if not any(el.get("href") == href for el in _find(root, "qti-stylesheet")):
                insert_before(item_body, etree.Element("qti-stylesheet", {"href": href, "type": "text/css"}))
            warnings.add(
                "shared-vocabulary-stylesheet",
                f"QTI 3 shared vocabulary classes (qti-*) are styled by the added {href} stylesheet.",
            )
        else:
            warnings.add(
                "shared-vocabulary-classes",
                "QTI 3 shared vocabulary classes (qti-*) were kept; QTI 2.1 players will ignore them.",
            )

    stats = {"data": 0}
    _rename_tree(root, warnings, stats)
    if stats["data"] > 0:
        warnings.add("data-attributes-removed", f"{stats['data']} data-* attribute(s) were removed.")

    root_nsmap: Dict[Optional[str], str] = {None: QTI21_NAMESPACE, "xsi": XSI_NAMESPACE}
    for prefix, uri in root.nsmap.items():
        if prefix and prefix not in root_nsmap and uri != QTI3_NAMESPACE and uri not in _SSML_NAMESPACES:
            root_nsmap[prefix] = uri
    root_attributes = [(_SCHEMA_LOCATION, QTI21_SCHEMA_LOCATION)] + [
        (name, value) for name, value in root.attrib.items() if name != _SCHEMA_LOCATION
    ]
    output = rebuild(
        root,
        lambda el: tag_name(el) if namespace(el) else f"{{{QTI21_NAMESPACE}}}{tag_name(el)}",
        root_nsmap,
        root_attributes,
        prefix_hints={PCI_NAMESPACE: "pci"},
    )
    before, after = root_siblings(root)
    for node in before:
        if not (isinstance(node, etree._ProcessingInstruction) and node.target == "xml-model"):
            output.addprevious(_copy_top_level(node))
    for node in reversed(after):
        output.addnext(_copy_top_level(node))
    return Qti21ConversionResult(serialize_document(output), warnings.warnings)


def _copy_top_level(node: etree._Element) -> etree._Element:
    if isinstance(node, etree._ProcessingInstruction):
        return etree.ProcessingInstruction(node.target, node.text)
    return etree.Comment(node.text or "")
