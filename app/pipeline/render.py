import math
import sys
from dataclasses import dataclass
from pathlib import Path
import fitz  # PyMuPDF

# Large-format drawings (2.5 m x 1.7 m sheets exist in this corpus) reach
# hundreds of megapixels at 300 dpi, past PIL's DecompressionBombError limit of
# 178.9 MP. Budget the pixel count instead: 80 MP is below PIL's *warning*
# threshold (89.5 MP), so no global PIL state has to be touched and oversized
# pages simply render at a reduced effective dpi. RenderResult.scale always
# reports the resolution actually used — callers must convert pixels to points
# with that, never with the requested dpi.
MAX_RENDER_PIXELS = 80_000_000


@dataclass
class RenderResult:
    png_path: Path
    width: int
    height: int
    scale: float          # pixels per PDF point — the EFFECTIVE one
    page_rect: tuple      # (x0, y0, x1, y1) in PDF points

    @property
    def dpi(self) -> float:
        """Effective dpi: what the page was actually rendered at."""
        return self.scale * 72.0


def _pixel_count(w_pt: float, h_pt: float, scale: float) -> int:
    """Pixels a page of this size yields at this scale. PyMuPDF snaps the
    transformed rect outward, so allow a pixel of growth per axis and keep the
    budget a real ceiling rather than an approximate one."""
    return (math.ceil(w_pt * scale) + 1) * (math.ceil(h_pt * scale) + 1)


def _budget_scale(w_pt: float, h_pt: float, scale: float, max_pixels: int) -> float:
    """The largest scale <= `scale` whose render fits inside `max_pixels`."""
    if w_pt <= 0 or h_pt <= 0 or max_pixels <= 0:
        return scale
    if _pixel_count(w_pt, h_pt, scale) <= max_pixels:
        return scale
    clamped = math.sqrt(max_pixels / (w_pt * h_pt))
    while clamped > 0 and _pixel_count(w_pt, h_pt, clamped) > max_pixels:
        clamped *= 0.99          # cover the outward snap; converges immediately
    return clamped


def render_page(pdf_path, dpi: int = 200, out_dir: Path = None, page_index: int = 0,
                max_pixels: int = MAX_RENDER_PIXELS) -> RenderResult:
    out_dir = Path(out_dir or Path(pdf_path).parent)
    out_dir.mkdir(parents=True, exist_ok=True)
    doc = fitz.open(pdf_path)
    page = doc[page_index]
    rect = page.rect
    scale = _budget_scale(rect.width, rect.height, dpi / 72.0, max_pixels)
    if scale < dpi / 72.0:
        print(f"[sindri.render] page {rect.width:.0f}x{rect.height:.0f} pt exceeds "
              f"the {max_pixels / 1e6:.0f} MP budget at {dpi} dpi; rendering at "
              f"{scale * 72.0:.0f} dpi", file=sys.stderr, flush=True)
    mat = fitz.Matrix(scale, scale)
    pix = page.get_pixmap(matrix=mat, alpha=False)
    png_path = out_dir / "page.png"
    pix.save(png_path)
    doc.close()
    return RenderResult(
        png_path=png_path,
        width=pix.width,
        height=pix.height,
        scale=scale,
        page_rect=(rect.x0, rect.y0, rect.x1, rect.y1),
    )
