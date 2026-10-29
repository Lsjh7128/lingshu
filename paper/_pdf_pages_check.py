from pypdf import PdfReader

r = PdfReader("main-en.pdf")
print("pages:", len(r.pages))
# watermarks / annotations
ann = 0
for i, p in enumerate(r.pages, 1):
    a = p.get("/Annots")
    if a:
        ann += len(a)
print("annotations:", ann)
for i in (2, 3, 7, 8, 10, 11, 17, 19, 26):
    t = r.pages[i - 1].extract_text() or ""
    print("\n===== page %d =====" % i)
    print(t[:900])
