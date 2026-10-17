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

// Presence is checked before supply; ETHOS remains the public-key trust owner.
#CommitTrustPreflight: """
	case "${ETHOS_COMMIT_ALLOWED_SIGNERS:-}" in
	  *[![:space:]]*) ;;
	  *) printf '%s\n' 'ETHOS_COMMIT_ALLOWED_SIGNERS must contain operator-managed public commit trust' >&2; exit 1 ;;
	esac
	"""

// The invoking ETHOS identity selects one native source build on each runner.
#EthosSupply: {
	source: "https://github.com/HengYangDS/ethos.git"
	commit: "efe866cdaaf7e9a5f971ed706343d0b2d1294cc6"
	tree:   "3583262bf6c50731af5f9d9716056cd0e00d857d"
	root:   "build/runtime/ethos-source"
	linuxPackages: ["curl", "ca-certificates", "libatomic1"]
	linuxInstall: "apt-get install -qq -y --no-install-recommends " + strings.Join(#EthosSupply.linuxPackages, " ")
	checkout:     """
        git clone --filter=blob:none --no-checkout \(#EthosSupply.source) \(#EthosSupply.root)
        git -C \(#EthosSupply.root) checkout --detach \(#EthosSupply.commit)
        test "$(git -C \(#EthosSupply.root) rev-parse HEAD)" = \(#EthosSupply.commit)
        test "$(git -C \(#EthosSupply.root) rev-parse 'HEAD^{tree}')" = \(#EthosSupply.tree)
        """
	prepare: """
		uv sync --locked --no-install-project --group dev
		PYTHONPATH=src .venv/bin/python -B -c 'from pathlib import Path; from tools.ci.toolchain.native import prepare_mise, prepare; root = Path.cwd(); prepare(root, "node", mise=prepare_mise(root))'
		PYTHONPATH=src .venv/bin/python -B -c 'import os, subprocess; from pathlib import Path; from ethos.adapters.repo.runtime.materialization.node.supply import NodeRuntime, NodeSupply; root = Path.cwd(); native = NodeRuntime.read(NodeSupply.read(root).installed_root(root), windows=os.name == "nt"); subprocess.run([str(native.executable), str(native.npm), "ci", "--ignore-scripts"], env=dict(os.environ, PATH=str(native.executable.parent) + os.pathsep + os.environ["PATH"], npm_config_audit="false", npm_config_fund="false", npm_config_update_notifier="false"), stdin=subprocess.DEVNULL, timeout=120, check=True)'
		.venv/bin/python -B -I -m uv sync --locked --group dev --no-editable
		.venv/bin/python -B -I -m ethos.cli --version
		.venv/bin/python -B -c 'import os; from pathlib import Path; from tools.ci.toolchain.environment import bind_commit_trust_material; bind_commit_trust_material(Path(os.environ["OPENAI_RESPONSES_PROXY_COMMIT_ROOT"]), os.environ["ETHOS_COMMIT_ALLOWED_SIGNERS"])'
		"""
}

#CommitAdmission: """
    \(#EthosSupply.root)/.venv/bin/python -B -I -m ethos.cli hook commit-range --target-ref "$OPENAI_RESPONSES_PROXY_COMMIT_TARGET" --proposed-head "$OPENAI_RESPONSES_PROXY_COMMIT_HEAD" --remote-head "$OPENAI_RESPONSES_PROXY_COMMIT_BASE" --remote "$OPENAI_RESPONSES_PROXY_COMMIT_PEER" --trusted-baseline refs/remotes/origin/dev --root . --json
    """
