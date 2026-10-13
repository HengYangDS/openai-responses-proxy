# Agent Entry Points

This repository is **OpenAI Responses Proxy**. It provides a local data-plane
compatibility adapter for third-party Responses endpoints; it is not the owner
of Codex conversation history or client configuration.

## Canonical Surfaces

- Project overview and setup: [README](README.md)
- Contribution and verification workflow: [CONTRIBUTING](CONTRIBUTING.md)
- Documentation root: [docs/README](docs/README.md)
- ETHOS adoption profile: [.ethos/profile.toml](.ethos/profile.toml)
- Active specification changes: [OpenSpec](openspec/)
- Authority and runtime boundary: [architecture](docs/architecture/authority-and-runtime-boundary.md)
- Change and release policy: [governance](docs/governance/release-and-change-policy.md)
- Decision records: [Decision register](docs/decisions/decision-register.md)
- Durable boundary decision: [DR-0001](docs/decisions/dr-0001-control-plane-data-plane-boundary.md)
- Evidence policy: [evidence](docs/evidence/evidence-policy.md)
- Independent forge operations: [operations](docs/operations/forge-operations.md)
- Release history: [CHANGELOG](CHANGELOG.md)

## Authority Order

1. Current user instruction and approved operational authorization.
2. Source code, tests, `VERSION`, and CI configuration.
3. Canonical documentation and durable decisions under `docs/`.
4. Generated runtime deployment under the platform data directory.
5. Logs, request captures, and host-local caches.

The installed runtime is a re-creatable post-release projection, never a source
of truth. Pre-release deployment is an invalid state.
Do not modify Codex session JSONL, SQLite state, archives, or model metadata to
repair a replay issue.

## Boundaries

- **Codex Desktop** owns per-conversation model selection and transcripts.
- **Client control planes** own provider configuration, credentials, endpoint
  selection, and client projection.
- **This proxy** owns local outbound Responses compatibility, its released
  payload and deployment, and native supervision.
- The proxy never reads or changes client configuration. Consumers use
  their own control plane to select one ordinary loopback HTTP endpoint.

## Required Verification

```bash
mise install --locked
mise run bootstrap
mise run check
```

`mise.toml` owns these tasks and binds uv to the exact locked Python installation
and this worktree's `.venv`, independent of an ambient activated environment.
Nox provisions separate `.nox/<session>` compatibility environments. Use
`mise run quick` while editing and rerun `mise run bootstrap` after dependency
locks change. `mise run release` verifies a native asset without registering a
service; `mise run native` also exercises the real host service lifecycle and
requires explicit operational authorization. See [CONTRIBUTING](CONTRIBUTING.md).

Use `openai-responses-proxy status --json` for read-only runtime evidence. Reload
and upgrade are transactional lifecycle mutations and must be communicated
before execution. Installation accepts only a fresh target, one verified
current native listener, or the same signed artifact's verified installation
that lacks native supervision; an incompatible payload must be removed
explicitly.

Release preparation requires a clean accepted release checkout. Native builders
produce each platform asset from that frozen source; tag creation binds the
release commit and verifies its signature against external trust. One assembled
bundle is signed and published independently to each selected Forge. Installation
admits that archive through its signed checksum inventory, exact platform and
payload manifest. Dual-Forge publication is verified separately and is not an
installer dependency.

Uninstall must prove native-service absence and exact owned-process exit before
payload mutation. Process ownership requires the exact installed executable and
one declared private service role; identity is re-read before signalling and
boundedly rechecked afterwards. `--purge` trusts only a valid current payload
manifest, preserves unknown install content, and exits nonzero when residue
remains. Linux supervision requires a systemd user manager; installation starts
no session-only fallback process. A failed service-manager observation is
unknown, not evidence that a registered service is absent.
