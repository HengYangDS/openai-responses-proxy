package ci

// One supply contract selects immutable executors for each peer adapter.
#Toolchains: {
	githubActions: {
		checkout: "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1"          // v7.0.1
		python:   "actions/setup-python@6d4fd22b20465ccc0f7354476588e8df7c9ca0e0"      // v7.1.0
		uv:       "astral-sh/setup-uv@07a047e608db84b3d8c06cd9a7c7dcc9c355c18e"        // v10.3.2
		mise:     "jdx/mise-action@82a612c5ab8bcda7cc5db6b7e81a8e7584c3dc6e"           // v5.2.3
		upload:   "actions/upload-artifact@cf430e030ddbb5b0abf93d22962f4752f3646cd9"   // v7.0.2
		download: "actions/download-artifact@9000827ccba6bdab643e8b6fd33ac0654aef8333" // v8.0.2
	}
	// The Debian variant carries the shell that GitLab's Docker executor runs; the scratch default has none.
	gitlabMiseImage: "ghcr.io/jdx/mise:2026.10.19-debian@sha256:9b8995abd457c975a4f5150b423f6569ee5a01960662ffa4c06c859867e59a21"
	quality:         "python,uv,node,cue,aqua:tamasfe/taplo,github:gitleaks/gitleaks,github:rhysd/actionlint,aqua:koalaman/shellcheck,github:lycheeverse/lychee"
}

// Local Nox and hosted CI consume text projections of this one release runtime.
#ReleaseRuntime: {
	python:     "3.14.9"
	linuxImage: "python:" + python + "-bookworm@sha256:7742c7daca5b166a5a81795531dee9701c032bc0ea00027740e5e9eabfed17c6"
}
