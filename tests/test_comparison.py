"""Semantic safeguards: these tests compare text, never clinical correctness."""

from app.comparison import compare_sections
from app.documents import normalize_sections, parse_sections


def compare(old, new):
    return compare_sections(parse_sections(old), parse_sections(new))


def test_align_by_heading_not_order_and_aliases():
    result = compare("【成分】相同\n【用法用量】模拟内容", "【用法与用量】模拟内容\n【成份】相同")
    assert result["changes"] == []
    assert result["unchanged_sections"] == 2
    assert any("顺序" in warning for warning in result["warnings"])


def test_add_remove_modify_and_unchanged_count():
    result = compare("【药品名称】模拟品\n【成份】模拟甲\n【包装】旧条目", "【药品名称】模拟品\n【成份】模拟乙\n【贮藏】新条目")
    assert [(c["section_key"], c["kind"]) for c in result["changes"]] == [("成份", "modified"), ("贮藏", "added"), ("包装", "removed")]
    assert result["unchanged_sections"] == 1
    assert result["changes"][1]["old_location"] == "无对应章节"
    assert result["changes"][2]["new_location"] == "无对应章节"


def test_repeated_headings_are_retained_and_warning_is_explicit():
    result = compare("【成份】A\n【成份】B", "【成分】A\n【成分】C")
    assert result["unchanged_sections"] == 1
    assert result["changes"][0]["section_key"] == "成份#2"
    assert any("重复标题" in warning for warning in result["warnings"])


def test_no_header_document_is_compared_as_whole_text():
    result = compare("模拟旧文", "模拟新文")
    assert result["changes"][0]["section_key"] == "全文"
    assert result["warnings"]


def test_negation_number_and_unit_changes_never_normalized_away():
    result = compare("【注意事项】可以使用。\n【规格】模拟参数每次5mg", "【注意事项】不可以使用。\n【规格】模拟参数每次0.5g")
    assert len(result["changes"]) == 2
    assert all(change["priority"] == "high" for change in result["changes"])
    assert "数字或比例变化" in result["changes"][1]["flags"]
    assert "单位或剂型计量词变化" in result["changes"][1]["flags"]


def test_numeric_token_spaces_not_removed():
    result = compare("【规格】模拟 1 0 mg", "【规格】模拟 10 mg")
    assert len(result["changes"]) == 1


def test_layout_whitespace_is_ignored_but_numeric_separators_remain():
    result = compare("【注意事项】仅限\n模拟   测试", "【注意事项】仅限模拟测试")
    assert result["changes"] == []


def test_segments_reconstruct_both_originals_and_are_not_preescaped():
    old = "<script>模拟一</script>\n5 mg"
    new = "<img>模拟二 & 测试</img>\n10 mg"
    result = compare_sections(normalize_sections([{"title": "规格", "text": old}]), normalize_sections([{"title": "规格", "text": new}]))
    change = result["changes"][0]
    assert "".join(s["text"] for s in change["segments"] if s["op"] != "insert") == old
    assert "".join(s["text"] for s in change["segments"] if s["op"] != "delete") == new
    assert change["old_location"] == "手工校正章节"


def test_long_sections_use_explicit_bounded_fallback():
    old = "模拟旧文" * 5000
    new = "模拟新文" * 5000
    result = compare_sections(normalize_sections([{"title": "备注", "text": old}]), normalize_sections([{"title": "备注", "text": new}]))
    change = result["changes"][0]
    assert [s["op"] for s in change["segments"]] == ["delete", "insert"]
    assert any("长章节" in flag for flag in change["flags"])


def test_empty_added_heading_still_appears():
    result = compare("【药品名称】模拟品", "【药品名称】模拟品\n【注意事项】")
    assert len(result["changes"]) == 1
    assert result["changes"][0]["kind"] == "added"


def test_literal_suffix_cannot_overwrite_duplicate_section():
    result = compare("【成份】A\n【成份】B\n【成份#2】C", "【成份】A\n【成份】B2\n【成份#2】C2")
    assert result["unchanged_sections"] == 1
    assert len(result["changes"]) == 2
    assert {c["section_key"] for c in result["changes"]} == {"成份#2", "成份＃2"}
