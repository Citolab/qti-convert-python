from pathlib import Path

import pytest
from helpers import load, text, xpath
from xml_compare import canonical

from qti_convert import upgrade_qti2_to_qti3

# fixtures/upgrader/<name>.qti2.xml is the input, <name>.qti3.xml the output of the original qti2xTo30.xsl,
# so the upgrader is checked against it.
FIXTURES = Path(__file__).parent / "fixtures" / "upgrader"
NAMES = sorted(p.name[: -len(".qti2.xml")] for p in FIXTURES.glob("*.qti2.xml"))


def test_fixtures_are_present():
    assert len(NAMES) > 10


@pytest.mark.parametrize("name", NAMES)
def test_matches_the_xslt_output(name):
    source = (FIXTURES / f"{name}.qti2.xml").read_bytes()
    expected = (FIXTURES / f"{name}.qti3.xml").read_bytes()
    assert canonical(upgrade_qti2_to_qti3(source)) == canonical(expected)


def _upgrade(body: str):
    return load(
        upgrade_qti2_to_qti3(
            '<assessmentItem xmlns="http://www.imsglobal.org/xsd/imsqti_v2p1" identifier="x" adaptive="false" '
            f'timeDependent="false">{body}</assessmentItem>'
        )
    )


def test_converts_elements_missing_from_the_xslt_lists():
    root = _upgrade(
        '<responseProcessing><responseCondition><responseIf><durationLT><variable identifier="duration"/>'
        '<baseValue baseType="duration">1</baseValue></durationLT><exitResponse/></responseIf></responseCondition>'
        "</responseProcessing>"
    )
    assert len(root.xpath("//qti-duration-lt")) == 1

    test = load(
        upgrade_qti2_to_qti3(
            """<assessmentTest xmlns="http://www.imsglobal.org/xsd/imsqti_v2p1" identifier="t" title="t">
        <testPart identifier="p" navigationMode="linear" submissionMode="individual"><assessmentSection identifier="s" title="s" visible="true">
          <assessmentItemRef identifier="i" href="i.xml"><variableMapping sourceIdentifier="A" targetIdentifier="B"/><templateDefault templateIdentifier="T"><baseValue baseType="integer">1</baseValue></templateDefault></assessmentItemRef>
        </assessmentSection></testPart>
        <outcomeProcessing><outcomeCondition><outcomeIf><isNull><variable identifier="X"/></isNull><exitTest/></outcomeIf><outcomeElseIf><isNull><variable identifier="Y"/></isNull><exitTest/></outcomeElseIf></outcomeCondition></outcomeProcessing>
        <testFeedback access="atEnd" outcomeIdentifier="F" showHide="show" identifier="f"><p>Done</p></testFeedback>
      </assessmentTest>"""
        )
    )
    for name in ["qti-variable-mapping", "qti-template-default", "qti-outcome-else-if", "qti-exit-test"]:
        assert test.xpath(f"//{name}"), name
    assert text(test.xpath("//qti-test-feedback/qti-content-body/p")[0]) == "Done"


def test_converts_a_stimulus_body():
    root = load(
        upgrade_qti2_to_qti3(
            '<assessmentStimulus xmlns="http://www.imsglobal.org/xsd/imsqti_v2p2" identifier="s" title="s">'
            "<stimulusBody><p>Text</p></stimulusBody></assessmentStimulus>"
        )
    )
    assert text(root.xpath("/qti-assessment-stimulus/qti-stimulus-body/p")[0]) == "Text"


def test_keeps_the_children_of_a_video_object_once_and_inline_svg_in_its_namespace():
    xml = upgrade_qti2_to_qti3(
        '<assessmentItem xmlns="http://www.imsglobal.org/xsd/imsqti_v2p1" identifier="x" adaptive="false" '
        'timeDependent="false"><itemBody><object type="video/mp4" data="v.mp4" width="100">fallback</object>'
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><circle stroke-width="2" r="4"/></svg>'
        "</itemBody></assessmentItem>"
    )
    root = load(xml)
    assert text(root.xpath("//video")[0]) == "fallback"
    assert root.xpath("//video/source")[0].get("src") == "v.mp4"
    assert '<svg xmlns="http://www.w3.org/2000/svg"' in xml
    assert root.xpath("//circle")[0].get("stroke-width") == "2"


def test_converts_prefixed_qti2_elements():
    xml = upgrade_qti2_to_qti3(
        '<qti:assessmentItem xmlns:qti="http://www.imsglobal.org/xsd/imsqti_v2p1" identifier="x" adaptive="false" '
        'timeDependent="false"><qti:itemBody><qti:p>x</qti:p></qti:itemBody></qti:assessmentItem>'
    )
    assert '<qti-assessment-item xmlns="http://www.imsglobal.org/xsd/imsqtiasi_v3p0"' in xml
    assert "<qti-item-body><p>x</p></qti-item-body>" in xml


def test_output_starts_with_the_declaration_and_schematron_association():
    xml = upgrade_qti2_to_qti3(
        '<?xml version="1.0" encoding="UTF-8"?>\n<assessmentItem xmlns="http://www.imsglobal.org/xsd/imsqti_v2p1" '
        'identifier="x"/>'
    )
    lines = xml.splitlines()
    assert lines[0] == '<?xml version="1.0" encoding="UTF-8"?>'
    assert lines[1].startswith('<?xml-model href="https://purl.imsglobal.org/spec/qti/v3p0/schema/xsd/')
    assert 'xsi:schemaLocation="http://www.imsglobal.org/xsd/imsqtiasi_v3p0 ' in lines[2]


def test_accepts_html_entities_and_bytes():
    xml = upgrade_qti2_to_qti3(
        b'<?xml version="1.0" encoding="UTF-8"?><assessmentItem xmlns="http://www.imsglobal.org/xsd/imsqti_v2p1" '
        b'identifier="x"><itemBody><p>a&nbsp;b &amp; c</p></itemBody></assessmentItem>'
    )
    assert text(xpath(xml, "//p")[0]) == "a b & c"
