"""Index organizers' original PDF pages and distinguish released samples from extracted PDF cells.

Requires pdfplumber for the one-time build. Querying and rebuilding SQLite from the result is stdlib only.
"""
import argparse
import gzip
import json
from pathlib import Path
import re
import sqlite3
import unicodedata

import pdfplumber

from build_cf_archive import DEFAULT_ROOT
from cf_archive_catalog import compact, normalized, sha_file
from fetch_cf_icpc import YEARS
from fetch_cf_standings import atomic, js, stamp


def pdf_title_key(title):
    # LaTeX often places accents as separate glyphs in the PDF text layer.
    text = unicodedata.normalize("NFKD", title.replace(r"\textbackslash", "\\"))
    return "".join(ch for ch in text.casefold() if ch.isalnum())


def sample_files(out, cid, idx):
    directory = out / "samples" / str(cid) / idx
    results = []
    for path in sorted(directory.glob("*.in")):
        answer = path.with_suffix(".ans")
        if answer.is_file():
            results.append({"input":path.read_bytes().decode("utf-8-sig"), "output":answer.read_bytes().decode("utf-8-sig")})
    return results


def table_samples(page):
    examples = []
    for table in page.extract_tables():
        if not table or len(table[0]) < 2:
            continue
        first = [" ".join((c or "").casefold().split()) for c in table[0][:2]]
        if first not in (["standard input", "standard output"], ["input", "output"]):
            continue
        for row in table[1:]:
            if len(row) >= 2 and all(isinstance(cell, str) for cell in row[:2]) and any(c.strip() for c in row[:2]):
                examples.append({"input":row[0], "output":row[1]})
    return examples


def sections(path, apac):
    records = []
    current = None
    pattern = r"(?m)^Problem ([A-Z])[ \t]*\n([^\n]+)" if apac else r"(?m)^Problem ([A-Z])\.[ \t]+([^\n]+)"
    with pdfplumber.open(path) as book:
        for n, page in enumerate(book.pages, start=1):
            text = page.extract_text(x_tolerance=1, y_tolerance=3) or ""
            heading = re.search(pattern, text)
            if heading:
                idx, title = heading.groups()
                current = dict(index=idx, title=title.strip(), startPage=n, endPage=n, text=[], samples=[])
                records.append(current)
                text = text[heading.start():]
            if current:
                current["endPage"] = n
                current["text"].append(text)
                if not apac:
                    current["samples"].extend(table_samples(page))
            page.close()
    if len({r["index"] for r in records}) != len(records):
        raise ValueError("Duplicate PDF headings in " + path.name)
    return records


def olympiad_problem(path, file):
    short_year = file["edition"][:-4] + file["edition"][-2:]
    day = {1:"first", 2:"second"}[file["day"]]
    texts, samples = [], []
    with pdfplumber.open(path) as book:
        for n, page in enumerate(book.pages):
            text = page.extract_text(x_tolerance=1, y_tolerance=3) or ""
            if n == 0:
                edition = f"open olympiad in informatics {short_year}. {day} day"
                if edition not in text.casefold():
                    raise ValueError("Unexpected Olympiad date or day: " + path.name)
                title = re.search(r"(?m)^Problem [^.\n]{1,70}\.[ \t]+([^\n]+)", text)
                if not title:
                    raise ValueError("Missing Olympiad problem heading: " + path.name)
            texts.append(text)
            samples.extend(table_samples(page))
            page.close()
    return [{"index": file["index"], "title": title[1], "startPage":1, "endPage":len(texts),
             "text":texts, "samples":samples}]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, default=DEFAULT_ROOT)
    root = parser.parse_args().archive.resolve()
    out = root / "statements/icpc"
    sources = json.loads((out / "files-manifest.json").read_text("utf-8"))
    with sqlite3.connect((root / "catalog.sqlite3").as_uri() + "?mode=ro", uri=True) as db:
        names = {(c,i):n for c,i,n in db.execute("SELECT contest_id,idx,name FROM problems")}
        names.update({(c,i):n for c,i,n in db.execute("SELECT contest_id,idx,name FROM contest_problems")})
    rows, mismatches = [], []
    for file in sources["files"]:
        if sha_file(out / file["file"]) != file["sha256"]:
            raise ValueError("Corrupt ICPC source " + file["file"])
        if not file["file"].endswith(".pdf"):
            continue
        cid = file["contestId"]
        apac = cid in YEARS.values()
        path = out / file["file"]
        groups = olympiad_problem(path, file) if "edition" in file else sections(path, apac)
        for section in groups:
            idx, title = section["index"], section["title"]
            cf_name = names.get((cid,idx))
            # The 2025 day-one official handout (QOJ 13280, page 1) uses the definite article.
            published_variant = (cid, idx, cf_name, title.casefold()) == (2079, "B", "Arithmetic Exercise", "the arithmetic exercise")
            if not cf_name or (pdf_title_key(title) != pdf_title_key(cf_name) and not published_variant):
                mismatches.append({"contestId":cid,"index":idx,"officialCF":cf_name,"pdfHeading":title})
                continue
            samples = sample_files(out, cid, idx) if apac else section["samples"]
            a, z = section["startPage"], section["endPage"]
            book = {"file":"statements/icpc/" + file["file"], "firstPage":a, "lastPage":z, "url":file["url"],
                    "sha256":file["sha256"]}
            rows.append({"key":f"{cid}/{idx}", "title":cf_name, "pdfHeading":title,
                         "titleMatch":"published_title_variant" if published_variant else (
                             "exact" if normalized(title)==normalized(cf_name) else "same_base_letters_and_index"),
                         "description":"\n\n".join(section["text"]),
                         "examples":samples, "booklet":book, "sampleOrigin":"official_sample_files" if apac else "pdf_sample_table",
                         "note":f"官方题册 {book['file']}，第 {a}–{z} 页。图、数学排版和样例格式以该页为准。"})
        print(cid, sum(r["key"].startswith(f"{cid}/") for r in rows), flush=True)
    path = out / "records.jsonl.gz"
    atomic(path, gzip.compress(b"".join((compact(row)+"\n").encode("utf-8") for row in rows), mtime=0))
    info = dict(provider="ICPC APAC, NERC and Open Olympiad booklets", revision=sha_file(out/"files-manifest.json"),
                records=len(rows), file=path.name, sha256=sha_file(path), checkedAt=stamp(),
                examples=sum(len(row["examples"]) for row in rows), unlinkedHeadings=mismatches)
    atomic(out / "manifest.json", js(info))
    print(info)


if __name__ == "__main__":
    main()
