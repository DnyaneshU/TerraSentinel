from terrasentinel.scanner import _norm_path, _norm_sev, _parse_checkov, _salvage_json


def test_parse_checkov_dict():
    data = {
        "check_type": "terraform",
        "results": {
            "failed_checks": [
                {
                    "check_id": "CKV_AWS_24",
                    "check_name": "Ensure no SSH ingress from 0.0.0.0/0",
                    "file_path": "/main.tf",
                    "file_line_range": [10, 20],
                    "resource": "aws_security_group.web",
                    "severity": None,
                    "guideline": "https://example.com",
                }
            ]
        },
    }
    findings = _parse_checkov(data)
    assert len(findings) == 1
    f = findings[0]
    assert f.check_id == "CKV_AWS_24"
    assert f.file == "main.tf"
    assert f.line == 10
    assert f.resource == "aws_security_group.web"
    assert f.scanner == "checkov"
    assert f.severity is None


def test_parse_checkov_list_form():
    data = [
        {"results": {"failed_checks": []}},
        {
            "results": {
                "failed_checks": [
                    {"check_id": "CKV_AWS_18", "check_name": "logging", "file_path": "a.tf"}
                ]
            }
        },
    ]
    findings = _parse_checkov(data)
    assert [f.check_id for f in findings] == ["CKV_AWS_18"]


def test_parse_checkov_empty():
    assert _parse_checkov({"results": {"failed_checks": []}}) == []
    assert _parse_checkov({}) == []


def test_normalizers():
    assert _norm_path("\\modules\\s3\\main.tf") == "modules/s3/main.tf"
    assert _norm_path("/main.tf") == "main.tf"
    assert _norm_sev("HIGH") == "high"
    assert _norm_sev("nonsense") is None
    assert _norm_sev(None) is None


def test_salvage_json():
    assert _salvage_json('garbage {"a": 1}') == {"a": 1}
    assert _salvage_json("no json here") is None
