"""Executable contracts for owned constants and their declared projections."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from openai_responses_proxy import product_identity as identity
from tools.quality import hard_coding as checker

ROOT = Path(__file__).resolve().parents[2]


class TestHardCodingResponsibility:
    """Require controlled values to have one owner and explicit projections."""

    def test_product_identity_is_one_runtime_owner(self) -> None:
        assert identity.PRODUCT_SLUG == "openai-responses-proxy"
        assert identity.DISPLAY_NAME == "OpenAI Responses Proxy"
        assert identity.COMMAND_NAME == identity.PRODUCT_SLUG
        assert identity.ENVIRONMENT_PREFIX == "OPENAI_RESPONSES_PROXY"
        assert identity.SERVICE_ID == f"{identity.PRODUCT_SLUG}.watchdog"
        assert identity.RELEASE_NAMESPACE == f"{identity.PRODUCT_SLUG}-release"

    def test_controlled_values_have_complete_positive_ownership(self) -> None:
        report = checker.audit()

        assert report["errors"] == []
        assert report["ok"] is True
        assert set(report["kinds"]) == {
            "derived-projection",
            "domain-constant",
            "policy-parameter",
            "supply-chain-pin",
        }
        assert set(report["surfaces"]) == {
            "configuration-field",
            "documentation-entrypoint",
            "environment-variable",
            "native-resource",
            "network-control-route",
            "network-provider-route",
            "public-command",
            "public-result",
            "release-artifact",
        }

    @pytest.mark.parametrize(
        ("field", "error"), [("surfaces", "surface"), ("invariant", "invariant")]
    )
    def test_public_surface_mapping_requires_complete_contract(
        self, field: str, error: str, tmp_path: Path
    ) -> None:
        source = (ROOT / ".config/checks/hard-coding/policy.toml").read_text(encoding="utf-8")
        policy = tmp_path / "hard-coding.toml"
        policy.write_text(source.replace(f"{field} = ", f"removed_{field} = ", 1), encoding="utf-8")

        report = checker.audit(policy_path=policy)

        assert any(error in item for item in report["errors"])

    def test_public_surface_has_one_authoritative_mapping(self, tmp_path: Path) -> None:
        source = (ROOT / ".config/checks/hard-coding/policy.toml").read_text(encoding="utf-8")
        policy = tmp_path / "hard-coding.toml"
        policy.write_text(
            source.replace(
                'projections = ["pyproject.toml"]',
                'surfaces = ["public-command"]\nprojections = ["pyproject.toml"]',
                1,
            ),
            encoding="utf-8",
        )

        report = checker.audit(policy_path=policy)

        assert "hard_coding_surface_multiple_owners:public-command" in report["errors"]

    def test_hard_coding_policy_rejects_duplicate_owners(self, tmp_path: Path) -> None:
        source = (ROOT / ".config/checks/hard-coding/policy.toml").read_text(encoding="utf-8")
        control = source.split("[[controls]]", 2)[1]
        malformed = source + "\n[[controls]]" + control
        policy = tmp_path / "hard-coding.toml"
        policy.write_text(malformed, encoding="utf-8")

        report = checker.audit(policy_path=policy)

        assert "hard_coding_duplicate_control:product-slug" in report["errors"]

    def test_product_identity_projection_drift_fails_the_audit(self, tmp_path: Path) -> None:
        policy = tmp_path / ".config/checks/hard-coding/policy.toml"
        policy.parent.mkdir(parents=True)
        shutil.copy2(ROOT / ".config/checks/hard-coding/policy.toml", policy)
        owner = tmp_path / "src/openai_responses_proxy/product_identity.py"
        owner.parent.mkdir(parents=True)
        shutil.copy2(ROOT / "src/openai_responses_proxy/product_identity.py", owner)
        pyproject = tmp_path / "pyproject.toml"
        source = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        pyproject.write_text(
            source.replace('name = "openai-responses-proxy"', 'name = "drifted-product"', 1),
            encoding="utf-8",
        )

        report = checker.audit(root=tmp_path, policy_path=policy)

        assert (
            "hard_coding_projection_value_mismatch:product-slug:PACKAGE_NAME:"
            "pyproject.toml:project.name"
        ) in report["errors"]


@pytest.mark.parametrize(
    ("source", "error"),
    [
        ("[broken", "hard_coding_policy_invalid"),
        (
            'schema_version=true\nallowed_kinds=["domain-constant"]\ncontrols=[]',
            "hard_coding_schema_version",
        ),
        (
            'schema_version=1.0\nallowed_kinds=["domain-constant"]\ncontrols=[]',
            "hard_coding_schema_version",
        ),
        ("schema_version=1\nallowed_kinds=[]\ncontrols=[]", "hard_coding_allowed_kinds"),
        (
            'schema_version=1\nallowed_kinds=["domain-constant"]\ncontrols=[]',
            "hard_coding_controls",
        ),
        ('schema_version=1\nallowed_kinds=["domain-constant"]\ncontrols=1', "hard_coding_controls"),
    ],
)
def test_empty_or_malformed_policy_cannot_establish_acceptance(source, error, tmp_path):
    policy = tmp_path / "policy.toml"
    policy.write_text(source)
    report = checker.audit(tmp_path, policy)
    assert not report["ok"]
    assert any(str(item).startswith(error) for item in report["errors"])


@pytest.mark.parametrize(
    ("control", "error"),
    [
        ("controls=[1]", "control_must_be_table"),
        ('[[controls]]\nid=""', "control_id_invalid"),
        ('[[controls]]\nid="x"', "control_field_missing"),
        ('[[controls]]\nid="x"\nkind=[]', "control_kind_invalid"),
        ('[[controls]]\nid="x"\nowner="missing"\nprojections=["missing"]', "owner_missing"),
    ],
)
def test_invalid_control_is_reported_instead_of_crashing(control, error, tmp_path):
    policy = tmp_path / "policy.toml"
    policy.write_text('schema_version=1\nallowed_kinds=["domain-constant"]\n' + control)
    report = checker.audit(tmp_path, policy)
    assert not report["ok"]
    assert any(error in str(item) for item in report["errors"])


@pytest.fixture
def projection_root(tmp_path):
    (tmp_path / "owner.py").write_text('VALUE="x"\nALIAS=VALUE\nIGNORED=call()\nleft=right=1\n')
    (tmp_path / "target.toml").write_text('[project]\nname="x"\n[project.scripts]\nx="main"\n')
    return tmp_path


@pytest.mark.parametrize(
    ("checks", "error"),
    [
        (None, None),
        ([], "checks_invalid"),
        ([1], "check_must_be_table"),
        ([{}], "check_invalid"),
        (
            [
                {
                    "source": "VALUE",
                    "target": "target.toml",
                    "selector": "project.name",
                    "match": "bad",
                }
            ],
            "match_invalid",
        ),
        (
            [
                {
                    "source": "ABSENT",
                    "target": "target.toml",
                    "selector": "project.name",
                    "match": "toml-value",
                }
            ],
            "source_missing",
        ),
        (
            [
                {
                    "source": "VALUE",
                    "target": "target.toml",
                    "selector": "absent",
                    "match": "toml-value",
                }
            ],
            "selector_missing",
        ),
        (
            [
                {
                    "source": "VALUE",
                    "target": "target.toml",
                    "selector": "project.name",
                    "match": "toml-key",
                }
            ],
            "value_mismatch",
        ),
        (
            [
                {
                    "source": "VALUE",
                    "target": "target.toml",
                    "selector": "project.name",
                    "match": "toml-value",
                }
            ],
            None,
        ),
        (
            [
                {
                    "source": "ALIAS",
                    "target": "target.toml",
                    "selector": "project.scripts",
                    "match": "toml-key",
                }
            ],
            None,
        ),
    ],
)
def test_projection_checks_follow_the_declared_owner(projection_root, checks, error):
    errors = checker._projection_errors(
        root=projection_root, identifier="x", owner="owner.py", checks=checks
    )
    assert bool(errors) == (error is not None)
    if error is not None:
        assert any(error in item for item in errors)


@pytest.mark.parametrize(
    ("owner", "source", "error"),
    [("owner.txt", "x", "unsupported"), ("owner.py", "if", "owner_invalid")],
)
def test_projection_owner_must_be_parseable_python(owner, source, error, tmp_path):
    (tmp_path / owner).write_text(source)
    errors = checker._projection_errors(root=tmp_path, identifier="x", owner=owner, checks=[{}])
    assert error in errors[0]


def test_audit_cli_preserves_failure_status_and_json(mocker, capsys):
    mocker.patch.object(checker, "audit", return_value={"ok": False, "errors": ["missing"]})
    with pytest.raises(SystemExit) as stopped:
        checker.main()
    assert stopped.value.code == 1
    assert '"ok": false' in capsys.readouterr().out
    with pytest.raises(SystemExit, match="accepts no arguments"):
        checker.main(("unexpected",))


def test_listener_port_literals_have_one_production_owner():
    production = ROOT / "src" / "openai_responses_proxy"
    owner = production / "runtime" / "config.py"
    owner_text = owner.read_text(encoding="utf-8")
    assert owner_text.count("8792") == 1
    assert "8791" not in owner_text
    copied = []
    for source in production.rglob("*.py"):
        if source == owner:
            continue
        text = source.read_text(encoding="utf-8")
        if "8791" in text or "8792" in text:
            copied.append(source.relative_to(ROOT).as_posix())
    assert copied == []
