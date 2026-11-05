"""Build the curated catalog from the two official snapshots and pinned statement releases."""
import csv
import gzip
import hashlib
import json
import re
import unicodedata

VP_READY_VIEW = """
CREATE VIEW IF NOT EXISTS vp_ready_contests AS
    SELECT c.contest_id, c.name, c.start_time_seconds, c.duration_seconds, count(*) AS problem_count
    FROM contests c JOIN contest_problems s USING(contest_id)
    JOIN problem_text m USING(contest_id,idx)
    WHERE c.phase='FINISHED' AND c.type IN ('CF','ICPC') AND c.duration_seconds BETWEEN 60 AND 86400
      AND c.contest_id BETWEEN 1 AND 99999
    GROUP BY c.contest_id
    HAVING min(m.body_id IS NOT NULL AND m.sample_id IS NOT NULL AND s.type='PROGRAMMING'
       AND length(s.idx) BETWEEN 1 AND 6 AND s.idx NOT GLOB '*[^A-Z0-9]*')=1;
"""

SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE contests (
    contest_id INTEGER PRIMARY KEY, name TEXT NOT NULL, phase TEXT NOT NULL,
    type TEXT NOT NULL, start_time_seconds INTEGER, duration_seconds INTEGER NOT NULL
);
CREATE TABLE texts (
    text_id INTEGER PRIMARY KEY, provider TEXT NOT NULL, source_key TEXT NOT NULL,
    source_title TEXT NOT NULL, body TEXT NOT NULL, input_format TEXT, output_format TEXT,
    interaction_format TEXT, note TEXT, time_limit TEXT, memory_limit TEXT,
    examples_json TEXT NOT NULL, example_count INTEGER NOT NULL,
    UNIQUE(provider, source_key)
);
CREATE TABLE text_keys (
    contest_id INTEGER NOT NULL, idx TEXT NOT NULL, text_id INTEGER NOT NULL REFERENCES texts,
    via TEXT NOT NULL, PRIMARY KEY(contest_id, idx, text_id)
);
CREATE TABLE problems (
    contest_id INTEGER NOT NULL REFERENCES contests, idx TEXT NOT NULL, name TEXT NOT NULL,
    type TEXT NOT NULL, points REAL, rating INTEGER, tags_json TEXT NOT NULL, solved_count INTEGER NOT NULL,
    PRIMARY KEY(contest_id, idx)
);
CREATE TABLE standings_files (
    contest_id INTEGER PRIMARY KEY REFERENCES contests, status TEXT NOT NULL,
    file TEXT, rows INTEGER, problem_count INTEGER, sha256 TEXT, checked_at TEXT NOT NULL,
    url TEXT NOT NULL, error TEXT, frozen INTEGER
);
CREATE TABLE contest_problems (
    contest_id INTEGER NOT NULL REFERENCES standings_files, ordinal INTEGER NOT NULL,
    idx TEXT NOT NULL, name TEXT NOT NULL, type TEXT NOT NULL, points REAL, rating INTEGER,
    tags_json TEXT NOT NULL, PRIMARY KEY(contest_id, idx), UNIQUE(contest_id, ordinal)
);
CREATE TABLE problem_text (
    contest_id INTEGER NOT NULL REFERENCES contests, idx TEXT NOT NULL, name TEXT NOT NULL,
    body_id INTEGER REFERENCES texts, sample_id INTEGER REFERENCES texts, match_rule TEXT NOT NULL,
    url TEXT NOT NULL, PRIMARY KEY(contest_id, idx)
);
CREATE VIEW problem_details AS
    SELECT p.contest_id, p.idx, p.name, p.rating, p.tags_json, p.solved_count,
        m.url, m.match_rule, t.provider AS body_source, t.body, t.input_format, t.output_format,
        t.interaction_format, t.note, t.time_limit, t.memory_limit,
        e.provider AS example_source, e.examples_json, coalesce(e.example_count, 0) AS example_count
    FROM problems p JOIN problem_text m USING(contest_id, idx)
    LEFT JOIN texts t ON t.text_id=m.body_id LEFT JOIN texts e ON e.text_id=m.sample_id;
