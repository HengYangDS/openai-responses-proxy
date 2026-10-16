package ci

import "strings"

// Native executors and every peer consume this one verification contract.
#RuntimeMatrix: python: ["3.12", "3.13", "3.14"]

// Nox and GitHub consume this tracked text projection of the same matrix.
#PythonVersions: strings.Join(#RuntimeMatrix.python, "\n")

#VerificationJobs: {
	quality: session:       "quality"
	compatibility: session: "tests"
	performance: session:   "performance"
	governance: module:     "tools.quality.governance"
	metadata: module:       "tools.release.metadata"
}
