from pathlib import Path

path = Path("scripts/issue1840_reviewer_fix.py")
text = path.read_text(encoding="utf-8")
old = '''def add_failure_field(path: str, anchor: str = "      sponsored?: boolean;\\n") -> None:
    text = load(path)
    if "      broadcastAttempted?: boolean;\\n" in text:
        return
    if text.count(anchor) != 1:
        raise RuntimeError(f"{path}: cannot place broadcastAttempted; anchor count={text.count(anchor)}")
    save(path, text.replace(anchor, anchor + "      broadcastAttempted?: boolean;\\n", 1))
'''
new = '''def add_failure_field(path: str, anchor: str = "      sponsored?: boolean;\\n") -> None:
    text = load(path)
    failure_start = text.find("  | {\\n      success: false;")
    if failure_start < 0:
        raise RuntimeError(f"{path}: failure union arm not found")
    failure_end = text.find("\\n    };", failure_start)
    if failure_end < 0:
        raise RuntimeError(f"{path}: failure union arm end not found")
    failure = text[failure_start:failure_end]
    if "broadcastAttempted?: boolean;" in failure:
        return
    pos = failure.rfind(anchor.rstrip("\\n"))
    if pos < 0:
        raise RuntimeError(f"{path}: anchor missing from failure union: {anchor!r}")
    absolute = failure_start + pos + len(anchor.rstrip("\\n"))
    text = text[:absolute] + "\\n      broadcastAttempted?: boolean;" + text[absolute:]
    save(path, text)
'''
count = text.count(old)
if count != 1:
    raise RuntimeError(f"expected one add_failure_field definition, got {count}")
path.write_text(text.replace(old, new, 1), encoding="utf-8", newline="\n")
print("patch selector fixed")
