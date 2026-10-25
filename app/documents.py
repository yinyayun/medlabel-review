"""Extract and structure drug leaflets without inferring medical meaning.

Only user supplied text is processed.  The original file/text is retained by the
API; these sections are a reviewable representation, never a replacement source.
"""

from __future__ import annotations

import io
import re
import unicodedata
import zipfile
from collections import Counter
from pathlib import PurePath


MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_TEXT_CHARS = 1_000_000
MAX_PDF_PAGES = 100
MAX_SECTIONS = 500
MAX_DOCX_EXPANDED = 40 * 1024 * 1024

# Alias matching is deliberately conservative.  Similar clinical concepts must
# not be merged simply because a fuzzy text matcher considers their names close.
_ALIASES = {
    "药品名称": "药品名称", "药物名称": "药品名称",
    "成份": "成份", "成分": "成份", "主要成份": "成份", "主要成分": "成份",
    "性状": "性状", "适应症": "适应症", "适应证": "适应症",
    "功能主治": "功能主治", "规格": "规格", "用法用量": "用法用量",
    "用法与用量": "用法用量", "用法及用量": "用法用量",
    "不良反应": "不良反应", "禁忌": "禁忌", "禁忌症": "禁忌",
    "禁忌证": "禁忌", "注意事项": "注意事项", "警告": "警告",
    "警示语": "警告", "警示": "警告", "黑框警告": "黑框警告",
    "孕妇及哺乳期妇女用药": "孕妇及哺乳期妇女用药",
    "孕妇和哺乳期妇女用药": "孕妇及哺乳期妇女用药",
    "孕妇及哺乳期用药": "孕妇及哺乳期妇女用药",
    "儿童用药": "儿童用药", "老年用药": "老年用药", "老年患者用药": "老年用药",
    "特殊人群用药": "特殊人群用药", "药物相互作用": "药物相互作用",
    "药物过量": "药物过量", "临床试验": "临床试验",
    "药理毒理": "药理毒理", "药理作用": "药理作用", "毒理研究": "毒理研究",
    "药代动力学": "药代动力学", "贮藏": "贮藏", "储藏": "贮藏",
    "包装": "包装", "有效期": "有效期", "执行标准": "执行标准",
    "批准文号": "批准文号", "药品批准文号": "批准文号",
    "上市许可持有人": "上市许可持有人", "药品上市许可持有人": "上市许可持有人",
    "生产企业": "生产企业", "生产厂家": "生产企业",
    "核准日期": "核准日期", "修改日期": "修改日期", "修订日期": "修改日期",
}
_BRACKET_HEADING = re.compile(r"^\s*(?:【([^】\n]{1,60})】|\[([^\]\n]{1,60})\]|［([^］\n]{1,60})］)\s*[:：]?\s*(.*)$")
_NUMBER_PREFIX = re.compile(r"^\s*(?:(?:[一二三四五六七八九十百]+|\d+)[、.．]\s*|[（(](?:[一二三四五六七八九十百]+|\d+)[）)]\s*)")


def _clean_text(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\ufeff", "")
    if "\x00" in text:
        raise ValueError("文档包含二进制或空字符，请转换为文字版文件后重试")
    if len(text) > MAX_TEXT_CHARS:
        raise ValueError("文档文字超过 100 万字符，请拆分为单份药品说明书")
    if not text.strip():
        raise ValueError("未提取到可用文字；扫描件需要先进行 OCR 并人工核对")
    return text


def _pdf_contains_image(page, reader) -> bool:
    """Inspect resource graphs and parsed operators, never text-density guesses.

    PDF images can coexist with a tiny text layer (for example only a page
    number), be nested in Form XObjects, or be embedded as inline images. V1
    deliberately rejects all such pages, including otherwise harmless logos.
    """
    from pypdf.generic import ContentStream

    seen_resources: set[int] = set()
    seen_streams: set[int] = set()

    def resolve(value):
        return value.get_object() if hasattr(value, "get_object") else value

    def inspect(stream, resources, depth=0):
        if depth > 32 or len(seen_resources) + len(seen_streams) > 2000:
            raise ValueError("PDF 图形结构过于复杂，无法确认文字完整性；请转换为完整文字并核对")
        stream = resolve(stream)
        resources = resolve(resources) if resources else {}
        if stream is not None and id(stream) not in seen_streams:
            seen_streams.add(id(stream))
            parsed = stream if isinstance(stream, ContentStream) else ContentStream(stream, reader)
            if any(operator == b"INLINE IMAGE" for _, operator in parsed.operations):
                return True
        if id(resources) in seen_resources:
            return False
        seen_resources.add(id(resources))
        for category in ("/XObject", "/Pattern"):
            objects = resolve(resources.get(category, {}))
            for reference in objects.values():
                obj = resolve(reference)
                if obj.get("/Subtype") == "/Image":
                    return True
                if obj.get("/Subtype") == "/Form" or obj.get("/PatternType") == 1:
                    if inspect(obj, obj.get("/Resources", resources), depth + 1):
                        return True
        return False

    resources = page.get("/Resources", {})
    if inspect(page.get_contents(), resources):
        return True
    # Annotation appearance streams may also contain images not referenced by
    # the main page content stream (for example a scanned stamp or attachment).
    for reference in resolve(page.get("/Annots", [])):
        annotation = resolve(reference)
        appearances = resolve(annotation.get("/AP", {}))
        for appearance in appearances.values():
            appearance = resolve(appearance)
            candidates = [appearance] if hasattr(appearance, "get_data") else [resolve(value) for value in appearance.values()]
            for candidate in candidates:
                if inspect(candidate, candidate.get("/Resources", resources)):
                    return True
    return False


def _pdf_text(data: bytes) -> str:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data), strict=False)
    if reader.is_encrypted:
        raise ValueError("PDF 已加密，请提供解密后的文字版 PDF")
    if len(reader.pages) > MAX_PDF_PAGES:
        raise ValueError("PDF 超过 100 页，请拆分为单份药品说明书")
    texts: list[str] = []
    length = 0
    for index, page in enumerate(reader.pages, start=1):
        if _pdf_contains_image(page, reader):
            raise ValueError(f"此 PDF 含图片内容，可能遗漏文字；请先转换为完整文字并核对（第 {index} 页）")
        text = page.extract_text() or ""
        if text.strip():
            texts.append(text)
            length += len(text) + 2
        if length > MAX_TEXT_CHARS:
            raise ValueError("文档文字超过 100 万字符，请拆分为单份药品说明书")
    return "\n\n".join(texts)


