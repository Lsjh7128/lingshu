# -*- coding: utf-8 -*-
"""Layout fixes: wide tables -> table*, long hashes -> seqsplit, verbatim -> lstlisting."""
import re
import os

ROOT = os.path.dirname(os.path.abspath(__file__))


def read(p):
    return open(p, encoding="utf-8").read()


def write(p, s):
    open(p, "w", encoding="utf-8", newline="\n").write(s)


def starify(path, count=None):
    """Convert the first `count` single-column table envs to table*."""
    s = read(path)
    out = []
    i = 0
    n = 0
    while True:
        m = re.search(r"\\begin\{table\}(\[[^\]]*\])?", s[i:])
        if not m:
            out.append(s[i:])
            break
        if count is not None and n >= count:
            out.append(s[i:])
            break
        start = i + m.start()
        end = s.find("\\end{table}", start)
        assert end > 0, path
        end += len("\\end{table}")
        block = s[start:end].replace("\\begin{table}", "\\begin{table*}", 1)
        block = block.replace("\\end{table}", "\\end{table*}", 1)
        out.append(s[i:start])
        out.append(block)
        i = end
        n += 1
    write(path, "".join(out))
    print("starified", os.path.basename(path), n)


# A) wide single-column tables -> table*
starify(os.path.join(ROOT, "sections", "10-appendix.tex"), 3)
starify(os.path.join(ROOT, "sections", "06c-host.tex"), 1)
starify(os.path.join(ROOT, "sections", "10d-appendix-hostpilot.tex"), 1)
# zh: tables are single-column page width already, but keep structure identical
starify(os.path.join(ROOT, "sections-zh", "10-appendix.tex"), 3)
starify(os.path.join(ROOT, "sections-zh", "06c-host.tex"), 1)
starify(os.path.join(ROOT, "sections-zh", "10d-appendix-hostpilot.tex"), 1)

# B) ablation table: smaller font + tighter columns
p = os.path.join(ROOT, "sections", "06-experiments.tex")
s = read(p)
s = s.replace("\\centering\\footnotesize\n\\caption{Three-arm ablation.",
              "\\centering\\scriptsize\\setlength{\\tabcolsep}{2pt}\n\\caption{Three-arm ablation.", 1)
write(p, s)
print("shrunk ablation table")

# C) long hex hashes -> \seqsplit
HASH = re.compile(r"\\texttt\{([0-9a-f]{32,})\}")
for f in ["sections/06e-actuator.tex", "sections/10c-appendix-tool.tex",
          "sections-zh/06e-actuator.tex", "sections-zh/10c-appendix-tool.tex"]:
    p2 = os.path.join(ROOT, f)
    if not os.path.exists(p2):
        continue
    s = read(p2)
    s2, k = HASH.subn(lambda m: "\\texttt{\\seqsplit{%s}}" % m.group(1), s)
    write(p2, s2)
    print("seqsplit", os.path.basename(f), k)

# D) verbatim -> lstlisting (breaklines) in appendix
p = os.path.join(ROOT, "sections", "10-appendix.tex")
s = read(p)
s, k = re.subn(r"\\begin\{verbatim\}(.*?)\\end\{verbatim\}",
               lambda m: "\\begin{lstlisting}" + m.group(1) + "\\end{lstlisting}",
               s, flags=re.S)
write(p, s)
print("verbatim->lstlisting", k)
