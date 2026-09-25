import io
import zipfile

from helpers import load

from qti_convert import (
    PackageReferenceResolver,
    ReferenceResolution,
    fix_package_references,
    fix_package_references_files,
    package_to_zip,
    read_package,
)
from qti_convert.cli import main

QTI2 = "http://www.imsglobal.org/xsd/imsqti_v2p1"
QTI3 = "http://www.imsglobal.org/xsd/imsqtiasi_v3p0"
PNG = b"\x89PNG"


def qti2_item(body, head=""):
    return (
        f'\ufeff<?xml version="1.0" encoding="utf-8" standalone="yes"?>\n<assessmentItem identifier="i" '
        f'xmlns="{QTI2}">\n  {head}\n  <itemBody>{body}</itemBody>\n'
        '  <responseProcessing templateLocation="/templates/rp.xml" />\n</assessmentItem>'
    ).encode()


def package(item, extra=None):
    files = {
        "imsmanifest.xml": b"<manifest/>",
        "questions/q1.xml": item,
        "mediafiles/a.png": PNG,
        "css/q1.css": b"p {}",
        "templates/rp.xml": b"<responseProcessing/>",
    }
    files.update(extra or {})
    return files


def src_of(files, path="questions/q1.xml", xpath="//img"):
    return [el.get("src") for el in load(files[path]).xpath(xpath)]


def test_package_root_relative_and_root_absolute_references():
    item = qti2_item('<img src="mediafiles/a.png" alt="" />', '<stylesheet href="css/q1.css" type="text/css" />')
    result = fix_package_references_files(package(item))
    xml = result.files["questions/q1.xml"].decode("utf-8")
    assert '<img src="../mediafiles/a.png" alt="" />' in xml
    assert '<stylesheet href="../css/q1.css" type="text/css" />' in xml
    assert 'templateLocation="../templates/rp.xml"' in xml
    assert {(f.attribute, f.method, f.target) for f in result.fixed} == {
        ("src", "package-root", "mediafiles/a.png"),
        ("href", "package-root", "css/q1.css"),
        ("templateLocation", "package-root", "templates/rp.xml"),
    }
    assert result.unresolved == []


def test_only_the_references_change():
    item = qti2_item('<img src="mediafiles/a.png" alt="" /><br />')
    fixed = fix_package_references_files(package(item)).files["questions/q1.xml"]
    expected = item.replace(b'src="mediafiles/a.png"', b'src="../mediafiles/a.png"')
    assert fixed == expected.replace(b'"/templates/rp.xml"', b'"../templates/rp.xml"')


def test_leaves_correct_references_and_other_files_alone():
    item = qti2_item('<img src="../mediafiles/a.png" alt=""/><a href="https://example.com/x.png">x</a>')
    files = package(item, {"templates/rp.xml": b"<responseProcessing/>"})
    files["questions/q1.xml"] = item.replace(b"/templates/rp.xml", b"../templates/rp.xml")
    result = fix_package_references_files(files)
    assert result.fixed == [] and result.unresolved == []
    assert all(result.files[path] is files[path] for path in files)


def test_is_idempotent():
    first = fix_package_references_files(package(qti2_item('<img src="mediafiles/a.png"/>')))
    second = fix_package_references_files(first.files)
    assert second.fixed == []
    assert second.files == first.files


def test_finds_files_by_name():
    item = qti2_item('<img src="images/a.png"/><img src="C:\\Users\\me\\b.png"/><img src="file:///C:/tmp/c%20d.png"/>')
    result = fix_package_references_files(package(item, {"media/deep/b.png": PNG, "media/c d.png": PNG}))
    assert src_of(result.files) == ["../mediafiles/a.png", "../media/deep/b.png", "../media/c%20d.png"]
    assert {f.method for f in result.fixed if f.attribute == "src"} == {"file-name"}
    assert fix_package_references_files(package(item), search_by_file_name=False).unresolved


def test_prefers_the_file_whose_folders_match_and_reports_a_tie():
    item = qti2_item('<img src="media/x/a.png"/><img src="other/b.png"/>')
    extra = {"m1/x/a.png": PNG, "m2/y/a.png": PNG, "p/b.png": PNG, "q/b.png": PNG}
    result = fix_package_references_files(package(item, extra))
    assert src_of(result.files) == ["../m1/x/a.png", "other/b.png"]
    unresolved = result.unresolved[0]
    assert unresolved.value == "other/b.png"
    assert sorted(unresolved.candidates) == ["p/b.png", "q/b.png"]


def test_case_mismatch_query_and_encoding():
    item = qti2_item('<img src="../MediaFiles/A.PNG"/><img src="mediafiles/my%20image.png?v=2"/>')
    result = fix_package_references_files(package(item, {"mediafiles/my image.png": PNG}))
    assert src_of(result.files) == ["../mediafiles/a.png", "../mediafiles/my%20image.png?v=2"]
    assert [f.method for f in result.fixed if f.attribute == "src"] == ["case", "package-root"]


