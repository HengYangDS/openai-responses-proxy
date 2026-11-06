## ADDED Requirements

### Requirement: Tracked content holds no peer coordinates

The changelog and the publication declaration SHALL name no publication peer's
repository, and no tracked content SHALL name the private peer's host, port,
namespace, project path or project ID. Each peer SHALL be resolved from the
clone's Git configuration.

#### Scenario: A test needs a GitLab project

- **WHEN** a test exercises GitLab publication
- **THEN** it SHALL use a fixture host and project ID
- **AND** it SHALL NOT contain the operator's private peer identity.

#### Scenario: A release is recorded in the changelog

- **WHEN** CHANGELOG records a release
- **THEN** it SHALL contain no comparison link or other peer URL.
