import re, glob, os

for f in sorted(glob.glob("sections/*.tex")):
    s = open(f, encoding="utf-8").read()
    for m in re.finditer(r"\\begin\{(table\*?)\}", s):
        start = m.end()
        seg = s[start:start + 1500]
        spec = re.search(r"\\begin\{tabular\}\{", seg)
        line = s[:m.start()].count("\n") + 1
        spec_txt = "?"
        if spec:
            depth = 0
            i = spec.end()
            j = i
            while j < len(seg):
                if seg[j] == "{":
                    depth += 1
                elif seg[j] == "}":
                    if depth == 0:
                        break
                    depth -= 1
                j += 1
            spec_txt = seg[i:j]
        print("%-28s %-6s spec=%s" % (os.path.basename(f) + ":" + str(line), m.group(1), spec_txt[:90]))
