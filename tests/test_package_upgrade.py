import zipfile
from pathlib import Path

from helpers import load, text

from qti_convert import (
    apply_transforms,
    convert_manifest_to_qti3,
    downgrade_package_files,
    read_package,
    upgrade_item,
    upgrade_package,
    upgrade_package_files,
)
from qti_convert.cli import main
from qti_convert.transforms import external_scored, min_choices_to_one, object_to_audio, ssml_to_span

SAMPLE_PACKAGE = Path(__file__).parent / "fixtures" / "sample-package"
QTI2 = "http://www.imsglobal.org/xsd/imsqti_v2p1"


def qti2_item(identifier, body, extra=""):
    return (
        f'<?xml version="1.0" encoding="UTF-8"?>\n<assessmentItem xmlns="{QTI2}" identifier="{identifier}" '
        f'title="{identifier}" adaptive="false" timeDependent="false">{extra}<itemBody>{body}</itemBody></assessmentItem>'
    )


def qti21_sample_package():
    """The sample QTI 3 package, downgraded: a realistic QTI 2.1 package."""
    return downgrade_package_files(read_package(SAMPLE_PACKAGE)).files


def test_converts_a_qti21_manifest():
    xml = convert_manifest_to_qti3(
        """<?xml version="1.0" encoding="UTF-8"?>
<imscp:manifest xmlns:imscp="http://www.imsglobal.org/xsd/imscp_v1p1" xmlns:imsmd="http://ltsc.ieee.org/xsd/LOM" identifier="M">
  <imscp:metadata><imscp:schema>QTIv2.1 Package</imscp:schema><imscp:schemaversion>1.0.0</imscp:schemaversion></imscp:metadata>
  <imscp:organizations/>
  <imscp:resources>
    <imscp:resource identifier="T" type="imsqti_test_xmlv2p1" href="test.xml"><imscp:file href="test.xml"/></imscp:resource>
    <imscp:resource identifier="I" type="imsqti_item_xmlv2p1" href="item.xml"><imscp:file href="item.xml"/></imscp:resource>
    <imscp:resource identifier="S" type="imsqti_stimulus_xmlv2p2" href="s.xml"><imscp:file href="s.xml"/></imscp:resource>
    <imscp:resource identifier="C" type="associatedcontent/imsqti_xmlv2p1/learning-application-resource" href="a.png"/>
  </imscp:resources>
</imscp:manifest>"""
    )
    assert '<manifest xmlns="http://www.imsglobal.org/xsd/qti/qtiv3p0/imscp_v1p1"' in xml
    assert 'xmlns:imsqti="http://www.imsglobal.org/xsd/imsqti_metadata_v3p0"' in xml
    assert "imscp:" not in xml
    root = load(xml)
    assert text(root.xpath("//schema")[0]) == "QTI Package"
    assert text(root.xpath("//schemaversion")[0]) == "3.0.0"
    types = {r.get("identifier"): r.get("type") for r in root.xpath("//resource")}
    assert types == {
        "T": "imsqti_test_xmlv3p0",
        "I": "imsqti_item_xmlv3p0",
        "S": "imsqti_stimulus_xmlv3p0",
        "C": "webcontent",
    }


def test_upgrades_every_qti_file_of_a_package():
    source = qti21_sample_package()
    files = upgrade_package_files(source).files
    assert sorted(files) == sorted(source)
    for path, content in files.items():
        if not path.endswith(".xml"):
            assert content is source[path]
            continue
        if path == "imsmanifest.xml":
            assert "imsqti_item_xmlv3p0" in content
            assert "v2p1" not in content
        else:
            assert 'xmlns="http://www.imsglobal.org/xsd/imsqtiasi_v3p0"' in content, path
    test = load(files["AssessmentTest.xml"])
    assert len(test.xpath("//qti-assessment-item-ref")) == 9
    # the default transforms ran on the items
    choice = load(files["ITEM_CHOICE.xml"]).xpath("//qti-choice-interaction")[0]
    assert choice.get("min-choices") == "1"


def test_leaves_qti3_files_alone():
    qti3 = read_package(SAMPLE_PACKAGE)
    files = upgrade_package_files(qti3).files
    assert files["ITEM_CHOICE.xml"] is qti3["ITEM_CHOICE.xml"]


def test_syncs_item_ref_identifiers_with_the_items():
    files = upgrade_package_files(
        {
            "imsmanifest.xml": """<manifest xmlns="http://www.imsglobal.org/xsd/imscp_v1p1" identifier="M"><resources>
            <resource identifier="test" type="imsqti_test_xmlv2p1" href="tests/test.xml"><file href="tests/test.xml"/><dependency identifierref="RES-1"/></resource>
            <resource identifier="RES-1" type="imsqti_item_xmlv2p1" href="items/item1.xml"><file href="items/item1.xml"/></resource>
          </resources></manifest>""",
            "tests/test.xml": f"""<assessmentTest xmlns="{QTI2}" identifier="T" title="T">
            <testPart identifier="P" navigationMode="linear" submissionMode="individual">
              <assessmentSection identifier="S" title="S" visible="true"><assessmentItemRef identifier="REF-1" href="../items/item1.xml"/></assessmentSection>
            </testPart></assessmentTest>""",
            "items/item1.xml": qti2_item("ITEM-1", "<p>x</p>"),
        }
    ).files
    assert load(files["tests/test.xml"]).xpath("//qti-assessment-item-ref")[0].get("identifier") == "ITEM-1"
    manifest = load(files["imsmanifest.xml"])
    assert manifest.xpath("//resource[@href='items/item1.xml']")[0].get("identifier") == "ITEM-1"
    assert manifest.xpath("//resource[@identifier='test']/dependency")[0].get("identifierref") == "ITEM-1"