CREATE VIEW contest_problem_details AS
    SELECT s.contest_id, s.ordinal, s.idx, s.name, s.type, s.points, s.rating, s.tags_json,
        p.solved_count, m.url, m.match_rule, t.provider AS body_source, t.source_key,
        t.body, t.input_format, t.output_format, t.interaction_format, t.note, t.time_limit, t.memory_limit,
        e.provider AS example_source, e.examples_json, coalesce(e.example_count, 0) AS example_count
    FROM contest_problems s JOIN problem_text m USING(contest_id,idx)
    LEFT JOIN problems p USING(contest_id,idx)
    LEFT JOIN texts t ON t.text_id=m.body_id LEFT JOIN texts e ON e.text_id=m.sample_id;
""" + VP_READY_VIEW


def compact(obj):
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def sha_stream(stream):
    """Hash binary streams, including decoded gzip data, on Python 3.10+."""
    digest = hashlib.sha256()
    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
        digest.update(chunk)
    return digest.hexdigest()


def sha_file(path):
    with path.open("rb") as stream:
        return sha_stream(stream)


def read_verified(path, expected, *, compressed=False):
    data = gzip.decompress(path.read_bytes()) if compressed else path.read_bytes()
    if hashlib.sha256(data).hexdigest() != expected:
        raise ValueError(f"SHA-256 mismatch: {path}")
    return data


def key_parts(key):
    cid, idx = key.split("/")
    return int(cid), idx


def normalized(title):
    return "".join(c for c in unicodedata.normalize("NFKC", title).casefold() if c.isalnum())


def body_available(body):
    value = (body or "").strip()
    if not normalized(value.replace("[image]", "")):
        return False
    return not re.fullmatch(r"(?:view the statement as a pdf\.?|see the problem statement here:\s*https?://\S+)",
                            value, re.IGNORECASE)


def prepare_examples(examples):
    if examples is None:
        return []
    if not isinstance(examples, list):
        raise ValueError("Unexpected examples format")
    for example in examples:
        if not all(isinstance(example.get(k), str) for k in ("input", "output")):
            raise ValueError("Incomplete example fields")
    return [{"input": e["input"], "output": e["output"]} for e in examples
            if e["input"].strip() or e["output"].strip()]


def add_text(db, provider, key, aliases, title, sections, limits, examples):
    # Keep original line breaks and empty strings: some problems legitimately have empty I/O.
    examples = prepare_examples(examples)
    cur = db.execute("INSERT INTO texts VALUES(NULL,?,?,?,?,?,?,?,?,?,?,?,?)",
                     (provider, key, title, sections[0] or "", *sections[1:], *limits,
                      compact(examples), len(examples)))
    ids = [(key, "direct")] + [(x, "published_alias") for x in (aliases or []) if x != key]
    db.executemany("INSERT INTO text_keys VALUES(?,?,?,?)",
                   [(*key_parts(k), cur.lastrowid, via) for k, via in dict(ids).items()])


def add_openr1(db, root):
    manifest = read_json(root / "statements-openr1-manifest.json")
    for shard in manifest["shards"]:
        path = root / shard["file"]
        if sha_file(path) != shard["sha256"]:
            raise ValueError(f"Damaged statement shard: {path}")
        with gzip.open(path, "rt", encoding="utf-8") as stream:
            count = 0
            for line in stream:
                row = json.loads(line)
                if key_parts(row["id"]) != (int(row["contest_id"]), row["index"]):
                    raise ValueError("Inconsistent problem key in Open-R1")
                sections = [row.get(k) for k in ("description", "input_format", "output_format",
                                                 "interaction_format", "note")]
                limits = [str(row[k]) + unit if row.get(k) is not None else None
                          for k, unit in (("time_limit", " seconds"), ("memory_limit", " megabytes"))]
                add_text(db, "openr1", row["id"], row.get("aliases"), row["title"], sections,
                         limits, row.get("examples"))
                count += 1
        if count != shard["rows"]:
            raise ValueError(f"Statement count mismatch: {path}")
    db.execute("INSERT INTO meta VALUES('openr1_revision',?)", (manifest["source"]["revision"],))


def add_ronin(db, root):
    directory = root / "statements" / "ronin"
    manifest = read_json(directory / "manifest.json")
    info = manifest["files"]["problems.csv"]
    with gzip.open(directory / info["file"], "rb") as raw:
        if sha_stream(raw) != info["sha256Decoded"]:
            raise ValueError("Damaged supplemental CSV")
    with gzip.open(directory / info["file"], "rt", encoding="utf-8-sig", newline="") as source:
        rendered_key_differences = 0
        for row in csv.DictReader(source):
            cid, idx = row["contest_id"], row["problem_index"]
            # The CSV's rendered ID column has spreadsheet formatting, e.g. 177E1 -> 1.77E+03.
            # The separate contest and index columns retain the original values.
            rendered_key_differences += row["id"] != cid + idx
            sections = [row["statement"], row["input_specification"], row["output_specification"],
                        None, row["note"]]
            add_text(db, "ronin", f"{cid}/{idx}", [], row["title"], sections,
                     (row["time_limit"], row["memory_limit"]), json.loads(row["examples"]))
    db.execute("INSERT INTO meta VALUES('ronin_revision',?)", (manifest["revision"],))
    db.execute("INSERT INTO meta VALUES('ronin_rendered_key_differences',?)", (str(rendered_key_differences),))


def attach_text(db, cid, idx, name):
    candidates = db.execute("""
        SELECT t.text_id, t.provider, t.source_title, k.via, t.example_count, t.body
        FROM text_keys k JOIN texts t USING(text_id) WHERE k.contest_id=? AND k.idx=?
        ORDER BY k.via='direct' DESC, t.provider='openr1' DESC, t.provider='ronin' DESC, t.text_id
    """, (cid, idx)).fetchall()
    official = normalized(name)
    matched = [r for r in candidates if official and normalized(r["source_title"]) == official]
    rule = matched[0]["via"] if matched else "source_missing"
    # Official titles can differ from the published English header (e.g. 1B). Only accept
    # a different name if both releases independently carry that name under this exact ID.
    if not matched and candidates:
        direct = [r for r in candidates if r["via"] == "direct"]
        if len({r["provider"] for r in direct}) >= 2 and len({normalized(r["source_title"]) for r in direct}) == 1:
            matched, rule = direct, "two_direct_sources_agree_on_title_variant"
        else:
            rule = "title_requires_review"
    body = next((r for r in matched if body_available(r["body"])), None)
    samples = next((r for r in matched if r["example_count"]), None)
    if matched and body is None:
        rule += "_only_reference_or_empty"
    db.execute("INSERT OR REPLACE INTO problem_text VALUES(?,?,?,?,?,?,?)",
               (cid, idx, name, body["text_id"] if body else None, samples["text_id"] if samples else None,
                rule, f"https://codeforces.com/contest/{cid}/problem/{idx}"))


def initialize(db, root, snapshot):
    db.executescript(SCHEMA)
    sources = read_json(snapshot / "sources.json")
    raw = {}
    for name, source in sources.items():
        raw[name] = json.loads(read_verified(snapshot / source["file"], source["sha256Decoded"], compressed=True))
        if raw[name]["status"] != "OK":
            raise ValueError(f"API response status is not OK: {name}")
        db.execute("INSERT INTO meta VALUES(?,?)", (name + "_sha256", source["sha256Decoded"]))
    contests = raw["contests"]["result"]
    db.executemany("INSERT INTO contests VALUES(?,?,?,?,?,?)",
                   [(c["id"], c["name"], c["phase"], c["type"], c.get("startTimeSeconds"), c["durationSeconds"])
                    for c in contests])
    add_openr1(db, root)
    add_ronin(db, root)
    result = raw["problemset"]["result"]
    stats = {(p["contestId"], p["index"]): p["solvedCount"] for p in result["problemStatistics"]}
    if len(stats) != len(result["problemStatistics"]):
        raise ValueError("Duplicate statistics key")
    for p in result["problems"]:
        cid, idx, name = p["contestId"], p["index"], p["name"]
        db.execute("INSERT INTO problems VALUES(?,?,?,?,?,?,?,?)",
                   (cid, idx, name, p["type"], p.get("points"), p.get("rating"), compact(p["tags"]),
                    stats.pop((cid, idx))))
        attach_text(db, cid, idx, name)
    if stats:
        raise ValueError("Statistics having no problem")
    db.execute("INSERT INTO meta VALUES('schema_version','1')")
    db.commit()


def upgrade_matches(db):
    with db:
        db.execute("BEGIN IMMEDIATE")
        if db.execute("SELECT 1 FROM meta WHERE key='quality_revision' AND value='2'").fetchone():
            return
        affected = list(db.execute("""SELECT text_id,examples_json FROM texts t WHERE EXISTS
            (SELECT 1 FROM json_each(t.examples_json) e WHERE trim(json_extract(e.value,'$.input'))=''
            AND trim(json_extract(e.value,'$.output'))='')"""))
        for row in affected:
            examples = prepare_examples(json.loads(row["examples_json"]))
            db.execute("UPDATE texts SET examples_json=?,example_count=? WHERE text_id=?",
                       (compact(examples), len(examples), row["text_id"]))
        for row in list(db.execute("SELECT contest_id,idx,name FROM problem_text")):
            attach_text(db, *row)
        db.execute("INSERT OR REPLACE INTO meta VALUES('quality_revision','2')")


def add_release(db, root, provider):
    directory = root / "statements" / provider
    path = directory / "manifest.json"
    if not path.exists():
        return
    manifest = read_json(path)
    with db:
        db.execute("BEGIN IMMEDIATE")
        hash_key = provider + "_sha256"
        old = db.execute("SELECT value FROM meta WHERE key=?", (hash_key,)).fetchone()
        if old and old[0] == manifest["sha256"]:
            return
        source = directory / manifest["file"]
        if sha_file(source) != manifest["sha256"]:
            raise ValueError(f"Damaged {provider} supplemental records")
        records = {}
        with gzip.open(source, "rt", encoding="utf-8") as stream:
            for line in stream:
                item = json.loads(line)
                key = item["key"]
                if key in records:
                    raise ValueError(f"Conflicting {provider} source keys: {key}")
                records[key] = item
        for key, row in records.items():
            cid, idx = key_parts(key)
            existing = db.execute("SELECT text_id FROM texts WHERE provider=? AND source_key=?",
                                  (provider,key)).fetchone()
            if not existing:
                add_text(db, provider, key, [], row["title"], [row["description"],None,None,None,row.get("note")],
                         [None,None], row["examples"])
            else:
                examples = prepare_examples(row["examples"])
                db.execute("""UPDATE texts SET source_title=?, body=?, note=?, examples_json=?, example_count=?
                              WHERE text_id=?""",
                           (row["title"],row["description"],row.get("note"),compact(examples),len(examples),existing[0]))
            if "booklet" in row:
                b = row["booklet"]
                db.execute("INSERT OR REPLACE INTO booklet_pages VALUES(?,?,?,?,?,?,?,?)",
                           (cid,idx,b["file"],b["firstPage"],b["lastPage"],b["url"],b["sha256"],row["sampleOrigin"]))
            ref = db.execute("SELECT name FROM problem_text WHERE contest_id=? AND idx=?", (cid, idx)).fetchone()
            if ref:
                attach_text(db, cid, idx, ref["name"])
        db.execute("INSERT OR REPLACE INTO meta VALUES(?,?)", (provider+"_revision",manifest["revision"]))
        db.execute("INSERT OR REPLACE INTO meta VALUES(?,?)", (hash_key,manifest["sha256"]))


def add_backfill(db, root):
    db.execute(VP_READY_VIEW)
    db.execute("""CREATE TABLE IF NOT EXISTS booklet_pages (
        contest_id INTEGER NOT NULL REFERENCES contests, idx TEXT NOT NULL, file TEXT NOT NULL,
        first_page INTEGER NOT NULL, last_page INTEGER NOT NULL, url TEXT NOT NULL,
        sha256 TEXT NOT NULL, sample_origin TEXT NOT NULL, PRIMARY KEY(contest_id,idx)
    )""")
    upgrade_matches(db)
    for provider in ("deepmind","icpc"):
        add_release(db, root, provider)
