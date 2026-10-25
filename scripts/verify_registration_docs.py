"""Verify the rendered registration package and write its artifact manifest.

Run after Word generation and PDF conversion. Requires pypdf; does not modify
source documents, Word files, PDFs, or the source-code manifest.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sys
from urllib.parse import unquote
from zipfile import ZipFile

from pypdf import PdfReader


ROOT = Path(__file__).resolve().parents[1]
STEMS = (
    '01_用户操作说明书', '02_软件设计说明书', '03_登记申请信息草稿',
    '04_提交材料核对单', '05_源程序鉴别材料', '06_源程序编排说明',
)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify(base: Path) -> dict:
    source_path = base / 'source_manifest.json'
    source = json.loads(source_path.read_text(encoding='utf-8'))
    baseline = source['baseline_commit']
    require(bool(re.fullmatch(r'[0-9a-f]{40}', baseline)), '代码基线不是完整 Git SHA')
    require(source['submitted_pages'] == len(source['pages']) == 60,
            '源程序清单须包含 60 个提交页')
    full_pages = source['full_layout_pages']
    expected_selection = list(range(1, 31)) + list(range(full_pages - 29, full_pages + 1))
    require([p['full_layout_page'] for p in source['pages']] == expected_selection,
            '源程序清单不是完整编排的前 30 页和后 30 页')

    images = []
    markdown_files = sorted((base / 'source').glob('*.md'))
    for stem in STEMS:
        if not stem.startswith('05_'):
            require((base / 'source' / f'{stem}.md').is_file(), f'缺少 Markdown：{stem}')
    for markdown in markdown_files:
        for target in re.findall(r'!\[[^\]]*\]\(([^)]+)\)', markdown.read_text(encoding='utf-8')):
            image = (markdown.parent / unquote(target.strip().strip('<>'))).resolve()
            require(image.is_file(), f'图片引用不存在：{markdown.name} → {target}')
            images.append({'markdown': str(markdown.relative_to(base)), 'target': target})

    documents = []
    code_pages = []
    for stem in STEMS:
        word, pdf = base / 'word' / f'{stem}.docx', base / 'pdf' / f'{stem}.pdf'
        require(word.is_file() and pdf.is_file(), f'缺少 Word 或 PDF：{stem}')
        with ZipFile(word) as archive:
            require('word/document.xml' in archive.namelist() and archive.testzip() is None,
                    f'Word 文件结构损坏：{word.name}')
        reader = PdfReader(pdf)
        require(not reader.is_encrypted, f'PDF 不应加密：{pdf.name}')
        require(len(reader.pages) > 0, f'PDF 无页面：{pdf.name}')
        texts = [page.extract_text() or '' for page in reader.pages]
        for number, text in enumerate(texts, 1):
            require('\ufffd' not in text, f'PDF 含替换字符：{pdf.name}，第 {number} 页')
            require(bool(text.strip()), f'PDF 含空白或无法提取文字的页面：{pdf.name}，第 {number} 页')
        if stem.startswith('05_'):
            require(len(texts) == source['submitted_pages'], '源程序 PDF 页数与清单不一致')
            for number, (text, page) in enumerate(zip(texts, source['pages']), 1):
                require(page['submission_page'] == number, f'源程序清单提交页码不连续：{number}')
                actual = [int(n) for n in re.findall(r'(?m)^(\d{5})[ \t]+', text)]
                first, last = page['first']['display_row'], page['last']['display_row']
                expected = list(range(first, last + 1))
                require(len(actual) == page['display_rows'] and actual == expected,
                        f'源程序 PDF 第 {number} 页显示行号不符：实际 {len(actual)} 行，'
                        f'清单 {page["display_rows"]} 行，预期行号 {first}—{last}')
                require(page['display_rows'] == (35 if number == 60 else 55),
                        f'源程序清单第 {number} 页不是预期的 55 行（末页 35 行）')
                code_pages.append({'submission_page': number, 'full_layout_page': page['full_layout_page'],
                                   'display_rows': len(actual), 'first_display_row': first, 'last_display_row': last})
        documents.append({'document': stem, 'pages': len(texts), 'characters': sum(map(len, texts)),
                          'word': {'path': str(word.relative_to(base)), 'sha256': sha256(word)},
                          'pdf': {'path': str(pdf.relative_to(base)), 'sha256': sha256(pdf)}})
    return {'software': source['software'], 'version': source['version'], 'baseline_commit': baseline,
            'verified_at_utc': datetime.now(timezone.utc).isoformat(timespec='seconds'),
            'source_manifest_sha256': sha256(source_path), 'documents': documents,
            'markdown_image_references': images, 'source_pdf_pages': code_pages,
            'checks': {'word_pdf_pairs': len(documents), 'images_exist': True,
                       'pdf_replacement_characters': 0, 'source_page_and_line_numbers_match': True}}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-dir', type=Path, default=ROOT / 'docs/registration')
    args = parser.parse_args()
    try:
        base = args.base_dir.resolve()
        manifest = verify(base)
        output = base / 'document_manifest.json'
        temporary = output.with_suffix('.json.tmp')
        temporary.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        temporary.replace(output)
    except Exception as exc:
        print(f'文档核验失败：{exc}', file=sys.stderr)
        return 1
    for item in manifest['documents']:
        print(f'{item["document"]}.pdf：{item["pages"]} 页')
    print(f'文档核验通过，清单已写入：{output}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
