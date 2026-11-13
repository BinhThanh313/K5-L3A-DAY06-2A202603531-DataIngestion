"""Read and normalize source data for the Day 06 Data Ingestion pipeline."""
from __future__ import annotations

import re
import unicodedata
from pathlib import Path
from typing import Any

import pypdf

from .models import DocumentPage

# Physical pages excluded per GUIDE.md rules (cover, empty, copyright, TOC, lists, abbreviations, acknowledgments, dividers, back cover)
EXCLUDED_PAGE_REASONS: dict[int, str] = {
    1: "cover_page",
    2: "empty_page",
    3: "inner_title_page",
    4: "copyright_and_disclaimer",
    5: "table_of_contents",
    6: "list_of_figures",
    7: "list_of_tables_and_figures",
    8: "abbreviations_en",
    9: "abbreviations_vi",
    10: "acknowledgments",
    19: "chapter_1_divider",
    42: "chapter_2_divider",
    62: "empty_page",
    63: "empty_page",
    64: "back_cover_contact",
}

ROMAN_PAGES = ["i", "ii", "iii", "iv", "v", "vi", "vii", "viii"]

LAST_INGESTION_REPORT: dict[str, Any] = {}


def _infer_page_metadata(page_num: int) -> dict[str, Any]:
    """Assign verified structural metadata based on the report's Table of Contents (page 5)."""
    if 11 <= page_num <= 18:
        section = "tom_luoc"
        section_title = "Tóm lược tổng quan"
        printed_page = ROMAN_PAGES[page_num - 11]
        if page_num <= 14:
            sub_section = "Diễn biến kinh tế gần đây"
        elif page_num <= 16:
            sub_section = "Triển vọng và hàm ý chính sách"
        else:
            sub_section = "Chuyên đề đặc biệt: Chuyển đổi sang xe điện"
    elif 20 <= page_num <= 41:
        section = "kinh_te_vi_mo"
        section_title = "Chương 1: Diễn biến và triển vọng kinh tế"
        printed_page = str(page_num - 10)
        sub_section = (
            "1.1 Diễn biến kinh tế gần đây"
            if page_num <= 37
            else "1.2 Triển vọng và hàm ý chính sách"
        )
    elif 43 <= page_num <= 61:
        section = "chuyen_doi_xe_dien"
        section_title = "Chương 2: Chuyển đổi sang xe điện"
        printed_page = str(page_num - 10)
        if page_num <= 44:
            sub_section = "2.1 Bối cảnh"
        elif page_num <= 47:
            sub_section = "2.2 Chuyển đổi sang xe điện bao hàm điều gì?"
        elif page_num <= 50:
            sub_section = "2.3 Các kịch bản về chuyển sang xe điện"
        elif page_num <= 53:
            sub_section = "2.4 Tác động đến nhu cầu điện"
        elif page_num <= 55:
            sub_section = "2.5 Tác động đến khí thải nhà kính"
        elif page_num <= 57:
            sub_section = "2.6 Tác động đến kinh tế"
        else:
            sub_section = "2.7 Khuyến nghị chính sách"
    else:
        section = "other"
        section_title = "Khác"
        sub_section = "Khác"
        printed_page = str(page_num)

    return {
        "section": section,
        "section_title": section_title,
        "sub_section": sub_section,
        "printed_page": printed_page,
        "year": 2025,
        "language": "vi",
        "document_type": "economic_report",
    }


