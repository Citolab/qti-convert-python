import io
import re
import zipfile
from pathlib import Path

from helpers import XML_LANG, codes, load, text
from xml_compare import structure

from qti_convert import (
    QTI3_SHARED_VOCABULARY_CSS,
    Qti21ConversionResult,
    convert_manifest_to_qti21,
    convert_qti3_to_qti21,
    downgrade_package,
    downgrade_package_files,
    package_to_zip,
    upgrade_qti2_to_qti3,
)

SAMPLE_PACKAGE = Path(__file__).parent / "fixtures" / "sample-package"

QTI3_CHOICE = """<?xml version="1.0" encoding="UTF-8"?>
<?xml-model href="https://purl.imsglobal.org/spec/qti/v3p0/schema/xsd/imsqti_asiv3p0_v1p0.xsd" type="application/xml" schematypens="http://purl.oclc.org/dsdl/schematron"?>
<qti-assessment-item xmlns="http://www.imsglobal.org/xsd/imsqtiasi_v3p0" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
  xsi:schemaLocation="http://www.imsglobal.org/xsd/imsqtiasi_v3p0 https://purl.imsglobal.org/spec/qti/v3p0/schema/xsd/imsqti_asiv3p0_v1p0.xsd"
  identifier="choice" title="Choice" adaptive="false" time-dependent="false" xml:lang="en">
  <qti-response-declaration identifier="RESPONSE" cardinality="single" base-type="identifier">
    <qti-correct-response><qti-value>A</qti-value></qti-correct-response>
  </qti-response-declaration>
  <qti-outcome-declaration identifier="SCORE" cardinality="single" base-type="float" external-scored="human"/>
  <qti-item-body>
    <div class="qti-layout-row" data-foo="bar" aria-label="row">
      <qti-choice-interaction response-identifier="RESPONSE" max-choices="1" data-max-selections-message="no">
        <qti-prompt>Kies één &amp; alleen één</qti-prompt>
        <qti-simple-choice identifier="A">A</qti-simple-choice>
        <qti-simple-choice identifier="B">B</qti-simple-choice>
      </qti-choice-interaction>
    </div>
    <math xmlns="http://www.w3.org/1998/Math/MathML"><mi mathvariant="bold">x</mi></math>
  </qti-item-body>
  <qti-response-processing template="https://purl.imsglobal.org/spec/qti/v3p0/rptemplates/match_correct.xml"/>
  <qti-modal-feedback outcome-identifier="FEEDBACK" identifier="correct" show-hide="show">
    <qti-content-body><p>Well done</p></qti-content-body>
  </qti-modal-feedback>
</qti-assessment-item>"""


def _item(identifier: str, body: str) -> str:
    return (
        f'<qti-assessment-item xmlns="http://www.imsglobal.org/xsd/imsqtiasi_v3p0" identifier="{identifier}" '
        f'adaptive="false" time-dependent="false"><qti-item-body>{body}</qti-item-body></qti-assessment-item>'
    )


