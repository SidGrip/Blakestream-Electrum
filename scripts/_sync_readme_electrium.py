#!/usr/bin/env python3
"""Insert/replace the "Electrium Wallet" subsection in a coin repo README.

Idempotent: the section is wrapped in <!-- BEGIN/END electrium-build --> markers.
On re-run it replaces the marked block; on first run it inserts at the end of the
"## Build Options" section (before the next "## " heading), else appends a new
"## Electrium Wallet" section.

usage: _sync_readme_electrium.py <template> <readme> <COIN_CODE> <COIN_NAME>
"""
import re
import sys
from pathlib import Path

BEGIN = "<!-- BEGIN electrium-build -->"
END = "<!-- END electrium-build -->"


def main():
    tmpl, readme, code, name = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
    snippet = Path(tmpl).read_text(encoding="utf-8")
    snippet = snippet.replace("@COIN_CODE@", code).replace("@COIN_NAME@", name)
    block = f"{BEGIN}\n{snippet.rstrip()}\n{END}\n"

    p = Path(readme)
    txt = p.read_text(encoding="utf-8") if p.exists() else f"# {name}\n"

    if BEGIN in txt and END in txt:
        txt = re.sub(re.escape(BEGIN) + r".*?" + re.escape(END) + r"\n?", block, txt, flags=re.S)
    else:
        lines = txt.splitlines(keepends=True)
        bo = next((i for i, l in enumerate(lines) if re.match(r"^##\s+Build Options", l)), None)
        if bo is None:
            txt = txt.rstrip() + "\n\n## Electrium Wallet\n\n" + block
        else:
            nxt = next((i for i in range(bo + 1, len(lines)) if re.match(r"^##\s", lines[i])), len(lines))
            lines.insert(nxt, "\n" + block + "\n")
            txt = "".join(lines)

    p.write_text(txt, encoding="utf-8")
    print("  README.md: Electrium section synced")


if __name__ == "__main__":
    main()
