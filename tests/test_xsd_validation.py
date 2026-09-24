"""Validates the output against the official IMS XSDs (skipped when they can't be downloaded).

The schemas are downloaded once and cached; imported MathML/SSML/XInclude/APIP schemas are replaced by lax stubs, so
the QTI part is checked strictly. The TAO fixtures are left out: their QTI 2.2 input is itself not valid.
"""

import os
import re
import tempfile
import urllib.request
from pathlib import Path

import pytest
from lxml import etree
from test_shared_stimuli import build_package

from qti_convert import (
    convert_qti3_to_qti21,
    downgrade_package_files,
    extract_shared_stimuli,
    read_package,
    upgrade_qti2_to_qti3,
)

QTI21_XSD_URL = "https://www.imsglobal.org/xsd/qti/qtiv2p1/imsqti_v2p1p2.xsd"
QTI3_XSD_URL = "https://purl.imsglobal.org/spec/qti/v3p0/schema/xsd/imsqti_asiv3p0p1_v1p0.xsd"
XML_XSD_URL = "https://www.w3.org/2001/xml.xsd"
CACHE_DIR = Path(tempfile.gettempdir()) / "qti-convert-python-xsd-cache"
LAX_NAMESPACES = [
    "http://www.w3.org/1998/Math/MathML",
    "http://www.w3.org/2001/XInclude",
    "http://www.w3.org/2001/10/synthesis",
    "http://www.imsglobal.org/xsd/apip/apipv1p0/imsapip_qtiv1p0",
]
FIXTURES = Path(__file__).parent / "fixtures"


def _download(url: str) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": "qti-convert-tests"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read().decode("utf-8")


def _lax_schema(namespace, elements):
    definitions = "\n".join(
        f'  <xs:element name="{name}"><xs:complexType mixed="true"><xs:sequence><xs:any processContents="skip" '
        f'minOccurs="0" maxOccurs="unbounded"/></xs:sequence><xs:anyAttribute processContents="skip"/></xs:complexType>'
        f"</xs:element>"
        for name in elements
    )
    return (
        '<?xml version="1.0"?>\n<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema" '
        f'targetNamespace="{namespace}" elementFormDefault="qualified">\n{definitions}\n</xs:schema>'
    )


def _prepare_schema(name: str, url: str):
    schema_path = CACHE_DIR / f"{name}-local.xsd"
    if not schema_path.exists():
        if os.environ.get("QTI_CONVERT_OFFLINE"):
            return None
        try:
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            schema = _download(url)
            if not (CACHE_DIR / "xml.xsd").exists():
                (CACHE_DIR / "xml.xsd").write_text(_download(XML_XSD_URL), encoding="utf-8")
            for namespace, location in re.findall(
                r'<xs:import\s+namespace="([^"]+)"\s+schemaLocation="([^"]+)"\s*/>', schema
            ):
                if namespace == "http://www.w3.org/XML/1998/namespace":
                    schema = schema.replace(location, "xml.xsd")
                elif namespace in LAX_NAMESPACES:
                    # stub every element the QTI schema references in this namespace
                    prefixes = [p for p, uri in re.findall(r'xmlns:([\w-]+)="([^"]+)"', schema) if uri == namespace]
                    elements = sorted({e for p in prefixes for e in re.findall(rf'ref="{p}:([\w-]+)"', schema)})
                    stub = f"{name}-stub-{LAX_NAMESPACES.index(namespace)}.xsd"
                    (CACHE_DIR / stub).write_text(_lax_schema(namespace, elements or ["unused"]), encoding="utf-8")
                    schema = schema.replace(location, stub)
            schema_path.write_text(schema, encoding="utf-8")
        except OSError as error:
            pytest.skip(f"{name} XSD could not be downloaded: {error}")
    return etree.XMLSchema(etree.parse(str(schema_path)))


def _errors(schema, xml: str) -> str:
    document = etree.fromstring(xml.encode("utf-8"))
    if schema.validate(document):
        return ""
    return "\n".join(f"{e.line}: {e.message}" for e in schema.error_log)


@pytest.fixture(scope="module")
def qti3_schema():
    schema = _prepare_schema("qti3", QTI3_XSD_URL)
    if schema is None:
        pytest.skip("offline")
    return schema


@pytest.fixture(scope="module")
def qti21_schema():
    schema = _prepare_schema("qti21", QTI21_XSD_URL)
    if schema is None:
        pytest.skip("offline")
    return schema


UPGRADER_FIXTURES = sorted(
    p.name[: -len(".qti2.xml")] for p in (FIXTURES / "upgrader").glob("*.qti2.xml") if not p.name.startswith("tao-")
)


@pytest.mark.parametrize("name", UPGRADER_FIXTURES)
def test_qti3_output_validates(qti3_schema, name):
    output = upgrade_qti2_to_qti3((FIXTURES / "upgrader" / f"{name}.qti2.xml").read_bytes())
    assert _errors(qti3_schema, output) == ""


