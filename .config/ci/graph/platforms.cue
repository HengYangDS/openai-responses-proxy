package ci

import "list"

// Each supported platform once: the release asset that its native builder produces and its runner
// on each Forge that runs it. The peer adapters read their runner selectors here and name none
// themselves.
#Platforms: {
	macos: {
		release: "macos-arm64"
		github:  "macos-latest"
		// GitLab selects a macOS guest by tags: review for merge requests, protected for dev.
		gitlab: {
			review: ["macos-latest", "review"]
			protected: ["macos-latest", "protected"]
		}
	}
	linux: {
		release: "linux-x86_64"
		github:  "ubuntu-24.04"
		// GitLab project variables hold the Linux runner tags.
		gitlab: {
			review: ["$OPENAI_RESPONSES_PROXY_GITLAB_LINUX_REVIEW_RUNNER_TAG"]
			protected: ["$OPENAI_RESPONSES_PROXY_GITLAB_LINUX_RUNNER_TAG"]
		}
	}
}

// The released platform identities, in the product's sorted order.
#ReleasePlatforms: list.SortStrings([for _, platform in #Platforms {platform.release}])

// Both Forges run the Python compatibility job natively on this platform.
#CompatibilityPlatform: #Platforms.macos
