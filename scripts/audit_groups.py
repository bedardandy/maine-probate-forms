"""Quick audit of group annotations in a validation JSON."""
import argparse
import json
import pathlib
import sys
from collections import Counter, defaultdict


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("json_path", type=pathlib.Path)
    args = ap.parse_args()
    v = json.loads(args.json_path.read_text())
    fields = v.get("fields", [])
    print(f"validation: {args.json_path}")
    print(f"fields: {len(fields)}")

    roles = Counter(f.get("group_role", "independent") for f in fields)
    print(f"roles: {dict(roles)}")

    groups: dict[str, list] = defaultdict(list)
    parents: Counter = Counter()
    for f in fields:
        gid = f.get("group_id")
        if gid:
            groups[gid].append(f)
        pgid = f.get("parent_group_id") or ""
        if pgid:
            parents[pgid] += 1

    print(f"\ndistinct group_ids: {len(groups)}")
    for gid, items in sorted(groups.items()):
        role = items[0].get("group_role")
        pages = sorted({i["page"] for i in items})
        options = [i.get("group_option", "") for i in items]
        print(f"  {gid!r:<40} role={role:<14} pages={pages} options={options}")

    if parents:
        print(f"\nparent_group_id usage:")
        for p, n in parents.most_common():
            print(f"  {p!r}: {n}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
