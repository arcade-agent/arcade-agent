# Releasing arcade-agent

Releases use the official Release Please action and PyPI trusted publishing.
Merging a regular change updates a release PR after CI succeeds. Merging that
release PR advances the version and changelog; successful main CI creates the
GitHub release, and `publish.yml` publishes that commit's verified CI distributions.
There is no automatic merge of release PRs.

## Version policy

The manifest starts at the already published `0.3.0` release. Its bootstrap SHA
is the commit tagged `v0.3.0`, so the first generated changelog excludes older
history. Python `always-bump-minor` versioning proposes `0.4.0`, then `0.5.0`,
for subsequent releases. Fixes, features, and documentation changes can produce
release PRs; CI/chore-only changes do not. Prefer squash merges whose final
commit messages follow Conventional Commits.

This policy also bumps minor for breaking changes. Review their migration notes
and switch to the `default` versioning strategy when committing to a stable
`1.0` API. Minor numbering is a release policy, not a promise of compatibility.

Release Please updates `pyproject.toml`, `CHANGELOG.md`, its manifest, and the
annotated package/action version pins in the README, composite action, reusable
workflow, and copyable workflow example. Keep `x-release-please-version` markers
on those pins. Runtime versions such as Python `3.12` are not release pins.

## Maintainer setup

- Enable Actions to create pull requests. The CI release job needs contents,
  issues, and pull-request write permission for release branches, tags and labels.
- The default `GITHUB_TOKEN` requires no added secret. Under current GitHub rules,
  CI of a bot-created/updated release PR can require a writer to approve workflows.
  For fully automatic release-PR CI, explicitly configure a suitably scoped
  GitHub App installation token instead; this change does not provision one.
- Keep the existing PyPI trusted publisher bound to this repository,
  `.github/workflows/publish.yml` and the `pypi` environment. Check any environment
  restrictions against main-branch publishing. The workflow filename and
  environment remain the same; only its trigger and build handoff change.

See [GitHub workflow triggering rules](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/trigger-a-workflow)
and [PyPI publisher configuration](https://docs.pypi.org/trusted-publishers/adding-a-publisher/).
These account settings need maintainer verification; local tests cannot prove
GitHub App installation, repository policy or live PyPI OIDC authorization.

## CI and publication boundary

CI builds one wheel and one sdist, checks them with Twine, and records repository,
source SHA, package version and SHA-256 hashes in `release-build.json`. Test,
analysis and package jobs must succeed before Release Please runs on main.
An outdated CI run skips Release Please if main has advanced.

The publisher is triggered by successful CI completion, rather than relying on
bot-created `release.published` events. It re-reads the CI run through GitHub's
API and accepts only a successful main push of this repository's `ci.yml`.
It downloads the identified artifact from that run and verifies the manifest,
both distribution metadata/filenames and hashes. A published stable GitHub
release must resolve to the same source commit, including annotated tags.
Ordinary pushes with no release or a tag belonging to another commit skip
publication. Failed/PR/fork/wrong-workflow runs cannot feed the publisher.

Only validated wheel/sdist files are forwarded to the separate job with PyPI
OIDC permission. No artifact code is executed, and no package is rebuilt in the
publisher. Only HTTP 404 from PyPI means a version is absent; on HTTP 200,
the helper compares uploaded filenames/hashes with the CI distributions. A
complete matching upload skips publishing; a partial matching upload retries
missing files, and different bytes at an existing filename fail. Invalid JSON,
network/other HTTP errors also fail. Publishing is serialized per version and
already uploaded files can be skipped on retry.

This removes the old direct version-file push trigger and `gh release create`
step: Release Please is now the only tag/release owner. It automates version and
changelog work and reuses CI builds; there is no measured build-speed claim.
CI now also checks packaging on regular PRs/pushes, which adds a parallel job.

## Retry and recovery

If GitHub release creation succeeded but PyPI publication failed, retry the
publisher with the successful **main push CI run ID at the release tag's SHA**:

```bash
gh workflow run publish.yml --ref main -f ci-run-id=123456789
```

The ID is an example. The workflow revalidates the run, artifact, metadata,
release commit and PyPI state, so retry cannot substitute a failed or PR run.
Release-dist artifacts are retained for 30 days. If the artifact is lost or
CI failed after a tag was created, rerun CI at that original main push commit
while GitHub still allows reruns, and use the successful rerun's artifact.
After that window, this retry path cannot rebuild an expired artifact. Use a
reviewed recovery process with equivalent source/CI checks or ship a new version.
Do not substitute a newer main build or move an already published tag.
Regenerated builds can differ byte-for-byte; a hash conflict with a partially
uploaded release requires its original artifact or a new version.

If main advances around release creation and the resulting tag points at an
earlier commit, automatic publishing conservatively skips the newer artifact.
Use the successful CI run at the actual release SHA for retry. Likewise, if a
release was created later than its source CI's publication attempt, dispatch
the publisher with that source CI run ID. These checks protect source identity
at the cost of a manual retry in such races.

If a change is needed before publication, fix it through a reviewed PR and a
new release version. To pause automation, disable the CI release job and the
publish workflow together. Reverting this migration restores the previous
workflow's direct-publish behavior; review that behavior before reverting.