def normalize_text(text: str) -> str:
    """Normalize Unicode (NFC) and whitespace while preserving logical paragraph structure."""
    if not text:
        return ""
    text = unicodedata.normalize("NFC", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\u00a0", " ")
    raw_lines = [re.sub(r"[ \t]+", " ", ln).strip() for ln in text.split("\n")]
    raw_lines = [ln for ln in raw_lines if ln]
    if not raw_lines:
        return ""

    paragraphs: list[str] = []
    cur = raw_lines[0]
    struct_re = re.compile(
        r"^(Hình\s+\d|Bảng\s+\d|Nguồn:|Ghi chú:|Lưu ý:|Chương\s+\d|CHƯƠNG|[12]\.[1-7]\s+[A-ZĐÀ-Ỹ]|[•l]\s|TÓM LƯỢC TỔNG QUAN)"
    )
    header_re = re.compile(
        r"(HÀNH TRÌNH BỨT PHÁ:\s*CHUYỂN ĐỔI SANG XE ĐIỆN|BÁO CÁO ĐIỂM LẠI THÁNG\s*0?3/2025)"
    )
    sent_end_re = re.compile(r"(\.|!|\?|:|;)(\s*\d{1,2})?$")

    for ln in raw_lines[1:]:
        if (
            header_re.search(cur)
            or header_re.search(ln)
            or struct_re.match(ln)
            or cur.startswith(("Nguồn:", "Ghi chú:", "Lưu ý:"))
            or sent_end_re.search(cur)
        ):
            paragraphs.append(cur)
            cur = ln
        else:
            if cur.endswith("-"):
                cur = cur + ln
            else:
                cur = cur + " " + ln
    if cur:
        paragraphs.append(cur)

    return "\n\n".join(paragraphs)


def load_pdf(
    path: Path,
    document_id: str,
    source: str,
    only_valid_content: bool = True,
) -> list[DocumentPage]:
    """Extract each physical page, normalize text, log empty/excluded pages, and retain metadata."""
    reader = pypdf.PdfReader(str(path))
    pages: list[DocumentPage] = []
    empty_pages: list[dict[str, Any]] = []
    excluded_pages: list[dict[str, Any]] = []
    valid_pages_summary: list[dict[str, Any]] = []

    for idx, pdf_page in enumerate(reader.pages):
        page_num = idx + 1
        try:
            raw_text = pdf_page.extract_text() or ""
        except Exception as exc:
            empty_pages.append(
                {
                    "document_id": document_id,
                    "source": source,
                    "page": page_num,
                    "status": "extraction_error",
                    "error": str(exc),
                    "raw_chars": 0,
                }
            )
            continue

        norm_text = normalize_text(raw_text)
        if not norm_text.strip():
            empty_pages.append(
                {
                    "document_id": document_id,
                    "source": source,
                    "page": page_num,
                    "status": "empty_page",
                    "reason": EXCLUDED_PAGE_REASONS.get(page_num, "no_extractable_text"),
                    "raw_chars": len(raw_text.strip()),
                }
            )
            continue

        if page_num in EXCLUDED_PAGE_REASONS:
            excluded_pages.append(
                {
                    "document_id": document_id,
                    "source": source,
                    "page": page_num,
                    "status": "excluded_non_content",
                    "reason": EXCLUDED_PAGE_REASONS[page_num],
                    "raw_chars": len(raw_text.strip()),
                    "normalized_chars": len(norm_text),
                }
            )
            if only_valid_content:
                continue

        meta = _infer_page_metadata(page_num)
        meta["raw_chars"] = len(raw_text.strip())
        meta["normalized_chars"] = len(norm_text)

        doc_page = DocumentPage(
            document_id=document_id,
            source=source,
            page=page_num,
            text=norm_text,
            metadata=meta,
        )
        pages.append(doc_page)
        valid_pages_summary.append(
            {
                "page": page_num,
                "printed_page": meta["printed_page"],
                "section": meta["section"],
                "sub_section": meta["sub_section"],
                "raw_chars": meta["raw_chars"],
                "normalized_chars": meta["normalized_chars"],
            }
        )

    LAST_INGESTION_REPORT.clear()
    LAST_INGESTION_REPORT.update(
        {
            "document_id": document_id,
            "source": source,
            "total_physical_pages": len(reader.pages),
            "valid_content_pages_count": len(pages),
            "empty_or_error_pages_count": len(empty_pages),
            "excluded_non_content_pages_count": len(excluded_pages),
            "total_normalized_chars": sum(len(p.text) for p in pages),
            "empty_or_error_pages": empty_pages,
            "excluded_non_content_pages": excluded_pages,
            "valid_pages": valid_pages_summary,
        }
    )
    return pages
