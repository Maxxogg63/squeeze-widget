import fitz
import os

src = os.path.join(os.path.dirname(__file__), "Инструкция БИТ.pdf")
out = os.path.join(os.path.dirname(__file__), "_bit_pdf_pages")
os.makedirs(out, exist_ok=True)
doc = fitz.open(src)
print("pages", doc.page_count)
for i, page in enumerate(doc):
    text = page.get_text("text").strip()
    print("--- page", i + 1, "text_len", len(text), "---")
    if text:
        print(text[:3000])
    pix = page.get_pixmap(matrix=fitz.Matrix(1.8, 1.8))
    path = os.path.join(out, f"page_{i + 1:02d}.png")
    pix.save(path)
    print("saved", path, pix.width, pix.height)
