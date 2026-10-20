package ci

import "strings"

// Native executors and every peer consume this one verification contract.
#RuntimeMatrix: python: ["3.12", "3.13", "3.14"]

// Nox and GitHub consume this tracked text projection of the same matrix.
#PythonVersions: strings.Join(#RuntimeMatrix.python, "\n")

#VerificationJobs: {
	quality: session:       "quality"
	compatibility: session: "tests"
	performance: session:   "performance"
	governance: module:     "tools.quality.governance"
	metadata: module:       "tools.release.metadata"
}

// Project jobs bind one native uv configuration; external suppliers keep theirs.
#RepositoryUvEnvironment: {
	UV_CONFIG_FILE: ".config/mise/uv.toml"
	UV_NO_CONFIG:   "false"
	[string]:       string
}

// A nonblank value differs from its leading whitespace; no tools run before supply.
#CommitTrustPreflight: #"test "${ETHOS_COMMIT_ALLOWED_SIGNERS:-}" != "${ETHOS_COMMIT_ALLOWED_SIGNERS+${ETHOS_COMMIT_ALLOWED_SIGNERS%%[![:space:]]*}}""#

// The invoking ETHOS identity selects one native source build on each runner.
#EthosSupply: {
	source: "https://github.com/HengYangDS/ethos.git"
	commit: "f726c249ab4e362c84835aabc87605a434b66919"
	tree:   "951897502bb194c04ad14ab14fb06fed69cc50e6"
	root:   "build/runtime/ethos-source"
	linuxPackages: ["curl", "ca-certificates", "libatomic1"]
	linuxInstall: "apt-get install -qq -y --no-install-recommends " + strings.Join(#EthosSupply.linuxPackages, " ")
	checkout: [
		"git clone --filter=blob:none --no-checkout \(#EthosSupply.source) \(#EthosSupply.root)",
		"git -C \(#EthosSupply.root) checkout --detach \(#EthosSupply.commit)",
		"git -C \(#EthosSupply.root) diff --exit-code \(#EthosSupply.tree) HEAD --",
	]
	prepare: [
		"uv --directory \(#EthosSupply.root) sync --locked --no-install-project --group dev",
		"uv --directory \(#EthosSupply.root) run --locked --no-sync python -B -m tools.ci.toolchain.native --root . --mise node vale",
		"uv --directory \(#EthosSupply.root) run --locked --no-sync python -B -m tools.ci.toolchain.npm --seed ci --ignore-scripts",
		"uv --directory \(#EthosSupply.root) sync --locked --group dev --no-editable",
		"uv --directory \(#EthosSupply.root) run --locked --no-sync python -B -I -m ethos.cli --version",
		"uv --directory \(#EthosSupply.root) run --locked --no-sync python -B -c 'import os; from pathlib import Path; from tools.ci.toolchain.environment import bind_commit_trust_material; bind_commit_trust_material(Path(os.environ[\"OPENAI_RESPONSES_PROXY_COMMIT_ROOT\"]), os.environ[\"ETHOS_COMMIT_ALLOWED_SIGNERS\"])'",
	]
}

// GitHub's native Bash executor supplies UID; no runner identity is hard-coded.
#LinuxUserManagerSteps: [{
	name:  "Start the runner user systemd manager"
	shell: "bash"
	run:   "sudo systemctl start \"user@${UID}.service\""
}, {
	name:  "Bind the runner user systemd environment"
	shell: "bash"
	run:   "printf '%s\\n' \"XDG_RUNTIME_DIR=/run/user/${UID}\" \"DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/${UID}/bus\" >> \"$GITHUB_ENV\""
}]

#CommitAdmission: """
    \(#EthosSupply.root)/.venv/bin/python -B -I -m ethos.cli hook commit-range --target-ref "$OPENAI_RESPONSES_PROXY_COMMIT_TARGET" --proposed-head "$OPENAI_RESPONSES_PROXY_COMMIT_HEAD" --remote-head "$OPENAI_RESPONSES_PROXY_COMMIT_BASE" --remote "$OPENAI_RESPONSES_PROXY_COMMIT_PEER" --trusted-baseline refs/remotes/origin/dev --root . --json
    """
