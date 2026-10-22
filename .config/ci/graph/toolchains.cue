package ci

// One supply contract selects immutable executors for each peer adapter.
#Toolchains: {
	githubActions: {
		checkout: "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1"          // v7.0.1
		python:   "actions/setup-python@6d4fd22b20465ccc0f7354476588e8df7c9ca0e0"      // v7.1.0
		uv:       "astral-sh/setup-uv@07a047e608db84b3d8c06cd9a7c7dcc9c355c18e"        // v10.3.2
		mise:     "jdx/mise-action@8418fedb1110381a0d3ba51a7a415d7c1b884550"           // v5.2.2
		upload:   "actions/upload-artifact@cf430e030ddbb5b0abf93d22962f4752f3646cd9"   // v7.0.2
		download: "actions/download-artifact@9000827ccba6bdab643e8b6fd33ac0654aef8333" // v8.0.2
	}
	gitlabMiseImage: "ghcr.io/jdx/mise@sha256:f01b88463f3a8396b2273d88469bd09d097aa3cacfe343177eb5457f1d8d2a92"
	quality:         "python,uv,node,cue,aqua:tamasfe/taplo,github:gitleaks/gitleaks,github:rhysd/actionlint,aqua:koalaman/shellcheck,github:lycheeverse/lychee"
}

// Local Nox and hosted CI consume text projections of this one release runtime.
#ReleaseRuntime: {
	python:     "3.14.9"
	linuxImage: "python:" + python + "-bookworm@sha256:17c39e0248a67f0b5ff0f6468b35c07a7473064ccf602132bb403c3a5b64a3f5"
}
