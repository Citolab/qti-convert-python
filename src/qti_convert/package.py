"""Conversion of whole QTI content packages (imsmanifest.xml with items, tests, stimuli and assets)."""

from __future__ import annotations

import io
import os
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple, Union

from lxml import etree

from ._xml import (
    XSI_NAMESPACE,
    XmlInput,
    elements,
    iter_by_local_name,
    local_name,
    namespace,
    parse,
    rebuild,
    root_siblings,
    serialize_document,
    tag_name,
    to_text,
)
from .downgrade import (
    Qti21ConversionResult,
    Qti21Warning,
    convert_qti3_to_qti21,
    qti3_resource_type_to_qti21,
)
from .paths import dirname, join_path, normalize_path, relative_path
from .shared_stimuli import SharedStimuliReport
from .shared_stimuli import extract_shared_stimuli as _extract_shared_stimuli
from .stylesheet import QTI3_SHARED_VOCABULARY_CSS, QTI3_SHARED_VOCABULARY_CSS_PATH
from .transforms import Transform, apply_transforms
from .upgrade import upgrade_qti2_to_qti3

FileContent = Union[str, bytes]
PackageFiles = Dict[str, FileContent]
PackageSource = Union[str, "os.PathLike[str]", bytes, IO[bytes], Mapping[str, FileContent]]

QTI3_IMSCP_NAMESPACE = "http://www.imsglobal.org/xsd/qti/qtiv3p0/imscp_v1p1"
QTI3_METADATA_NAMESPACE = "http://www.imsglobal.org/xsd/imsqti_metadata_v3p0"
QTI3_MANIFEST_SCHEMA_LOCATION = " ".join(
    [
        "http://ltsc.ieee.org/xsd/LOM https://purl.imsglobal.org/spec/md/v1p3/schema/xsd/imsmd_loose_v1p3p2.xsd",
        f"{QTI3_IMSCP_NAMESPACE} https://purl.imsglobal.org/spec/qti/v3p0/schema/xsd/imsqtiv3p0_imscpv1p2_v1p0.xsd",
        f"{QTI3_METADATA_NAMESPACE} https://purl.imsglobal.org/spec/qti/v3p0/schema/xsd/imsqti_metadatav3p0_v1p0.xsd",
    ]
)
IMSCP21_NAMESPACE = "http://www.imsglobal.org/xsd/imscp_v1p1"
QTI21_METADATA_NAMESPACE = "http://www.imsglobal.org/xsd/imsqti_metadata_v2p1"
IMSCP21_SCHEMA_LOCATION = " ".join(
    [
        f"{IMSCP21_NAMESPACE} http://www.imsglobal.org/xsd/qti/qtiv2p1/qtiv2p1_imscpv1p2_v1p0.xsd",
        f"{QTI21_METADATA_NAMESPACE} http://www.imsglobal.org/xsd/qti/qtiv2p1/imsqti_metadata_v2p1p1.xsd",
        "http://ltsc.ieee.org/xsd/LOM http://www.imsglobal.org/xsd/imsmd_loose_v1p3p2.xsd",
    ]
)
_SCHEMA_LOCATION = f"{{{XSI_NAMESPACE}}}schemaLocation"


def is_manifest(path: str) -> bool:
    return path == "imsmanifest.xml" or path.endswith("/imsmanifest.xml")


def root_local_name(xml: str) -> str:
    """Local name of the root element, found without parsing the document."""
    stripped = re.sub(r"<\?[\s\S]*?\?>|<!--[\s\S]*?-->|<!DOCTYPE[^>]*>", "", xml)
    match = re.search(r"<([A-Za-z_][\w.:-]*)", stripped)
    return match.group(1).split(":")[-1] if match else ""


def _rebuild_document(
    root: etree._Element,
    tag_for: Callable[[etree._Element], str],
    nsmap: Dict[Optional[str], str],
    attributes: List[Tuple[str, str]],
) -> etree._Element:
    output = rebuild(root, tag_for, nsmap, attributes)
    before, after = root_siblings(root)
    for node in before:
        if isinstance(node, etree._Comment):
            output.addprevious(etree.Comment(node.text or ""))
    for node in reversed(after):
        if isinstance(node, etree._Comment):
            output.addnext(etree.Comment(node.text or ""))
    return output


