Homebrew packaging notes for Plobi Agent.

> **STATUS: not published.** No Plobi Homebrew formula has ever been released.
> `plobi-agent.rb` in this directory is a **placeholder** — its `url:` is a
> template pointing at a GitHub release asset that does not exist yet, and its
> `sha256:` is a literal placeholder. Users must not be told to run
> `brew install plobi-agent`; the channel is kept on purpose while packaging is
> still being decided, and this note is the honest status. The steps below are
> the flow for *when we do publish*, not a description of today.

Use `packaging/homebrew/plobi-agent.rb` as a tap or `homebrew-core` starting point.

Key choices:
- Stable builds should target the semver-named sdist asset attached to each GitHub release, not the CalVer tag tarball.
- `faster-whisper` now lives in the `voice` extra, which keeps wheel-only transitive dependencies out of the base Homebrew formula.
- The wrapper exports `PLOBI_BUNDLED_SKILLS`, `PLOBI_OPTIONAL_SKILLS`, and `PLOBI_MANAGED=homebrew` so packaged installs keep runtime assets and defer upgrades to Homebrew.

Typical update flow:
1. Bump the formula `url`, `version`, and `sha256`.
2. Refresh Python resources with `brew update-python-resources --print-only plobi-agent`.
3. Keep `ignore_packages: %w[certifi cryptography pydantic]`.
4. Verify `brew audit --new --strict plobi-agent` and `brew test plobi-agent`.
