package ci

// One supply contract selects immutable executors for each peer adapter.
#Toolchains: {
	githubActions: {
		checkout: "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1"          // v7.0.1
		python:   "actions/setup-python@6d4fd22b20465ccc0f7354476588e8df7c9ca0e0"      // v7.1.0
		uv:       "astral-sh/setup-uv@68559e99cc807c1eb7e9238fd43a15bba460eec2"        // v10.3.3
		mise:     "jdx/mise-action@f5be966e4a917d54244728e6a5fdcd1d93a35e6c"           // v5.2.6
		upload:   "actions/upload-artifact@cf430e030ddbb5b0abf93d22962f4752f3646cd9"   // v7.0.2
		download: "actions/download-artifact@9000827ccba6bdab643e8b6fd33ac0654aef8333" // v8.0.2
	}
	// The Debian variant carries the shell that GitLab's Docker executor runs; the scratch default has none.
	gitlabMiseImage: "ghcr.io/jdx/mise:2026.10.25-debian@sha256:00a8a9d05a300e5a15a89d6d3a87ea88ec4e78c4702b78d49aa1d3250af61427"
	quality:         "python,uv,node,cue,aqua:tamasfe/taplo,github:gitleaks/gitleaks,github:rhysd/actionlint,aqua:koalaman/shellcheck,github:lycheeverse/lychee"
}

// Local Nox and hosted CI consume text projections of this one release runtime.
#ReleaseRuntime: {
	python:     "3.14.10"
	linuxImage: "python:" + python + "-bookworm@sha256:a8cf203c684b61fd2676ec5a17159cf68ed38ad471481e1f4ebc4679825250bd"
}
