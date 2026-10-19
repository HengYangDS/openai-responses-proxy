package ci

import "list"

// Platform builders and service lifecycle remain distinct acceptance jobs.
#NativeJobs: {
	"native-assets": {
		name:              "Native asset (${{ matrix.platform }})"
		if:                #Conditions.nativeProof
		needs:             "python-matrix"
		"runs-on":         "${{ matrix.runner }}"
		"timeout-minutes": 20
		strategy: {
			"fail-fast": false
			matrix: include: [{
				platform: "macos-arm64"
				runner:   "macos-26"
			}, {
				platform: "windows-x86_64"
				runner:   "windows-2025"
			}]
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
			with: "python-version": "${{ needs.python-matrix.outputs.release }}"
		}, {
			#UvSetup
		}, {
			name: "Install the locked release tool environment"
			run:  "uv sync --locked --group quality"
		}, {
			name: "Build and accept the native release asset"
			run:  "uv run --locked --no-sync nox -s release -- \"${{ runner.temp }}/native-assets\""
		}, {
			uses: #Toolchains.githubActions.upload
			with: {
				name:                "native-${{ matrix.platform }}"
				path:                "${{ runner.temp }}/native-assets"
				"if-no-files-found": "error"
				"retention-days":    7
			}
		}]
	}
	"native-linux": {
		name:              "Native asset (linux-x86_64)"
		if:                #Conditions.nativeProof
		needs:             "python-matrix"
		"runs-on":         "ubuntu-24.04"
		container:         "${{ needs.python-matrix.outputs.linux-release-image }}"
		"timeout-minutes": 20
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
			name: "Materialize the canonical release source root"
			id:   "release-source"
			run:  "git -c safe.directory=\"$GITHUB_WORKSPACE\" worktree add --detach /workspace HEAD"
		}, {
			name:                "Install the locked release tool environment"
			"working-directory": "/workspace"
			run:                 "uv sync --locked --group quality --python python --no-python-downloads"
		}, {
			name:                "Build the native release asset"
			"working-directory": "/workspace"
			run:                 "uv run --locked --no-sync --python python --no-python-downloads nox -s release_asset -- \"$GITHUB_WORKSPACE/.release-assets/linux-x86_64\""
		}, {
			name: "Retire the canonical release source root"
			if:   "always() && steps.release-source.outcome == 'success'"
			run:  "git -c safe.directory=\"$GITHUB_WORKSPACE\" worktree remove --force /workspace"
		}, {
			uses: #Toolchains.githubActions.upload
			with: {
				name:                "native-linux-x86_64"
				path:                "${{ github.workspace }}/.release-assets/linux-x86_64"
				"if-no-files-found": "error"
				"retention-days":    7
			}
		}]
	}
	"native-linux-lifecycle": {
		name: "Native lifecycle (linux-x86_64)"
		if:   #Conditions.nativeProof
		needs: ["python-matrix", "native-linux"]
		"runs-on":         "ubuntu-24.04"
		"timeout-minutes": 10
		steps: list.Concat([[{
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
			uses: #Toolchains.githubActions.download
			with: {
				name: "native-linux-x86_64"
				path: "${{ runner.temp }}/native-linux"
			}
		}, {
			name: "Prepare the Linux executable destination"
			run:  "install -d \"$RUNNER_TEMP/native-linux/runtime\""
		}, {
			name: "Materialize the Linux executable"
			run:  "tar -xzf \"$RUNNER_TEMP\"/native-linux/openai-responses-proxy-*-linux-x86_64.tar.gz --strip-components=1 -C \"$RUNNER_TEMP/native-linux/runtime\""
		}, {
			name: "Install the locked release tool environment"
			run:  "uv sync --locked --group quality"
		}], #LinuxUserManagerSteps, [{
			name: "Prove the native Linux service lifecycle"
			env: {
				OPENAI_RESPONSES_PROXY_NATIVE_EXECUTABLE: "${{ runner.temp }}/native-linux/runtime/bin/openai-responses-proxy"
				OPENAI_RESPONSES_PROXY_NATIVE_BUNDLE:     "${{ runner.temp }}/native-linux/runtime/bin"
			}
			run: "uv run --locked --no-sync python -m pytest -q --rootdir . -c .config/checks/pytest/pytest.toml tests/release/lifecycle"
		}]])
	}
}
