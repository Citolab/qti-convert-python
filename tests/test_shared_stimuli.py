import re

import pytest
from helpers import XML_LANG, load, text

from qti_convert import extract_shared_stimuli


def passage(img_src, body="De Waddenzee is een ondiepe zee tussen de Waddeneilanden en het vasteland."):
    return f"""
    <h2>De Waddenzee</h2>
    <p>{body} Twee keer per dag valt een groot deel droog. Dan kun je over de bodem lopen, maar alleen met een gids, want het water komt snel terug.</p>
    <p><img src="{img_src}" alt="Wad"/></p>"""


def choice(identifier):
    return f"""
    <qti-choice-interaction response-identifier="RESPONSE" max-choices="1">
      <qti-prompt>Vraag {identifier}</qti-prompt>
      <qti-simple-choice identifier="A">A</qti-simple-choice><qti-simple-choice identifier="B">B</qti-simple-choice>
    </qti-choice-interaction>"""


def item(identifier, body):
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<qti-assessment-item xmlns="http://www.imsglobal.org/xsd/imsqtiasi_v3p0" identifier="{identifier}" title="{identifier}" adaptive="false" time-dependent="false" xml:lang="nl-NL">
  <qti-response-declaration identifier="RESPONSE" cardinality="single" base-type="identifier"><qti-correct-response><qti-value>A</qti-value></qti-correct-response></qti-response-declaration>
  <qti-outcome-declaration identifier="SCORE" cardinality="single" base-type="float"/>
  <qti-item-body>{body}
  </qti-item-body>
  <qti-response-processing template="https://purl.imsglobal.org/spec/qti/v3p0/rptemplates/match_correct.xml"/>
</qti-assessment-item>"""


def columns(left, right):
    return f"""
    <div class="qti-layout-row">
      <div class="qti-layout-col6">{left}</div>
      <div class="qti-layout-col6">{right}</div>
    </div>"""


def manifest(paths):
    resources = "\n".join(
        f'    <resource identifier="{re.sub(r"[^A-Za-z0-9_]", "_", p)}" type="imsqti_item_xmlv3p0" href="{p}"><file href="{p}"/></resource>'
        for p in paths
    )
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<manifest xmlns="http://www.imsglobal.org/xsd/qti/qtiv3p0/imscp_v1p1" identifier="M">
  <resources>
{resources}
  </resources>
</manifest>"""


BIRDS = (
    "<p>Een andere tekst over vogels die in de winter naar het zuiden trekken en in het voorjaar terugkomen om te "
    "broeden in de duinen en de polders. Sommige soorten vliegen duizenden kilometers.</p>"
)


def build_package():
    items = {
        "items/i1.xml": item(
            "i1", f"\n    <p>Lees de tekst en beantwoord de vraag.</p>{passage('../img/wad.png')}{choice('1')}"
        ),
        "items/i2.xml": item("i2", f"{passage('../img/wad.png')}{choice('2')}"),
        "items/sub/i3.xml": item("i3", f"{passage('../../img/wad.png')}{choice('3')}"),
        # near-duplicate: one word differs
        "items/i4.xml": item(
            "i4",
            f"{passage('../img/wad.png', 'De Waddenzee is een ondiepe zee tussen de eilanden en het vasteland.')}{choice('4')}",
        ),
        # nothing shared
        "items/i5.xml": item("i5", f"<p>Hoeveel is 3 + 4?</p>{choice('5')}"),
        # passage in the left column of a two-column layout
        "items/i6.xml": item("i6", columns(BIRDS, choice("6"))),
        "items/i7.xml": item("i7", columns(BIRDS, choice("7"))),
        # only a short shared instruction: not worth a stimulus
        "items/i8.xml": item("i8", f"<p>Kies het juiste antwoord.</p>{choice('8')}"),
        "items/i9.xml": item("i9", f"<p>Kies het juiste antwoord.</p>{choice('9')}"),
    }
    files = {"imsmanifest.xml": (manifest(list(items)), "manifest")}
    files.update({path: (content, "item") for path, content in items.items()})
    files["img/wad.png"] = (b"\x01\x02\x03", "other")
    return files


@pytest.fixture(scope="module")
def extracted():
    source = build_package()
    files, report = extract_shared_stimuli(source)
    passage_stimulus = next(s for s in report.stimuli if s.title == "De Waddenzee")
    column_stimulus = next(s for s in report.stimuli if s is not passage_stimulus)
    return source, files, report, passage_stimulus, column_stimulus