def _set_metadata_schema(manifest: etree._Element, schema: str, version: str) -> None:
    for metadata in [el for el in elements(manifest) if local_name(el) == "metadata"]:
        for name, value in (("schema", schema), ("schemaversion", version)):
            existing = iter_by_local_name(metadata, name)
            existing = [el for el in existing if el is not metadata]
            if existing:
                for el in existing:
                    el.text = value
            else:
                tag = f"{{{namespace(metadata)}}}{name}" if namespace(metadata) else name
                etree.SubElement(metadata, tag).text = value


# ---------------------------------------------------------------------------------------------------------------------
# QTI 2 -> QTI 3


def convert_manifest_to_qti3(manifest_xml: XmlInput) -> str:
    """Converts a QTI 2.x imsmanifest.xml to QTI 3: namespaces, schema version and resource types."""
    root = parse(manifest_xml)

    def is_content_packaging(uri: Optional[str]) -> bool:
        # a namespace-prefixed content packaging (<imscp:manifest>) ends up in the default namespace
        return uri is None or "imscp" in uri.lower()

    def tag_for(el: etree._Element) -> str:
        if is_content_packaging(namespace(el)):
            return f"{{{QTI3_IMSCP_NAMESPACE}}}{local_name(el)}"
        if el.prefix == "imsqti":
            return f"{{{QTI3_METADATA_NAMESPACE}}}{local_name(el)}"
        return tag_name(el)

    nsmap: Dict[Optional[str], str] = {
        None: QTI3_IMSCP_NAMESPACE,
        "imsqti": QTI3_METADATA_NAMESPACE,
        "xsi": XSI_NAMESPACE,
    }
    for prefix, uri in root.nsmap.items():
        if prefix and prefix not in nsmap and not is_content_packaging(uri):
            nsmap[prefix] = uri
    attributes = [(n, v) for n, v in root.attrib.items() if n != _SCHEMA_LOCATION]
    manifest = _rebuild_document(root, tag_for, nsmap, attributes + [(_SCHEMA_LOCATION, QTI3_MANIFEST_SCHEMA_LOCATION)])

    _set_metadata_schema(manifest, "QTI Package", "3.0.0")
    for resource in iter_by_local_name(manifest, "resource"):
        resource_type = resource.get("type") or ""
        if "item" in resource_type:
            resource.set("type", "imsqti_item_xmlv3p0")
        elif "test" in resource_type:
            resource.set("type", "imsqti_test_xmlv3p0")
        elif "stimulus" in resource_type:
            resource.set("type", "imsqti_stimulus_xmlv3p0")
        elif "associatedcontent" in resource_type:
            resource.set("type", "webcontent")
    return serialize_document(manifest)


def upgrade_item(qti2: XmlInput, transforms: Optional[Sequence[Transform]] = None) -> str:
    """Upgrades a QTI 2.x item and runs the package post-processing transforms on it."""
    return apply_transforms(upgrade_qti2_to_qti3(qti2), transforms)


def _sync_item_ref_identifiers(files: Dict[str, Tuple[FileContent, str]]) -> None:
    """Gives every qti-assessment-item-ref (and its manifest resource) the identifier of the item it points to."""
    manifest_path = next((p for p in files if is_manifest(p)), None)
    tests = [p for p, (_, t) in files.items() if t == "test"]
    if manifest_path is None or not tests:
        return
    manifest_dir = dirname(manifest_path)
    manifest = parse(files[manifest_path][0])
    manifest_changed = False
    items = {normalize_path(p): p for p, (_, t) in files.items() if t == "item"}

    for test_path in tests:
        test = parse(files[test_path][0])
        test_changed = False
        for ref in iter_by_local_name(test, "qti-assessment-item-ref"):
            href, ref_id = ref.get("href"), ref.get("identifier")
            item_path = items.get(normalize_path(join_path(dirname(test_path), href))) if href else None
            if item_path is None:
                continue
            item_id = parse(files[item_path][0]).get("identifier")
            if not item_id or item_id == ref_id:
                continue
            ref.set("identifier", item_id)
            test_changed = True
            for resource in iter_by_local_name(manifest, "resource"):
                if normalize_path(join_path(manifest_dir, resource.get("href") or "")) != normalize_path(item_path):
                    continue
                old_id = resource.get("identifier")
                if old_id == item_id:
                    continue
                resource.set("identifier", item_id)
                for dependency in iter_by_local_name(manifest, "dependency"):
                    if dependency.get("identifierref") == old_id:
                        dependency.set("identifierref", item_id)
                manifest_changed = True
        if test_changed:
            files[test_path] = (serialize_document(test), "test")
    if manifest_changed:
        files[manifest_path] = (serialize_document(manifest), "manifest")


