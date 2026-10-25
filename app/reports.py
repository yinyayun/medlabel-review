"""Portable review reports. All human-supplied HTML is escaped on output."""

from __future__ import annotations

import csv
from datetime import datetime, timezone
from html import escape
import io
import json
from typing import Any


STATUSES = {"pending": "待复核", "confirmed": "已确认", "ignored": "已忽略"}
KINDS = {"added": "新增章节", "removed": "删除章节", "modified": "内容修改"}
PRIORITIES = {"high": "重点关注", "normal": "常规"}


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def _html(value: Any) -> str:
    return escape(_text(value), quote=True)


def _csv_cell(value: Any) -> str:
    """Prevent spreadsheet applications interpreting untrusted cells as formulae.

    Leading whitespace can hide a formula marker, and tab/CR/LF themselves are
    unsafe leading characters in some spreadsheet importers. Prefix the entire
    original cell with a quote so the original content remains readable.
    """
    text = _text(value)
    trimmed = text.lstrip(" \t\r\n\v\f\ufeff")
    if (text[:1] in ("\t", "\r", "\n")) or (
        trimmed and trimmed[0] in "=+-@"
    ):
        return "'" + text
    return text


def _provenance(version: dict, label: str) -> str:
    fields = (
        ("版本名称", version.get("label")),
        ("版本日期", version.get("version_date")),
        ("资料来源", version.get("source")),
        ("原始文件", version.get("filename")),
        ("导入时间", version.get("created_at")),
        ("SHA-256", version.get("sha256")),
    )
    rows = "".join(
        f"<dt>{_html(key)}</dt><dd>{_html(value) or '—'}</dd>"
        for key, value in fields
    )
    warnings = version.get("warnings") or []
    warning_html = "".join(f"<li>{_html(w)}</li>" for w in warnings)
    if warning_html:
        warning_html = f'<ul class="warning">{warning_html}</ul>'
    return f'<section class="version"><h3>{_html(label)}</h3><dl>{rows}</dl>{warning_html}</section>'


def _change_html(change: dict, index: int) -> str:
    status = _text(change.get("status", "pending"))
    flags = change.get("flags") or []
    flag_html = "".join(f'<span class="flag">{_html(flag)}</span>' for flag in flags)
    segments = []
    for segment in change.get("segments") or []:
        text = _html(segment.get("text", ""))
        op = segment.get("op")
        if op == "delete":
            segments.append(f"<del>{text}</del>")
        elif op == "insert":
            segments.append(f"<ins>{text}</ins>")
        else:
            segments.append(text)
    inline = (
        '<details><summary>逐字差异（删除线为旧内容，下划线为新内容）</summary>'
        f'<pre class="inline-diff">{"".join(segments)}</pre></details>'
        if segments else ""
    )
    return f"""
    <article class="change">
      <header class="change-title"><h3>{index:02d} · {_html(change.get('section_title', '未命名章节'))}</h3>
      <span class="badge">{_html(KINDS.get(change.get('kind'), change.get('kind', '')))}</span>
      <span class="badge">{_html(PRIORITIES.get(change.get('priority'), change.get('priority', '')))}</span>
      <span class="badge">{_html(STATUSES.get(status, status))}</span></header>
      <div class="flags">{flag_html}</div>
      <div class="columns">
        <section><h4>旧版原文</h4><p class="location">{_html(change.get('old_location')) or '无对应章节'}</p>
          <pre>{_html(change.get('old_text')) or '（无）'}</pre></section>
        <section><h4>新版原文</h4><p class="location">{_html(change.get('new_location')) or '无对应章节'}</p>
          <pre>{_html(change.get('new_text')) or '（无）'}</pre></section>
      </div>
      {inline}
      <section class="review"><h4>人工审核记录</h4>
        <p>状态：{_html(STATUSES.get(status, status))}　·　操作员：{_html(change.get('reviewer')) or '—'}　·　记录修订号：{_html(change.get('revision', 0))}</p>
        <p>复核时间：{_html(change.get('reviewed_at')) or '尚未复核'}</p>
        <p class="note">{_html(change.get('note')) or '未填写审核意见'}</p>
      </section>
    </article>"""


