from pathlib import Path
import shutil
import pytest

FIXTURES = Path(__file__).parent / "fixtures"

@pytest.fixture(scope="session", autouse=True)
def ensure_sample_pdf():
    FIXTURES.mkdir(exist_ok=True)
    target = FIXTURES / "sample.pdf"
    if not target.exists():
        root_pdf = Path(__file__).parents[1] / "sample.pdf"
        shutil.copy(root_pdf, target)

@pytest.fixture
def sample_pdf():
    return FIXTURES / "sample.pdf"