@dataclass
class UpgradeResult:
    files: PackageFiles
    #: Set when shared stimulus extraction ran.
    shared_stimuli: Optional[SharedStimuliReport] = None


def upgrade_package_files(
    files: Mapping[str, FileContent],
    item_transforms: Optional[Sequence[Transform]] = None,
    sync_identifiers: bool = True,
    extract_shared_stimuli: Union[bool, Mapping[str, object]] = False,
    convert_item: Optional[Callable[[str], str]] = None,
    convert_test: Optional[Callable[[str], str]] = None,
    convert_stimulus: Optional[Callable[[str], str]] = None,
    convert_manifest: Optional[Callable[[str], str]] = None,
) -> UpgradeResult:
    """Converts the files of a QTI 2.x package to QTI 3. Files that are already QTI 3 and non-QTI files are kept.

    :param files: file contents by package path.
    :param item_transforms: post-processing of upgraded items; default ``transforms.DEFAULT_ITEM_TRANSFORMS``.
    :param sync_identifiers: give item refs in tests (and their manifest resources) the identifier of the item.
    :param extract_shared_stimuli: move content repeated across items into shared stimuli; True or a dict of
        ``extract_shared_stimuli`` options (min_text_length, similarity_threshold, stimulus_folder).
    :param convert_item: overrides the item conversion (QTI 2 XML in, QTI 3 XML out); likewise the others.
    """
    convert_item = convert_item or (lambda xml: upgrade_item(xml, item_transforms))
    convert_test = convert_test or upgrade_qti2_to_qti3
    convert_stimulus = convert_stimulus or upgrade_qti2_to_qti3
    convert_manifest = convert_manifest or convert_manifest_to_qti3

    processed: Dict[str, Tuple[FileContent, str]] = {}
    for path, content in files.items():
        if not path.lower().endswith(".xml"):
            processed[path] = (content, "other")
            continue
        xml = to_text(content)
        root = root_local_name(xml)
        try:
            if root in ("qti-assessment-test", "assessmentTest"):
                processed[path] = (convert_test(xml) if root == "assessmentTest" else content, "test")
            elif root in ("qti-assessment-item", "assessmentItem"):
                processed[path] = (convert_item(xml) if root == "assessmentItem" else content, "item")
            elif root in ("qti-assessment-stimulus", "assessmentStimulus"):
                processed[path] = (convert_stimulus(xml) if root == "assessmentStimulus" else content, "stimulus")
            elif is_manifest(path):
                processed[path] = (convert_manifest(xml), "manifest")
            else:
                processed[path] = (content, "other")
        except etree.XMLSyntaxError as error:
            raise ValueError(f"{path}: {error}") from error

    if sync_identifiers:
        _sync_item_ref_identifiers(processed)
    report = None
    if extract_shared_stimuli:
        options = extract_shared_stimuli if isinstance(extract_shared_stimuli, Mapping) else {}
        processed, report = _extract_shared_stimuli(processed, **options)  # type: ignore[arg-type]
    return UpgradeResult({path: content for path, (content, _) in processed.items()}, report)


# ---------------------------------------------------------------------------------------------------------------------
# QTI 3 -> QTI 2.1


