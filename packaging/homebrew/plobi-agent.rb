class PlobiAgent < Formula
  include Language::Python::Virtualenv

  desc "Self-improving AI agent that creates skills from experience"
  homepage "https://github.com/deyu-deng/Plobi"
  # ---- NOT PUBLISHED (placeholder) -------------------------------------------
  # This formula has never shipped a Plobi artifact. The URL below is a template
  # for where it *will* point once scripts/release.py attaches a semver-named
  # sdist to a GitHub release. Until then `brew install plobi-agent` cannot work,
  # and nothing here should be installed from any third-party tarball.
  # ---------------------------------------------------------------------------
  url "https://github.com/deyu-deng/Plobi/releases/download/vX.Y.Z/plobi_agent-X.Y.Z.tar.gz"
  sha256 "<replace-with-release-asset-sha256>"
  license "MIT"

  depends_on "certifi" => :no_linkage
  depends_on "cryptography" => :no_linkage
  depends_on "libyaml"
  depends_on "python@3.14"

  pypi_packages ignore_packages: %w[certifi cryptography pydantic]

  # Refresh resource stanzas after bumping the source url/version:
  #   brew update-python-resources --print-only plobi-agent

  def install
    venv = virtualenv_create(libexec, "python3.14")
    venv.pip_install resources
    venv.pip_install buildpath

    pkgshare.install "skills", "optional-skills"

    %w[plobi plobi-agent plobi-acp].each do |exe|
      next unless (libexec/"bin"/exe).exist?

      (bin/exe).write_env_script(
        libexec/"bin"/exe,
        PLOBI_BUNDLED_SKILLS: pkgshare/"skills",
        PLOBI_OPTIONAL_SKILLS: pkgshare/"optional-skills",
        PLOBI_MANAGED: "homebrew"
      )
    end
  end

  test do
    assert_match "Plobi Agent v#{version}", shell_output("#{bin}/plobi version")

    # `plobi update` is a disabled no-op in this build (no self-update), so the
    # managed-install proof comes from `plobi version` instead: PLOBI_MANAGED is
    # reported back as the install method.
    managed = shell_output("#{bin}/plobi version 2>&1", 30, env: { "PLOBI_MANAGED" => "homebrew" })
    assert_match "Install method: homebrew", managed
  end
end
