# -*- coding: utf-8 -*-
import os

ROOT = os.path.dirname(os.path.abspath(__file__))

EN = os.path.join(ROOT, "sections", "10-appendix.tex")
ZH = os.path.join(ROOT, "sections-zh", "10-appendix.tex")

pairs_en = [
    (r"Pipeline integration & nine functions & 65 & \texttt{test\_\allowbreak{}pipeline\_\allowbreak{}integration.\allowbreak{}py:\allowbreak{}46-\allowbreak{}-\allowbreak{}284} \\",
     r"Pipeline integration & \texttt{nine functions} (\texttt{test\_\allowbreak{}pipeline\_\allowbreak{}integration.\allowbreak{}py}) & 65 & \texttt{:46--284} \\"),
    (r"Constitution audit & five functions & 26 & \texttt{test\_\allowbreak{}constitution.\allowbreak{}py} \\",
     r"Constitution audit & \texttt{five functions} (\texttt{test\_\allowbreak{}constitution.\allowbreak{}py}) & 26 & --- \\"),
]
pairs_zh = [
    (r"Pipeline integration & 九个函数 & 65 & \texttt{test\_\allowbreak{}pipeline\_\allowbreak{}integration.\allowbreak{}py:\allowbreak{}46-\allowbreak{}-\allowbreak{}284} \\",
     r"Pipeline integration & \texttt{九个函数}（\texttt{test\_\allowbreak{}pipeline\_\allowbreak{}integration.\allowbreak{}py}） & 65 & \texttt{:46--284} \\"),
    (r"Constitution audit & 五个函数 & 26 & \texttt{test\_\allowbreak{}constitution.\allowbreak{}py} \\",
     r"Constitution audit & \texttt{五个函数}（\texttt{test\_\allowbreak{}constitution.\allowbreak{}py}） & 26 & --- \\"),
]


def patch(path, pairs):
    s = open(path, encoding="utf-8").read()
    for old, new in pairs:
        if old in s:
            s = s.replace(old, new, 1)
            print("OK  ", os.path.basename(path))
        else:
            print("MISS", os.path.basename(path), "|", old[:50])
    open(path, "w", encoding="utf-8", newline="\n").write(s)


patch(EN, pairs_en)
patch(ZH, pairs_zh)
