"""Post-processing of upgraded QTI 3 items, as done by the qti-convert package upgrade.

Each transform takes and changes the root element of a QTI 3 item in place. ``DEFAULT_ITEM_TRANSFORMS`` lists the
ones the package upgrade runs by default; pass your own list to ``upgrade_package_files`` to change that.
"""

from __future__ import annotations

from typing import Callable, Dict, List, Optional, Sequence

from lxml import etree

from ._xml import (
    XmlInput,
    append_nodes,
    is_element,
    local_name,
    namespace,
    nodes,
    parse,
    remove,
    replace_with,
    serialize_document,
)
from .names import QTI3_NAMESPACE

SSML_NAMESPACES = {"http://www.w3.org/2001/10/synthesis", "http://www.w3.org/2010/10/synthesis"}

Transform = Callable[[etree._Element], None]


def _q(name: str) -> str:
    return f"{{{QTI3_NAMESPACE}}}{name}"


def _qti(root: etree._Element, *names: str) -> List[etree._Element]:
    wanted = {_q(name) for name in names}
    return [el for el in root.iter() if is_element(el) and el.tag in wanted]


def _normalize_controls(root: etree._Element, name: str) -> None:
    for el in _qti(root, name):
        has_controls = el.get("data-dep-controls") == "true" or el.get("controls") is not None
        if has_controls:
            el.set("controls", "")
        elif "controls" in el.attrib:
            del el.attrib["controls"]


def _object_to_media(root: etree._Element, media: str) -> None:
    for obj in [el for el in _qti(root, "object") if (el.get("type") or "").startswith(media)]:
        new = etree.Element(_q(media))
        for attr in ("width", "height"):
            value = obj.get(attr)
            if value is not None:
                new.set(attr, value)
        if obj.get("data-dep-controls") == "true":
            new.set("controls", "true")
        source = etree.SubElement(new, _q("source"))
        data = obj.get("data")
        if data is not None:
            source.set("src", data)
        source.set("type", obj.get("type") or "")
        replace_with(obj, [new])


def object_to_img(root: etree._Element) -> None:
    """<object type="image/..."> -> <img>."""
    for obj in [el for el in _qti(root, "object") if (el.get("type") or "").startswith("image")]:
        img = etree.Element(_q("img"))
        for attr, value in (("width", obj.get("width")), ("height", obj.get("height")), ("src", obj.get("data"))):
            if value is not None:
                img.set(attr, value)
        img.set("alt", "".join(obj.itertext()))
        replace_with(obj, [img])


def object_to_video(root: etree._Element) -> None:
    """Normalizes the controls attribute of <video> and converts <object type="video/..."> to <video>."""
    _normalize_controls(root, "video")
    _object_to_media(root, "video")


def object_to_audio(root: etree._Element) -> None:
    """Normalizes the controls attribute of <audio> and converts <object type="audio/..."> to <audio>."""
    _normalize_controls(root, "audio")
    _object_to_media(root, "audio")


_SSML_TO_DATA: Dict[str, Sequence[str]] = {
    "sub": ("alias",),
    "break": ("time", "strength"),
    "say-as": ("interpret-as", "format", "detail"),
    "phoneme": ("ph", "alphabet"),
    "prosody": ("pitch", "rate", "volume", "contour", "range", "duration"),
    "emphasis": ("level",),
    "voice": ("gender", "age", "variant", "name", "languages"),
}


def _ssml_data_attribute(element: str, attribute: str) -> str:
    if element == "sub":
        return "data-ssml-sub-alias"
    if element == "say-as":
        return "data-ssml-say-as" if attribute == "interpret-as" else f"data-ssml-say-as-{attribute}"
    return f"data-ssml-{element}-{attribute}"


def ssml_to_span(root: etree._Element) -> None:
    """SSML elements -> <span data-ssml-*="..."> (e.g. <ssml:sub alias="x"> -> <span data-ssml-sub-alias="x">)."""
    for el in [el for el in root.iter() if is_element(el) and namespace(el) in SSML_NAMESPACES]:
        name = local_name(el)
        if name not in _SSML_TO_DATA or el.getparent() is None:
            continue
        span = etree.Element(_q("span"))
        for attribute in _SSML_TO_DATA[name]:
            value = el.get(attribute)
            # sub always gets its alias (possibly empty), like the TypeScript transform
            if value or (name == "sub" and attribute == "alias"):
                span.set(_ssml_data_attribute(name, attribute), value or "")
        if name != "break":
            append_nodes(span, nodes(el))
        replace_with(el, [span])


def strip_material_info(root: etree._Element) -> None:
    """Removes qti-companion-materials-info."""
    for el in _qti(root, "qti-companion-materials-info"):
        remove(el)


def min_choices_to_one(root: etree._Element) -> None:
    """Sets min-choices="1" on choice interactions without min-choices or with min-choices="0"."""
    for el in _qti(root, "qti-choice-interaction"):
        if el.get("min-choices") in (None, "", "0"):
            el.set("min-choices", "1")


def external_scored(root: etree._Element) -> None:
    """Marks the SCORE outcome as external-scored="human" when the item has no response processing."""
    if _qti(root, "qti-response-processing"):
        return
    for el in _qti(root, "qti-outcome-declaration"):
        if el.get("identifier") == "SCORE":
            el.set("external-scored", "human")


def dep_convert(root: etree._Element) -> None:
    """Dutch Extension Profile: .dep-dialogTrigger with data-stimulus-idref -> popover button + popover target."""
    for trigger in [
        el for el in root.iter() if is_element(el) and "dep-dialogTrigger" in (el.get("class") or "").split()
    ]:
        ref = trigger.get("data-stimulus-idref")
        if not ref or any(local_name(a) == "button" for a in trigger.iterancestors()):
            continue
        button = etree.Element(_q("button"), {"popovertarget": ref})
        replace_with(trigger, [button])
        button.append(trigger)
        for target in root.iter():
            if is_element(target) and target.get("id") == ref:
                target.set("popover", "")


#: Transforms run by the package upgrade by default, in this order.
DEFAULT_ITEM_TRANSFORMS: Sequence[Transform] = (
    object_to_img,
    object_to_video,
    object_to_audio,
    ssml_to_span,
    strip_material_info,
    min_choices_to_one,
    external_scored,
    dep_convert,
)


def apply_transforms(qti3: XmlInput, transforms: Optional[Sequence[Transform]] = None) -> str:
    """Runs transforms (default ``DEFAULT_ITEM_TRANSFORMS``) on a QTI 3 item and returns the XML."""
    root = parse(qti3)
    for transform in DEFAULT_ITEM_TRANSFORMS if transforms is None else transforms:
        transform(root)
    return serialize_document(root)