def convert_manifest_to_qti21(manifest_xml: XmlInput, inlined_stimulus_hrefs: Iterable[str] = ()) -> str:
    """Converts a QTI 3 imsmanifest.xml to QTI 2.1.

    Stimulus resources whose file was inlined into the items are removed; their dependencies and files are moved to
    the items that depended on them.
    """
    inlined = {normalize_path(href) for href in inlined_stimulus_hrefs}
    root = parse(manifest_xml)
    mapping = {QTI3_IMSCP_NAMESPACE: IMSCP21_NAMESPACE, QTI3_METADATA_NAMESPACE: QTI21_METADATA_NAMESPACE}

    def tag_for(el: etree._Element) -> str:
        uri = namespace(el)
        return f"{{{mapping[uri]}}}{local_name(el)}" if uri in mapping else tag_name(el)

    nsmap = {prefix: mapping.get(uri, uri) for prefix, uri in root.nsmap.items()}
    nsmap["xsi"] = XSI_NAMESPACE
    attributes = [(n, v) for n, v in root.attrib.items() if n != _SCHEMA_LOCATION]
    manifest = _rebuild_document(root, tag_for, nsmap, attributes + [(_SCHEMA_LOCATION, IMSCP21_SCHEMA_LOCATION)])
    if local_name(manifest) == "manifest":
        _set_metadata_schema(manifest, "QTIv2.1 Package", "1.0.0")

    resources = iter_by_local_name(manifest, "resource")
    inlined_stimuli: Dict[str, etree._Element] = {}
    for resource in resources:
        resource_type = resource.get("type") or ""
        if re.search("stimulus", resource_type, re.IGNORECASE):
            if normalize_path(resource.get("href") or "") in inlined:
                inlined_stimuli[resource.get("identifier") or ""] = resource
            else:
                resource.set("type", "webcontent")
        else:
            resource.set("type", qti3_resource_type_to_qti21(resource_type))

    def children(el: etree._Element, name: str) -> List[etree._Element]:
        return iter_by_local_name(el, name)

    for resource in resources:
        for dependency in children(resource, "dependency"):
            stimulus = inlined_stimuli.get(dependency.get("identifierref") or "")
            if stimulus is None:
                continue
            existing_dependencies = {d.get("identifierref") for d in children(resource, "dependency")}
            existing_files = {normalize_path(f.get("href") or "") for f in children(resource, "file")}
            for file in children(stimulus, "file"):
                href = normalize_path(file.get("href") or "")
                if href != normalize_path(stimulus.get("href") or "") and href not in existing_files:
                    etree.SubElement(resource, _same_namespace(resource, "file")).set("href", href)
            for stimulus_dependency in children(stimulus, "dependency"):
                ref = stimulus_dependency.get("identifierref")
                if ref and ref not in existing_dependencies:
                    new = etree.Element(_same_namespace(resource, "dependency"), {"identifierref": ref})
                    dependency.addprevious(new)
            _remove_element(dependency)
    for stimulus in inlined_stimuli.values():
        _remove_element(stimulus)
    return serialize_document(manifest)


def _same_namespace(el: etree._Element, name: str) -> str:
    return f"{{{namespace(el)}}}{name}" if namespace(el) else name


def _remove_element(el: etree._Element) -> None:
    """Removes an element with the whitespace before it (keeps manifests tidy)."""
    parent = el.getparent()
    if parent is None:
        return
    previous = el.getprevious()
    if el.tail is not None and not el.tail.strip():
        # keep the indentation of what follows, drop this element's own
        if previous is not None:
            previous.tail = el.tail
        else:
            parent.text = el.tail
        el.tail = None
    parent.remove(el)


def add_shared_vocabulary_stylesheet_to_manifest(
    manifest_xml: XmlInput, manifest_path: str, stylesheet_path: str, item_paths: Iterable[str]
) -> str:
    """Registers the shared vocabulary stylesheet in the manifest and adds it as a dependency of the given items."""
    root = parse(manifest_xml)
    resources_element = next(iter(iter_by_local_name(root, "resources")), None)
    if resources_element is None:
        return to_text(manifest_xml)
    uri = namespace(resources_element)

    def tag(name: str) -> str:
        return f"{{{uri}}}{name}" if uri else name

    manifest_dir = dirname(manifest_path)

    def package_path_of(href: str) -> str:
        return normalize_path(join_path(manifest_dir, href))

    resources = iter_by_local_name(root, "resource")
    existing = next((r for r in resources if package_path_of(r.get("href") or "") == stylesheet_path), None)
    resource_id = (existing.get("identifier") if existing is not None else None) or "QTI3_SHARED_VOCABULARY_CSS"
    if existing is not None:
        existing.set("identifier", resource_id)
    if existing is None:
        href = relative_path(manifest_dir, stylesheet_path)
        resource = etree.SubElement(
            resources_element, tag("resource"), {"identifier": resource_id, "type": "webcontent", "href": href}
        )
        etree.SubElement(resource, tag("file"), {"href": href})
    items = {normalize_path(p) for p in item_paths}
    for resource in resources:
        if package_path_of(resource.get("href") or "") not in items:
            continue
        if not any(d.get("identifierref") == resource_id for d in iter_by_local_name(resource, "dependency")):
            etree.SubElement(resource, tag("dependency"), {"identifierref": resource_id})
    return serialize_document(root)


