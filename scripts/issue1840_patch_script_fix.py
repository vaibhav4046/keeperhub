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
    match = re.search(r"\\|\\s*\\{\\n(?P<indent>\\s*)success: false;", text)
    if not match:
        raise RuntimeError(f"{path}: failure union arm not found")
    failure_start = match.start()
    # Result unions in these cores use either four or six spaces inside an arm.
    close_indent = match.group("indent")[:-2]
    failure_end = text.find("\\n" + close_indent + "};", failure_start)
    if failure_end < 0:
        raise RuntimeError(f"{path}: failure union arm end not found")
    failure = text[failure_start:failure_end]
    if "broadcastAttempted?: boolean;" in failure:
        return
    needle = anchor.strip()
    pos = failure.rfind(needle)
    if pos < 0:
        raise RuntimeError(f"{path}: anchor missing from failure union: {needle!r}")
    line_start = failure.rfind("\\n", 0, pos) + 1
    indent = failure[line_start:pos]
    absolute = failure_start + pos + len(needle)
    text = text[:absolute] + "\\n" + indent + "broadcastAttempted?: boolean;" + text[absolute:]
    save(path, text)
'''
count = text.count(old)
if count != 1:
    raise RuntimeError(f"expected one add_failure_field definition, got {count}")
path.write_text(text.replace(old, new, 1), encoding="utf-8", newline="\n")
print("patch selector fixed")