class TestConvertQti3ToQti21:
    def test_renames_elements_and_attributes_and_sets_the_qti21_namespace(self):
        result = convert_qti3_to_qti21(QTI3_CHOICE)
        xml = result.xml
        root = load(xml)
        assert not re.search(r"<qti-", xml)
        assert "xml-model" not in xml
        assert "<prompt>Kies één &amp; alleen één</prompt>" in xml
        assert '<assessmentItem xmlns="http://www.imsglobal.org/xsd/imsqti_v2p1"' in xml
        assert "imsqti_v2p1p2.xsd" in root.get("{http://www.w3.org/2001/XMLSchema-instance}schemaLocation")
        assert root.get("timeDependent") == "false"
        assert root.get(XML_LANG) == "en"
        assert root.xpath("//responseDeclaration")[0].get("baseType") == "identifier"
        interaction = root.xpath("//choiceInteraction")[0]
        assert interaction.get("responseIdentifier") == "RESPONSE"
        assert interaction.get("maxChoices") == "1"
        assert len(root.xpath("//simpleChoice")) == 2
        assert (
            root.xpath("//responseProcessing")[0].get("template")
            == "http://www.imsglobal.org/question/qti_v2p1/rptemplates/match_correct"
        )
        assert root.xpath("//outcomeDeclaration")[0].get("externalScored") is None
        assert {"removed-attribute", "data-attributes-removed", "shared-vocabulary-classes"} <= set(
            codes(result.warnings)
        )

    def test_unwraps_content_body_strips_data_and_aria_keeps_mathml(self):
        result = convert_qti3_to_qti21(QTI3_CHOICE)
        root = load(result.xml)
        assert text(root.xpath("//modalFeedback/p")[0]) == "Well done"
        assert root.xpath("//modalFeedback")[0].get("showHide") == "show"
        assert not root.xpath("//*[@data-foo]")
        assert not root.xpath("//*[@dataMaxSelectionsMessage]")
        # QTI 2.1 has no aria-*, role or dir
        assert root.xpath("//div")[0].get("aria-label") is None
        assert "accessibility-attributes-removed" in codes(result.warnings)
        assert root.xpath("//mi")[0].get("mathvariant") == "bold"
        assert '<math xmlns="http://www.w3.org/1998/Math/MathML">' in result.xml

    def test_converts_an_image_only_gap_text_to_gap_img(self):
        result = convert_qti3_to_qti21(
            """<qti-assessment-item xmlns="http://www.imsglobal.org/xsd/imsqtiasi_v3p0" identifier="g" adaptive="false" time-dependent="false">
      <qti-item-body><qti-gap-match-interaction response-identifier="RESPONSE">
        <qti-gap-text identifier="W1" match-max="1"><img src="a.png" alt="A"/></qti-gap-text>
        <qti-gap-text identifier="W2" match-max="1">text</qti-gap-text>
        <p>A <qti-gap identifier="G1"/></p>
      </qti-gap-match-interaction></qti-item-body></qti-assessment-item>"""
        )
        root = load(result.xml)
        gap_img = root.xpath("//gapImg")[0]
        assert gap_img.get("identifier") == "W1"
        assert gap_img.get("matchMax") == "1"
        assert root.xpath("//gapImg/object")[0].get("data") == "a.png"
        assert text(root.xpath("//gapText")[0]) == "text"
        assert "gap-text-to-gap-img" in codes(result.warnings)

    def test_adds_the_shared_vocabulary_stylesheet_when_asked(self):
        result = convert_qti3_to_qti21(QTI3_CHOICE, shared_vocabulary_stylesheet_href="../qti3-shared-vocabulary.css")
        stylesheet = load(result.xml).xpath("//stylesheet")[0]
        assert stylesheet.get("href") == "../qti3-shared-vocabulary.css"
        assert stylesheet.get("type") == "text/css"
        assert stylesheet.getnext().tag == "itemBody"
        assert "shared-vocabulary-stylesheet" in codes(result.warnings)
        assert "shared-vocabulary-classes" not in codes(result.warnings)

        # without qti-* classes there is nothing to style
        plain = convert_qti3_to_qti21(_item("p", "<p>x</p>"), shared_vocabulary_stylesheet_href="x.css")
        assert "stylesheet" not in plain.xml

    def test_maps_irregular_operator_names(self):
        result = convert_qti3_to_qti21(
            """<qti-assessment-item xmlns="http://www.imsglobal.org/xsd/imsqtiasi_v3p0" identifier="x" adaptive="false" time-dependent="false">
      <qti-response-processing><qti-response-condition><qti-response-if>
        <qti-duration-lt><qti-variable identifier="duration"/><qti-base-value base-type="duration">10</qti-base-value></qti-duration-lt>
        <qti-set-outcome-value identifier="SCORE"><qti-base-value base-type="float">1</qti-base-value></qti-set-outcome-value>
      </qti-response-if></qti-response-condition></qti-response-processing></qti-assessment-item>"""
        )
        assert "<durationLT>" in result.xml
        assert '<setOutcomeValue identifier="SCORE">' in result.xml

    def test_inlines_a_shared_stimulus_and_rebases_its_assets(self):
        item = """<qti-assessment-item xmlns="http://www.imsglobal.org/xsd/imsqtiasi_v3p0" identifier="i1" adaptive="false" time-dependent="false">
      <qti-assessment-stimulus-ref identifier="S1" href="../stimuli/s1.xml" title="Passage"/>
      <qti-item-body><p>Question</p></qti-item-body>
    </qti-assessment-item>"""
        stimulus = """<qti-assessment-stimulus xmlns="http://www.imsglobal.org/xsd/imsqtiasi_v3p0" identifier="S1" title="Passage" xml:lang="nl">
      <qti-stylesheet href="style.css" type="text/css"/>
      <qti-stimulus-body><p>Passage text</p><img src="img/a.png" alt="a"/></qti-stimulus-body>
    </qti-assessment-stimulus>"""
        hrefs = []

        def resolve(href):
            hrefs.append(href)
            return stimulus

        result = convert_qti3_to_qti21(item, resolve_stimulus=resolve)
        root = load(result.xml)
        assert hrefs == ["../stimuli/s1.xml"]
        assert not root.xpath("//assessmentStimulusRef")
        shared = root.xpath("//itemBody/div[@class='qti-shared-stimulus']")[0]
        assert text(shared.xpath("p")[0]) == "Passage text"
        assert shared.get(XML_LANG) == "nl"
        assert root.xpath("//img")[0].get("src") == "../stimuli/img/a.png"
        stylesheet = root.xpath("//stylesheet")[0]
        assert stylesheet.get("href") == "../stimuli/style.css"
        assert stylesheet.getnext().tag == "itemBody"
        assert "stimulus-inlined" in codes(result.warnings)

    def test_removes_an_unresolvable_stimulus_ref_with_a_warning(self):
        result = convert_qti3_to_qti21(
            '<qti-assessment-item xmlns="http://www.imsglobal.org/xsd/imsqtiasi_v3p0" identifier="i1" adaptive="false" '
            'time-dependent="false"><qti-assessment-stimulus-ref identifier="S1" href="s1.xml"/><qti-item-body/>'
            "</qti-assessment-item>"
        )
        assert "stimulus" not in result.xml
        assert "stimulus-unresolved" in codes(result.warnings)

    def test_converts_html5_media_and_graphic_interaction_images_to_object(self):
        result = convert_qti3_to_qti21(
            """<qti-assessment-item xmlns="http://www.imsglobal.org/xsd/imsqtiasi_v3p0" identifier="m" adaptive="false" time-dependent="false">
      <qti-item-body>
        <audio controls="controls"><source src="media/a.mp3" type="audio/mpeg"/>Audio</audio>
        <video src="media/v.mp4" width="320"/>
        <figure><img src="x.png" alt="x"/><figcaption>X</figcaption></figure>
        <qti-select-point-interaction response-identifier="RESPONSE" max-choices="1">
          <img src="map.png" width="60" height="40" alt="Map"/>
        </qti-select-point-interaction>
      </qti-item-body></qti-assessment-item>"""
        )
        root = load(result.xml)
        assert not root.xpath("//audio | //video | //figure | //figcaption")
        audio = root.xpath("//object[@data='media/a.mp3']")[0]
        assert audio.get("type") == "audio/mpeg"
        assert text(audio) == "Audio"
        # object is inline in QTI 2.1, so directly in the item body it gets a block wrapper
        assert len(root.xpath("//itemBody/div/object[@data='media/a.mp3']")) == 1
        video = root.xpath("//object[@data='media/v.mp4']")[0]
        assert video.get("type") == "video/mp4"
        assert video.get("width") == "320"
        background = root.xpath("//selectPointInteraction/object")[0]
        assert background.get("data") == "map.png"
        assert background.get("type") == "image/png"
        assert background.get("width") == "60"
        assert text(background) == "Map"
        assert {"media-to-object", "img-to-object", "html5-element"} <= set(codes(result.warnings))

    def test_wraps_a_pci_in_a_custom_interaction(self):
        result = convert_qti3_to_qti21(
            """<qti-assessment-item xmlns="http://www.imsglobal.org/xsd/imsqtiasi_v3p0" identifier="p" adaptive="false" time-dependent="false">
      <qti-item-body>
        <qti-portable-custom-interaction response-identifier="RESPONSE" custom-interaction-type-identifier="likert" module="likert">
          <qti-interaction-markup><div>markup</div></qti-interaction-markup>
        </qti-portable-custom-interaction>
      </qti-item-body></qti-assessment-item>"""
        )
        xml = result.xml
        assert not re.search(r"<qti-", xml)
        assert load(xml).xpath("//customInteraction")[0].get("responseIdentifier") == "RESPONSE"
        assert "<pci:portableCustomInteraction" in xml
        assert 'customInteractionTypeIdentifier="likert"' in xml
        assert "<pci:interactionMarkup>" in xml
        assert "pci" in codes(result.warnings)

    def test_strips_ssml_but_keeps_the_text(self):
        result = convert_qti3_to_qti21(
            _item(
                "s",
                '<p xmlns:ssml="http://www.w3.org/2001/10/synthesis">Say <ssml:sub alias="hello">hi</ssml:sub>!</p>',
            )
        )
        assert "<p>Say hi!</p>" in result.xml
        assert "synthesis" not in result.xml
        assert "ssml-removed" in codes(result.warnings)

    def test_leaves_qti2_input_unchanged(self):
        source = '<assessmentItem xmlns="http://www.imsglobal.org/xsd/imsqti_v2p1" identifier="x"/>'
        result = convert_qti3_to_qti21(source)
        assert result.xml == source
        assert result.warnings[0].code == "already-qti2"

    def test_round_trips_qti21_to_qti3_and_back(self):
        qti21 = """<?xml version="1.0" encoding="UTF-8"?>
<assessmentItem xmlns="http://www.imsglobal.org/xsd/imsqti_v2p1" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
  xsi:schemaLocation="http://www.imsglobal.org/xsd/imsqti_v2p1 http://www.imsglobal.org/xsd/qti/qtiv2p1/imsqti_v2p1p2.xsd"
  identifier="roundtrip" title="Round trip" adaptive="false" timeDependent="false">
  <responseDeclaration identifier="RESPONSE" cardinality="multiple" baseType="directedPair">
    <correctResponse><value>W1 G1</value></correctResponse>
    <mapping defaultValue="0"><mapEntry mapKey="W1 G1" mappedValue="1"/></mapping>
  </responseDeclaration>
  <outcomeDeclaration identifier="SCORE" cardinality="single" baseType="float"/>
  <outcomeDeclaration identifier="FEEDBACK" cardinality="single" baseType="identifier"/>
  <itemBody>
    <p>Intro <textEntryInteraction responseIdentifier="RESPONSE2" expectedLength="10"/></p>
    <gapMatchInteraction responseIdentifier="RESPONSE" shuffle="false">
      <gapText identifier="W1" matchMax="1">word</gapText>
      <p>A <gap identifier="G1"/> sentence.</p>
    </gapMatchInteraction>
    <feedbackBlock outcomeIdentifier="FEEDBACK" identifier="fb" showHide="show"><p>Feedback</p></feedbackBlock>
  </itemBody>
  <responseProcessing template="http://www.imsglobal.org/question/qti_v2p1/rptemplates/map_response"/>
</assessmentItem>"""
        qti3 = upgrade_qti2_to_qti3(qti21)
        assert "<qti-gap-match-interaction" in qti3
        assert structure(convert_qti3_to_qti21(qti3).xml) == structure(qti21)