def _docx_text(data: bytes) -> str:
    from docx import Document
    from docx.oxml.ns import qn

    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        members = archive.infolist()
        if len(members) > 2500 or sum(member.file_size for member in members) > MAX_DOCX_EXPANDED:
            raise ValueError("DOCX 解压后体积或文件数量过大，请简化文件后重试")
        if any(member.flag_bits & 1 for member in members):
            raise ValueError("不支持加密的 DOCX 文件")
        if any(member.file_size > 1024 * 1024 and member.file_size / max(member.compress_size, 1) > 200 for member in members):
            raise ValueError("DOCX 压缩比例异常，请重新保存为普通文档")
        if "word/document.xml" not in archive.namelist():
            raise ValueError("文件不是有效的 DOCX 文档")

    document = Document(io.BytesIO(data))
    headers, footers = [], []
    seen_parts: set[str] = set()
    for relation in document.part.rels.values():
        if relation.is_external or not relation.reltype.endswith(("/header", "/footer")):
            continue
        part = relation.target_part
        if str(part.partname) in seen_parts:
            continue
        seen_parts.add(str(part.partname))
        (headers if relation.reltype.endswith("/header") else footers).append(part.element)
    roots = headers + [document.element.body] + footers
    for root in roots:
        if root.xpath(".//w:footnoteReference | .//w:endnoteReference"):
            raise ValueError("DOCX 含脚注或尾注，请先转换为包含全部文字的 TXT，避免遗漏")
        if root.xpath(".//w:ins | .//w:del"):
            raise ValueError("DOCX 含未处理的修订记录，请确认修订后重新上传")
    # Iterate paragraphs in XML document order. This includes paragraphs inside
    # table cells and text boxes, which Document.paragraphs alone would omit.
    paragraphs: list[str] = []
    for root in roots:
        if root in headers:
            paragraphs.append("【页眉内容】")
        elif root in footers:
            paragraphs.append("【页脚内容】")
        for paragraph in root.iter(qn("w:p")):
            fragments: list[str] = []
            for node in paragraph.iter():
                # Text-box paragraphs are visited in their own iteration; skip
                # their text here to avoid duplication in the outer paragraph.
                parent = node.getparent()
                in_nested_paragraph = False
                while parent is not None and parent is not paragraph:
                    if parent.tag == qn("w:p"):
                        in_nested_paragraph = True
                        break
                    parent = parent.getparent()
                if in_nested_paragraph:
                    continue
                if node.tag == qn("w:t") and node.text:
                    fragments.append(node.text)
                elif node.tag == qn("w:tab"):
                    fragments.append("\t")
                elif node.tag in {qn("w:br"), qn("w:cr")}:
                    fragments.append("\n")
            paragraphs.append("".join(fragments))
    return "\n".join(paragraphs)


