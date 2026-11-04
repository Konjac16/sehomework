"""Text-only source comparison; no submitted code is executed or rendered as HTML."""
from difflib import SequenceMatcher


def segments(a, b):
    if max(len(a), len(b)) > 1000:
        return [[a, True]], [[b, True]]
    left, right = [], []
    for tag, i, j, k, l in SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if i != j:
            left.append([a[i:j], tag != "equal"])
        if k != l:
            right.append([b[k:l], tag != "equal"])
    return left, right


def compare_sources(before, after, full=False):
    if max(len(before), len(after)) > 200000:
        raise ValueError("单份代码超过 200000 字符，请下载后使用本地编辑器对比")
    a, b = before.splitlines(keepends=True), after.splitlines(keepends=True)
    if max(len(a), len(b)) > 5000:
        raise ValueError("单份代码超过 5000 行，请下载后使用本地编辑器对比")
    matcher = SequenceMatcher(None, a, b, autojunk=False)
    ops = matcher.get_opcodes()
    groups = [ops] if full else list(matcher.get_grouped_opcodes(3))
    hunks = []
    for group in groups:
        rows = []
        for tag, i, j, k, l in group:
            for offset in range(max(j-i, l-k)):
                ai = i+offset if i+offset < j else None
                bi = k+offset if k+offset < l else None
                old = a[ai].rstrip("\r\n") if ai is not None else ""
                new = b[bi].rstrip("\r\n") if bi is not None else ""
                left, right = segments(old, new) if tag == "replace" else ([[old, tag != "equal"]], [[new, tag != "equal"]])
                rows.append({"kind": tag, "oldLine": ai+1 if ai is not None else None,
                             "newLine": bi+1 if bi is not None else None, "old": left, "new": right})
        if rows:
            hunks.append(rows)
    return {"identical": before == after, "hunks": hunks,
            "removed": sum(j-i for tag, i, j, k, l in ops if tag != "equal"),
            "added": sum(l-k for tag, i, j, k, l in ops if tag != "equal"),
            "beforeEndsWithNewline": before.endswith("\n"), "afterEndsWithNewline": after.endswith("\n"),
            "lineEndingsDiffer": before.replace("\r\n", "\n") == after.replace("\r\n", "\n") and before != after}