@dataclass
class Qti21FileContext:
    #: Path of the file inside the package.
    path: str
    #: Resolves a stimulus href relative to this file to its QTI 3 XML (and marks it as inlined).
    resolve_stimulus: Callable[[str], Optional[str]]
    #: Href (relative to this file) of the shared vocabulary stylesheet, unless it is switched off.
    shared_vocabulary_stylesheet_href: Optional[str] = None


@dataclass
class Qti21PackageResult:
    files: PackageFiles
    warnings: List[Qti21Warning] = field(default_factory=list)


def default_convert_file_to_qti21(xml: str, context: Qti21FileContext) -> Qti21ConversionResult:
    return convert_qti3_to_qti21(
        xml,
        resolve_stimulus=context.resolve_stimulus,
        file_path=context.path,
        shared_vocabulary_stylesheet_href=context.shared_vocabulary_stylesheet_href,
    )


def downgrade_package_files(
    files: Mapping[str, FileContent],
    inject_shared_vocabulary_stylesheet: bool = True,
    convert_item: Callable[[str, Qti21FileContext], Qti21ConversionResult] = default_convert_file_to_qti21,
    convert_test: Callable[[str, Qti21FileContext], Qti21ConversionResult] = default_convert_file_to_qti21,
    convert_manifest: Callable[[str, Set[str]], str] = convert_manifest_to_qti21,
) -> Qti21PackageResult:
    """Converts all files of a QTI 3 package to QTI 2.1.

    Shared stimuli are inlined into the items that reference them and removed from the package. Non-QTI files (and
    QTI 2.x files) are passed through unchanged.

    :param inject_shared_vocabulary_stylesheet: add the QTI 3 shared vocabulary stylesheet
        (qti3-shared-vocabulary.css) to the package and to every item that uses qti-* classes, so QTI 2.1 players can
        style them.
    :param convert_item: overrides the item conversion; likewise convert_test. convert_manifest receives the package
        paths of the stimuli that were inlined into items.
    """
    warnings: List[Qti21Warning] = []
    xml_files = {path: to_text(content) for path, content in files.items() if path.lower().endswith(".xml")}
    by_normalized_path = {normalize_path(path): path for path in xml_files}

    inlined_stimulus_hrefs: Set[str] = set()
    items_with_shared_vocabulary: List[str] = []
    # The package root is the folder of the manifest; the stylesheet goes there
    manifest_path = next((p for p in xml_files if is_manifest(p)), None)
    stylesheet_path = join_path(dirname(manifest_path or ""), QTI3_SHARED_VOCABULARY_CSS_PATH)
    output: PackageFiles = {}
    stimulus_paths: List[str] = []

    for path, content in files.items():
        xml = xml_files.get(path)
        root = "" if xml is None else root_local_name(xml)
        if root == "qti-assessment-stimulus":
            stimulus_paths.append(path)
            continue
        # Non-QTI files (and QTI 2.x files) are passed through; the manifest is converted last
        if xml is None or is_manifest(path) or not root.startswith("qti-"):
            output[path] = content
            continue

        def resolve(href: str, base: str = path) -> Optional[str]:
            stimulus_path = by_normalized_path.get(normalize_path(join_path(dirname(base), href)))
            if stimulus_path is None:
                return None
            inlined_stimulus_hrefs.add(normalize_path(stimulus_path))
            return xml_files[stimulus_path]

        context = Qti21FileContext(
            path,
            resolve,
            relative_path(dirname(path), stylesheet_path) if inject_shared_vocabulary_stylesheet else None,
        )
        convert = convert_test if root == "qti-assessment-test" else convert_item
        result = convert(xml, context)
        output[path] = result.xml
        warnings.extend(result.warnings)
        if any(w.code == "shared-vocabulary-stylesheet" for w in result.warnings):
            items_with_shared_vocabulary.append(path)

    # Stimuli that no item referenced are kept (as a QTI 2.2 assessmentStimulus)
    for path in stimulus_paths:
        if normalize_path(path) in inlined_stimulus_hrefs:
            continue
        result = convert_qti3_to_qti21(xml_files[path], file_path=path)
        output[path] = result.xml
        warnings.extend(result.warnings)

    add_stylesheet = bool(items_with_shared_vocabulary)
    # An existing stylesheet with that name in the package is kept (and used)
    if add_stylesheet and stylesheet_path not in output:
        output[stylesheet_path] = QTI3_SHARED_VOCABULARY_CSS

    for path in xml_files:
        if is_manifest(path):
            manifest = convert_manifest(xml_files[path], inlined_stimulus_hrefs)
            if add_stylesheet:
                manifest = add_shared_vocabulary_stylesheet_to_manifest(
                    manifest, path, stylesheet_path, items_with_shared_vocabulary
                )
            output[path] = manifest

    return Qti21PackageResult(output, warnings)


