package ci

// Tagged assets and published predecessors have separate admission boundaries.
#ReleaseJobs: {
	"tag-metadata": {
		name:              "Tag metadata and governance"
		if:                #Conditions.tagPush
		needs:             "python-matrix"
		"runs-on":         "ubuntu-24.04"
		"timeout-minutes": 15
		steps: [{
			uses: #Toolchains.githubActions.checkout
			with: {
				"fetch-depth": 0
				"fetch-tags":  true
			}
		}, {
			uses: #Toolchains.githubActions.python
			with: "python-version": "${{ needs.python-matrix.outputs.latest }}"
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
			name: "Install the complete locked tool environment"
			run:  "uv sync --locked --all-groups"
		}, {
			name: "Verify exact release metadata"
			env:  #CommitEvent
			run:  """
					uv run --locked --no-sync python -m \(#VerificationJobs.metadata.module) --tag "$GITHUB_REF_NAME"
					uv run --locked --no-sync python -m tools.quality.repository
					"""
		}, {
			name: "Verify repository governance"
			run:  "uv run --locked --no-sync python -m pytest -q --rootdir . -c .config/checks/pytest/pytest.toml tests/quality/test_contract.py tests/forge/test_workflow_contracts.py tests/release/publication"
		}]
	}
	"release-compatibility": {
		name:              "Published release compatibility (${{ matrix.platform }})"
		if:                #Conditions.productProof
		needs:             "python-matrix"
		"runs-on":         "${{ matrix.runner }}"
		"timeout-minutes": 30
		strategy: {
			"fail-fast": false
			matrix: include: [{
				platform: "macos-arm64"
				runner:   "macos-26"
			}, {
				platform: "windows-x86_64"
				runner:   "windows-2025"
			}, {
				platform: "linux-x86_64"
				runner:   "ubuntu-24.04"
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
			name: "Resolve the exact published predecessor"
			env: GH_TOKEN: "${{ github.token }}"
			run: "uv run --locked --no-sync python -m tools.release.publication predecessor --repository \"${{ github.repository }}\" --candidate-version \"$(cat VERSION)\" --github-environment \"${{ github.env }}\""
		}, {
			name: "Download the published predecessor release"
			env: GH_TOKEN: "${{ github.token }}"
			run: "gh release download \"${{ env.OPENAI_RESPONSES_PROXY_PREVIOUS_RELEASE_TAG }}\" --pattern \"${{ env.OPENAI_RESPONSES_PROXY_PREVIOUS_RELEASE_PRODUCT }}-*-${{ matrix.platform }}.tar.gz\" --pattern \"${{ env.OPENAI_RESPONSES_PROXY_PREVIOUS_RELEASE_PRODUCT }}-${{ matrix.platform }}.manifest.json\" --pattern SHA256SUMS --pattern SHA256SUMS.sig --dir \"${{ runner.temp }}/previous-release\""
		}, {
			name: "Materialize the release trust anchor"
			env: {
				PREVIOUS_RELEASE_ASSET_TRUST: "${{ secrets.CODEX_RESPONSES_PROXY_RELEASE_ASSET_TRUST }}"
				RELEASE_ASSET_TRUST:          "${{ vars.OPENAI_RESPONSES_PROXY_RELEASE_ASSET_TRUST }}"
			}
			run: "python -c \"import os; from pathlib import Path; current = os.environ['RELEASE_ASSET_TRUST'].rstrip(); previous = os.environ['PREVIOUS_RELEASE_ASSET_TRUST'].rstrip() if os.environ['OPENAI_RESPONSES_PROXY_PREVIOUS_RELEASE_PRODUCT'] == 'codex-responses-proxy' else current; assert current and previous, 'release trust is unavailable'; Path(r'${{ runner.temp }}/previous-release-asset-trust').write_text(previous + '\\n', encoding='ascii'); Path(r'${{ runner.temp }}/release-asset-trust').write_text(current + '\\n', encoding='ascii')\""
		}, {
			name: "Bind the exact predecessor asset"
			run:  "python -c \"import glob, os; matches = glob.glob(r'${{ runner.temp }}/previous-release/${{ env.OPENAI_RESPONSES_PROXY_PREVIOUS_RELEASE_PRODUCT }}-*-${{ matrix.platform }}.tar.gz'); assert len(matches) == 1, matches; open(os.environ['GITHUB_ENV'], 'a', encoding='utf-8').write('OPENAI_RESPONSES_PROXY_PREVIOUS_RELEASE_ASSET=' + matches[0] + '\\n')\""
		}, {
			name: "Start the runner user systemd manager"
			if:   "matrix.platform == 'linux-x86_64'"
			run:  "user_id=$(id -u); runtime_dir=\"/run/user/${user_id}\"; sudo systemctl start \"user@${user_id}.service\"; printf '%s\\n' \"XDG_RUNTIME_DIR=${runtime_dir}\" \"DBUS_SESSION_BUS_ADDRESS=unix:path=${runtime_dir}/bus\" >> \"${GITHUB_ENV}\""
		}, {
			name: "Prove published predecessor upgrade and rollback"
			env: {
				OPENAI_RESPONSES_PROXY_PREVIOUS_RELEASE_TRUST_ANCHOR: "${{ runner.temp }}/previous-release-asset-trust"
				OPENAI_RESPONSES_PROXY_CURRENT_RELEASE_TRUST_ANCHOR:  "${{ runner.temp }}/release-asset-trust"
			}
			run: "uv run --locked --no-sync nox -s release_compatibility"
		}]
	}
	"release-assets": {
		name: "Release assets"
		if:   #Conditions.tagPush
		needs: ["python-matrix", "tag-metadata", "native-assets", "native-linux", "native-linux-lifecycle"]
		"runs-on":         "ubuntu-24.04"
		"timeout-minutes": 10
		steps: [{
			uses: #Toolchains.githubActions.checkout
		}, {
			uses: #Toolchains.githubActions.python
			with: "python-version": "${{ needs.python-matrix.outputs.latest }}"
		}, {
			#UvSetup
		}, {
			name: "Download native release assets"
			env: GH_TOKEN: "${{ github.token }}"
			run: "gh run download \"$GITHUB_RUN_ID\" --pattern 'native-*' --dir \"$RUNNER_TEMP/native\""
		}, {
			name: "Install the complete locked tool environment"
			run:  "uv sync --locked --all-groups"
		}, {
			name: "Materialize the protected release signing key"
			env: RELEASE_ASSET_SIGNING_KEY_TEXT: "${{ secrets.CODEX_RESPONSES_PROXY_RELEASE_ASSET_SIGNING_KEY }}"
			run: """
				install -m 600 /dev/null "$RUNNER_TEMP/release-asset-signing-key"
				printf '%s\\n' "$RELEASE_ASSET_SIGNING_KEY_TEXT" > "$RUNNER_TEMP/release-asset-signing-key"

				"""
		}, {
			name: "Assemble, sign, and verify the release set"
			env: {
				RELEASE_ASSET_SIGNING_KEY_PATH: "${{ runner.temp }}/release-asset-signing-key"
				RELEASE_ASSET_TRUST:            "${{ vars.OPENAI_RESPONSES_PROXY_RELEASE_ASSET_TRUST }}"
			}
			run: "uv run --locked --no-sync python -m tools.release.artifact assemble --input \"$RUNNER_TEMP/native/native-linux-x86_64\" --input \"$RUNNER_TEMP/native/native-macos-arm64\" --input \"$RUNNER_TEMP/native/native-windows-x86_64\" --output \"$RUNNER_TEMP/release-assets\" --sign"
		}, {
			uses: #Toolchains.githubActions.upload
			with: {
				name:                "release-assets"
				path:                "${{ runner.temp }}/release-assets"
				"if-no-files-found": "error"
				"retention-days":    7
			}
		}]
	}
	"published-release-compatibility": {
		name:              "Published release lifecycle (${{ matrix.platform }})"
		if:                #Conditions.publishedReleaseProof
		"runs-on":         "${{ matrix.runner }}"
		"timeout-minutes": 30
		strategy: {
			"fail-fast": false
			matrix: include: [{
				platform: "macos-arm64"
				runner:   "macos-26"
			}, {
				platform: "windows-x86_64"
				runner:   "windows-2025"
			}, {
				platform: "linux-x86_64"
				runner:   "ubuntu-24.04"
			}]
		}
		steps: [{
			uses: #Toolchains.githubActions.checkout
			with: {
				"fetch-depth": 0
				"fetch-tags":  true
				ref:           #Conditions.publishedReleaseCheckout
			}
		}, {
			uses: #Toolchains.githubActions.python
			with: "python-version-file": ".python-release"
		}, {
			#UvSetup
		}, {
			name: "Install the locked release tool environment"
			run:  "uv sync --locked --group quality"
		}, {
			name: "Verify the selected release uses this product runtime"
			run:  "git diff --exit-code \"${{ github.event.release.tag_name || inputs.release_tag }}^{commit}\" HEAD -- VERSION pyproject.toml uv.lock src/openai_responses_proxy"
		}, {
			name: "Resolve the exact published predecessor"
			env: GH_TOKEN: "${{ github.token }}"
			run: "uv run --locked --no-sync python -m tools.release.publication predecessor --repository \"${{ github.repository }}\" --candidate-tag \"${{ github.event.release.tag_name || inputs.release_tag }}\" --github-environment \"${{ github.env }}\""
		}, {
			name: "Download the published current release"
			env: GH_TOKEN: "${{ github.token }}"
			run: "gh release download \"${{ github.event.release.tag_name || inputs.release_tag }}\" --pattern \"openai-responses-proxy-*-${{ matrix.platform }}.tar.gz\" --pattern \"openai-responses-proxy-${{ matrix.platform }}.manifest.json\" --pattern SHA256SUMS --pattern SHA256SUMS.sig --dir \"${{ runner.temp }}/current-release\""
		}, {
			name: "Download the published predecessor release"
			env: GH_TOKEN: "${{ github.token }}"
			run: "gh release download \"${{ env.OPENAI_RESPONSES_PROXY_PREVIOUS_RELEASE_TAG }}\" --pattern \"${{ env.OPENAI_RESPONSES_PROXY_PREVIOUS_RELEASE_PRODUCT }}-*-${{ matrix.platform }}.tar.gz\" --pattern \"${{ env.OPENAI_RESPONSES_PROXY_PREVIOUS_RELEASE_PRODUCT }}-${{ matrix.platform }}.manifest.json\" --pattern SHA256SUMS --pattern SHA256SUMS.sig --dir \"${{ runner.temp }}/previous-release\""
		}, {
			name: "Materialize the release trust anchor"
			env: {
				PREVIOUS_RELEASE_ASSET_TRUST: "${{ secrets.CODEX_RESPONSES_PROXY_RELEASE_ASSET_TRUST }}"
				RELEASE_ASSET_TRUST:          "${{ vars.OPENAI_RESPONSES_PROXY_RELEASE_ASSET_TRUST }}"
			}
			run: "python -c \"import os; from pathlib import Path; current = os.environ['RELEASE_ASSET_TRUST'].rstrip(); previous = os.environ['PREVIOUS_RELEASE_ASSET_TRUST'].rstrip() if os.environ['OPENAI_RESPONSES_PROXY_PREVIOUS_RELEASE_PRODUCT'] == 'codex-responses-proxy' else current; assert current and previous, 'release trust is unavailable'; Path(r'${{ runner.temp }}/previous-release-asset-trust').write_text(previous + '\\n', encoding='ascii'); Path(r'${{ runner.temp }}/release-asset-trust').write_text(current + '\\n', encoding='ascii')\""
		}, {
			name: "Bind the exact published assets"
			run:  "python -c \"import glob, os; current = glob.glob(r'${{ runner.temp }}/current-release/openai-responses-proxy-*-${{ matrix.platform }}.tar.gz'); previous = glob.glob(r'${{ runner.temp }}/previous-release/${{ env.OPENAI_RESPONSES_PROXY_PREVIOUS_RELEASE_PRODUCT }}-*-${{ matrix.platform }}.tar.gz'); assert len(current) == len(previous) == 1, (current, previous); open(os.environ['GITHUB_ENV'], 'a', encoding='utf-8').write('OPENAI_RESPONSES_PROXY_CURRENT_RELEASE_ASSET=' + current[0] + '\\nOPENAI_RESPONSES_PROXY_PREVIOUS_RELEASE_ASSET=' + previous[0] + '\\n')\""
		}, {
			name: "Start the runner user systemd manager"
			if:   "matrix.platform == 'linux-x86_64'"
			run:  "user_id=$(id -u); runtime_dir=\"/run/user/${user_id}\"; sudo systemctl start \"user@${user_id}.service\"; printf '%s\\n' \"XDG_RUNTIME_DIR=${runtime_dir}\" \"DBUS_SESSION_BUS_ADDRESS=unix:path=${runtime_dir}/bus\" >> \"${GITHUB_ENV}\""
		}, {
			name: "Prove the published release lifecycle"
			env: {
				OPENAI_RESPONSES_PROXY_PREVIOUS_RELEASE_TRUST_ANCHOR: "${{ runner.temp }}/previous-release-asset-trust"
				OPENAI_RESPONSES_PROXY_CURRENT_RELEASE_TRUST_ANCHOR:  "${{ runner.temp }}/release-asset-trust"
			}
			run: "uv run --locked --no-sync nox -s published_release_compatibility -- --basetemp=\"${{ runner.temp }}/proxy-test\""
		}]
	}
}
