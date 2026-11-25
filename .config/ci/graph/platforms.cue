package ci

import "list"

// Each supported platform once: the release asset that its native builder produces and its runner
// on each Forge that runs it. The peer adapters read their runner selectors here and name none
// themselves. GitLab runs no macOS job yet: its project has no macOS runner, so a later Change adds
// `macos.gitlab` here and the GitLab job that reads it (drop-windows-and-intel-macos, D2).
#Platforms: {
	macos: {
		release: "macos-arm64"
		github:  "macos-latest"
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

// GitHub runs the Python compatibility job natively on this platform.
#CompatibilityPlatform: #Platforms.macos
