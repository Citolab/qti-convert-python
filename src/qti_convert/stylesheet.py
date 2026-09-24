"""The QTI 3 shared vocabulary stylesheet that the QTI 2.1 package conversion adds for QTI 2.1 players.

It is the 1EdTech QTI 3 shared css (https://www.imsglobal.org/sites/default/files/spec/qti/v3/guide/img/qti3p0.css)
without its page-level layout section, plus a row-relative grid and fixes for QTI 2.1 players. See the header of the
stylesheet.
"""

from importlib.resources import files

#: File name of the stylesheet the QTI 3 to QTI 2.1 package conversion adds next to the manifest.
QTI3_SHARED_VOCABULARY_CSS_PATH = "qti3-shared-vocabulary.css"

QTI3_SHARED_VOCABULARY_CSS: str = (
    files("qti_convert").joinpath("data").joinpath(QTI3_SHARED_VOCABULARY_CSS_PATH).read_text(encoding="utf-8")
)
