#!/usr/bin/env python3
"""Name UELib's unresolved bytecode tokens by aligning our tree with the 2006 oracle source.

For every class the two trees agree on, each `__NFUN_<n>__(A, B)` in our reconstruction is matched
against `A <op> B` in the studio source, and the token is credited with that operator. Tokens with a
clear, repeated winner are confident; the rest are left unknown rather than guessed (a wrong operator
name is worse than `__NFUN_n__`, because it reads as fact).

Output: inventory/token_map.csv (token, operator, votes, share, runner_up) and docs/TOKEN_MAP.md.
The oracle stays private: only token numbers, operator names and vote counts are published.

Usage: token-calibrate.py --oracle DIR --src DIR [-o inventory]
"""
import argparse
import csv
import os
import re
import sys
from collections import Counter, defaultdict

OPERATORS = ["!=", "==", ">=", "<=", "&&", "||", ">", "<", "+=", "-=", "*=", "/=",
             "%", "*", "/", "+", "-", "&", "|", "^", ">>", "<<", "~=", "@", "$", "**"]
TOKEN_CALL = re.compile(r"__NFUN_(\d+)__\(")
LEAF = re.compile(r"^[A-Za-z_][\w.]*(\[[^\]]*\])?$|^[-\d.]+$|^float\([^)]*\)$|^int\([^)]*\)$")
FUNC_DECL = re.compile(r"^\s*(?:function|event)\s+[\w\[\]<>.]+\s+(\w+)\s*\(", re.MULTILINE)


def split_args(text, start):
    """Split the call arguments beginning at text[start] (just after '('). Returns list of args."""
    depth, args, cur = 1, [], []
    i = start
    while i < len(text):
        ch = text[i]
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                args.append("".join(cur).strip())
                return args
        elif ch == "," and depth == 1:
            args.append("".join(cur).strip())
            cur = []
            i += 1
            continue
        cur.append(ch)
        i += 1
    return args


def function_bodies(text):
    """name -> body text, keyed by function name (first definition wins)."""
    out = {}
    matches = list(FUNC_DECL.finditer(text))
    for idx, m in enumerate(matches):
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
        out.setdefault(m.group(1), text[m.start():end])
    return out


def token_calls(body):
    """Yield (token, arg1, arg2) for binary-looking token calls in a body."""
    for m in TOKEN_CALL.finditer(body):
        args = split_args(body, m.end())
        if len(args) != 2:
            continue
        yield int(m.group(1)), args[0], args[1]


def oracle_operator(body, a, b):
    """If the source writes `a <op> b` (either order), return the operator."""
    if not (LEAF.match(a) and LEAF.match(b)):
        return None
    ea, eb = re.escape(a), re.escape(b)
    for op in OPERATORS:
        eo = re.escape(op)
        if re.search(rf"(?<![\w.]){ea}\s*{eo}\s*{eb}(?![\w(])", body) or \
           re.search(rf"(?<![\w.]){eb}\s*{eo}\s*{ea}(?![\w(])", body):
            return op
    return None


def load_tree(root):
    found = {}
    for dirpath, _d, filenames in os.walk(root):
        for name in filenames:
            if name.lower().endswith(".uc"):
                with open(os.path.join(dirpath, name), encoding="utf-8", errors="replace") as fh:
                    found.setdefault(name[:-3].lower(), fh.read())
    return found


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--oracle", default=os.environ.get("ORACLE_DIR"))
    ap.add_argument("--src", default=os.path.join(os.environ.get("WORK", os.path.expanduser("~/sg1/work")),
                                                  "UnrealScript"))
    ap.add_argument("-o", "--out", default="inventory")
    ap.add_argument("--min-votes", type=int, default=3)
    ap.add_argument("--min-share", type=float, default=0.7)
    args = ap.parse_args()
    if not args.oracle or not os.path.isdir(args.oracle):
        sys.exit("set --oracle / ORACLE_DIR to the private 2006 Classes tree")
    oracle = load_tree(args.oracle)

    votes = defaultdict(Counter)
    examples = {}
    used_classes = 0
    for path, _d, filenames in os.walk(args.src):
        for name in filenames:
            if not name.lower().endswith(".uc"):
                continue
            key = name[:-3].lower()
            if key not in oracle:
                continue
            with open(os.path.join(path, name), encoding="utf-8", errors="replace") as fh:
                ours = fh.read()
            o_bodies = function_bodies(oracle[key])
            if not o_bodies:
                continue
            used_classes += 1
            for fname, body in function_bodies(ours).items():
                if fname not in o_bodies:
                    continue
                for token, a, b in token_calls(body):
                    op = oracle_operator(o_bodies[fname], a, b)
                    if op:
                        votes[token][op] += 1
                        examples.setdefault((token, op), f"{fname}: {a} {op} {b}")

    rows = []
    for token, counter in votes.items():
        op, count = counter.most_common(1)[0]
        total = sum(counter.values())
        share = count / total
        runner = counter.most_common(2)[1][0] if len(counter) > 1 else ""
        rows.append({
            "token": token, "operator": op, "votes": count, "total": total,
            "share": round(share, 3), "runner_up": runner,
            "confident": count >= args.min_votes and share >= args.min_share,
            "example": examples[(token, op)],
        })
    rows.sort(key=lambda r: (-r["votes"], r["token"]))

    os.makedirs(args.out, exist_ok=True)
    with open(os.path.join(args.out, "token_map.csv"), "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["token", "operator", "votes", "total", "share",
                                           "runner_up", "confident", "example"])
        w.writeheader()
        for r in rows:
            w.writerow({**r, "confident": str(r["confident"]).lower()})

    confident = [r for r in rows if r["confident"]]
    print(f"{used_classes} aligned classes -> {len(rows)} tokens observed, {len(confident)} confident")
    for r in confident[:15]:
        print(f"  __NFUN_{r['token']}__ = {r['operator']:3s}  {r['votes']}/{r['total']}  eg {r['example']}")

    lines = ["# Token map: naming `__NFUN_n__` from the oracle", "",
             "Generated by `tools/token-calibrate.py`. For each class both trees agree on, every",
             "binary `__NFUN_<n>__(A, B)` in our reconstruction is aligned against the studio source's",
             "`A <op> B`. A token is only called confident with repeated agreement.", "",
             f"{used_classes} aligned classes; {len(rows)} distinct tokens observed, "
             f"{len(confident)} confident.", "",
             "## Confident mappings", "", "| token | operator | votes | share | runner-up |",
             "|---|---|---|---|---|"]
    for r in confident:
        lines.append(f"| `__NFUN_{r['token']}__` | `{r['operator']}` | {r['votes']}/{r['total']} "
                     f"| {r['share']} | {r['runner_up'] or '-'} |")
    lines += ["", "## Not confident (left unnamed on purpose)", "",
              "| token | best guess | votes | share |", "|---|---|---|---|"]
    for r in [r for r in rows if not r["confident"]][:20]:
        lines.append(f"| `__NFUN_{r['token']}__` | `{r['operator']}` | {r['votes']}/{r['total']} "
                     f"| {r['share']} |")
    lines += ["", "## Applying", "",
              "The map is applied by extending UELib's UE2 token table (a local patch under",
              "`tools/uelib-patches/`), not by rewriting the output: a rewriter would make the tree",
              "diverge from the bytecode and break the L2 comparator. Re-run `make decompile`",
              "afterwards and check `docs/DECOMPILER_GAPS.md` for the drop.", ""]
    with open(os.path.join("docs", "TOKEN_MAP.md"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(lines))
    print("wrote docs/TOKEN_MAP.md")


if __name__ == "__main__":
    main()