def test_extracted_shared_stimuli_validate(qti3_schema):
    files, report = extract_shared_stimuli(build_package())
    for path in ["items/i1.xml", "items/sub/i3.xml", "items/i6.xml", *[s.path for s in report.stimuli]]:
        assert _errors(qti3_schema, files[path][0]) == "", path


def _qti3(body: str, head: str = "") -> str:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<qti-assessment-item xmlns="http://www.imsglobal.org/xsd/imsqtiasi_v3p0" identifier="i" title="t" adaptive="false" time-dependent="false" xml:lang="en">
  <qti-response-declaration identifier="RESPONSE" cardinality="single" base-type="identifier"><qti-correct-response><qti-value>A</qti-value></qti-correct-response></qti-response-declaration>
  <qti-outcome-declaration identifier="SCORE" cardinality="single" base-type="float" external-scored="human"/>
  <qti-outcome-declaration identifier="FEEDBACK" cardinality="single" base-type="identifier"/>
  {head}
  <qti-item-body>{body}</qti-item-body>
  <qti-response-processing template="https://purl.imsglobal.org/spec/qti/v3p0/rptemplates/match_correct.xml"/>
  <qti-modal-feedback outcome-identifier="FEEDBACK" identifier="A" show-hide="show"><qti-content-body><p>Modal</p></qti-content-body></qti-modal-feedback>
</qti-assessment-item>"""


CHOICE = """<qti-choice-interaction response-identifier="RESPONSE" max-choices="1" orientation="horizontal" data-max-selections-message="x">
  <qti-prompt>Pick</qti-prompt><qti-simple-choice identifier="A">A</qti-simple-choice><qti-simple-choice identifier="B">B</qti-simple-choice>
</qti-choice-interaction>"""

CASES = {
    "feedback, MathML and shared vocabulary": _qti3(
        f"""
    <div class="qti-layout-row" data-x="1"><div class="qti-layout-col6"><p>Solve <math xmlns="http://www.w3.org/1998/Math/MathML"><mi>x</mi></math></p></div></div>
    {CHOICE}
    <qti-feedback-block outcome-identifier="FEEDBACK" identifier="A" show-hide="show"><qti-content-body><p>Right</p></qti-content-body></qti-feedback-block>
    <qti-rubric-block view="scorer" use="scoring"><qti-content-body><p>Rubric</p></qti-content-body></qti-rubric-block>"""
    ),
    "HTML5 media and elements": _qti3(
        f"""
    <figure><img src="a.png" alt="a"/><figcaption>A</figcaption></figure>
    <section><p>Section</p></section>
    <audio controls="controls"><source src="a.mp3" type="audio/mpeg"/></audio>
    <video src="v.mp4" width="100"/>
    {CHOICE}"""
    ),
    "graphic interaction image": _qti3(
        '<qti-select-point-interaction response-identifier="RESPONSE" max-choices="1"><qti-prompt>Click</qti-prompt>'
        '<img src="map.png" width="60" height="40" alt="Map"/></qti-select-point-interaction>'
    ),
    "SSML": _qti3(
        f'<p xmlns:ssml="http://www.w3.org/2001/10/synthesis">Say <ssml:sub alias="hello">hi</ssml:sub></p>{CHOICE}'
    ),
    "PCI": _qti3(
        """
    <qti-portable-custom-interaction response-identifier="RESPONSE" custom-interaction-type-identifier="likert" module="likert">
      <qti-interaction-markup><div>markup</div></qti-interaction-markup>
    </qti-portable-custom-interaction>"""
    ),
}


@pytest.mark.parametrize("name", list(CASES))
def test_qti21_output_validates(qti21_schema, name):
    result = convert_qti3_to_qti21(CASES[name], shared_vocabulary_stylesheet_href="qti3-shared-vocabulary.css")
    assert _errors(qti21_schema, result.xml) == ""


def test_qti21_output_with_an_inlined_stimulus_validates(qti21_schema):
    item = _qti3(CHOICE).replace(
        "<qti-item-body>", '<qti-assessment-stimulus-ref identifier="S" href="s.xml"/><qti-item-body>'
    )
    stimulus = """<qti-assessment-stimulus xmlns="http://www.imsglobal.org/xsd/imsqtiasi_v3p0" identifier="S" title="S">
      <qti-stylesheet href="s.css" type="text/css"/><qti-stimulus-body><p>Passage</p></qti-stimulus-body></qti-assessment-stimulus>"""
    assert _errors(qti21_schema, convert_qti3_to_qti21(item, resolve_stimulus=lambda href: stimulus).xml) == ""


def test_qti21_sample_package_validates(qti21_schema):
    files = downgrade_package_files(read_package(FIXTURES / "sample-package")).files
    for path, content in files.items():
        if path.endswith(".xml") and path != "imsmanifest.xml":
            assert _errors(qti21_schema, content) == "", path


def test_round_tripped_sample_package_validates(qti3_schema):
    from qti_convert import upgrade_package_files

    qti21 = downgrade_package_files(read_package(FIXTURES / "sample-package")).files
    for path, content in upgrade_package_files(qti21).files.items():
        if path.endswith(".xml") and path != "imsmanifest.xml":
            assert _errors(qti3_schema, content) == "", path
