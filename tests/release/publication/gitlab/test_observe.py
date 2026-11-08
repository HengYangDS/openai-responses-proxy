"""GitLab hosted release and CI observation contracts."""

from __future__ import annotations

from typing import TYPE_CHECKING
from typing import cast

import pytest

from tests.release.artifact.fixtures import release_bundle
from tools.release.publication.gitlab import observe as gitlab

if TYPE_CHECKING:
    from pytest_mock import MockerFixture


class GitLabObservationContracts:
    """Normalize exact hosted identities within one Forge boundary."""

    @staticmethod
    def _gitlab_job(name: str, commit: str, pipeline_id: int = 7) -> dict[str, object]:
        return {
            "name": name,
            "status": "success",
            "allow_failure": False,
            "ref": "v1.2.3",
            "tag": True,
            "pipeline": {"id": pipeline_id, "sha": commit, "ref": "v1.2.3", "source": "push"},
            "commit": {"id": commit},
        }

    @staticmethod
    def _normalize_gitlab(
        commit: str,
        fixture: tuple[
            dict[str, object], list[dict[str, object]], dict[str, object], dict[str, bytes]
        ],
    ) -> dict[str, object]:
        pipeline, jobs, release, assets = fixture
        return gitlab.normalize(
            repository="group/repo",
            tag="v1.2.3",
            commit_oid=commit,
            pipeline=pipeline,
            jobs=jobs,
            release=release,
            assets=assets,
        )

    @staticmethod
    def _gitlab_fixture(
        commit: str,
    ) -> tuple[dict[str, object], list[dict[str, object]], dict[str, object], dict[str, bytes]]:
        pipeline: dict[str, object] = {
            "id": 7,
            "sha": commit,
            "ref": "v1.2.3",
            "tag": True,
            "source": "push",
            "status": "success",
        }
        jobs = [
            GitLabObservationContracts._gitlab_job(name, commit)
            for name in gitlab.DEFAULT_REQUIRED_JOBS
        ]
        release: dict[str, object] = {
            "tag_name": "v1.2.3",
            "name": "OpenAI Responses Proxy v1.2.3",
            "commit": {"id": commit},
            "upcoming_release": False,
            "description": (
                "Provider-native source release. See CHANGELOG.md for user-relevant changes."
            ),
            "evidences": [{"sha": "f" * 40}],
        }
        return (pipeline, jobs, release, release_bundle(("linux-x86_64",)))

    def test_gitlab_requires_exact_tag_pipeline_jobs_and_release(self) -> None:
        commit = "a" * 40
        pipeline, jobs, release, assets = self._gitlab_fixture(commit)
        result = self._normalize_gitlab(commit, (pipeline, jobs, release, assets))
        ci = cast("dict[str, object]", result["ci"])
        assert ci["status"] == "success"
        jobs[-1]["allow_failure"] = True
        with pytest.raises(gitlab.GitLabProofError):
            self._normalize_gitlab(commit, (pipeline, jobs, release, assets))

    def test_gitlab_collection_and_malformed_boundaries_fail_closed(
        self, *, mocker: MockerFixture
    ) -> None:
        commit, tag_object = ("a" * 40, "b" * 40)
        pipeline, jobs, release, asset_bytes = self._gitlab_fixture(commit)
        release["assets"] = {
            "links": [
                {"name": name, "url": f"https://gitlab.example/assets/{index}"}
                for index, name in enumerate(asset_bytes, 1)
            ]
        }
        tag_record = {"name": "v1.2.3", "target": tag_object, "commit": {"id": commit}}
        api = mocker.patch.object(gitlab, "_api", side_effect=[tag_record, pipeline])
        mocker.patch.object(gitlab.hosted, "executable", return_value="glab")
        download = mocker.patch.object(
            gitlab.hosted, "api_bytes", side_effect=list(asset_bytes.values())
        )
        mocker.patch.object(gitlab, "_api_pages", side_effect=[[pipeline], jobs, [release]])
        result = gitlab.collect(
            api_base="https://gitlab.example/api/v4/",
            repository="group/repo",
            tag="v1.2.3",
            tag_object_oid=tag_object,
            commit_oid=commit,
        )
        assert result["repository"] == "group/repo"
        assert api.call_args_list[0].args[0] == (
            "https://gitlab.example/api/v4/projects/group%2Frepo/repository/tags/v1.2.3"
        )
        assert [call.args[0] for call in download.call_args_list] == [
            ("glab", "api", "--method", "GET", f"https://gitlab.example/assets/{index}")
            for index in range(1, len(asset_bytes) + 1)
        ]
        with pytest.raises(gitlab.GitLabProofError):
            self._normalize_gitlab(
                commit, (pipeline, [{"name": "not-required"}, *jobs[:-1]], release, asset_bytes)
            )
        wrong_commit = {**tag_record, "commit": {"id": "0" * 40}}
        mocker.patch.object(gitlab, "_api", return_value=wrong_commit)
        with pytest.raises(gitlab.GitLabProofError, match="commit differs"):
            gitlab.collect(
                api_base="https://gitlab.example/api/v4",
                repository="group/repo",
                tag="v1.2.3",
                tag_object_oid=tag_object,
                commit_oid=commit,
            )
        mocker.patch.object(gitlab, "_api", side_effect=[tag_record, {**pipeline, "id": 8}])
        mocker.patch.object(gitlab, "_api_pages", return_value=[pipeline])
        with pytest.raises(gitlab.GitLabProofError, match="detail identity"):
            gitlab.collect(
                api_base="https://gitlab.example/api/v4",
                repository="group/repo",
                tag="v1.2.3",
                tag_object_oid=tag_object,
                commit_oid=commit,
            )
        for value in ([], None):
            with pytest.raises(gitlab.GitLabProofError):
                gitlab._mapping(value, "malformed")
        for value in ([], {}):
            with pytest.raises(gitlab.GitLabProofError):
                gitlab._stable_id(value)
        for value, expected in (([1], [1]), ({"id": 1}, [{"id": 1}])):
            assert gitlab._page_items(value) == expected
        with pytest.raises(gitlab.GitLabProofError):
            gitlab._page_items("wrong")
        with pytest.raises(gitlab.GitLabProofError):
            gitlab._evidence({})
        with pytest.raises(gitlab.GitLabProofError, match="HTTP"):
            gitlab.collect(
                api_base="file:///tmp",
                repository="group/repo",
                tag="v1.2.3",
                tag_object_oid=tag_object,
                commit_oid=commit,
            )
        pipeline, jobs, release, assets = self._gitlab_fixture(commit)
        for mutation in (
            lambda pipeline, _jobs, _release: pipeline.update(yaml_errors="bad"),
            lambda _pipeline, jobs, _release: jobs[0].update(ref="wrong"),
            lambda _pipeline, jobs, _release: jobs[0].update(commit={"id": "0" * 40}),
            lambda _pipeline, _jobs, release: release.update(commit={"id": "0" * 40}),
            lambda _pipeline, _jobs, release: release.update(evidences=[{"sha": ""}]),
        ):
            pipeline, jobs, release, assets = self._gitlab_fixture(commit)
            mutation(pipeline, jobs, release)
            with pytest.raises(gitlab.GitLabProofError):
                self._normalize_gitlab(commit, (pipeline, jobs, release, assets))

    def test_gitlab_cli_transport_and_pagination_translate_failures(
        self, *, mocker: MockerFixture
    ) -> None:
        completed = mocker.Mock(stdout='[{"id":1}]\n{"id":2}\n')
        mocker.patch.object(gitlab.hosted, "executable", return_value="glab")
        mocker.patch.object(gitlab.subprocess, "run", return_value=completed)
        assert gitlab._api_pages("endpoint") == [{"id": 1}, {"id": 2}]
        mocker.stopall()
        mocker.patch.object(gitlab.hosted, "executable", return_value="glab")
        mocker.patch.object(gitlab.hosted, "api_json", return_value={})
        assert gitlab._api("endpoint") == {}
        mocker.stopall()
        for failure in (OSError("missing"), ValueError("bad")):
            mocker.patch.object(gitlab.hosted, "executable", return_value="glab")
            mocker.patch.object(gitlab.subprocess, "run", side_effect=failure) if isinstance(
                failure, OSError
            ) else mocker.patch.object(gitlab.json, "loads", side_effect=failure)
            with pytest.raises(gitlab.GitLabProofError):
                gitlab._api_pages("endpoint")
            mocker.stopall()

    def test_gitlab_required_job_duplicates_fail_closed(self) -> None:
        commit = "a" * 40
        pipeline, jobs, release, gitlab_assets = self._gitlab_fixture(commit)
        jobs.append(self._gitlab_job(gitlab.DEFAULT_REQUIRED_JOBS[0], commit))
        with pytest.raises(gitlab.GitLabProofError):
            self._normalize_gitlab(commit, (pipeline, jobs, release, gitlab_assets))

    def test_gitlab_provider_candidates_and_release_identity_fail_closed(
        self, subtests: pytest.Subtests
    ) -> None:
        commit = "a" * 40
        pipeline, jobs, release, assets = self._gitlab_fixture(commit)
        for mutation in (
            lambda: pipeline.update(source="web"),
            lambda: release.update(description="wrong"),
            lambda: release.update(evidences=[{"sha": "f" * 40}, {"sha": "e" * 40}]),
        ):
            pipeline.update(source="push")
            release.update(
                description=(
                    "Provider-native source release. See CHANGELOG.md for user-relevant changes."
                ),
                evidences=[{"sha": "f" * 40}],
            )
            mutation()
            with (
                subtests.test(pipeline=pipeline, release=release),
                pytest.raises(gitlab.GitLabProofError),
            ):
                self._normalize_gitlab(commit, (pipeline, jobs, release, assets))
