"""Convert QTI 2.x to QTI 3.0 and QTI 3.0 to QTI 2.1.

Single documents::

    from qti_convert import upgrade_qti2_to_qti3, convert_qti3_to_qti21

    qti3_xml = upgrade_qti2_to_qti3(qti2_xml)
    result = convert_qti3_to_qti21(qti3_xml)
    result.xml, result.warnings

Content packages (zip, folder or a dict of files)::

    from qti_convert import upgrade_package, downgrade_package

    upgrade_package("qti2.zip", "qti3.zip")
    warnings = downgrade_package("qti3.zip", "qti21.zip").warnings

Broken file references in a package (QTI 2.x or 3), e.g. package-root-relative image paths::

    from qti_convert import fix_package_references

    result = fix_package_references("package.zip", "package-fixed.zip")
    result.fixed, result.unresolved
"""

from .downgrade import Qti21ConversionResult, Qti21Warning, convert_qti3_to_qti21
from .package import (
    Qti21FileContext,
    Qti21PackageResult,
    UpgradeResult,
    add_shared_vocabulary_stylesheet_to_manifest,
    convert_manifest_to_qti3,
    convert_manifest_to_qti21,
    downgrade_package,
    downgrade_package_files,
    package_to_zip,
    read_package,
    upgrade_item,
    upgrade_package,
    upgrade_package_files,
    write_package,
)
from .references import (
    REFERENCE_ATTRIBUTES,
    FixedReference,
    PackageReferenceResolver,
    ReferenceFixResult,
    ReferenceResolution,
    UnresolvedReference,
    fix_package_references,
    fix_package_references_files,
)
from .shared_stimuli import ExtractedStimulus, NearDuplicateContent, SharedStimuliReport, extract_shared_stimuli
from .stylesheet import QTI3_SHARED_VOCABULARY_CSS, QTI3_SHARED_VOCABULARY_CSS_PATH
from .transforms import DEFAULT_ITEM_TRANSFORMS, apply_transforms
from .upgrade import upgrade_qti2_to_qti3

__version__ = "0.2.0"

__all__ = [
    "DEFAULT_ITEM_TRANSFORMS",
    "QTI3_SHARED_VOCABULARY_CSS",
    "QTI3_SHARED_VOCABULARY_CSS_PATH",
    "REFERENCE_ATTRIBUTES",
    "FixedReference",
    "PackageReferenceResolver",
    "ReferenceFixResult",
    "ReferenceResolution",
    "UnresolvedReference",
    "ExtractedStimulus",
    "NearDuplicateContent",
    "Qti21ConversionResult",
    "Qti21FileContext",
    "Qti21PackageResult",
    "Qti21Warning",
    "SharedStimuliReport",
    "UpgradeResult",
    "add_shared_vocabulary_stylesheet_to_manifest",
    "apply_transforms",
    "convert_manifest_to_qti21",
    "convert_manifest_to_qti3",
    "convert_qti3_to_qti21",
    "downgrade_package",
    "downgrade_package_files",
    "extract_shared_stimuli",
    "fix_package_references",
    "fix_package_references_files",
    "package_to_zip",
    "read_package",
    "upgrade_item",
    "upgrade_package",
    "upgrade_package_files",
    "upgrade_qti2_to_qti3",
    "write_package",
]
