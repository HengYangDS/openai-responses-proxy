package ci

import "list"

// Each supported platform once: the release asset that its native builder produces and its runner
// on each Forge. The peer adapters read their runner selectors here and name none themselves.
#Platforms: {
	macos: {
		release: "macos-arm64"
		github:  "macos-latest"
		// GitLab selects a macOS guest by tags: review for merge requests, protected for dev, main
		// and tags.
		gitlab: {
			review: ["macos-latest", "review"]
			protected: ["macos-latest", "protected"]
			// Required once one review pipeline has proved which Python interpreters the guest
			// provides (drop-windows-and-intel-macos, D2, task 5.3); until then a failure is allowed.
			required: false
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

// The Python compatibility job runs natively on this platform on every Forge.
#CompatibilityPlatform: #Platforms.macos
