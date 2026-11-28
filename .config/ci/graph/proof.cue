package ci

import "strings"

// Native executors and every peer consume this one verification contract.
#RuntimeMatrix: python: ["3.12", "3.13", "3.14"]

// Nox and GitHub consume this tracked text projection of the same matrix.
#PythonVersions: strings.Join(#RuntimeMatrix.python, "\n")

#VerificationJobs: {
	quality: session:  "quality"
	coverage: session: "coverage"
	// Every Python compatibility job runs this session; the macOS jobs of both Forges also bind
	// its time bound, and GitLab's Linux job keeps the runner's own.
	compatibility: {
		session:        "tests"
		timeoutMinutes: 15
	}
	performance: session: "performance"
	governance: module:   "tools.quality.governance"
	metadata: module:     "tools.release.metadata"
}

// Project jobs bind one native uv configuration.
#RepositoryUvEnvironment: {
	UV_CONFIG_FILE: ".config/mise/uv.toml"
	UV_NO_CONFIG:   "false"
	[string]:       string
}

// A nonblank value differs from its leading whitespace; no tools run before supply.
#CommitTrustPreflight: #"test "${ETHOS_COMMIT_ALLOWED_SIGNERS:-}" != "${ETHOS_COMMIT_ALLOWED_SIGNERS+${ETHOS_COMMIT_ALLOWED_SIGNERS%%[![:space:]]*}}""#

// Commit admission runs the ETHOS release that the Mise configuration pins, installed from the lock.
#EthosRelease: {
	// The packages that ETHOS's Linux distribution expects beside its own bundled runtime.
	linuxPackages: ["git", "libatomic1", "openssh-client"]
	linuxInstall: "apt-get install -qq -y --no-install-recommends " + strings.Join(linuxPackages, " ")
	// ETHOS binds the operator's public commit trust before it admits a range.
	bindTrust: "mise exec --locked -- ethos hook bind-trust --apply --root . --json"
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
	mise exec --locked -- ethos hook commit-range --target-ref "$OPENAI_RESPONSES_PROXY_COMMIT_TARGET" --proposed-head "$OPENAI_RESPONSES_PROXY_COMMIT_HEAD" --remote-head "$OPENAI_RESPONSES_PROXY_COMMIT_BASE" --remote "$OPENAI_RESPONSES_PROXY_COMMIT_PEER" --trusted-baseline refs/remotes/origin/dev --root . --json
	"""
