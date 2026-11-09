package ci

import "strings"

// One supply contract selects immutable executors for each peer adapter.
#Toolchains: {
	githubActions: {
		checkout: "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1"          // v7.0.1
		python:   "actions/setup-python@6d4fd22b20465ccc0f7354476588e8df7c9ca0e0"      // v7.1.0
		uv:       "astral-sh/setup-uv@68559e99cc807c1eb7e9238fd43a15bba460eec2"        // v10.3.3
		mise:     "jdx/mise-action@b44d12e7d0d8ee7461bb893e1b1993500433185b"           // v5.2.7
		upload:   "actions/upload-artifact@cf430e030ddbb5b0abf93d22962f4752f3646cd9"   // v7.0.2
		download: "actions/download-artifact@9000827ccba6bdab643e8b6fd33ac0654aef8333" // v8.0.2
	}
	// The Debian variant carries the shell that GitLab's Docker executor runs; the scratch default has none.
	gitlabMiseImage: "ghcr.io/jdx/mise:2026.11.0-debian@sha256:294f2f1e29c0755bca7835a26e52a99eee0522381259c0be5834eaacc07a64c6"
	// GitHub's mise-action installs this release, read from the GitLab image's tag, so both peers run one Mise.
	miseVersion: strings.TrimSuffix(strings.Split(strings.Split(gitlabMiseImage, ":")[1], "@")[0], "-debian")
	quality:     "python,uv,node,cue,aqua:tamasfe/taplo,github:gitleaks/gitleaks,github:rhysd/actionlint,aqua:koalaman/shellcheck,github:lycheeverse/lychee"
}

// Local Nox and hosted CI consume text projections of this one release runtime. The image reference is its only
// literal, so one dependency proposal moves the Python patch and the image digest together.
#ReleaseRuntime: {
	linuxImage: "python:3.14.10-bookworm@sha256:3fe825da0a80fa8e2cd79baadeb76b03c0ddf3d6cdbc8346778a0208b9d6038c"
	python:     strings.TrimSuffix(strings.TrimPrefix(strings.SplitN(linuxImage, "@", 2)[0], "python:"), "-bookworm")
}
