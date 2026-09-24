# Changelog

## 0.1.0

First release: a Python port of the QTI 2 ↔ 3 conversions of [@citolab/qti-convert](https://github.com/Citolab/qti-convert) 0.7.1.

- `upgrade_qti2_to_qti3`: QTI 2.x item, test or stimulus to QTI 3.0
- `convert_qti3_to_qti21`: QTI 3.0 item, test or stimulus to QTI 2.1, with warnings
- Package conversion both ways (zip, folder or files), including manifests, item post-processing transforms,
  item ref identifier sync, shared stimulus extraction (upgrade) and inlining (downgrade), and the QTI 3 shared
  vocabulary stylesheet for QTI 2.1 players
- `qti-convert` command line tool

Differences from the TypeScript package:

- the package upgrade also converts standalone QTI 2.2 `assessmentStimulus` files and maps stimulus resource types in the manifest
- item ref identifier sync resolves item refs relative to the test file, and also updates manifest dependencies
- the Citolab question-bank cleanup (`qbCleanup`) is not included
