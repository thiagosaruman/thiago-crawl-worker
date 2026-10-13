from pathlib import Path
import re

WORKFLOW = Path(".github/workflows/thiago-crawl.yml")


def test_public_workflow_contract():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "workflow_dispatch:" in text
    inputs = re.search(r"workflow_dispatch:\s*\n\s*inputs:\s*\n(?P<body>.*?)(?=\npermissions:)", text, re.S)
    assert inputs is not None
    body = inputs.group("body")
    assert re.search(r"^\s{6}job_id:", body, re.M)
    assert not re.search(r"^\s{6}(url|topic|max_pages|max_depth|mode):", body, re.M)
    assert "contents: read" in text
    assert "id-token: write" in text
    assert "runs-on: ubuntu-latest" in text
    assert 'python-version: "3.11"' in text
    assert "python -m pip install -r requirements.txt" in text
    assert "scrapling install --force" in text
    assert "python -m pytest -q" in text
    assert "PUBLIC_RUNNER_MODE: redacted" in text
    assert "upload-artifact" not in text
    assert "set -x" not in text
    assert "TARGET_URL" not in text
    assert "CRAWL_TOPIC" not in text


def test_actions_are_sha_pinned_and_checkout_does_not_persist_credentials():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1" in text
    assert "actions/setup-python@5fda3b95a4ea91299a34e894583c3862153e4b97" in text
    assert "persist-credentials: false" in text
