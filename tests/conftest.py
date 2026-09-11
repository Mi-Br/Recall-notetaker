import json
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def transcript_json():
    return json.loads((FIXTURES / "transcript.json").read_text())


@pytest.fixture
def bot_done_json():
    return json.loads((FIXTURES / "bot_done.json").read_text())
