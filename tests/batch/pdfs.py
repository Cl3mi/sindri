"""PDF bytes built in code, so no test needs a fixture file named on the
command line (the Bash guard refuses a bare PDF extension there) and no test
can ever pick up a client drawing. MuPDF writes a fresh document id on every
save, so two calls never return the same bytes: a test that needs two
IDENTICAL files reuses one call's result."""
import fitz


def pdf_bytes(pages: int = 1, width: float = 595, height: float = 842) -> bytes:
    doc = fitz.open()
    try:
        for _ in range(pages):
            doc.new_page(width=width, height=height)
        return doc.tobytes()
    finally:
        doc.close()


def encrypted_pdf_bytes() -> bytes:
    """Needs a user password to open -- extraction could never read it."""
    doc = fitz.open()
    try:
        doc.new_page()
        return doc.tobytes(encryption=fitz.PDF_ENCRYPT_AES_256,
                           owner_pw="owner", user_pw="user")
    finally:
        doc.close()