def test_qti3_items_tests_modules_and_loose_attributes():
    item = f"""<qti-assessment-item xmlns="{QTI3}" identifier="i"><qti-item-body>
      <qti-portable-custom-interaction response-identifier="R" module="m">
        <qti-interaction-modules><qti-interaction-module id="m" primary-path="modules/m"/></qti-interaction-modules>
      </qti-portable-custom-interaction>
      <object data="mediafiles/a.png" type="image/png"><param name="flag" value="true"/><param name="img" value="mediafiles/a.png"/></object>
    </qti-item-body></qti-assessment-item>"""
    test = f"""<qti-assessment-test xmlns="{QTI3}" identifier="t"><qti-test-part identifier="p">
      <qti-assessment-section identifier="s"><qti-assessment-item-ref identifier="i" href="q1.xml"/>
      <qti-assessment-item-ref identifier="gone" href="gone.xml"/></qti-assessment-section></qti-test-part></qti-assessment-test>"""
    files = {
        "pkg/imsmanifest.xml": "<manifest/>",
        "pkg/items/q1.xml": item,
        "pkg/tests/test.xml": test,
        "pkg/modules/m.js": "",
        "pkg/mediafiles/a.png": PNG,
    }
    result = fix_package_references_files(files)
    root = load(result.files["pkg/items/q1.xml"])
    # the package root is the folder of the manifest; the .js extension stays left out
    assert root.xpath("//qti-interaction-module")[0].get("primary-path") == "../modules/m"
    assert root.xpath("//object")[0].get("data") == "../mediafiles/a.png"
    assert [p.get("value") for p in root.xpath("//param")] == ["true", "../mediafiles/a.png"]
    refs = load(result.files["pkg/tests/test.xml"]).xpath("//qti-assessment-item-ref")
    assert refs[0].get("href") == "../items/q1.xml"
    assert [(u.file, u.value) for u in result.unresolved] == [("pkg/tests/test.xml", "gone.xml")]


def test_zip_folder_and_cli(tmp_path, capsys):
    source = tmp_path / "in.zip"
    source.write_bytes(package_to_zip(package(qti2_item('<img src="mediafiles/a.png"/>'))))
    result = fix_package_references(source, tmp_path / "out.zip")
    assert len(result.fixed) == 2
    assert src_of(read_package(tmp_path / "out.zip")) == ["../mediafiles/a.png"]

    assert main(["fix-references", str(source), str(tmp_path / "out")]) == 0
    assert "2 reference(s) fixed, 0 not found" in capsys.readouterr().err
    assert src_of(read_package(tmp_path / "out")) == ["../mediafiles/a.png"]


def test_resolver_works_with_only_the_paths_of_a_zip():
    source = package_to_zip(package(qti2_item('<img src="mediafiles/a.png"/>')))
    with zipfile.ZipFile(io.BytesIO(source)) as archive:
        resolver = PackageReferenceResolver(archive.namelist())
    assert resolver.root_dir == ""
    fixed = resolver.resolve("questions/q1.xml", "mediafiles/a.png", "src")
    assert fixed == ReferenceResolution("mediafiles/a.png", "package-root", "../mediafiles/a.png")
    assert resolver.resolve("questions/q1.xml", "../mediafiles/a.png", "src") == ReferenceResolution("mediafiles/a.png")
    assert resolver.resolve("questions/q1.xml", "/templates/rp.xml").new_value == "../templates/rp.xml"


def test_resolver_skips_what_is_not_a_file_reference_and_stays_inside_the_package():
    resolver = PackageReferenceResolver(["pkg/imsmanifest.xml", "pkg/items/q1.xml", "pkg/img/a.png", "pkg/img/"])
    assert resolver.root_dir == "pkg"
    for value in ["https://example.com/a.png", "data:image/png;base64,AAAA", "#part", "", "  "]:
        assert resolver.resolve("pkg/items/q1.xml", value, "src") is None, value
    assert resolver.resolve("pkg/items/q1.xml", "true", "value") is None
    missing = resolver.resolve("pkg/items/q1.xml", "../../../etc/passwd", "src")
    assert missing == ReferenceResolution(None)
    # the package root comes before a search by name, and a found target is always a package path
    assert resolver.resolve("pkg/items/q1.xml", "../../img/a.png").target == "pkg/img/a.png"
    assert PackageReferenceResolver(["a/x.png", "b/x.png"]).resolve("q.xml", "x.png").candidates == (
        "a/x.png",
        "b/x.png",
    )
    assert PackageReferenceResolver(["img/a.png"], search_by_file_name=False).resolve("q/q.xml", "a.png").target is None
