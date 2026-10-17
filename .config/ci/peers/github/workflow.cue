package ci

#Conditions: {
	productProof:             "(github.event_name == 'pull_request' && github.base_ref == 'dev') || (github.event_name == 'push' && github.ref == 'refs/heads/dev')"
	tagPush:                  "github.event_name == 'push' && github.ref_type == 'tag'"
	nativeProof:              #Conditions.productProof + " || (" + #Conditions.tagPush + ")"
	productSHA:               "${{ github.event.pull_request.head.sha || github.sha }}"
	publishedReleaseProof:    "github.event_name == 'release' || github.event_name == 'workflow_dispatch'"
	publishedReleaseTag:      "${{ github.event.release.tag_name || inputs.release_tag }}"
	publishedReleaseCheckout: "${{ github.event_name == 'release' && github.event.release.tag_name || github.sha }}"
}

#CommitEvent: {
	OPENAI_RESPONSES_PROXY_COMMIT_BASE:   "${{ github.ref_type == 'tag' && github.sha || github.event.pull_request.base.sha || github.event.before }}"
	OPENAI_RESPONSES_PROXY_COMMIT_HEAD:   #Conditions.productSHA
	OPENAI_RESPONSES_PROXY_COMMIT_TARGET: "${{ github.event_name == 'pull_request' && format('refs/heads/{0}', github.base_ref) || github.ref }}"
	OPENAI_RESPONSES_PROXY_COMMIT_PEER:   "github"
}
#UvSetup: {
	uses: #Toolchains.githubActions.uv
	with: "cache-suffix": "${{ github.job }}-${{ strategy.job-index }}"
}

githubVerify: {
	name: "Verify"
	on: {
		workflow_call: secrets: {
			CODEX_RESPONSES_PROXY_RELEASE_ASSET_TRUST: required:       true
			CODEX_RESPONSES_PROXY_RELEASE_ASSET_SIGNING_KEY: required: false
		}
		push: tags: ["v*"]
		release: types: ["published"]
		workflow_dispatch: inputs: release_tag: {
			description: "Published vMAJOR.MINOR.PATCH tag to verify"
			required:    true
			type:        "string"
		}
	}
	permissions: contents: "read"
	env: {
		OPENAI_RESPONSES_PROXY_RELEASE_TAG_REMOTE: "origin"
		GIT_CONFIG_COUNT:                          "1"
		GIT_CONFIG_KEY_0:                          "init.defaultBranch"
		GIT_CONFIG_VALUE_0:                        "main"
	}
	concurrency: {
		group:                "verify-${{ github.workflow }}-${{ github.ref }}"
		"cancel-in-progress": true
	}
	jobs: {
		"python-matrix":                   #SourceJobs."python-matrix"
		"source-and-governance":           #SourceJobs."source-and-governance"
		"commit-admission":                #SourceJobs."commit-admission"
		python:                            #SourceJobs.python
		"python-windows":                  #SourceJobs."python-windows"
		"accepted-source":                 #SourceJobs."accepted-source"
		promotion:                         #SourceJobs.promotion
		"tag-metadata":                    #ReleaseJobs."tag-metadata"
		"python-quality":                  #SourceJobs."python-quality"
		performance:                       #SourceJobs.performance
		"native-assets":                   #NativeJobs."native-assets"
		"native-linux":                    #NativeJobs."native-linux"
		"native-linux-lifecycle":          #NativeJobs."native-linux-lifecycle"
		"release-compatibility":           #ReleaseJobs."release-compatibility"
		"release-assets":                  #ReleaseJobs."release-assets"
		"published-release-compatibility": #ReleaseJobs."published-release-compatibility"
		"branch-proof":                    #AdmissionJobs."branch-proof"
	}
}
