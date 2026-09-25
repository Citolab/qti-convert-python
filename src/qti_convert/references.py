"""Repairs broken file references in the items, tests and stimuli of a QTI package (QTI 2.x or 3).

Every reference (src, href, data, poster, template-location, ...) is resolved in this order:

1. relative to the file that contains it, as the specs require (a case-only mismatch is corrected);
2. relative to the package root, the folder of imsmanifest.xml. Many exports write package-root-relative paths
   (``mediafiles/a.png`` in ``questions/q1.xml``) or root-absolute ones (``/templates/rp.xml``);
3. by file name among all files in the package, when exactly one file matches best.

A reference found in step 2 or 3 is rewritten to the correct path relative to its file. References that can't be
found are left as they are and reported.

``PackageReferenceResolver`` does the resolving with only the file paths of the package, for code that reads a
package file by file instead of all at once.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple, Union
from urllib.parse import quote, unquote

from ._xml import is_element, local_name, parse, serialize_document, to_text
from .package import FileContent, PackageFiles, PackageSource, is_manifest, read_package, root_local_name, write_package
from .paths import dirname, join_path, normalize_path, relative_path

#: Attributes that hold a file reference.
REFERENCE_ATTRIBUTES: Tuple[str, ...] = (
    "src",
    "href",
    "data",
    "poster",
    "primary-path",
    "fallback-path",
    "template-location",
    "templateLocation",
    "backgroundimg",
    "background-img",
    "template-src",
    "templateSrc",
    "template-url",
    "templateurl",
    "value",
    "file",
    "sound",
    "video",
    "image",
)
#: Attributes that only count as a reference when the value looks like a file name (<param value="true"/> doesn't).
_LOOSE_ATTRIBUTES = {"value", "file", "sound", "video", "image"}
#: Module paths may leave out the .js extension.
_MODULE_ATTRIBUTES = {"primary-path", "fallback-path"}
_QTI_ROOTS = {
    "assessmentItem",
    "assessmentTest",
    "assessmentStimulus",
    "qti-assessment-item",
    "qti-assessment-test",
    "qti-assessment-stimulus",
}
_NOT_A_PATH = re.compile(r"^([a-z][a-z0-9+.-]*:|//|#)", re.IGNORECASE)
_FILE_NAME = re.compile(r"^[^\s?#]*\.[A-Za-z0-9]{1,5}([?#].*)?$")
_URL_SAFE = "/!$&'()*+,;=:@-._~"


@dataclass(frozen=True)
class FixedReference:
    #: Package path of the file that holds the reference.
    file: str
    element: str
    attribute: str
    value: str
    new_value: str
    #: Package path of the file the reference now points to.
    target: str
    #: How it was found: "case" (relative, but wrong case), "package-root" or "file-name".
    method: str


@dataclass(frozen=True)
class UnresolvedReference:
    file: str
    element: str
    attribute: str
    value: str
    #: Files with the same name that matched equally well (empty when nothing matched).
    candidates: Tuple[str, ...] = ()


@dataclass
class ReferenceFixResult:
    files: PackageFiles
    fixed: List[FixedReference] = field(default_factory=list)
    unresolved: List[UnresolvedReference] = field(default_factory=list)


@dataclass(frozen=True)
class ReferenceResolution:
    """Where a reference points, as found by ``PackageReferenceResolver.resolve``."""

    #: Package path of the file it points to; None when no file was found.
    target: Optional[str]
    #: "" when the reference is right as written, else how it was found: "case" (relative, but wrong case),
    #: "package-root" or "file-name".
    method: str = ""
    #: The value to write instead, relative to the referencing file; None when it needs no change or wasn't found.
    new_value: Optional[str] = None
    #: When not found: files with the same name that matched equally well.
    candidates: Tuple[str, ...] = ()


class PackageReferenceResolver:
    """Resolves file references against the paths of a package, without needing the file contents.

    Use it where a package is read file by file (e.g. from a zip with ``archive.namelist()``);
    ``fix_package_references_files`` uses it for whole packages. A resolved target is always a path in ``paths``,
    so a reference can never point outside the package.

    :param paths: the file paths of the package (folder entries ending in / are ignored).
    :param root_dir: the package root; default the folder of imsmanifest.xml, or the top when there is none.
    :param search_by_file_name: also look for a reference by its file name anywhere in the package.
    """

    def __init__(self, paths: Iterable[str], root_dir: Optional[str] = None, search_by_file_name: bool = True):
        self._paths: Set[str] = set()
        self._lower: Dict[str, str] = {}
        self._by_name: Dict[str, List[str]] = {}
        for path in paths:
            if path.endswith("/") or path in self._paths:
                continue
            self._paths.add(path)
            self._lower.setdefault(path.lower(), path)
            self._by_name.setdefault(path.rsplit("/", 1)[-1].lower(), []).append(path)
        if root_dir is None:
            manifest = next((p for p in sorted(self._paths, key=len) if is_manifest(p)), None)
            root_dir = dirname(manifest) if manifest else ""
        self.root_dir = normalize_path(root_dir)
        self.search_by_file_name = search_by_file_name

    @staticmethod
    def is_reference(value: str, attribute: Optional[str] = None) -> bool:
        """Whether a value is a reference to a file in the package (not a URL, data: URI or fragment).

        For the loose attributes (value, file, sound, video, image) the value must also look like a file name.
        """
        value = value.strip()
        if not value or (_NOT_A_PATH.match(value) and not _is_local_path(value)):
            return False
        return attribute not in _LOOSE_ATTRIBUTES or bool(_FILE_NAME.match(value))

    def resolve(self, file_path: str, value: str, attribute: Optional[str] = None) -> Optional[ReferenceResolution]:
        """Resolves a reference found in the file at ``file_path``; None when the value is not a file reference.

        :param attribute: the attribute the value came from; primary-path and fallback-path may leave out .js, and
            the loose attributes only count when the value looks like a file name.
        """
        if not self.is_reference(value, attribute):
            return None
        module = attribute in _MODULE_ATTRIBUTES
        file_dir = dirname(file_path)
        target, method, encode, candidates = self._find_target(file_dir, value, module)
        if method == "file-name" and not self.search_by_file_name:
            target, method, candidates = None, "", []
        if target is None:
            return ReferenceResolution(None, candidates=tuple(candidates))
        if not method:
            return ReferenceResolution(target)
        split = re.search(r"[?#]", value.strip())
        suffix = value.strip()[split.start() :] if split else ""
        target_ref = (
            target[: -len(".js")] if module and not _has_extension(value) and target.endswith(".js") else target
        )
        new_path = relative_path(file_dir, target_ref)
        new_value = (quote(new_path, safe=_URL_SAFE) if encode else new_path) + suffix
        return ReferenceResolution(target, method, new_value)

    def _find(self, path: str, module: bool) -> Tuple[Optional[str], bool]:
        """(package path, exact) of a file, trying a .js extension for modules; exact is False for a case mismatch."""
        for candidate in (path, f"{path}.js") if module else (path,):
            if candidate in self._paths:
                return candidate, True
            if candidate.lower() in self._lower:
                return self._lower[candidate.lower()], False
        return None, False

    def _by_file_name(self, path: str, module: bool) -> List[str]:
        name = path.rsplit("/", 1)[-1].lower()
        matches = list(self._by_name.get(name, []))
        if module and not matches:
            matches = list(self._by_name.get(f"{name}.js", []))
        if len(matches) <= 1:
            return matches
        # prefer the files whose folders match the most trailing folders of the reference
        wanted = [p.lower() for p in path.split("/") if p not in ("", ".", "..")]

        def score(match: str) -> int:
            parts = match.lower().split("/")
            common = 0
            while common < min(len(parts), len(wanted)) and parts[-1 - common] == wanted[-1 - common]:
                common += 1
            return common

        best = max(score(m) for m in matches)
        return sorted(m for m in matches if score(m) == best)

    def _find_target(self, file_dir: str, value: str, module: bool) -> Tuple[Optional[str], str, bool, List[str]]:
        """(target, method, encode, candidates); target None means not found, method "" means it is fine."""
        raw = value.strip()
        split = re.search(r"[?#]", raw)
        path = (raw[: split.start()] if split else raw).replace("\\", "/")
        decoded = unquote(path)

        def by_name() -> Tuple[Optional[str], str, bool, List[str]]:
            matches = self._by_file_name(decoded, module)
            return (matches[0], "file-name", decoded != path, []) if len(matches) == 1 else (None, "", False, matches)

        if _is_local_path(raw):
            return by_name()
        candidates = [(path, False)] + ([(decoded, True)] if decoded != path else [])
        if not path.startswith("/"):
            for candidate, encode in candidates:
                target, exact = self._find(normalize_path(join_path(file_dir, candidate)), module)
                if target:
                    return target, "" if exact else "case", encode, []
        for candidate, encode in candidates:
            target, _ = self._find(normalize_path(join_path(self.root_dir, candidate.lstrip("/"))), module)
            if target:
                return target, "package-root", encode, []
        return by_name()


def _is_local_path(value: str) -> bool:
    return bool(re.match(r"^(file:|[a-z]:[\\/])", value, re.IGNORECASE))


def fix_package_references_files(
    files: Mapping[str, FileContent],
    attributes: Sequence[str] = REFERENCE_ATTRIBUTES,
    search_by_file_name: bool = True,
) -> ReferenceFixResult:
    """Repairs the file references in the items, tests and stimuli of a package (a {path: content} dict).

    Files without broken references are returned unchanged (the same object).

    :param attributes: attribute names that hold references.
    :param search_by_file_name: also look for a reference by its file name anywhere in the package.
    """
    resolver = PackageReferenceResolver(files, search_by_file_name=search_by_file_name)
    wanted = set(attributes)
    result = ReferenceFixResult(dict(files))

    for path, content in files.items():
        if not path.lower().endswith(".xml") or root_local_name(to_text(content)) not in _QTI_ROOTS:
            continue
        root = parse(content)
        # (attribute name as written, old value) -> new value; the same reference always resolves the same way
        replacements: Dict[Tuple[str, str], str] = {}
        for el in root.iter():
            if not is_element(el):
                continue
            for name, value in list(el.attrib.items()):
                attribute = name.rsplit("}", 1)[-1]
                if attribute not in wanted:
                    continue
                resolution = resolver.resolve(path, value, attribute)
                if resolution is None:
                    continue
                if resolution.target is None:
                    result.unresolved.append(
                        UnresolvedReference(path, local_name(el), attribute, value, resolution.candidates)
                    )
                    continue
                if resolution.new_value is None:
                    continue
                replacements[(_written_name(el, name), value)] = resolution.new_value
                result.fixed.append(
                    FixedReference(
                        path,
                        local_name(el),
                        attribute,
                        value,
                        resolution.new_value,
                        resolution.target,
                        resolution.method,
                    )
                )
        if replacements:
            result.files[path] = _rewrite(content, replacements, root)
    return result


def _written_name(el, name: str) -> str:
    """The attribute name as it appears in the source (xlink:href rather than {...xlink}href)."""
    if not name.startswith("{"):
        return name
    uri, local = name[1:].split("}", 1)
    prefix = next((p for p, u in el.nsmap.items() if u == uri and p), None)
    return f"{prefix}:{local}" if prefix else local


def _escape(value: str, quote_char: str) -> str:
    value = value.replace("&", "&amp;").replace("<", "&lt;")
    return value.replace('"', "&quot;") if quote_char == '"' else value.replace("'", "&apos;")


def _rewrite(content: FileContent, replacements: Dict[Tuple[str, str], str], root) -> FileContent:
    """Replaces only the changed attribute values, so the rest of the file stays byte-for-byte the same."""
    try:
        text = content.decode("utf-8") if isinstance(content, bytes) else content
    except UnicodeDecodeError:
        return serialize_document(root)
    for (name, value), new_value in replacements.items():
        text = _replace_attribute(text, name, value, new_value)
    return text.encode("utf-8") if isinstance(content, bytes) else text


def _replace_attribute(text: str, name: str, value: str, new_value: str) -> str:
    pattern = re.compile(rf"(\s{re.escape(name)}\s*=\s*)([\"'])(.*?)\2", re.S)

    def replace(match: re.Match[str]) -> str:
        quote_char = match.group(2)
        if match.group(3) not in (value, _escape(value, quote_char)):
            return match.group(0)
        return f"{match.group(1)}{quote_char}{_escape(new_value, quote_char)}{quote_char}"

    return pattern.sub(replace, text)


def _has_extension(value: str) -> bool:
    return bool(os.path.splitext(re.split(r"[?#]", value)[0])[1])


def fix_package_references(
    source: PackageSource, target: Optional[Union[str, os.PathLike[str]]] = None, **options
) -> ReferenceFixResult:
    """Repairs the file references of a package (zip, folder or files); writes it to ``target`` if given.

    Options are those of ``fix_package_references_files``.
    """
    result = fix_package_references_files(read_package(source), **options)
    if target is not None:
        write_package(result.files, target)
    return result
