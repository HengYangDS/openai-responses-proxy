---
subject: openai-responses-proxy:documentation
role: index
state: canonical
relations:
  part_of: ../README.md
---

# Documentation

Choose the entry for your task. Product use belongs in the project README;
development and release work belong in CONTRIBUTING. The remaining documents
own architecture, decisions, evidence, and governance without duplicating those
journeys.

| Domain         | Document                                                                         | Owns                                                        |
| -------------- | -------------------------------------------------------------------------------- | ----------------------------------------------------------- |
| Overview       | [Project README](../README.md)                                                   | Product purpose and supported scope.                        |
| Concepts       | [Product boundary](../README.md#product-boundary)                                | Data-plane and client-control responsibilities.             |
| Setup          | [Install](../README.md#install)                                                  | Native installation and upgrade eligibility.                |
| Client routing | [Configure a client route](../README.md#configure-a-client-route)                | Explicit upstream and loopback composition.                 |
| Operations     | [Operate](../README.md#operate)                                                  | Status, diagnosis, reload, rollback, recovery, and removal. |
| Contribution   | [CONTRIBUTING](../CONTRIBUTING.md)                                               | Locked development, verification, and release workflow.     |
| Architecture   | [Authority and runtime boundary](architecture/authority-and-runtime-boundary.md) | Components, Provider admission, and runtime projection.     |
| Security       | [Threat model](architecture/threat-model.md)                                     | Assets, trust boundaries, controls, and accepted risks.     |
| Decisions      | [Decisions](decisions/README.md)                                                 | Durable rulings, alternatives and revisit conditions.       |
| Evidence       | [Evidence](evidence/README.md)                                                   | Proof requirements, observations, and acceptance limits.    |
| Governance     | [Release and change policy](governance/release-and-change-policy.md)             | Change, release, and contributor rules.                     |
| Publication    | [Forge operations](operations/forge-operations.md)                               | Independent GitLab and GitHub operation.                    |
| Specifications | [OpenSpec](../openspec/)                                                         | Current specifications and active change.                   |
| History        | [Changelog](../CHANGELOG.md)                                                     | Published release history.                                  |

Source, tests, `VERSION`, and CI remain higher authority than prose. Generated
runtime files and host logs are evidence only.