def test_extracts_one_stimulus_per_shared_passage(extracted):
    _, _, report, passage_stimulus, column_stimulus = extracted
    assert len(report.stimuli) == 2
    assert sorted(passage_stimulus.items) == ["items/i1.xml", "items/i2.xml", "items/sub/i3.xml"]
    assert sorted(column_stimulus.items) == ["items/i6.xml", "items/i7.xml"]
    assert re.match(r"^stimuli/STIM_[0-9a-f]{8}\.xml$", passage_stimulus.path)


def test_creates_the_stimulus_file_with_rebased_assets(extracted):
    _, files, _, passage_stimulus, _ = extracted
    xml = files[passage_stimulus.path][0]
    assert '<qti-assessment-stimulus xmlns="http://www.imsglobal.org/xsd/imsqtiasi_v3p0"' in xml
    root = load(xml)
    assert root.get("identifier") == passage_stimulus.identifier
    assert root.get(XML_LANG) == "nl-NL"
    assert text(root.xpath("/qti-assessment-stimulus/qti-stimulus-body/h2")[0]) == "De Waddenzee"
    assert root.xpath("/qti-assessment-stimulus/qti-stimulus-body/p/img")[0].get("src") == "../img/wad.png"


def test_replaces_the_passage_in_the_items_by_a_stimulus_ref(extracted):
    _, files, _, passage_stimulus, _ = extracted
    i1 = load(files["items/i1.xml"][0])
    ref = i1.xpath("//qti-assessment-stimulus-ref")[0]
    assert ref.get("identifier") == passage_stimulus.identifier
    assert ref.get("href") == f"../{passage_stimulus.path}"
    assert ref.getnext().tag == "qti-item-body"
    assert not i1.xpath("//qti-item-body//h2 | //qti-item-body//img")
    # the item's own intro stays
    assert [text(p) for p in i1.xpath("//qti-item-body/p")] == ["Lees de tekst en beantwoord de vraag."]
    assert len(i1.xpath("//qti-choice-interaction")) == 1

    i3 = load(files["items/sub/i3.xml"][0])
    assert i3.xpath("//qti-assessment-stimulus-ref")[0].get("href") == f"../../{passage_stimulus.path}"


def test_removes_an_emptied_layout_column_and_unwraps_a_single_remaining_column(extracted):
    _, files, _, _, column_stimulus = extracted
    root = load(files["items/i6.xml"][0])
    assert not root.xpath("//*[contains(@class, 'qti-layout')]")
    assert len(root.xpath("//qti-item-body/qti-choice-interaction")) == 1
    assert root.xpath("//qti-assessment-stimulus-ref")[0].get("identifier") == column_stimulus.identifier


def test_registers_the_stimuli_in_the_manifest(extracted):
    _, files, _, passage_stimulus, _ = extracted
    root = load(files["imsmanifest.xml"][0])
    resource = root.xpath(f"//resource[@identifier='{passage_stimulus.identifier}']")[0]
    assert resource.get("type") == "imsqti_stimulus_xmlv3p0"
    assert [f.get("href") for f in resource.xpath("file")] == [passage_stimulus.path, "img/wad.png"]
    for identifier in ["items_i1_xml", "items_i2_xml", "items_sub_i3_xml"]:
        dependency = root.xpath(f"//resource[@identifier='{identifier}']/dependency")[0]
        assert dependency.get("identifierref") == passage_stimulus.identifier, identifier
    assert not root.xpath("//resource[@identifier='items_i4_xml']/dependency")


def test_only_reports_near_duplicates_and_leaves_other_items_untouched(extracted):
    source, files, report, _, _ = extracted
    pairs = [tuple(sorted(d.items)) for d in report.near_duplicates]
    assert ("items/i1.xml", "items/i4.xml") in pairs
    assert ("items/i2.xml", "items/i4.xml") in pairs
    assert all("items/i4.xml" in d.items for d in report.near_duplicates)
    for path in ["items/i4.xml", "items/i5.xml", "items/i8.xml", "items/i9.xml"]:
        assert files[path][0] is source[path][0], path
    assert files["img/wad.png"] is source["img/wad.png"]


def test_does_not_turn_a_shared_logo_into_a_stimulus():
    logo = '<p><img src="logo.png" alt="Logo"/></p>'
    _, report = extract_shared_stimuli(
        {"a.xml": (item("a", f"{logo}{choice('a')}"), "item"), "b.xml": (item("b", f"{logo}{choice('b')}"), "item")}
    )
    assert report.stimuli == []


def test_does_nothing_when_there_is_no_shared_content():
    source = build_package()
    single = {path: value for path, value in source.items() if path in ("items/i5.xml", "imsmanifest.xml")}
    files, report = extract_shared_stimuli(single)
    assert report.stimuli == [] and report.near_duplicates == []
    assert files == single
