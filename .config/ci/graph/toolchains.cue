package ci

// One supply contract selects immutable executors for each peer adapter.
#Toolchains: {
	githubActions: {
		checkout: "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1"          // v7.0.1
		python:   "actions/setup-python@5fda3b95a4ea91299a34e894583c3862153e4b97"      // v7.0.0
		uv:       "astral-sh/setup-uv@c18668ad3cf93ea998bef934396af7bb5c839dc7"        // v10.2.0
		mise:     "jdx/mise-action@c2a87611a18de5b3828c5652fe268e992400cb5c"           // v4.3.0
		upload:   "actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a"   // v7.0.1
		download: "actions/download-artifact@3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c" // v8.0.1
	}
	gitlabMiseImage: "ghcr.io/jdx/mise@sha256:f01b88463f3a8396b2273d88469bd09d097aa3cacfe343177eb5457f1d8d2a92"
	quality:         "python,uv,node,cue,aqua:tamasfe/taplo,github:gitleaks/gitleaks,github:rhysd/actionlint,aqua:koalaman/shellcheck,github:lycheeverse/lychee"
}
