package ci

// Source and compatibility jobs share the provider-neutral verification contract.
#SourceJobs: {
	"commit-admission": {
		name:              "Native commit admission"
		if:                "github.event_name == 'pull_request' || github.event_name == 'push'"
		"runs-on":         #Platforms.linux.github
		"timeout-minutes": 15
		env: {
			ETHOS_COMMIT_ALLOWED_SIGNERS: "${{ vars.ETHOS_COMMIT_ALLOWED_SIGNERS }}"
			MISE_ENABLE_TOOLS:            #Toolchains.ethos
		}
		steps: [{
			name: "Require external public commit trust"
			run:  #CommitTrustPreflight
		}, {
			uses: #Toolchains.githubActions.checkout
			with: {
				"fetch-depth": 0
				"fetch-tags":  true
				ref:           #Conditions.productSHA
			}
		}, {
			uses: #Toolchains.githubActions.mise
			with: {
				version: #Toolchains.miseVersion
				install: true
				cache:   true
				// The installed set keys the cache, apart from the jobs that install the other tools.
				install_args: #Toolchains.ethos
			}
		}, {
			name: "Refresh native ETHOS Linux prerequisite metadata"
			run:  "sudo apt-get update -qq"
		}, {
			name: "Install native ETHOS Linux prerequisites"
			run:  "sudo \(#EthosRelease.linuxInstall)"
		}, {
			name: "Bind the external public commit trust"
			run:  #EthosRelease.bindTrust
		}, {
			name: "Admit the exact introduced commit range"
			env:  #CommitEvent
			run:  #CommitAdmission
		}]
	}
	"python-matrix": {
		name:              "Resolve supported Python versions"
		if:                #Conditions.nativeProof
		"runs-on":         #Platforms.linux.github
		"timeout-minutes": 5
		outputs: {
			versions:              "${{ steps.versions.outputs.value }}"
			floor:                 "${{ steps.versions.outputs.floor }}"
			latest:                "${{ steps.versions.outputs.latest }}"
			release:               "${{ steps.versions.outputs.release }}"
			"linux-release-image": "${{ steps.versions.outputs.linux-release-image }}"
		}
		steps: [{
			uses: #Toolchains.githubActions.checkout
			with: ref: #Conditions.productSHA
		}, {
			#UvSetup
		}, {
			name: "Install the locked matrix tool environment"
			run:  "uv sync --locked --all-groups"
		}, {
			name: "Read the repository Python matrix"
			id:   "versions"
			run:  "uv run --locked --no-sync python -m tools.quality.python_matrix"
		}]
	}
	"source-and-governance": {
		name:              "Source and governance"
		if:                #Conditions.productProof
		"runs-on":         #Platforms.linux.github
		"timeout-minutes": 10
		env: MISE_ENABLE_TOOLS: #Toolchains.quality
		steps: [{
			uses: #Toolchains.githubActions.checkout
			with: {
				"fetch-depth": 0
				"fetch-tags":  true
				ref:           #Conditions.productSHA
			}
		}, {
			uses: #Toolchains.githubActions.python
			with: "python-version-file": ".config/mise/python-release"
		}, {
			#UvSetup
		}, {
			uses: #Toolchains.githubActions.mise
			with: {
				version: #Toolchains.miseVersion
				install: true
				cache:   true
			}
		}, {
			name: "Install and audit locked Node repository tools"
			run:  "npm ci --ignore-scripts"
		}, {
			name: "Audit locked Node repository tool signatures"
			run:  "npm audit signatures"
		}, {
			name: "Install the complete locked tool environment"
			run:  "uv sync --locked --all-groups"
		}, {
			name: "Confirm source identity and repository governance"
			run:  "uv run --locked --no-sync python -m \(#VerificationJobs.governance.module)"
		}]
	}
	python: {
		name:              "Python ${{ matrix.python-version }}"
		if:                #Conditions.productProof
		needs:             "python-matrix"
		"runs-on":         #CompatibilityPlatform.github
		"timeout-minutes": #VerificationJobs.compatibility.timeoutMinutes
		strategy: {
			"fail-fast": false
			matrix: "python-version": "${{ fromJSON(needs.python-matrix.outputs.versions) }}"
		}
		steps: [{
			uses: #Toolchains.githubActions.checkout
			with: {
				"fetch-depth": 0
				"fetch-tags":  true
				ref:           #Conditions.productSHA
			}
		}, {
			uses: #Toolchains.githubActions.python
			with: "python-version": "${{ matrix.python-version }}"
		}, {
			#UvSetup
		}, {
			name: "Compile and test"
			run:  "uv run --locked --group quality nox -s \"\(#VerificationJobs.compatibility.session)-${{ matrix.python-version }}\""
		}]
	}
	"accepted-source": {
		name:              "Accepted source"
		if:                "github.event_name == 'push' && github.ref_type == 'branch'"
		"runs-on":         #Platforms.linux.github
		"timeout-minutes": 10
		steps: [{
			uses: #Toolchains.githubActions.checkout
			with: {
				"fetch-depth": 0
				"fetch-tags":  true
			}
		}, {
			uses: #Toolchains.githubActions.python
			with: "python-version-file": ".config/mise/python-release"
		}, {
			#UvSetup
		}, {
			name: "Confirm accepted source and metadata"
			run:  "uv sync --locked --all-groups"
		}, {
			name: "Verify accepted release metadata"
			run:  "uv run --locked --no-sync python -m \(#VerificationJobs.metadata.module)"
		}, {
			name: "Verify accepted repository structure"
			run:  "uv run --locked --no-sync python -m tools.quality.repository"
		}]
	}
	promotion: {
		name:              "Promote dev to main"
		if:                "github.event_name == 'pull_request' && github.base_ref == 'main' && github.head_ref == 'dev'"
		"runs-on":         #Platforms.linux.github
		"timeout-minutes": 10
		steps: [{
			uses: #Toolchains.githubActions.checkout
			with: {
				"fetch-depth": 0
				"fetch-tags":  true
				ref:           "${{ github.event.pull_request.head.sha }}"
			}
		}, {
			uses: #Toolchains.githubActions.python
			with: "python-version-file": ".config/mise/python-release"
		}, {
			#UvSetup
		}, {
			name: "Prove exact dev-to-main promotion"
			run:  "git fetch origin main dev --tags --force --prune --prune-tags"
		}, {
			name: "Verify promotion ancestry"
			run:  "git merge-base --is-ancestor origin/main \"${{ github.event.pull_request.head.sha }}\""
		}, {
			name: "Install the complete locked tool environment"
			run:  "uv sync --locked --all-groups"
		}, {
			name: "Verify promoted release metadata"
			run:  "uv run --locked --no-sync python -m \(#VerificationJobs.metadata.module)"
		}, {
			name: "Verify promoted repository structure"
			run:  "uv run --locked --no-sync python -m tools.quality.repository"
		}]
	}
	"python-quality": {
		name:              "Python quality"
		if:                #Conditions.productProof
		needs:             "python-matrix"
		"runs-on":         #Platforms.linux.github
		"timeout-minutes": 15
		env: MISE_ENABLE_TOOLS: #Toolchains.quality
		steps: [{
			uses: #Toolchains.githubActions.checkout
			with: {
				"fetch-depth": 0
				"fetch-tags":  true
				ref:           #Conditions.productSHA
			}
		}, {
			uses: #Toolchains.githubActions.python
			with: "python-version": "${{ needs.python-matrix.outputs.floor }}"
		}, {
			#UvSetup
		}, {
			uses: #Toolchains.githubActions.mise
			with: {
				version: #Toolchains.miseVersion
				install: true
				cache:   true
			}
		}, {
			name: "Verify lint, format, types, structure, docstrings, and product branch coverage"
			run:  "uv run --locked --group quality nox -s \(#VerificationJobs.quality.session) \(#VerificationJobs.coverage.session)"
		}]
	}
	performance: {
		name:              "Performance"
		if:                #Conditions.productProof
		needs:             "python-matrix"
		"runs-on":         #Platforms.linux.github
		"timeout-minutes": 10
		steps: [{
			uses: #Toolchains.githubActions.checkout
			with: {
				"fetch-depth": 0
				"fetch-tags":  true
				ref:           #Conditions.productSHA
			}
		}, {
			uses: #Toolchains.githubActions.python
			with: "python-version": "${{ needs.python-matrix.outputs.release }}"
		}, {
			#UvSetup
		}, {
			name: "Measure deterministic product overhead"
			run:  "uv run --locked --group quality nox -s \(#VerificationJobs.performance.session) -- \"$RUNNER_TEMP/performance\""
		}, {
			uses: #Toolchains.githubActions.upload
			with: {
				name:                "performance"
				path:                "${{ runner.temp }}/performance/*.json"
				"if-no-files-found": "error"
				"retention-days":    7
			}
		}]
	}
}
