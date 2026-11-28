package ci

import "list"

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
	// Commit admission runs from the Mise image of the source job and installs only the pinned ETHOS
	// release; that image carries Git and libatomic1, and the job adds the rest that ETHOS expects.
	#commitAdmission: {
		stage:   "verify"
		timeout: "15m"
		image:   #productJobs."source-and-governance".image
		variables: _miseData & {
			GIT_DEPTH:                          "0"
			MISE_ENABLE_TOOLS:                  #Toolchains.ethos
			OPENAI_RESPONSES_PROXY_COMMIT_HEAD: "$CI_COMMIT_SHA"
			OPENAI_RESPONSES_PROXY_COMMIT_PEER: "gitlab"
		}
		// Nothing here runs uv, so the job restores only its own Mise installs.
		cache: [#miseInstalls & {_set: "ethos"}]
		before_script: [
			#CommitTrustPreflight,
			"mise install --locked",
			"apt-get update -qq",
			#EthosRelease.linuxInstall,
			#EthosRelease.bindTrust,
		]
		script: [#CommitAdmission]
		tags: [...string]
		rules: [...{...}]
	}
	"commit-admission": #commitAdmission & {
		tags: #Platforms.linux.gitlab.review
		rules: [{
			if: "$CI_PIPELINE_SOURCE == \"merge_request_event\""
			variables: {
				OPENAI_RESPONSES_PROXY_COMMIT_BASE:   "$CI_MERGE_REQUEST_DIFF_BASE_SHA"
				OPENAI_RESPONSES_PROXY_COMMIT_TARGET: "refs/heads/$CI_MERGE_REQUEST_TARGET_BRANCH_NAME"
			}
		}]
	}
	"commit-admission-accepted": #commitAdmission & {
		tags: #Platforms.linux.gitlab.protected
		rules: [{
			if: "$CI_COMMIT_TAG"
			variables: {
				OPENAI_RESPONSES_PROXY_COMMIT_BASE:   "$CI_COMMIT_SHA"
				OPENAI_RESPONSES_PROXY_COMMIT_TARGET: "refs/tags/$CI_COMMIT_TAG"
			}
		}, {
			if: "$CI_COMMIT_BRANCH == \"dev\" || $CI_COMMIT_BRANCH == \"main\""
			variables: {
				OPENAI_RESPONSES_PROXY_COMMIT_BASE:   "$CI_COMMIT_BEFORE_SHA"
				OPENAI_RESPONSES_PROXY_COMMIT_TARGET: "refs/heads/$CI_COMMIT_REF_NAME"
			}
		}]
	}
	stages: ["verify", "release"]
	variables: {
		DEBIAN_FRONTEND:                  "noninteractive"
		UV_PYTHON_FLOOR_IMAGE:            "ghcr.io/astral-sh/uv:0.13.8-python3.12-trixie-slim@sha256:ee8a8854267a3bd09ac0364420803fb74a069068b5fb279a2cf1df664f7273db"
		UV_PYTHON_LATEST_IMAGE:           "ghcr.io/astral-sh/uv:0.13.8-python3.14-trixie-slim@sha256:81a1340fea1e72ad4a8e9a016128b0c61041cf182ea49fd788023e52842bc147"
		UV_CACHE_DIR:                     _uvCache.UV_CACHE_DIR
		UV_PYTHON_INSTALL_DIR:            _uvCache.UV_PYTHON_INSTALL_DIR
		OPENAI_RESPONSES_PROXY_CI_TARGET: "linux-arm64"
	}
	// Every job's uv cache lies in the project directory, which the job cache keeps.
	_uvCache: {
		UV_CACHE_DIR:          "$CI_PROJECT_DIR/.cache/uv"
		UV_PYTHON_INSTALL_DIR: "$CI_PROJECT_DIR/.cache/uv/python"
	}
	default: {
		image: name: "$UV_PYTHON_LATEST_IMAGE"
		tags: #Platforms.linux.gitlab.protected
		cache: {
			key: "uv-$OPENAI_RESPONSES_PROXY_CI_TARGET"
			paths: [".cache/uv/"]
		}
	}

	// Each role selects its runners from the platform declaration: review for merge requests and
	// protected for dev.
	#productRoles: [{
		suffix: ""
		runner: "review"
		rules: [{if: "$CI_PIPELINE_SOURCE == \"merge_request_event\" && $CI_MERGE_REQUEST_TARGET_BRANCH_NAME == \"dev\""}]
	}, {
		suffix: "-accepted"
		runner: "protected"
		rules: [{if: "$CI_COMMIT_BRANCH == \"dev\""}]
	}]

	// Every bootstrapped job installs binutils: PyInstaller needs objdump wherever a session packs the product.
	#systemBootstrap: [
		"uv tree --frozen --offline --universal --depth 0",
		"uv --version",
		"apt-get update -qq",
		"apt-get install -qq -y --no-install-recommends git openssh-client binutils",
	]
	#qualityBootstrap: list.Concat([#systemBootstrap, [
		"git fetch --tags --force --prune --prune-tags origin",
		"uv sync --locked --group quality --python python --no-python-downloads",
	]])

	// The Python compatibility job's own steps: GitLab's Linux job runs them after the system
	// bootstrap, and its macOS job runs each through the locked Mise.
	#compatibilitySteps: {
		before_script: [
			"git fetch --tags --force --prune --prune-tags origin",
			"uv sync --locked --group quality --python python --no-python-downloads",
			"uv python install --no-bin $PYTHON_VERSION",
		]
		script: [
			"python --version",
			"uv run --locked --no-sync --python python --no-python-downloads nox -s \"\(#VerificationJobs.compatibility.session)-$PYTHON_VERSION\"",
		]
	}

	#productJobs: {
		"source-and-governance": {
			stage: "verify"
			image: {
				name: #Toolchains.gitlabMiseImage
				entrypoint: [""]
			}
			variables: _miseData & {
				GIT_DEPTH:         "0"
				MISE_ENABLE_TOOLS: #Toolchains.quality
			}
			cache: [default.cache, #miseInstalls & {_set: "quality"}]
			before_script: [
				"mise install --locked",
				"mise exec --locked -- npm ci --ignore-scripts",
				"mise exec --locked -- npm audit signatures",
				"git fetch --tags --force --prune --prune-tags origin",
				"mise exec --locked -- uv sync --locked --group quality --python python --no-python-downloads",
			]
			script: [
				"mise exec --locked -- uv run --locked --no-sync --python python --no-python-downloads python -m \(#VerificationJobs.governance.module)",
			]
		}
		"verify-python": {
			stage: "verify"
			parallel: matrix: [{PYTHON_VERSION: #RuntimeMatrix.python}]
			variables: GIT_DEPTH: "0"
			before_script: list.Concat([#systemBootstrap, #compatibilitySteps.before_script])
			script: #compatibilitySteps.script
		}
		"verify-python-quality": {
			stage: "verify"
			image: name:          "$UV_PYTHON_FLOOR_IMAGE"
			variables: GIT_DEPTH: "0"
			before_script: list.Concat([#systemBootstrap, [
				"git fetch --tags --force --prune --prune-tags origin",
			]])
			script: [
				"uv sync --locked --group quality --python python --no-python-downloads",
				"uv run --locked --no-sync --python python --no-python-downloads nox -s \(#VerificationJobs.quality.session) \(#VerificationJobs.coverage.session)",
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

	// The macOS Python job that GitHub runs as well: a shell runner reads no image and has no
	// apt-get, so the job inherits neither the Linux default nor the global variables. The runner
	// supplies Mise, which the repository's min_version bounds below; the job records which Mise ran,
	// keeps Mise's installs, cache and state inside the project directory, cached per target and
	// interpreter, and runs every step through the locked Mise. A failure fails the pipeline.
	#compatibilityPlatformJob: {
		stage:   "verify"
		timeout: "\(#VerificationJobs.compatibility.timeoutMinutes)m"
		inherit: {
			default:   false
			variables: false
		}
		parallel: matrix: [{PYTHON_VERSION: #RuntimeMatrix.python}]
		variables: #RepositoryUvEnvironment & _uvCache & _miseData & {
			GIT_DEPTH:                        "0"
			MISE_ENABLE_TOOLS:                #Toolchains.compatibility
			OPENAI_RESPONSES_PROXY_CI_TARGET: #CompatibilityPlatform.release
		}
		tags: [...string]
		rules: [...{...}]
		cache: {
			key: "\(default.cache.key)-$PYTHON_VERSION"
			paths: list.Concat([default.cache.paths, [".cache/mise/"]])
		}
		before_script: list.Concat([[
			"mise --version",
			"mise install --locked",
		], [for command in #compatibilitySteps.before_script {"mise exec --locked -- \(command)"}]])
		script: [for command in #compatibilitySteps.script {"mise exec --locked -- \(command)"}]
	}

	// A Linux job in the Mise image keeps its installs in a cache keyed by the lock, one per tool set.
	// A locked install downloads the archive and, where the lock records provenance, asks GitHub
	// without a token for its attestation; from the cache, each runner does both once per lock.
	#miseInstalls: {
		_set: string
		key: {
			files: [".config/mise/mise.lock"]
			prefix: "mise-\(_set)-$OPENAI_RESPONSES_PROXY_CI_TARGET"
		}
		paths: [".cache/mise/data/"]
	}

	// Mise's installs, download cache and state lie in the project directory, which the job cache
	// keeps, so no tool comes from or goes to the runner account's home.
	_miseData: {
		MISE_DATA_DIR:  "$CI_PROJECT_DIR/.cache/mise/data"
		MISE_CACHE_DIR: "$CI_PROJECT_DIR/.cache/mise/cache"
		MISE_STATE_DIR: "$CI_PROJECT_DIR/.cache/mise/state"
	}
	for role in #productRoles {
		for id, job in #productJobs {
			"\(id)\(role.suffix)": job & {
				tags:      #Platforms.linux.gitlab[role.runner]
				rules:     role.rules
				variables: #RepositoryUvEnvironment
			}
		}
		"verify-python-macos\(role.suffix)": #compatibilityPlatformJob & {
			tags:  #CompatibilityPlatform.gitlab[role.runner]
			rules: role.rules
		}
	}
	"verify-accepted-source": {
		stage: "verify"
		rules: [{if: "$CI_COMMIT_BRANCH == \"dev\" || $CI_COMMIT_BRANCH == \"main\""}]
		variables: #RepositoryUvEnvironment & {GIT_DEPTH: "0"}
		before_script: #qualityBootstrap
		script: [
			"uv run --locked --no-sync --python python --no-python-downloads python -m \(#VerificationJobs.metadata.module)",
			"uv run --locked --no-sync --python python --no-python-downloads python -m tools.quality.repository",
		]
	}
	"verify-promotion": {
		stage: "verify"
		tags:  #Platforms.linux.gitlab.review
		rules: [{
			if: "$CI_PIPELINE_SOURCE == \"merge_request_event\" && $CI_MERGE_REQUEST_SOURCE_BRANCH_NAME == \"dev\" && $CI_MERGE_REQUEST_TARGET_BRANCH_NAME == \"main\""
		}]
		variables: #RepositoryUvEnvironment & {GIT_DEPTH: "0"}
		before_script: list.Concat([#systemBootstrap, [
			"git fetch origin main dev --tags --force --prune --prune-tags",
			"uv sync --locked --group quality --python python --no-python-downloads",
		]])
		script: [
			"git merge-base --is-ancestor origin/main \"$CI_COMMIT_SHA\"",
			"uv run --locked --no-sync --python python --no-python-downloads python -m \(#VerificationJobs.metadata.module)",
			"uv run --locked --no-sync --python python --no-python-downloads python -m tools.quality.repository",
		]
	}
	"verify-release-tag": {
		stage: "release"
		rules: [{if: "$CI_COMMIT_TAG"}]
		variables: #RepositoryUvEnvironment & {GIT_DEPTH: "0"}
		before_script: list.Concat([#qualityBootstrap, [
			"test -f \"${OPENAI_RESPONSES_PROXY_GITLAB_TAG_TRUST:-}\"",
		]])
		script: [
			"uv run --locked --no-sync --python python --no-python-downloads python -m \(#VerificationJobs.metadata.module) --tag \"$CI_COMMIT_TAG\"",
			"uv run --locked --no-sync --python python --no-python-downloads python -m tools.release.tag_signature . \"$CI_COMMIT_TAG\" \"$OPENAI_RESPONSES_PROXY_GITLAB_TAG_TRUST\"",
			"uv run --locked --no-sync --python python --no-python-downloads python -m tools.quality.repository",
		]
	}
}
