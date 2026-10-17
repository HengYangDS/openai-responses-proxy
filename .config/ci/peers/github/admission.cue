package ci

import (
	"encoding/json"
	"list"
)

// GitHub job identities adapt the shared proof contract to branch admission.
#ProductProofJobs: [
	"commit-admission",
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

#BranchAdmissionRules: [{
	when: {GITHUB_EVENT_NAME: "pull_request", GITHUB_BASE_REF: "dev"}
	jobs: #ProductProofJobs
}, {
	when: {GITHUB_EVENT_NAME: "pull_request", GITHUB_BASE_REF: "main"}
	jobs: ["commit-admission", "promotion"]
}, {
	when: {GITHUB_EVENT_NAME: "push", GITHUB_REF: "refs/heads/dev"}
	jobs: list.Concat([#ProductProofJobs, ["accepted-source"]])
}, {
	when: {GITHUB_EVENT_NAME: "push", GITHUB_REF: "refs/heads/main"}
	jobs: ["commit-admission", "accepted-source"]
}, {
	when: {GITHUB_EVENT_NAME: "push", GITHUB_REF_TYPE: "tag"}
	jobs: []
}, {
	when: GITHUB_EVENT_NAME: "release"
	jobs: []
}, {
	when: GITHUB_EVENT_NAME: "workflow_dispatch"
	jobs: []
}]

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
			with: ref: #Conditions.productSHA
		}, {
			uses: #Toolchains.githubActions.python
			with: "python-version-file": ".config/mise/python-release"
		}, {
			name: "Reject missing branch proof"
			env: {
				NEEDS_JSON:             "${{ toJSON(needs) }}"
				BRANCH_ADMISSION_RULES: json.Marshal(#BranchAdmissionRules)
			}
			run: "python -m tools.ci.admission"
		}]
	}
}
