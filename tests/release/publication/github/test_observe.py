"""GitHub hosted release and CI observation contracts."""

from __future__ import annotations

import subprocess
from typing import cast

import pytest

from tests.release.artifact.fixtures import release_bundle
from tools.release.publication.github import observe as github


class GitHubObservationContracts:
    """Normalize exact hosted identities within one Forge boundary."""

    @staticmethod
    def _github_fixture(
        commit: str,
    ) -> tuple[
        list[dict[str, object]],
        dict[int, list[dict[str, object]]],
        dict[str, object],
        dict[str, bytes],
    ]:
        runs: list[dict[str, object]] = [
            {
                "id": 1,
                "name": "Verify",
                "path": ".github/workflows/verify.yml",
                "event": "push",
                "head_branch": "v1.2.3",
                "head_sha": commit,
                "status": "completed",
                "conclusion": "success",
            }
        ]
        jobs: dict[int, list[dict[str, object]]] = {
            1: [
                {"name": name, "status": "completed", "conclusion": "success"}
                for name in github.DEFAULT_REQUIRED_JOBS
            ]
        }
        release: dict[str, object] = {
            "id": 3,
            "tag_name": "v1.2.3",
            "name": "OpenAI Responses Proxy v1.2.3",
            "draft": False,
            "prerelease": False,
            "published_at": "2026-07-29T00:00:00Z",
        }
        assets = release_bundle()
        return (runs, jobs, release, assets)

    @staticmethod
    def _normalize_github(
        commit: str,
        fixture: tuple[
            list[dict[str, object]],
            dict[int, list[dict[str, object]]],
            dict[str, object],
            dict[str, bytes],
        ],
    ):
        runs, jobs, release, assets = fixture
        return github.normalize(
            repository="owner/repo",
            tag="v1.2.3",
            commit_oid=commit,
            runs=runs,
            jobs=jobs,
            release=release,
            assets=assets,
        )

    def test_github_requires_exact_verify_run_and_release_record(self) -> None:
        commit = "a" * 40
        runs, jobs, release, assets = self._github_fixture(commit)
        result = self._normalize_github(commit, (runs, jobs, release, assets))
        ci = cast("dict[str, object]", result["ci"])
        release_result = cast("dict[str, object]", result["release"])
        assert ci["status"] == "success"
        assert release_result["commit_oid"] == commit
        runs[0]["head_sha"] = "b" * 40
        with pytest.raises(github.GitHubProofError):
            self._normalize_github(commit, (runs, jobs, release, assets))

    def test_github_tag_push_remains_unique_after_release_event(self) -> None:
        commit = "a" * 40
        runs, jobs, release, assets = self._github_fixture(commit)
        runs.append(dict(runs[0], id=2, event="release"))

        result = self._normalize_github(commit, (runs, jobs, release, assets))

        assert cast("dict[str, object]", result["ci"])["id"] == 1

    def test_github_selects_the_newest_published_ancestor_before_the_candidate(
        self, *, mocker
    ) -> None:
        """Resolve one immutable predecessor instead of inheriting GitHub's latest pointer."""
        releases = [
            {"tag_name": "v3.1.1", "draft": False, "prerelease": False},
            {"tag_name": "v3.1.0", "draft": False, "prerelease": False},
            {"tag_name": "v3.0.5", "draft": False, "prerelease": False},
            {"tag_name": "v4.0.0", "draft": True, "prerelease": False},
        ]
        mocker.patch.object(github, "_api_pages", return_value=[releases])
        merge_base = mocker.patch.object(
            subprocess,
            "run",
            side_effect=(
                subprocess.CompletedProcess((), 1, "", ""),
                subprocess.CompletedProcess((), 0, "", ""),
            ),
        )
        assert github.published_predecessor(repository="owner/repo", version="3.1.2") == "v3.1.0"
        assert [call.args[0][-2] for call in merge_base.call_args_list] == [
            "refs/tags/v3.1.1^{commit}",
            "refs/tags/v3.1.0^{commit}",
        ]

    def test_github_predecessor_selection_rejects_ambiguous_or_missing_history(
        self, *, mocker, subtests
    ) -> None:
        """Fail closed when hosted release records cannot name one local ancestor."""
        cases = (
            (
                [
                    {"tag_name": "v3.1.0", "draft": False, "prerelease": False},
                    {"tag_name": "v3.1.0", "draft": False, "prerelease": False},
                ],
                (0, 0),
                "ambiguous",
            ),
            ([{"tag_name": "v4.0.0", "draft": False, "prerelease": False}], (), "unavailable"),
        )
        for releases, statuses, message in cases:
            mocker.patch.object(github, "_api_pages", return_value=[releases])
            mocker.patch.object(
                subprocess,
                "run",
                side_effect=[
                    subprocess.CompletedProcess((), status, "", "") for status in statuses
                ],
            )
            with (
                subtests.test(message=message),
                pytest.raises(github.GitHubProofError, match=message),
            ):
                github.published_predecessor(repository="owner/repo", version="3.1.2")

    @pytest.mark.parametrize(
        ("repository", "version"),
        [("", "3.1.2"), ("owner/repo", "03.1.2"), ("owner/repo", "v3.1.2")],
    )
    def test_github_predecessor_request_requires_a_repository_and_strict_version(
        self, repository: str, version: str, *, mocker
    ) -> None:
        """Refuse a malformed candidate before any hosted read."""
        pages = mocker.patch.object(github, "_api_pages")
        with pytest.raises(github.GitHubProofError, match="request is invalid"):
            github.published_predecessor(repository=repository, version=version)
        pages.assert_not_called()

    def test_github_predecessor_rejects_a_published_tag_outside_the_release_grammar(
        self, *, mocker
    ) -> None:
        """A published release named by a non-canonical tag cannot become the predecessor."""
        releases = [{"tag_name": "v03.1.0", "draft": False, "prerelease": False}]
        mocker.patch.object(github, "_api_pages", return_value=[releases])
        with pytest.raises(github.GitHubProofError, match="published release tag is invalid"):
            github.published_predecessor(repository="owner/repo", version="3.1.2")

    def test_github_collection_binds_api_tag_object_to_fetched_identity(self, *, mocker) -> None:
        commit = "a" * 40
        tag_object = "b" * 40
        runs, _, _, asset_bytes = self._github_fixture(commit)
        responses = [
            {"ref": "refs/tags/v1.2.3", "object": {"type": "tag", "sha": tag_object}},
            {"tag": "v1.2.3", "sha": tag_object, "object": {"type": "commit", "sha": commit}},
            {"workflow_runs": runs},
            {
                "jobs": [
                    {"name": name, "status": "completed", "conclusion": "success"}
                    for name in github.DEFAULT_REQUIRED_JOBS
                ]
            },
            {
                "id": 3,
                "tag_name": "v1.2.3",
                "name": "OpenAI Responses Proxy v1.2.3",
                "draft": False,
                "prerelease": False,
                "published_at": "2026-07-29T00:00:00Z",
                "assets": [
                    {"name": name, "url": f"https://api.example/assets/{index}"}
                    for index, name in enumerate(asset_bytes, 1)
                ],
            },
        ]
        mocker.patch.object(github.hosted, "executable", return_value="gh")
        mocker.patch.object(github, "_api", side_effect=responses[:2])
        mocker.patch.object(
            github,
            "_api_pages",
            side_effect=[
                [{"workflow_runs": [runs[0]]}],
                [{"jobs": responses[3]["jobs"]}],
                [[responses[4]]],
            ],
        )
        mocker.patch.object(github.hosted, "api_bytes", side_effect=list(asset_bytes.values()))
        github.collect(
            repository="owner/repo", tag="v1.2.3", tag_object_oid=tag_object, commit_oid=commit
        )
        responses[0] = {"object": {"type": "tag", "sha": "c" * 40}}
        mocker.patch.object(github.hosted, "executable", return_value="gh")
        mocker.patch.object(github, "_api", side_effect=responses[:1])
        with pytest.raises(github.GitHubProofError):
            github.collect(
                repository="owner/repo", tag="v1.2.3", tag_object_oid=tag_object, commit_oid=commit
            )

    def test_github_malformed_boundaries_and_hosted_transport_fail_closed(
        self, subtests, *, mocker
    ) -> None:
        commit, tag_object = ("a" * 40, "b" * 40)
        runs, jobs, release, assets = self._github_fixture(commit)
        cases = (
            ("run-id", lambda: runs[0].update(id=True)),
            ("incomplete-job", lambda: jobs[1][0].update(status="queued")),
            ("release-id", lambda: release.update(id=True)),
        )
        for name, mutate in cases:
            runs, jobs, release, assets = self._github_fixture(commit)
            mutate()
            with subtests.test(name=name), pytest.raises(github.GitHubProofError):
                self._normalize_github(commit, (runs, jobs, release, assets))
        for helper, value in ((github._mapping, []), (github._mappings, {})):
            with pytest.raises(github.GitHubProofError):
                helper(value, "malformed")
        mocker.patch.object(github.hosted, "executable", return_value="gh")
        mocker.patch.object(github.hosted, "api_json", return_value={})
        with pytest.raises(github.GitHubProofError):
            github._api_pages("endpoint")
        mocker.patch.object(github.hosted, "executable", return_value="gh")
        mocker.patch.object(github.hosted, "api_json", return_value={})
        assert github._api("endpoint") == {}
        responses = [
            {"ref": "refs/tags/v1.2.3", "object": {"type": "tag", "sha": tag_object}},
            {"tag": "wrong", "sha": tag_object, "object": {"type": "commit", "sha": commit}},
        ]
        mocker.patch.object(github, "_api", side_effect=responses)
        with pytest.raises(github.GitHubProofError, match="tag identity"):
            github.collect(
                repository="owner/repo", tag="v1.2.3", tag_object_oid=tag_object, commit_oid=commit
            )

    def test_github_required_job_duplicates_fail_closed(self) -> None:
        commit = "a" * 40
        runs, github_jobs, github_release, github_assets = self._github_fixture(commit)
        duplicated = github.DEFAULT_REQUIRED_JOBS[0]
        github_jobs[1].append({"name": duplicated, "status": "completed", "conclusion": "success"})
        with pytest.raises(github.GitHubProofError):
            self._normalize_github(commit, (runs, github_jobs, github_release, github_assets))

    def test_github_provider_candidates_and_release_identity_fail_closed(self, subtests) -> None:
        commit = "a" * 40
        runs, github_jobs, github_release, github_assets = self._github_fixture(commit)
        for mutation in (
            lambda: runs.append(dict(runs[0], id=4)),
            lambda: github_release.update(published_at=None),
        ):
            runs[:] = runs[:2]
            github_release.update(published_at="2026-07-29T00:00:00Z")
            mutation()
            with (
                subtests.test(runs=runs, release=github_release),
                pytest.raises(github.GitHubProofError),
            ):
                self._normalize_github(commit, (runs, github_jobs, github_release, github_assets))