def _html_report(comparison: dict, exported_at: str) -> bytes:
    changes = comparison.get("changes") or []
    counts = {key: sum(c.get("status", "pending") == key for c in changes) for key in STATUSES}
    old = comparison.get("old_version") or {}
    new = comparison.get("new_version") or {}
    warnings = "".join(f"<li>{_html(w)}</li>" for w in comparison.get("warnings") or [])
    warning_block = f'<aside class="warning"><strong>比对提示</strong><ul>{warnings}</ul></aside>' if warnings else ""
    blocks = "".join(_change_html(c, i + 1) for i, c in enumerate(changes))
    if not blocks:
        blocks = '<section class="empty">未检出章节内容差异。此结果不等同于临床或法规一致性结论。</section>'
    html = f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; img-src data:; base-uri 'none'; form-action 'none'">
<title>说明书变更审核报告 #{_html(comparison.get('id'))}</title>
<style>
*{{box-sizing:border-box}}body{{margin:0;background:#f3f6f9;color:#1c3042;font:14px/1.7 system-ui,-apple-system,"Microsoft YaHei",sans-serif}}
main{{max-width:1150px;margin:32px auto;background:white;padding:40px;border-radius:16px}}h1{{margin:0 0 10px;font-size:28px;line-height:1.4}}h2{{margin:28px 0 14px;font-size:20px}}h3,h4{{margin:0 0 8px}}
.eyebrow{{color:#087f85;font-size:12px;letter-spacing:2px}}.muted,.location{{color:#5e6f7c}}.location{{font-size:12px;margin-top:0}}
.stats,.columns,.versions{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:18px}}.stats{{grid-template-columns:repeat(4,minmax(0,1fr));margin:25px 0}}
.stat,.version{{padding:18px;border:1px solid #dce5ec;border-radius:10px}}.stat strong{{display:block;font-size:26px;color:#087f85}}dl{{display:grid;grid-template-columns:76px minmax(0,1fr);gap:6px 10px;margin:0}}dt{{color:#5e6f7c}}dd{{margin:0;overflow-wrap:anywhere}}
.change{{border:1px solid #dce5ec;border-radius:10px;margin:18px 0;padding:22px}}.change-title{{display:flex;gap:8px;align-items:center;flex-wrap:wrap}}.change-title h3{{margin-right:auto}}.badge,.flag{{display:inline-block;border-radius:4px;padding:2px 7px;font-size:12px;background:#eaf2f7}}.flag{{background:#fff0d4;color:#78510a;margin:0 5px 8px 0}}.flags{{margin-top:8px}}
pre{{font:inherit;font-size:13px;white-space:pre-wrap;overflow-wrap:anywhere;background:#f6f8fa;padding:14px;border-radius:6px;margin:0}}.review{{margin-top:16px;border-top:1px solid #dce5ec;padding-top:14px}}.review p{{margin:5px 0}}.note{{white-space:pre-wrap;overflow-wrap:anywhere}}details{{margin-top:14px}}summary{{cursor:pointer;color:#087f85}}del{{background:#fee4e4;text-decoration:line-through}}ins{{background:#d9f3e4;text-decoration:underline}}.warning{{background:#fff7e9;padding:14px;border-radius:8px}}.empty{{padding:28px;border:1px dashed #a8c4d2;border-radius:8px}}footer{{margin-top:30px;border-top:1px solid #dce5ec;padding-top:15px;color:#5e6f7c;font-size:12px}}.print-tip{{font-size:12px;color:#5e6f7c}}
@media(max-width:700px){{main{{padding:20px;margin:0;border-radius:0}}.columns,.versions{{grid-template-columns:1fr}}.stats{{grid-template-columns:repeat(2,1fr)}}}}
@media print{{@page{{size:A4;margin:15mm}}body{{background:white;font-size:10pt}}main{{padding:0;margin:0;max-width:none}}.print-tip{{display:none}}.change{{break-inside:auto;border-radius:0}}.change-title,.review{{break-inside:avoid}}pre{{font-size:9pt}}a{{color:inherit}}details{{display:none}}}}
</style></head><body><main>
<div class="eyebrow">MEDLABEL REVIEW · V1.0</div>
<h1>药品说明书版本比对与变更审核报告</h1>
<p><strong>{_html(comparison.get('drug_name', ''))}</strong> · 比对任务 #{_html(comparison.get('id'))}</p>
<p class="muted">任务创建：{_html(comparison.get('created_at'))}　｜　导出时间：{_html(exported_at)}（UTC）</p>
<p class="print-tip">可使用浏览器“打印 / 另存为 PDF”保存此报告。</p>
<div class="stats"><div class="stat"><strong>{len(changes)}</strong>变更章节</div><div class="stat"><strong>{counts['pending']}</strong>待复核</div><div class="stat"><strong>{counts['confirmed']}</strong>已确认</div><div class="stat"><strong>{counts['ignored']}</strong>已忽略</div></div>
<p class="muted">未变更章节：{_html(comparison.get('unchanged_sections', 0))}。本报告反映导出时点的审核状态。</p>
<h2>资料来源与版本追溯</h2><div class="versions">{_provenance(old, '旧版')}{_provenance(new, '新版')}</div>
{warning_block}<h2>章节差异与审核意见</h2>{blocks}
<footer>药品说明书版本比对与变更审核系统 V1.0。此工具用于文本比对与记录整理，不判断临床正确性或替代专业审核。操作员名称是本地配置的记录标签，未经身份认证；本报告不构成电子签名。行号指向提取文本，手工校正章节的位置应结合原始文件复核。文件 SHA-256 用于核对原始文件内容。</footer>
</main></body></html>"""
    return html.encode("utf-8")


def _csv_report(comparison: dict, exported_at: str) -> bytes:
    stream = io.StringIO(newline="")
    writer = csv.writer(stream)
    writer.writerow([
        "任务ID", "药品名称", "导出时间UTC", "旧版名称", "旧版日期", "旧版来源", "旧版文件", "旧版SHA256",
        "新版名称", "新版日期", "新版来源", "新版文件", "新版SHA256", "章节", "变化类型", "关注级别",
        "旧版位置", "新版位置", "旧版内容", "新版内容", "关注标记", "审核状态", "审核意见", "本地操作员（未认证）", "复核时间", "修订号",
    ])
    old, new = comparison.get("old_version") or {}, comparison.get("new_version") or {}
    common = [comparison.get("id"), comparison.get("drug_name"), exported_at]
    for version in (old, new):
        common.extend(version.get(key) for key in ("label", "version_date", "source", "filename", "sha256"))
    changes = comparison.get("changes") or []
    if not changes:
        writer.writerow([_csv_cell(value) for value in common + ["未检出章节内容差异"] + [""] * 12])
    for change in changes:
        row = common + [
            change.get("section_title"), KINDS.get(change.get("kind"), change.get("kind")),
            PRIORITIES.get(change.get("priority"), change.get("priority")),
            change.get("old_location"), change.get("new_location"), change.get("old_text"), change.get("new_text"),
            "；".join(_text(flag) for flag in change.get("flags") or []),
            STATUSES.get(change.get("status"), change.get("status")), change.get("note"),
            change.get("reviewer"), change.get("reviewed_at"), change.get("revision"),
        ]
        writer.writerow([_csv_cell(value) for value in row])
    return stream.getvalue().encode("utf-8-sig")


def export_report(comparison: dict, format: str) -> tuple[bytes, str, str]:
    """Return a ready-to-download body, content type, and safe ASCII filename."""
    exported_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    identifier = str(comparison.get("id", "report"))
    safe_id = "".join(char for char in identifier if char.isascii() and (char.isalnum() or char in "-_")) or "report"
    if format == "html":
        return _html_report(comparison, exported_at), "text/html; charset=utf-8", f"comparison-{safe_id}.html"
    if format == "csv":
        return _csv_report(comparison, exported_at), "text/csv; charset=utf-8", f"comparison-{safe_id}.csv"
    if format == "json":
        payload = dict(comparison)
        payload["exported_at"] = exported_at
        return json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8"), "application/json; charset=utf-8", f"comparison-{safe_id}.json"
    raise ValueError("不支持的导出格式，请选择 html、csv 或 json。")