MANIFEST = """<?xml version="1.0" encoding="UTF-8"?>
<manifest xmlns="http://www.imsglobal.org/xsd/qti/qtiv3p0/imscp_v1p1" xmlns:imsqti="http://www.imsglobal.org/xsd/imsqti_metadata_v3p0" identifier="M">
  <metadata><schema>QTI Package</schema><schemaversion>3.0.0</schemaversion></metadata>
  <organizations/>
  <resources>
    <resource identifier="test" type="imsqti_test_xmlv3p0" href="test.xml"><file href="test.xml"/><dependency identifierref="item"/></resource>
    <resource identifier="item" type="imsqti_item_xmlv3p0" href="items/item.xml">
      <file href="items/item.xml"/><dependency identifierref="stim"/>
    </resource>
    <resource identifier="stim" type="imsqti_stimulus_xmlv3p0" href="stimuli/s1.xml">
      <file href="stimuli/s1.xml"/><file href="stimuli/style.css"/><dependency identifierref="img"/>
    </resource>
    <resource identifier="img" type="webcontent" href="stimuli/img/a.png"><file href="stimuli/img/a.png"/></resource>
  </resources>
</manifest>"""


class TestConvertManifestToQti21:
    def test_converts_namespaces_schema_version_and_resource_types(self):
        xml = convert_manifest_to_qti21(MANIFEST)
        assert '<manifest xmlns="http://www.imsglobal.org/xsd/imscp_v1p1"' in xml
        assert 'xmlns:imsqti="http://www.imsglobal.org/xsd/imsqti_metadata_v2p1"' in xml
        root = load(xml)
        assert text(root.xpath("//schema")[0]) == "QTIv2.1 Package"
        assert text(root.xpath("//schemaversion")[0]) == "1.0.0"
        types = {r.get("identifier"): r.get("type") for r in root.xpath("//resource")}
        assert types["test"] == "imsqti_test_xmlv2p1"
        assert types["item"] == "imsqti_item_xmlv2p1"
        assert types["stim"] == "webcontent"

    def test_replaces_inlined_stimulus_resources_by_their_files_and_dependencies(self):
        root = load(convert_manifest_to_qti21(MANIFEST, {"stimuli/s1.xml"}))
        assert not root.xpath("//resource[@identifier='stim']")
        item = root.xpath("//resource[@identifier='item']")[0]
        assert [d.get("identifierref") for d in item.xpath("dependency")] == ["img"]
        assert [f.get("href") for f in item.xpath("file")] == ["items/item.xml", "stimuli/style.css"]


