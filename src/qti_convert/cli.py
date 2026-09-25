"""Command line interface: ``qti-convert upgrade|downgrade INPUT OUTPUT``."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import List, Optional, Sequence

from . import __version__
from .downgrade import Qti21Warning, convert_qti3_to_qti21
from .package import downgrade_package, root_local_name, upgrade_item, upgrade_package
from .references import fix_package_references
from .upgrade import upgrade_qti2_to_qti3


def _is_package(path: Path) -> bool:
    return path.is_dir() or path.suffix.lower() == ".zip"


def _print_warnings(warnings: Sequence[Qti21Warning], quiet: bool) -> None:
    if quiet:
        return
    for warning in warnings:
        location = f"{warning.file}: " if warning.file else ""
        print(f"warning [{warning.code}] {location}{warning.message}", file=sys.stderr)


def _upgrade(args: argparse.Namespace) -> int:
    source, target = Path(args.input), Path(args.output)
    if _is_package(source):
        result = upgrade_package(
            source,
            target,
            item_transforms=[] if args.no_transforms else None,
            extract_shared_stimuli=args.extract_shared_stimuli,
        )
        if result.shared_stimuli and not args.quiet:
            for stimulus in result.shared_stimuli.stimuli:
                print(f"shared stimulus {stimulus.path}: {', '.join(stimulus.items)}", file=sys.stderr)
            for duplicate in result.shared_stimuli.near_duplicates:
                print(f"near-duplicate content ({duplicate.similarity}): {' '.join(duplicate.items)}", file=sys.stderr)
        return 0
    xml = source.read_bytes()
    output = upgrade_qti2_to_qti3(xml) if args.no_transforms else _upgrade_single(xml)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(output, encoding="utf-8")
    return 0


def _upgrade_single(xml: bytes) -> str:
    # items get the same post-processing as in a package; tests and stimuli are only upgraded
    is_item = root_local_name(xml.decode("utf-8", errors="replace")) == "assessmentItem"
    return upgrade_item(xml) if is_item else upgrade_qti2_to_qti3(xml)


def _downgrade(args: argparse.Namespace) -> int:
    source, target = Path(args.input), Path(args.output)
    if _is_package(source):
        result = downgrade_package(
            source, target, inject_shared_vocabulary_stylesheet=not args.no_shared_vocabulary_css
        )
        _print_warnings(result.warnings, args.quiet)
        return 0
    result = convert_qti3_to_qti21(source.read_bytes(), file_path=source.name)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(result.xml, encoding="utf-8")
    _print_warnings(result.warnings, args.quiet)
    return 0


def _fix_references(args: argparse.Namespace) -> int:
    result = fix_package_references(
        Path(args.input), Path(args.output), search_by_file_name=not args.no_file_name_search
    )
    if not args.quiet:
        for fixed in result.fixed:
            print(f"fixed [{fixed.method}] {fixed.file}: {fixed.value} -> {fixed.new_value}", file=sys.stderr)
        for unresolved in result.unresolved:
            candidates = f" (candidates: {', '.join(unresolved.candidates)})" if unresolved.candidates else ""
            print(f"not found {unresolved.file}: {unresolved.value}{candidates}", file=sys.stderr)
        print(f"{len(result.fixed)} reference(s) fixed, {len(result.unresolved)} not found", file=sys.stderr)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="qti-convert",
        description="Convert QTI 2.x to QTI 3.0 (upgrade) and QTI 3.0 to QTI 2.1 (downgrade), or repair the file "
        "references of a package (fix-references). "
        "INPUT and OUTPUT are a single XML file, a package .zip or a package folder.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)

    upgrade = commands.add_parser("upgrade", help="QTI 2.x -> QTI 3.0")
    upgrade.add_argument("input", help="QTI 2.x XML file, package .zip or package folder")
    upgrade.add_argument("output", help="output XML file, .zip or folder")
    upgrade.add_argument(
        "--no-transforms", action="store_true", help="only convert; skip the post-processing of upgraded items"
    )
    upgrade.add_argument(
        "--extract-shared-stimuli",
        action="store_true",
        help="move content repeated across items (e.g. a reading passage) into shared stimuli",
    )
    upgrade.add_argument("-q", "--quiet", action="store_true", help="do not print reports")
    upgrade.set_defaults(run=_upgrade)

    downgrade = commands.add_parser("downgrade", help="QTI 3.0 -> QTI 2.1")
    downgrade.add_argument("input", help="QTI 3 XML file, package .zip or package folder")
    downgrade.add_argument("output", help="output XML file, .zip or folder")
    downgrade.add_argument(
        "--no-shared-vocabulary-css",
        action="store_true",
        help="do not add qti3-shared-vocabulary.css for items that use qti-* classes",
    )
    downgrade.add_argument("-q", "--quiet", action="store_true", help="do not print warnings")
    downgrade.set_defaults(run=_downgrade)

    fix = commands.add_parser(
        "fix-references",
        help="repair broken file references (images, stylesheets, ...) in a QTI 2.x or 3 package",
        description="Resolves every reference relative to its file, then relative to the package root, then by "
        "file name, and rewrites the ones that only resolve the second or third way.",
    )
    fix.add_argument("input", help="package .zip or package folder")
    fix.add_argument("output", help="output .zip or folder")
    fix.add_argument("--no-file-name-search", action="store_true", help="do not look for files by name")
    fix.add_argument("-q", "--quiet", action="store_true", help="do not print what was fixed")
    fix.set_defaults(run=_fix_references)
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    source = Path(args.input)
    if not source.exists():
        print(f"qti-convert: {source} does not exist", file=sys.stderr)
        return 2
    try:
        return args.run(args)
    except (ValueError, OSError) as error:
        print(f"qti-convert: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