def extract_text(filename: str, data: bytes) -> str:
    """Extract a supported document or raise a user-facing ``ValueError``.

    Filenames are used only to select format, never as filesystem destinations.
    Size checks are duplicated here so callers other than HTTP cannot bypass them.
    """
    if not isinstance(data, bytes) or not data:
        raise ValueError("文件为空，请选择包含文字的文件")
    if len(data) > MAX_FILE_BYTES:
        raise ValueError("文件超过 10 MB，请压缩或拆分后重试")
    extension = PurePath(filename.replace("\\", "/")).suffix.lower()
    try:
        if extension == ".txt":
            text = None
            for encoding in ("utf-8-sig", "gb18030"):
                try:
                    text = data.decode(encoding)
                    break
                except UnicodeDecodeError:
                    pass
            if text is None:
                raise ValueError("TXT 编码无法识别，请另存为 UTF-8")
        elif extension == ".pdf":
            if not data.lstrip().startswith(b"%PDF-"):
                raise ValueError("文件内容不是有效的 PDF")
            text = _pdf_text(data)
        elif extension == ".docx":
            text = _docx_text(data)
        else:
            raise ValueError("仅支持 TXT、文字版 PDF 和 DOCX 文件；旧版 DOC 请另存为 DOCX")
        return _clean_text(text)
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError("文档无法解析，可能已损坏或格式不匹配；请重新导出为文字版文件") from exc


def canonical_title(title: str) -> str:
    """Normalize only heading formatting and explicitly documented aliases."""
    normalized = unicodedata.normalize("NFKC", title).strip().strip("【】[]［］:： ")
    normalized = _NUMBER_PREFIX.sub("", normalized)
    compact = re.sub(r"\s+", "", normalized)
    return _ALIASES.get(compact, compact or "未命名章节")


def _heading(line: str) -> tuple[str, str] | None:
    match = _BRACKET_HEADING.match(line)
    if match:
        title = next(part for part in match.groups()[:3] if part is not None).strip()
        # [1] at the start of a reference paragraph is not a section title.
        if re.fullmatch(r"[\d\W_]+", title):
            return None
        return title, match.group(4)
    stripped = _NUMBER_PREFIX.sub("", line.strip())
    # Unbracketed headings must exactly match a known heading, or be followed
    # by a colon. Arbitrary short body sentences are never promoted to headings.
    title, separator, remainder = stripped.partition("：")
    if not separator:
        title, separator, remainder = stripped.partition(":")
    compact = re.sub(r"\s+", "", title)
    if compact in _ALIASES:
        return title.strip(), remainder.strip() if separator else ""
    return None


def _assign_keys(sections: list[dict]) -> list[dict]:
    counts: Counter[str] = Counter()
    for section in sections:
        # Reserve ASCII '#' for the occurrence suffix; literal title characters
        # must not collide with generated keys such as 成份#2.
        canonical = canonical_title(section["title"]).replace("#", "＃")
        counts[canonical] += 1
        section["key"] = canonical if counts[canonical] == 1 else f"{canonical}#{counts[canonical]}"
    return sections


def parse_sections(text: str) -> list[dict]:
    """Split sections, preserving preamble, duplicates, and source line ranges.

    Line numbers refer to extracted text, not PDF page layout. Unknown bracketed
    headings remain separate sections. Unstructured documents become one 全文
    section so content cannot silently disappear.
    """
    text = _clean_text(text)
    lines = text.split("\n")
    sections: list[dict] = []
    current: dict | None = None
    buffer: list[str] = []

    def flush(end_line: int) -> None:
        if current is None:
            return
        current["text"] = "\n".join(buffer).strip()
        current["end_line"] = max(current["start_line"], end_line)
        sections.append(current)

    for number, line in enumerate(lines, start=1):
        heading = _heading(line)
        if heading is not None:
            flush(number - 1)
            title, remainder = heading
            current = {"title": title, "start_line": number}
            buffer = [remainder] if remainder else []
        else:
            if current is None:
                if not line.strip():
                    continue
                current = {"title": "前言", "start_line": number}
            buffer.append(line)
    flush(len(lines))
    if len(sections) == 1 and sections[0]["title"] == "前言":
        sections[0]["title"] = "全文"
    if len(sections) > MAX_SECTIONS:
        raise ValueError("章节数量超过 500，请检查文件格式或拆分文档")
    return _assign_keys(sections)


def normalize_sections(sections: list[dict]) -> list[dict]:
    """Validate a manual section correction and regenerate stable keys.

    Source line numbers are intentionally cleared: manual corrections cannot
    honestly claim exact correspondence with extraction line positions.
    """
    if not isinstance(sections, list) or not sections:
        raise ValueError("至少保留一个章节")
    if len(sections) > MAX_SECTIONS:
        raise ValueError("章节数量不能超过 500")
    result: list[dict] = []
    total = 0
    for section in sections:
        if not isinstance(section, dict):
            raise ValueError("章节格式错误")
        title = section.get("title")
        text = section.get("text")
        if not isinstance(title, str) or not title.strip() or len(title.strip()) > 120:
            raise ValueError("章节标题应为 1 至 120 个字符")
        if not isinstance(text, str) or "\x00" in text:
            raise ValueError("章节内容应为有效文本")
        text = text.replace("\r\n", "\n").replace("\r", "\n").strip()
        total += len(text)
        result.append({"title": title.strip(), "text": text, "start_line": None, "end_line": None})
    if total > MAX_TEXT_CHARS:
        raise ValueError("章节文字总量不能超过 100 万字符")
    if not any(section["text"] for section in result):
        raise ValueError("章节内容不能全部为空")
    return _assign_keys(result)
