package ci

// Source and compatibility jobs share the provider-neutral verification contract.
#SourceJobs: {
	"commit-admission": {
		name:              "Native commit admission"
		if:                "github.event_name == 'pull_request' || github.event_name == 'push'"
		"runs-on":         "ubuntu-24.04"
		"timeout-minutes": 15
		env: {
			ETHOS_COMMIT_ALLOWED_SIGNERS:       "${{ vars.ETHOS_COMMIT_ALLOWED_SIGNERS }}"
			OPENAI_RESPONSES_PROXY_COMMIT_ROOT: "${{ github.workspace }}"
		}
		steps: [{
			uses: #Toolchains.githubActions.checkout
			with: {
				"fetch-depth": 0
				"fetch-tags":  true
				ref:           #Conditions.productSHA
			}
		}, {
			#UvSetup
		}, {
			name: "Install native ETHOS Linux prerequisites"
			run:  "sudo apt-get update -qq\nsudo \(#EthosSupply.linuxInstall)"
		}, {
			name: "Select the exact native ETHOS source"
			run:  #EthosSupply.checkout
		}, {
			name:                "Prepare locked native ETHOS supply and external commit trust"
			"working-directory": #EthosSupply.root
			run:                 #EthosSupply.prepare
		}, {
			name: "Admit the exact introduced commit range"
			env:  #CommitEvent
			run:  #CommitAdmission
		}]
	}
	"python-matrix": {
		name:              "Resolve supported Python versions"
		if:                #Conditions.nativeProof
		"runs-on":         "ubuntu-24.04"
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
		"runs-on":         "ubuntu-24.04"
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
			with: "python-version-file": ".config/mise/python-release"
		}, {
			#UvSetup
		}, {
			uses: #Toolchains.githubActions.mise
			with: {
				install: true
				cache:   true
			}
		}, {
			name: "Install and audit locked Node repository tools"
			run: """
				npm ci --ignore-scripts
				npm audit signatures
				"""
		}, {
			name: "Confirm source identity and repository governance"
			run:  """
					uv sync --locked --all-groups
					uv run --locked --no-sync python -m \(#VerificationJobs.governance.module) --online-links
					"""
		}]
	}
	python: {
		name:              "Python ${{ matrix.python-version }}"
		if:                #Conditions.productProof
		needs:             "python-matrix"
		"runs-on":         "macos-26"
		"timeout-minutes": 15
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
	"python-windows": {
		name:              "Python ${{ matrix.python-version }} (Windows)"
		if:                #Conditions.productProof
		needs:             "python-matrix"
		"runs-on":         "windows-2025"
		"timeout-minutes": 15
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
		"runs-on":         "ubuntu-24.04"
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
			run:  """
					uv sync --locked --all-groups
					uv run --locked --no-sync python -m \(#VerificationJobs.metadata.module)
					uv run --locked --no-sync python -m tools.quality.repository

					"""
		}]
	}
	promotion: {
		name:              "Promote dev to main"
		if:                "github.event_name == 'pull_request' && github.base_ref == 'main' && github.head_ref == 'dev'"
		"runs-on":         "ubuntu-24.04"
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
			run:  """
					git fetch origin main dev --tags --force --prune --prune-tags
					git merge-base --is-ancestor origin/main "${{ github.event.pull_request.head.sha }}"
					uv sync --locked --all-groups
					uv run --locked --no-sync python -m \(#VerificationJobs.metadata.module)
					uv run --locked --no-sync python -m tools.quality.repository
					"""
		}]
	}
	"python-quality": {
		name:              "Python quality"
		if:                #Conditions.productProof
		needs:             "python-matrix"
		"runs-on":         "ubuntu-24.04"
		"timeout-minutes": 15
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
				install: true
				cache:   true
			}
		}, {
			name: "Verify lint, format, types, structure, docstrings, and product branch coverage"
			run:  "uv run --locked --group quality nox -s \(#VerificationJobs.quality.session)"
		}]
	}
	performance: {
		name:              "Performance"
		if:                #Conditions.productProof
		needs:             "python-matrix"
		"runs-on":         "ubuntu-24.04"
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
