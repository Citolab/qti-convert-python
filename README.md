# qti-convert

Convert [QTI](https://www.1edtech.org/standards/qti) content between versions, in Python:

- **upgrade**: QTI 2.x (2.0, 2.1, 2.2) → QTI 3.0
- **downgrade**: QTI 3.0 → QTI 2.1

It converts single items, tests and stimuli as well as whole content packages (`imsmanifest.xml` with its items,
tests, stimuli and assets; as a `.zip`, a folder or a dict of files). It depends only on [lxml](https://lxml.de) and
works without XSLT.

This is the Python version of the conversions in the TypeScript [@citolab/qti-convert](https://github.com/Citolab/qti-convert)
package. It gives the same results and is tested against the same fixtures.

## Installation

```bash
pip install citolab-qti-convert
```

Requires Python 3.9 or newer.

## Command line

```bash
# QTI 2.x -> QTI 3.0
qti-convert upgrade package-qti2.zip package-qti3.zip
qti-convert upgrade item-qti2.xml item-qti3.xml
qti-convert upgrade ./qti2-folder ./qti3-folder --extract-shared-stimuli

# QTI 3.0 -> QTI 2.1 (warnings are printed to stderr)
qti-convert downgrade package-qti3.zip package-qti21.zip
qti-convert downgrade item-qti3.xml item-qti21.xml
```

`INPUT` and `OUTPUT` can each be an XML file, a `.zip`, or a folder. Run `qti-convert upgrade --help` or
`qti-convert downgrade --help` for all options.

## Python API

### Single documents

```python
from qti_convert import upgrade_qti2_to_qti3, convert_qti3_to_qti21

qti3_xml = upgrade_qti2_to_qti3(qti2_xml)        # str or bytes in, str out

result = convert_qti3_to_qti21(qti3_xml)
result.xml                                         # QTI 2.1 XML
for warning in result.warnings:                    # what could not be converted one-to-one
    print(warning.code, warning.message)
```

`convert_qti3_to_qti21` takes these options:

- `resolve_stimulus`: a function that returns the XML of a stimulus for the `href` of a
  `qti-assessment-stimulus-ref`. That stimulus is then inlined into the item body; without it, stimulus refs are
  removed with a warning.
- `shared_vocabulary_stylesheet_href`: adds a stylesheet link, so QTI 2.1 players can style the QTI 3 `qti-*`
  classes.
- `file_path`: tags the warnings with this path.

### Packages

```python
from qti_convert import upgrade_package, downgrade_package

# source: path to a .zip or folder, zip bytes, a binary file object, or a {path: content} dict
# target (optional): a .zip path or a folder
result = upgrade_package("package-qti2.zip", "package-qti3.zip")
result.files                                       # {path: content} of the converted package

result = downgrade_package("package-qti3.zip", "package-qti21.zip")
result.warnings
```

`upgrade_package_files` and `downgrade_package_files` do the same with a `{path: str | bytes}` dict in and out, without
any file I/O.

The package **upgrade**:

- converts items, tests, stimuli and the manifest (namespaces, schema version and resource types)
- runs post-processing transforms on the upgraded items (`qti_convert.transforms.DEFAULT_ITEM_TRANSFORMS`). They
  convert `<object>` media to `<img>`/`<video>`/`<audio>` and SSML to `data-ssml-*` spans, remove companion
  materials, set `min-choices="1"` on choice interactions, mark items without response processing as
  `external-scored`, and handle Dutch Extension Profile dialog triggers. Pass `item_transforms=[...]` to choose your
  own, or `[]` to skip them.
- gives item refs in tests the identifier of the item they point to, and updates the manifest to match
  (`sync_identifiers=True`)
- can optionally move content that is repeated across items (typically a reading passage) into shared
  `qti-assessment-stimulus` files (`extract_shared_stimuli=True`, or a dict with `min_text_length`,
  `similarity_threshold` and `stimulus_folder`). The result then includes a report of what was extracted and of
  near-duplicate content.

The package **downgrade**:

- converts every QTI 3 file, and passes non-QTI files and QTI 2.x files through unchanged
- inlines shared stimuli into the items that reference them, and removes them from the package and manifest
- adds `qti3-shared-vocabulary.css` next to the manifest and links it from the items that use `qti-*` classes
  (switch this off with `inject_shared_vocabulary_stylesheet=False`)
- accepts `convert_item`, `convert_test` and `convert_manifest` callbacks to override the default conversions

## What the downgrade changes

QTI 3.0 has features that QTI 2.1 lacks. The downgrade converts what it can and reports the rest as warnings
(`Qti21Warning.code`):

| Code | What happened |
| --- | --- |
| `stimulus-inlined` / `stimulus-unresolved` | a shared stimulus was inlined, or removed because it could not be found |
| `stimulus-standalone` | an `assessmentStimulus` exists only in QTI 2.2; QTI 2.1 players will not recognise it |
| `removed-element` / `removed-attribute` | a QTI 3-only element (e.g. `qti-catalog-info`) or attribute (e.g. `external-scored`) was removed |
| `data-attributes-removed` / `accessibility-attributes-removed` | `data-*`, `aria-*`, `role` and `dir` attributes were removed |
| `media-to-object` / `img-to-object` / `gap-text-to-gap-img` | HTML5 media and images in graphic interactions became `<object>`; an image-only gap text became a `gapImg` |
| `html5-element` | HTML5 elements such as `<section>` or `<figure>` became `<div>`/`<span>` |
| `ssml-removed` | SSML markup was removed; its text was kept |
| `pci` | a portable custom interaction was wrapped in a `customInteraction` |
| `shared-vocabulary-stylesheet` / `shared-vocabulary-classes` | `qti-*` classes are styled by the added stylesheet, or were kept without styling |
| `already-qti2` / `not-qti` | the input was left unchanged |

## Upgrade compared to the ETS XSLT

The upgrade follows `qti2xTo30.xsl` (ETS, Apache-2.0), with these fixes:

- `stimulusBody`, `durationLT`/`durationGTE` and a few other QTI 2.x elements that the XSLT does not list get their
  `qti-` name
- `testFeedback` content is wrapped in `qti-content-body`, like the other feedback elements
- `qti-rubric-block` gets the `use` attribute that QTI 3 requires
- elements are matched by local name, so prefixed QTI 2 elements convert correctly
- inline SVG stays in the SVG namespace, and a video `<object>` keeps its children once

HTML named entities such as `&nbsp;` are accepted in the input, even though XML does not define them.

## Development

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e '.[dev]'
pytest                 # the XSD validation tests download the IMS schemas once; set QTI_CONVERT_OFFLINE=1 to skip them
ruff check src tests && ruff format --check src tests
python -m build && twine check dist/*
```

### Releasing

1. Update `__version__` in `src/qti_convert/__init__.py` and `CHANGELOG.md`.
2. Commit, then tag and push: `git tag v0.1.0 && git push origin v0.1.0`.
3. The `publish` workflow builds the package and publishes it to PyPI through
   [trusted publishing](https://docs.pypi.org/trusted-publishers/). Configure it once on PyPI for this repository,
   with workflow `publish.yml` and environment `pypi`.

## License

GPL-3.0-only, like the TypeScript package. The upgrade is derived from `qti2xTo30.xsl` by ETS (Apache-2.0).
