from dataclasses import dataclass
from pathlib import Path
import fitz  # PyMuPDF


@dataclass
class RenderResult:
    png_path: Path
    width: int
    height: int
    scale: float          # pixels per PDF point
    page_rect: tuple      # (x0, y0, x1, y1) in PDF points


def render_page(pdf_path, dpi: int = 200, out_dir: Path = None, page_index: int = 0) -> RenderResult:
    out_dir = Path(out_dir or Path(pdf_path).parent)
    out_dir.mkdir(parents=True, exist_ok=True)
    doc = fitz.open(pdf_path)
    page = doc[page_index]
    scale = dpi / 72.0
    mat = fitz.Matrix(scale, scale)
    pix = page.get_pixmap(matrix=mat, alpha=False)
    png_path = out_dir / "page.png"
    pix.save(png_path)
    rect = page.rect
    doc.close()
    return RenderResult(
        png_path=png_path,
        width=pix.width,
        height=pix.height,
        scale=scale,
        page_rect=(rect.x0, rect.y0, rect.x1, rect.y1),
    )