# ---------------------------------------------------------------------------------------------------------------------
# Reading and writing packages


def _skip(path: str) -> bool:
    return "__MACOSX" in path or path.endswith(".DS_Store")


def read_package(source: PackageSource) -> PackageFiles:
    """Reads a package from a zip file (path, bytes or binary file object), a folder, or a mapping of files."""
    if isinstance(source, Mapping):
        return dict(source)
    if isinstance(source, (str, os.PathLike)) and Path(source).is_dir():
        folder = Path(source)
        return {
            file.relative_to(folder).as_posix(): file.read_bytes()
            for file in sorted(folder.rglob("*"))
            if file.is_file() and not _skip(file.relative_to(folder).as_posix())
        }
    zip_source = io.BytesIO(source) if isinstance(source, bytes) else source
    with zipfile.ZipFile(zip_source) as archive:  # type: ignore[arg-type]
        return {
            info.filename: archive.read(info)
            for info in archive.infolist()
            if not info.is_dir() and not _skip(info.filename)
        }


def package_to_zip(files: Mapping[str, FileContent]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for path, content in files.items():
            archive.writestr(path, content.encode("utf-8") if isinstance(content, str) else content)
    return buffer.getvalue()


def write_package(files: Mapping[str, FileContent], target: Union[str, os.PathLike[str]]) -> None:
    """Writes a package to a .zip file, or to a folder for any other path."""
    target_path = Path(target)
    if target_path.suffix.lower() == ".zip":
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_bytes(package_to_zip(files))
        return
    for path, content in files.items():
        file = target_path / path
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes(content.encode("utf-8") if isinstance(content, str) else content)


def upgrade_package(
    source: PackageSource, target: Optional[Union[str, os.PathLike[str]]] = None, **options
) -> UpgradeResult:
    """Converts a QTI 2.x package (zip, folder or files) to QTI 3; writes it to ``target`` (.zip or folder) if given.

    Options are those of ``upgrade_package_files``.
    """
    result = upgrade_package_files(read_package(source), **options)
    if target is not None:
        write_package(result.files, target)
    return result


def downgrade_package(
    source: PackageSource, target: Optional[Union[str, os.PathLike[str]]] = None, **options
) -> Qti21PackageResult:
    """Converts a QTI 3 package (zip, folder or files) to QTI 2.1; writes it to ``target`` (.zip or folder) if given.

    Options are those of ``downgrade_package_files``.
    """
    result = downgrade_package_files(read_package(source), **options)
    if target is not None:
        write_package(result.files, target)
    return result


__all__ = [
    "PackageFiles",
    "Qti21FileContext",
    "Qti21PackageResult",
    "UpgradeResult",
    "add_shared_vocabulary_stylesheet_to_manifest",
    "convert_manifest_to_qti21",
    "convert_manifest_to_qti3",
    "default_convert_file_to_qti21",
    "downgrade_package",
    "downgrade_package_files",
    "is_manifest",
    "package_to_zip",
    "read_package",
    "upgrade_item",
    "upgrade_package",
    "upgrade_package_files",
    "write_package",
]
