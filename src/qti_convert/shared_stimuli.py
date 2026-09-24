"""Moves content that is repeated across QTI 3 items into shared qti-assessment-stimulus files.

Typically a reading passage copied into every item of a cluster: it is moved into one stimulus referenced by the
items, and registered in the manifest.
"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

from lxml import etree

from ._xml import (
    XML_NAMESPACE,
    descendants,
    elements,
    is_element,
    iter_by_local_name,
    local_name,
    namespace,
    nodes,
    parse,
    remove,
    remove_leading_whitespace,
    replace_with,
    serialize_document,
    text_content,
    to_text,
)
from .names import QTI3_NAMESPACE
from .paths import dirname, is_relative_url, join_path, normalize_path, relative_path

_BLOCK_ELEMENTS = set(
    "p div table img figure blockquote h1 h2 h3 h4 h5 h6 ul ol dl pre object audio video picture section article "
    "aside hr".split()
)
_RICH_ELEMENTS = {"img", "table", "figure", "object", "audio", "video", "picture", "svg", "math"}
_ASSET_ATTRIBUTES = ("src", "href", "data", "poster")
_IGNORED_ATTRIBUTES = {"id", "identifier"}
_REF_INSERT_BEFORE = {"qti-companion-materials-info", "qti-stylesheet", "qti-item-body"}
_XML_LANG = f"{{{XML_NAMESPACE}}}lang"


@dataclass
class ExtractedStimulus:
    identifier: str
    path: str
    title: str
    #: Paths of the items that now reference the stimulus.
    items: List[str]


@dataclass
class NearDuplicateContent:
    items: Tuple[str, str]
    similarity: float


@dataclass
class SharedStimuliReport:
    stimuli: List[ExtractedStimulus] = field(default_factory=list)
    #: Similar but not identical content; reported only, never extracted.
    near_duplicates: List[NearDuplicateContent] = field(default_factory=list)


@dataclass
class _Candidate:
    path: str
    root: etree._Element
    blocks: List[etree._Element]
    keys: List[str]
    #: The layout column holding the blocks, when the stimulus is one column of a qti-layout-row.
    column: Optional[etree._Element] = None


def _collapse(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _has_class(el: etree._Element, pattern: str) -> bool:
    return any(re.match(pattern, c) for c in (el.get("class") or "").split())


def _is_qti(el: etree._Element) -> bool:
    return local_name(el).startswith("qti-")


def _contains_qti(el: etree._Element) -> bool:
    return _is_qti(el) or any(_is_qti(d) for d in descendants(el))


def _is_rich(el: etree._Element) -> bool:
    return local_name(el) in _RICH_ELEMENTS or any(local_name(d) in _RICH_ELEMENTS for d in descendants(el))


def _hash(text: str) -> str:
    """FNV-1a over UTF-16 code units (like the TypeScript version), for short stable identifiers."""
    h = 0x811C9DC5
    data = text.encode("utf-16-le")
    for i in range(0, len(data), 2):
        h ^= data[i] | (data[i + 1] << 8)
        h = (h * 0x01000193) & 0xFFFFFFFF
    return f"{h:08x}"


def _canonical(node, item_dir: str) -> str:
    """Order-independent form of a block: sorted attributes, no ids, collapsed text, package-absolute asset paths."""
    if isinstance(node, str):
        return _collapse(node)
    if not is_element(node):
        return ""
    attrs = []
    for name, value in node.attrib.items():
        if name in _IGNORED_ATTRIBUTES:
            continue
        if name in _ASSET_ATTRIBUTES and is_relative_url(value):
            value = normalize_path(join_path(item_dir, value))
        attrs.append((name, value))
    attributes = "".join(f' {name}="{value}"' for name, value in sorted(attrs))
    inner = "".join(_canonical(child, item_dir) for child in nodes(node))
    return f"<{local_name(node)}{attributes}>{inner}</{local_name(node)}>"


def _find_candidate(path: str, xml: str) -> Optional[_Candidate]:
    """The item content that could be a stimulus: its non-interaction layout column, or the leading blocks."""
    root = parse(xml)
    item_body = next(iter(iter_by_local_name(root, "qti-item-body")), None)
    if item_body is None:
        return None
    item_dir = dirname(path)

    def make(blocks: List[etree._Element], column: Optional[etree._Element] = None) -> Optional[_Candidate]:
        if not blocks:
            return None
        return _Candidate(path, root, blocks, [_canonical(b, item_dir) for b in blocks], column)

    row = next((el for el in elements(item_body) if _has_class(el, r"^qti-layout-row$")), None)
    if row is not None:
        columns = [el for el in elements(row) if _has_class(el, r"^qti-layout-col")]
        column = next((col for col in columns if not _contains_qti(col)), None)
        if column is not None and any(col is not column and _contains_qti(col) for col in columns):
            return make(elements(column), column)

    blocks: List[etree._Element] = []
    for item in nodes(item_body):
        if isinstance(item, str) and not item.strip():
            continue
        if isinstance(item, str) or not is_element(item) or local_name(item) not in _BLOCK_ELEMENTS:
            break
        if _contains_qti(item):
            break
        blocks.append(item)
    return make(blocks)


def _longest_common_run(a: List[str], b: List[str]) -> Tuple[int, int, int]:
    """Longest run of consecutive equal keys in a and b: (start_a, start_b, length)."""
    best = (0, 0, 0)
    lengths = [0] * (len(b) + 1)
    for i in range(1, len(a) + 1):
        for j in range(len(b), 0, -1):
            lengths[j] = lengths[j - 1] + 1 if a[i - 1] == b[j - 1] else 0
            if lengths[j] > best[2]:
                best = (i - lengths[j], j - lengths[j], lengths[j])
    return best


def _index_of_run(keys: List[str], run: List[str]) -> int:
    for i in range(len(keys) - len(run) + 1):
        if keys[i : i + len(run)] == run:
            return i
    return -1


def _words(text: str) -> Set[str]:
    return set(re.findall(r"[^\W_]+", text.lower()))


def _jaccard(a: Set[str], b: Set[str]) -> float:
    shared = len(a & b)
    return 1.0 if len(a) + len(b) == 0 else shared / (len(a) + len(b) - shared)


def _rebase_assets(el: etree._Element, from_dir: str, to_dir: str, assets: Dict[str, None]) -> None:
    for node in [el, *descendants(el)]:
        for attr in _ASSET_ATTRIBUTES:
            value = node.get(attr)
            if not value or not is_relative_url(value):
                continue
            if attr == "href" and local_name(node) == "a" and not re.search(r"\.\w+$", value):
                continue
            absolute = normalize_path(join_path(from_dir, value))
            assets[absolute] = None  # an ordered set
            node.set(attr, relative_path(to_dir, absolute))


def _clone_rebased(el: etree._Element, from_dir: str, to_dir: str, assets: Dict[str, None]) -> etree._Element:
    clone = copy.deepcopy(el)
    clone.tail = None
    _rebase_assets(clone, from_dir, to_dir, assets)
    return clone


def _remove_with_leading_whitespace(el: etree._Element) -> None:
    remove_leading_whitespace(el)
    remove(el)


def _content_text(content) -> str:
    return to_text(content) if isinstance(content, (str, bytes)) else ""


def _is_item(path: str, file_type: str, content) -> bool:
    if file_type == "item":
        return True
    return path.endswith(".xml") and bool(re.search(r"<qti-assessment-item[\s>]", _content_text(content)))


def extract_shared_stimuli(
    files: Dict[str, Tuple[object, str]],
    min_text_length: int = 150,
    similarity_threshold: float = 0.9,
    stimulus_folder: str = "stimuli",
) -> Tuple[Dict[str, Tuple[object, str]], SharedStimuliReport]:
    """Moves content that is identical in two or more QTI 3 items into shared stimulus files.

    :param files: package files by path, as ``(content, type)`` with type one of ``test``, ``item``, ``manifest``,
        ``stimulus`` or ``other``. Items without shared content are returned unchanged.
    :param min_text_length: minimum text length of shared content without images/tables to be worth extracting.
    :param similarity_threshold: word overlap (0-1) from which non-identical content is reported as a near-duplicate.
    :param stimulus_folder: package folder for the created stimulus files.
    :returns: the updated files and a report of what was extracted.
    """
    candidates: List[_Candidate] = []
    for path, (content, file_type) in files.items():
        if not _is_item(path, file_type, content):
            continue
        candidate = _find_candidate(path, _content_text(content))
        if candidate:
            candidates.append(candidate)

    def run_text(candidate: _Candidate, start: int, length: int) -> str:
        return _collapse(" ".join(text_content(b) for b in candidate.blocks[start : start + length]))

    # Enough text, or rich content (image, table, ...) with at least some text; a lone shared logo is not a stimulus
    def is_worth_sharing(candidate: _Candidate, start: int, length: int) -> bool:
        text_length = len(run_text(candidate, start, length))
        blocks = candidate.blocks[start : start + length]
        return text_length >= min_text_length or (text_length >= 40 and any(_is_rich(b) for b in blocks))

    # Shared runs of blocks, longest (by text) first
    runs: Dict[str, Tuple[List[str], int]] = {}
    for i in range(len(candidates)):
        for j in range(i + 1, len(candidates)):
            start, _, length = _longest_common_run(candidates[i].keys, candidates[j].keys)
            if length == 0 or not is_worth_sharing(candidates[i], start, length):
                continue
            keys = candidates[i].keys[start : start + length]
            runs["\0".join(keys)] = (keys, len(run_text(candidates[i], start, length)))

    output = dict(files)
    report = SharedStimuliReport()
    assigned: Dict[int, str] = {}
    extracted_text: Dict[int, str] = {}
    stimulus_assets: Dict[str, Dict[str, None]] = {}

    for keys, _ in sorted(runs.values(), key=lambda run: (-run[1], -len(run[0]))):
        members = [
            (candidate, _index_of_run(candidate.keys, keys))
            for candidate in candidates
            if id(candidate) not in assigned
        ]
        members = [(candidate, start) for candidate, start in members if start != -1]
        if len(members) < 2:
            continue

        identifier = f"STIM_{_hash(chr(0).join(keys))}"
        while normalize_path(f"{stimulus_folder}/{identifier}.xml") in output:
            identifier += "_"
        stimulus_path = normalize_path(f"{stimulus_folder}/{identifier}.xml")
        stimulus_dir = dirname(stimulus_path)

        # The stimulus is built from the first item; the others have the same content
        first, first_start = members[0]
        first_dir = dirname(first.path)
        assets: Dict[str, None] = {}
        blocks = first.blocks[first_start : first_start + len(keys)]
        heading = next((b for b in blocks if re.match(r"^h[1-6]$", local_name(b))), None)
        text = _collapse(" ".join(text_content(b) for b in blocks))
        title = (
            _collapse(text_content(heading))
            if heading is not None
            else (f"{text[:57]}..." if len(text) > 60 else text or identifier)
        )

        item_root = first.root
        uri = namespace(item_root) or QTI3_NAMESPACE
        nsmap: Dict[Optional[str], str] = {prefix: value for prefix, value in item_root.nsmap.items() if prefix}
        nsmap[None] = uri
        stimulus = etree.Element(f"{{{uri}}}qti-assessment-stimulus", nsmap=nsmap)
        stimulus.set("identifier", identifier)
        stimulus.set("title", title)
        lang = item_root.get(_XML_LANG)
        if lang:
            stimulus.set(_XML_LANG, lang)
        stimulus.text = "\n  "

        for stylesheet in iter_by_local_name(item_root, "qti-stylesheet"):
            clone = _clone_rebased(stylesheet, first_dir, stimulus_dir, assets)
            clone.tail = "\n  "
            stimulus.append(clone)
        body = etree.SubElement(stimulus, f"{{{uri}}}qti-stimulus-body")
        body.text = "\n    "
        for index, block in enumerate(blocks):
            clone = _clone_rebased(block, first_dir, stimulus_dir, assets)
            clone.tail = "\n    " if index < len(blocks) - 1 else "\n  "
            body.append(clone)
        body.tail = "\n"
        output[stimulus_path] = (serialize_document(stimulus) + "\n", "stimulus")
        stimulus_assets[identifier] = assets

        for candidate, start in members:
            extracted_text[id(candidate)] = run_text(candidate, start, len(keys))
            for block in candidate.blocks[start : start + len(keys)]:
                _remove_with_leading_whitespace(block)
            column = candidate.column
            if column is not None and not elements(column) and not (column.text or "").strip():
                row = column.getparent()
                _remove_with_leading_whitespace(column)
                remaining = elements(row) if row is not None else []
                if row is not None and len(remaining) == 1:
                    replace_with(row, nodes(remaining[0]))
            root = candidate.root
            root_uri = namespace(root) or QTI3_NAMESPACE
            ref = etree.Element(f"{{{root_uri}}}qti-assessment-stimulus-ref")
            ref.set("identifier", identifier)
            ref.set("href", relative_path(dirname(candidate.path), stimulus_path))
            ref.set("title", title)
            insert_before = next((el for el in elements(root) if local_name(el) in _REF_INSERT_BEFORE), None)
            if insert_before is not None:
                ref.tail = "\n  "
                insert_before.addprevious(ref)
            else:
                root.append(ref)
            output[candidate.path] = (serialize_document(root), files[candidate.path][1])
            assigned[id(candidate)] = identifier
        report.stimuli.append(ExtractedStimulus(identifier, stimulus_path, title, [c.path for c, _ in members]))

    # Report content that is almost, but not exactly, the same.
    # Items that got a stimulus are compared on the extracted content, others on their whole candidate content.
    full_text = {
        id(c): extracted_text.get(id(c), _collapse(" ".join(text_content(b) for b in c.blocks))) for c in candidates
    }
    for i in range(len(candidates)):
        for j in range(i + 1, len(candidates)):
            a, b = candidates[i], candidates[j]
            if id(a) in assigned and assigned.get(id(a)) == assigned.get(id(b)):
                continue
            text_a, text_b = full_text[id(a)], full_text[id(b)]
            if text_a == text_b or min(len(text_a), len(text_b)) < min_text_length:
                continue
            similarity = _jaccard(_words(text_a), _words(text_b))
            if similarity >= similarity_threshold:
                report.near_duplicates.append(NearDuplicateContent((a.path, b.path), round(similarity, 2)))

    if report.stimuli:
        _update_manifest(output, report, stimulus_assets)
    return output, report


def _update_manifest(
    files: Dict[str, Tuple[object, str]], report: SharedStimuliReport, stimulus_assets: Dict[str, Dict[str, None]]
) -> None:
    manifest_path = next((p for p in files if p == "imsmanifest.xml" or p.endswith("/imsmanifest.xml")), None)
    if manifest_path is None:
        return
    manifest_dir = dirname(manifest_path)
    content, file_type = files[manifest_path]
    root = parse(_content_text(content))
    resources = next(iter(iter_by_local_name(root, "resources")), None)
    if resources is None:
        return
    uri = namespace(resources)
    tag = (lambda name: f"{{{uri}}}{name}") if uri else (lambda name: name)

    for stimulus in report.stimuli:
        resource = etree.SubElement(resources, tag("resource"))
        resource.set("identifier", stimulus.identifier)
        resource.set("type", "imsqti_stimulus_xmlv3p0")
        resource.set("href", relative_path(manifest_dir, stimulus.path))
        for path in [stimulus.path, *[a for a in stimulus_assets[stimulus.identifier] if a in files]]:
            etree.SubElement(resource, tag("file")).set("href", relative_path(manifest_dir, path))
        for item_path in stimulus.items:
            item_resource = next(
                (
                    r
                    for r in iter_by_local_name(root, "resource")
                    if normalize_path(join_path(manifest_dir, r.get("href") or "")) == normalize_path(item_path)
                ),
                None,
            )
            if item_resource is None:
                continue
            dependencies = [el for el in elements(item_resource) if local_name(el) == "dependency"]
            if not any(d.get("identifierref") == stimulus.identifier for d in dependencies):
                etree.SubElement(item_resource, tag("dependency")).set("identifierref", stimulus.identifier)
    files[manifest_path] = (serialize_document(root), file_type)


__all__ = [
    "ExtractedStimulus",
    "NearDuplicateContent",
    "SharedStimuliReport",
    "extract_shared_stimuli",
]
