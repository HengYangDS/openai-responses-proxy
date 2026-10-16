package ci

import "list"

#GitLabCommitEvent: "export OPENAI_RESPONSES_PROXY_COMMIT_BASE=\"${CI_MERGE_REQUEST_DIFF_BASE_SHA:-$CI_COMMIT_BEFORE_SHA}\" OPENAI_RESPONSES_PROXY_COMMIT_HEAD=\"$CI_COMMIT_SHA\"; if [ -n \"$CI_COMMIT_TAG\" ]; then export OPENAI_RESPONSES_PROXY_COMMIT_BASE=\"$CI_COMMIT_SHA\"; fi; "

gitlab: {
	workflow: rules: [{
		if: "$CI_COMMIT_TAG"
	}, {
		if: "$CI_PIPELINE_SOURCE == \"merge_request_event\""
	}, {
		if: "$CI_COMMIT_BRANCH == \"dev\" || $CI_COMMIT_BRANCH == \"main\""
	}, {
		if:   "$CI_COMMIT_BRANCH && $CI_OPEN_MERGE_REQUESTS"
		when: "never"
	}]
	stages: ["verify", "release"]
	variables: {
		DEBIAN_FRONTEND:                           "noninteractive"
		OPENAI_RESPONSES_PROXY_RELEASE_TAG_REMOTE: "origin"
		UV_PYTHON_FLOOR_IMAGE:                     "ghcr.io/astral-sh/uv:0.12.18-python3.12-trixie-slim@sha256:38f41574703989d6e5f02be80a3d687b00f98744cce86908097bcd34bcb7eb98"
		UV_PYTHON_LATEST_IMAGE:                    "ghcr.io/astral-sh/uv:0.12.18-python3.14-trixie-slim@sha256:00facf17b58b02b725155862c5cd637f688f906bf7eb5b5194647886d8805cf3"
		UV_CACHE_DIR:                              "$CI_PROJECT_DIR/.cache/uv"
		UV_PYTHON_INSTALL_DIR:                     "$CI_PROJECT_DIR/.cache/uv/python"
		OPENAI_RESPONSES_PROXY_CI_TARGET:          "linux-arm64"
	}
	default: {
		image: name: "$UV_PYTHON_LATEST_IMAGE"
		tags: ["$OPENAI_RESPONSES_PROXY_GITLAB_LINUX_RUNNER_TAG"]
		cache: {
			key: "uv-$OPENAI_RESPONSES_PROXY_CI_TARGET"
			paths: [".cache/uv/"]
		}
	}

	#productRoles: [{
		suffix: ""
		tags: ["$OPENAI_RESPONSES_PROXY_GITLAB_LINUX_REVIEW_RUNNER_TAG"]
		rules: [{if: "$CI_PIPELINE_SOURCE == \"merge_request_event\" && $CI_MERGE_REQUEST_TARGET_BRANCH_NAME == \"dev\""}]
	}, {
		suffix: "-accepted"
		tags: ["$OPENAI_RESPONSES_PROXY_GITLAB_LINUX_RUNNER_TAG"]
		rules: [{if: "$CI_COMMIT_BRANCH == \"dev\""}]
	}]
	#uvContract: """
		UV_REQUIREMENT="$(python -c 'import tomllib; print(tomllib.load(open("pyproject.toml", "rb"))["tool"]["uv"]["required-version"])')"
		UV_VERSION="$(uv --version)"
		UV_VERSION="${UV_VERSION#uv }"
		ACTUAL_UV_VERSION="${UV_VERSION%% *}"
		EXPECTED_UV_VERSION="${UV_REQUIREMENT#==}"
		if [ "$ACTUAL_UV_VERSION" != "$EXPECTED_UV_VERSION" ]; then
		  printf 'uv version mismatch: expected %s, actual %s\n' "$EXPECTED_UV_VERSION" "$ACTUAL_UV_VERSION" >&2
		  exit 1
		fi
		"""
	#systemBootstrap: [
		#uvContract,
		"apt-get update -qq",
		"apt-get install -qq -y --no-install-recommends git openssh-client",
	]
	#qualityBootstrap: list.Concat([#systemBootstrap, [
		"git fetch --tags --force --prune --prune-tags origin",
		"uv sync --locked --group quality --python python --no-python-downloads",
	]])

	#productJobs: {
		"source-and-governance": {
			stage: "verify"
			image: {
				name: #Toolchains.gitlabMiseImage
				entrypoint: [""]
			}
			variables: {
				GIT_DEPTH:         "0"
				MISE_ENABLE_TOOLS: #Toolchains.quality
			}
			before_script: [
				"mise install --locked",
				"npm ci --ignore-scripts",
				"npm audit signatures",
				"git fetch --tags --force --prune --prune-tags origin",
				"mise exec --locked -- uv sync --locked --group quality --python python --no-python-downloads",
			]
			script: [
				#GitLabCommitEvent + "mise exec --locked -- uv run --locked --no-sync --python python --no-python-downloads python -m \(#VerificationJobs.governance.module) --online-links",
			]
		}
		"verify-python": {
			stage: "verify"
			parallel: matrix: [{PYTHON_VERSION: #RuntimeMatrix.python}]
			variables: GIT_DEPTH: "0"
			before_script: list.Concat([#systemBootstrap, [
				"apt-get install -qq -y --no-install-recommends binutils",
				"git fetch --tags --force --prune --prune-tags origin",
				"uv sync --locked --group quality --python python --no-python-downloads",
				"uv python install --no-bin $PYTHON_VERSION",
			]])
			script: [
				"python --version",
				"uv run --locked --no-sync --python python --no-python-downloads nox -s \"\(#VerificationJobs.compatibility.session)-$PYTHON_VERSION\"",
			]
		}
		"verify-python-quality": {
			stage: "verify"
			image: name:          "$UV_PYTHON_FLOOR_IMAGE"
			variables: GIT_DEPTH: "0"
			before_script: list.Concat([#systemBootstrap, [
				"apt-get install -qq -y --no-install-recommends binutils",
				"git fetch --tags --force --prune --prune-tags origin",
			]])
			script: [
				"uv sync --locked --group quality --python python --no-python-downloads",
				"uv run --locked --no-sync --python python --no-python-downloads nox -s \(#VerificationJobs.quality.session)",
			]
		}
		"verify-performance": {
			stage: "verify"
			variables: GIT_DEPTH: "0"
			before_script: #systemBootstrap
			script: [
				"uv sync --locked --group quality --python python --no-python-downloads",
				"uv run --locked --no-sync --python python --no-python-downloads nox -s \(#VerificationJobs.performance.session) -- \"$CI_PROJECT_DIR/.performance\"",
			]
			artifacts: {
				when: "always"
				paths: [".performance/latency.json", ".performance/memory.json"]
			}
		}
	}
	for role in #productRoles {
		for id, job in #productJobs {
			"\(id)\(role.suffix)": job & {
				tags:  role.tags
				rules: role.rules
			}
		}
	}
	"verify-accepted-source": {
		stage: "verify"
		rules: [{if: "$CI_COMMIT_BRANCH == \"dev\" || $CI_COMMIT_BRANCH == \"main\""}]
		variables: GIT_DEPTH: "0"
		before_script: #qualityBootstrap
		script: [
			"uv run --locked --no-sync --python python --no-python-downloads python -m \(#VerificationJobs.metadata.module)",
			#GitLabCommitEvent + "uv run --locked --no-sync --python python --no-python-downloads python -m tools.quality.repository",
		]
	}
	"verify-promotion": {
		stage: "verify"
		tags: ["$OPENAI_RESPONSES_PROXY_GITLAB_LINUX_REVIEW_RUNNER_TAG"]
		rules: [{
			if: "$CI_PIPELINE_SOURCE == \"merge_request_event\" && $CI_MERGE_REQUEST_SOURCE_BRANCH_NAME == \"dev\" && $CI_MERGE_REQUEST_TARGET_BRANCH_NAME == \"main\""
		}]
		variables: GIT_DEPTH: "0"
		before_script: list.Concat([#systemBootstrap, [
			"git fetch origin main dev --tags --force --prune --prune-tags",
			"uv sync --locked --group quality --python python --no-python-downloads",
		]])
		script: [
			"git merge-base --is-ancestor origin/main \"$CI_COMMIT_SHA\"",
			"uv run --locked --no-sync --python python --no-python-downloads python -m \(#VerificationJobs.metadata.module)",
			#GitLabCommitEvent + "uv run --locked --no-sync --python python --no-python-downloads python -m tools.quality.repository",
		]
	}
	"verify-release-tag": {
		stage: "release"
		rules: [{if: "$CI_COMMIT_TAG"}]
		variables: GIT_DEPTH: "0"
		before_script: list.Concat([#qualityBootstrap, [
			"test -f \"${OPENAI_RESPONSES_PROXY_GITLAB_TAG_TRUST:-}\"",
		]])
		script: [
			"uv run --locked --no-sync --python python --no-python-downloads python -m \(#VerificationJobs.metadata.module) --tag \"$CI_COMMIT_TAG\"",
			"uv run --locked --no-sync --python python --no-python-downloads python -m tools.release.tag_signature . \"$CI_COMMIT_TAG\" \"$OPENAI_RESPONSES_PROXY_GITLAB_TAG_TRUST\"",
			#GitLabCommitEvent + "uv run --locked --no-sync --python python --no-python-downloads python -m tools.quality.repository",
		]
	}
}