def test_extracts_shared_stimuli_when_asked():
    passage = (
        "<h2>Passage</h2><p>" + "Een lange gedeelde tekst die in elk item van het cluster staat en daarom beter een "
        "gedeelde stimulus kan zijn, zodat hij maar een keer hoeft te worden onderhouden." + "</p>"
    )
    interaction = '<choiceInteraction responseIdentifier="RESPONSE" maxChoices="1"><simpleChoice identifier="A">A</simpleChoice></choiceInteraction>'
    source = {
        "a.xml": qti2_item("a", passage + interaction),
        "b.xml": qti2_item("b", passage + interaction),
    }
    result = upgrade_package_files(source, extract_shared_stimuli=True)
    assert len(result.shared_stimuli.stimuli) == 1
    stimulus = result.shared_stimuli.stimuli[0]
    assert stimulus.path in result.files
    assert load(result.files["a.xml"]).xpath("//qti-assessment-stimulus-ref")[0].get("href") == stimulus.path
    assert upgrade_package_files(source).shared_stimuli is None


def test_transforms():
    root = load(
        apply_transforms(
            upgrade_item(
                qti2_item(
                    "t",
                    '<p xmlns:ssml="http://www.w3.org/2001/10/synthesis">Zeg <ssml:sub alias="hallo">hoi</ssml:sub>'
                    '<ssml:break time="1s"/></p><object type="audio/mpeg" data="a.mp3" data-dep-controls="true"/>'
                    '<choiceInteraction responseIdentifier="R" maxChoices="1" minChoices="0"><simpleChoice identifier="A">A</simpleChoice></choiceInteraction>',
                    '<outcomeDeclaration identifier="SCORE" cardinality="single" baseType="float"/>',
                ),
                transforms=[],
            ),
            [ssml_to_span, object_to_audio, min_choices_to_one, external_scored],
        )
    )
    spans = root.xpath("//p/span")
    assert spans[0].get("data-ssml-sub-alias") == "hallo" and text(spans[0]) == "hoi"
    assert spans[1].get("data-ssml-break-time") == "1s"
    audio = root.xpath("//audio")[0]
    assert audio.get("controls") == "true"
    assert audio.xpath("source")[0].get("src") == "a.mp3"
    assert root.xpath("//qti-choice-interaction")[0].get("min-choices") == "1"
    assert root.xpath("//qti-outcome-declaration")[0].get("external-scored") == "human"


def test_zip_and_folder_round_trip(tmp_path):
    source_zip = tmp_path / "qti21.zip"
    with zipfile.ZipFile(source_zip, "w") as archive:
        for path, content in qti21_sample_package().items():
            archive.writestr(path, content)
        archive.writestr("__MACOSX/._ITEM_CHOICE.xml", b"junk")
    upgrade_package(source_zip, tmp_path / "qti3.zip")
    with zipfile.ZipFile(tmp_path / "qti3.zip") as archive:
        names = archive.namelist()
        assert "__MACOSX/._ITEM_CHOICE.xml" not in names
        assert "<qti-assessment-item" in archive.read("ITEM_GAP.xml").decode()
    upgrade_package(source_zip, tmp_path / "folder")
    assert (tmp_path / "folder" / "resources" / "map.png").read_bytes() == (
        SAMPLE_PACKAGE / "resources" / "map.png"
    ).read_bytes()


def test_cli(tmp_path, capsys):
    assert main(["downgrade", str(SAMPLE_PACKAGE), str(tmp_path / "qti21.zip")]) == 0
    assert "warning [img-to-object]" in capsys.readouterr().err
    assert main(["upgrade", str(tmp_path / "qti21.zip"), str(tmp_path / "qti3")]) == 0
    assert "<qti-assessment-test" in (tmp_path / "qti3" / "AssessmentTest.xml").read_text()

    item = tmp_path / "item.xml"
    item.write_text(qti2_item("x", "<p>x</p>"))
    assert main(["upgrade", str(item), str(tmp_path / "out" / "item3.xml")]) == 0
    assert "<qti-item-body><p>x</p></qti-item-body>" in (tmp_path / "out" / "item3.xml").read_text()
    assert main(["downgrade", "-q", str(tmp_path / "out" / "item3.xml"), str(tmp_path / "item21.xml")]) == 0
    assert "<itemBody><p>x</p></itemBody>" in (tmp_path / "item21.xml").read_text()
    assert main(["upgrade", str(tmp_path / "missing.xml"), str(tmp_path / "x.xml")]) == 2
