package ci

import (
	"list"
	"strings"
)

// GitHub job identities adapt the shared proof contract to branch admission.
#ProductProofJobs: [
	"source-and-governance",
	"python-matrix",
	"python",
	"python-windows",
	"python-quality",
	"performance",
	"native-assets",
	"native-linux",
	"native-linux-lifecycle",
	"release-compatibility",
]

githubAdmission: {
	name: "Branch admission"
	on: {
		pull_request: branches: ["dev", "main"]
		push: branches: ["dev", "main"]
	}
	permissions: contents: "read"
	jobs: {
		verify: {
			name: "Verify branch source"
			uses: "./.github/workflows/verify.yml"
			secrets: CODEX_RESPONSES_PROXY_RELEASE_ASSET_TRUST: "${{ secrets.CODEX_RESPONSES_PROXY_RELEASE_ASSET_TRUST }}"
		}
		admission: {
			name:      "Admission"
			needs:     "verify"
			if:        "always()"
			"runs-on": "ubuntu-24.04"
			steps: [{
				name: "Require completed branch verification"
				env: VERIFY_RESULT: "${{ needs.verify.result }}"
				run: "test \"$VERIFY_RESULT\" = success"
			}]
		}
	}
}

#AdmissionJobs: {
	"branch-proof": {
		name: "Branch proof"
		if:   "always()"
		needs: list.Concat([#ProductProofJobs, ["accepted-source", "promotion"]])
		"runs-on":         "ubuntu-24.04"
		"timeout-minutes": 5
		steps: [{
			uses: #Toolchains.githubActions.checkout
		}, {
			uses: #Toolchains.githubActions.python
			with: "python-version-file": ".python-release"
		}, {
			name: "Reject missing branch proof"
			env: {
				NEEDS_JSON:         "${{ toJSON(needs) }}"
				PRODUCT_PROOF_JOBS: strings.Join(#ProductProofJobs, " ")
			}
			run: """
				python - <<'PY'
				import json
				import os
				product = tuple(os.environ["PRODUCT_PROOF_JOBS"].split())
				event = os.environ["GITHUB_EVENT_NAME"]
				ref = os.environ["GITHUB_REF"]
				if event == "pull_request":
				    required = {"dev": product, "main": ("promotion",)}.get(
				        os.environ.get("GITHUB_BASE_REF", "")
				    )
				elif event == "push" and ref.startswith("refs/heads/"):
				    required = {
				        "dev": (*product, "accepted-source"),
				        "main": ("accepted-source",),
				    }.get(ref.removeprefix("refs/heads/"))
				elif event == "push" and ref.startswith("refs/tags/"):
				    required = ()
				elif event in {"release", "workflow_dispatch"}:
				    required = ()
				else:
				    required = None
				if required is None:
				    raise SystemExit("unsupported verification event")
				needs = json.loads(os.environ["NEEDS_JSON"])
				missing = [job for job in required if needs.get(job, {}).get("result") != "success"]
				if missing:
				    raise SystemExit("required branch proof failed: " + ", ".join(missing))
				PY
				"""
		}]
	}
}
