import pytesseract
from PIL import Image
from app.pipeline.ocr.base import OcrResult

# technical-notation char allowlist; psm 6 = uniform block of text
_CONFIG = (
    "--psm 6 "
    "-c tessedit_char_whitelist=0123456789,.±+-RØMAXxX°/ "
)


class TesseractBackend:
    def __init__(self, lang: str = "deu+eng", config: str = _CONFIG):
        self.lang = lang
        self.config = config

    def read_region(self, image: Image.Image) -> OcrResult:
        data = pytesseract.image_to_data(
            image, lang=self.lang, config=self.config,
            output_type=pytesseract.Output.DICT,
        )
        words, confs = [], []
        for txt, conf in zip(data["text"], data["conf"]):
            if txt.strip():
                words.append(txt.strip())
                try:
                    confs.append(float(conf))
                except ValueError:
                    pass
        text = " ".join(words)
        confidence = (sum(confs) / len(confs) / 100.0) if confs else 0.0
        return OcrResult(text=text, confidence=confidence)
