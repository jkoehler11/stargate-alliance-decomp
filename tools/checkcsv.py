"""Shape checks for artifacts whose generator needs the dump.

Four tools can only *compute* their artifacts with the game present, but `make ci` is dump-free, so their `--check` used
to be unusable there — it demanded a path and exited 2. The contract (`docs/BUILDING.md` §5) says every tool answering
`--check` must answer it without the dump; what it can honestly answer is "the committed artifact is still the shape the
tool writes". That is not nothing: it catches a truncated run, an empty file, a changed column set or a stub committed
in place of output — the failures that a regeneration cannot report because nothing regenerates in CI.

Each caller states its own columns and required keys, so this module has no idea what the artifacts mean.
"""
import csv
import os


def shape(path, columns, rows_min=1, key=None):
    """[(problem)] for one artifact: present, parseable, the expected columns, enough rows, no empty key."""
    if not os.path.isfile(path):
        return ["%s is missing" % os.path.basename(path)]
    try:
        with open(path, newline="", encoding="utf-8") as fh:
            reader = csv.reader(fh)
            try:
                header = next(reader)
            except StopIteration:
                return ["%s is empty" % os.path.basename(path)]
            body = list(reader)
    except (OSError, csv.Error, UnicodeDecodeError) as exc:
        return ["%s cannot be read: %s" % (os.path.basename(path), exc)]
    problems = []
    if header != columns:
        problems.append("%s has columns %s, expected %s" % (os.path.basename(path), header[:4], columns[:4]))
    if len(body) < rows_min:
        problems.append("%s has %d rows, expected at least %d" % (os.path.basename(path), len(body), rows_min))
    if key is not None and key in header:
        index = header.index(key)
        empty = sum(1 for r in body if len(r) <= index or not r[index].strip())
        if empty:
            problems.append("%s has %d rows with an empty %s" % (os.path.basename(path), empty, key))
    return problems


def report(name, problems, summary):
    """Print the one line `make ci` expects, and return the exit code."""
    if problems:
        print("FAIL %s: %s" % (name, "; ".join(problems[:5])))
        return 1
    print("ok   %s: %s" % (name, summary))
    return 0