def _read_folder(folder: Path):
    return {p.relative_to(folder).as_posix(): p.read_bytes() for p in sorted(folder.rglob("*")) if p.is_file()}


class TestPackageConversion:
    def test_converts_every_qti_file_of_the_sample_package(self):
        source = _read_folder(SAMPLE_PACKAGE)
        files = downgrade_package_files(source).files
        assert sorted(files) == sorted(source)
        for path, content in files.items():
            if not path.endswith(".xml"):
                assert content is source[path]
                continue
            assert not re.search(r"<qti-", content), path
            if path == "imsmanifest.xml":
                assert "imsqti_item_xmlv2p1" in content
                assert "v3p0" not in content
            else:
                assert 'xmlns="http://www.imsglobal.org/xsd/imsqti_v2p1"' in content, path
        test = load(files["AssessmentTest.xml"])
        assert len(test.xpath("/assessmentTest/testPart/assessmentSection/assessmentItemRef")) == 9
        assert test.xpath("//testPart")[0].get("navigationMode") == "linear"

    def test_inlines_stimuli_drops_the_stimulus_file_and_round_trips_through_a_zip(self):
        source = package_to_zip(
            {
                "imsmanifest.xml": """<manifest xmlns="http://www.imsglobal.org/xsd/qti/qtiv3p0/imscp_v1p1" identifier="M"><resources>
        <resource identifier="item" type="imsqti_item_xmlv3p0" href="items/item.xml"><file href="items/item.xml"/><dependency identifierref="stim"/></resource>
        <resource identifier="stim" type="imsqti_stimulus_xmlv3p0" href="stimuli/s1.xml"><file href="stimuli/s1.xml"/></resource>
      </resources></manifest>""",
                "items/item.xml": """<qti-assessment-item xmlns="http://www.imsglobal.org/xsd/imsqtiasi_v3p0" identifier="item" adaptive="false" time-dependent="false">
        <qti-assessment-stimulus-ref identifier="stim" href="../stimuli/s1.xml"/><qti-item-body><p>Q</p></qti-item-body></qti-assessment-item>""",
                "stimuli/s1.xml": '<qti-assessment-stimulus xmlns="http://www.imsglobal.org/xsd/imsqtiasi_v3p0" identifier="stim" '
                'title="S"><qti-stimulus-body><p>Passage</p></qti-stimulus-body></qti-assessment-stimulus>',
            }
        )
        result = downgrade_package(source)
        with zipfile.ZipFile(io.BytesIO(package_to_zip(result.files))) as archive:
            assert sorted(archive.namelist()) == ["imsmanifest.xml", "items/item.xml"]
            item = archive.read("items/item.xml").decode()
            manifest = archive.read("imsmanifest.xml").decode()
        assert '<div class="qti-shared-stimulus"><p>Passage</p></div>' in item
        assert "stim" not in manifest
        assert any(w.code == "stimulus-inlined" and w.file == "items/item.xml" for w in result.warnings)

    def test_uses_the_conversion_callbacks_when_given(self):
        files = {
            "imsmanifest.xml": '<manifest xmlns="http://www.imsglobal.org/xsd/qti/qtiv3p0/imscp_v1p1" identifier="M"/>',
            "item.xml": _item("i", ""),
            "test.xml": '<qti-assessment-test xmlns="http://www.imsglobal.org/xsd/imsqtiasi_v3p0" identifier="t" title="t"/>',
        }
        seen = []

        def convert_item(xml, context):
            seen.append(f"item:{context.path}")
            result = convert_qti3_to_qti21(xml, file_path=context.path)
            return Qti21ConversionResult(result.xml.replace('identifier="i"', 'identifier="custom"'), result.warnings)

        def convert_test(xml, context):
            seen.append(f"test:{context.path}")
            return convert_qti3_to_qti21(xml)

        def convert_manifest(xml, inlined):
            seen.append("manifest")
            return xml.replace('identifier="M"', 'identifier="M2"')

        converted = downgrade_package_files(
            files, convert_item=convert_item, convert_test=convert_test, convert_manifest=convert_manifest
        ).files
        assert seen == ["item:item.xml", "test:test.xml", "manifest"]
        assert "<assessmentItem" in converted["item.xml"]
        assert 'identifier="custom"' in converted["item.xml"]
        assert 'identifier="M2"' in converted["imsmanifest.xml"]

    def test_the_shared_vocabulary_stylesheet_has_the_utilities_a_row_relative_grid_and_no_page_rules(self):
        css = QTI3_SHARED_VOCABULARY_CSS
        for rule in [
            ".qti-display-flex {",
            ".qti-margin-t-4 {",
            ".qti-align-center {",
            ".qti-list-style-type-lower-alpha {",
            ".qti-underline {",
        ]:
            assert rule in css
        assert not re.search(r"^\s*(body|html)\s*\{", css, re.M)
        assert not re.search(r"^\s*\.container(-fluid)?\b[^{]*\{", css, re.M)
        assert ".qti-layout-col6 { width:48.93617021276595%; }" in css
        assert not re.search(r"qti-layout-col\d+\s*\{\s*width:\d+px", css)
        assert "--table-border-color:" in css
        assert ".qti-float-clear-both { clear: both; }" in css

    def test_adds_the_shared_vocabulary_stylesheet_for_items_that_use_qti_classes(self):
        files = {
            "imsmanifest.xml": """<manifest xmlns="http://www.imsglobal.org/xsd/qti/qtiv3p0/imscp_v1p1" identifier="M"><resources>
          <resource identifier="A" type="imsqti_item_xmlv3p0" href="items/a.xml"><file href="items/a.xml"/></resource>
          <resource identifier="B" type="imsqti_item_xmlv3p0" href="items/b.xml"><file href="items/b.xml"/></resource>
        </resources></manifest>""",
            "items/a.xml": _item("A", '<div class="qti-layout-row"><div class="qti-layout-col6"><p>x</p></div></div>'),
            "items/b.xml": _item("B", "<p>y</p>"),
        }
        converted = downgrade_package_files(files).files
        assert converted["qti3-shared-vocabulary.css"] == QTI3_SHARED_VOCABULARY_CSS
        assert load(converted["items/a.xml"]).xpath("//stylesheet")[0].get("href") == "../qti3-shared-vocabulary.css"
        assert "stylesheet" not in converted["items/b.xml"]
        manifest = load(converted["imsmanifest.xml"])
        css = manifest.xpath("//resource[@href='qti3-shared-vocabulary.css']")[0]
        assert css.get("type") == "webcontent"
        assert css.xpath("file")[0].get("href") == "qti3-shared-vocabulary.css"
        assert manifest.xpath("//resource[@identifier='A']/dependency")[0].get("identifierref") == css.get("identifier")
        assert not manifest.xpath("//resource[@identifier='B']/dependency")

        # the stylesheet goes next to the manifest (the package root), also when that is a folder in the zip
        nested = downgrade_package_files({f"pkg/{path}": content for path, content in files.items()}).files
        assert "pkg/qti3-shared-vocabulary.css" in nested
        assert load(nested["pkg/items/a.xml"]).xpath("//stylesheet")[0].get("href") == "../qti3-shared-vocabulary.css"
        assert len(load(nested["pkg/imsmanifest.xml"]).xpath("//resource[@href='qti3-shared-vocabulary.css']")) == 1

        without_css = downgrade_package_files(files, inject_shared_vocabulary_stylesheet=False).files
        assert "qti3-shared-vocabulary.css" not in without_css
        assert "stylesheet" not in without_css["items/a.xml"]
