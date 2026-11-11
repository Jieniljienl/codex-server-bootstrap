# Third-party components / 第三方组件

The original payload targets Ubuntu 24.04 LTS, x86_64 / AMD64, with CPython 3.12.
The repository installer additionally supports Ubuntu 26.04 with CPython 3.14.
Two unmodified, same-version CPython 3.14 wheels (MarkupSafe 3.0.4 and PyYAML 6.0.3)
are distributed in the repository's wheels-ubuntu2604 directory. Their official
PyPI metadata/download URLs and SHA-256 values are recorded in its manifest.json.
Their original license files remain inside the unmodified wheels.
All bundled Linux executables are unmodified bytes extracted from the official fixed-version release assets. They were downloaded and checked against the SHA-256 digest exposed by the official GitHub release API. They were not executed on the Windows packaging host.

`deploy/assets-manifest.json` contains both upstream archive hashes and the SHA-256 hashes of the extracted executables and offline Python wheels. The installation process must verify extracted hashes before execution. GitHub release digests are an integrity check, not an independent cryptographic code-signing verification.

| Component | Version | Official source | License file bundled |
| --- | --- | --- | --- |
| Mihomo | v1.19.32, linux-amd64-compatible | https://github.com/MetaCubeX/mihomo/tree/v1.19.32 | `bin/linux-amd64/mihomo.LICENSE` (GPL-3.0) |
| CLIProxyAPI | v8.0.35, linux_amd64 | https://github.com/router-for-me/CLIProxyAPI/tree/v8.0.35 | `bin/linux-amd64/cli-proxy-api.LICENSE` (MIT) |
| OpenAI Codex CLI complete package | rust-v0.163.0, x86_64-unknown-linux-musl | https://github.com/openai/codex/tree/rust-v0.163.0 | `bin/linux-amd64/codex.LICENSE` (Apache-2.0) |

Official source archives corresponding to these releases:

- Mihomo: https://github.com/MetaCubeX/mihomo/archive/refs/tags/v1.19.32.tar.gz
- CLIProxyAPI: https://github.com/router-for-me/CLIProxyAPI/archive/refs/tags/v8.0.35.tar.gz
- Codex CLI: https://github.com/openai/codex/archive/refs/tags/rust-v0.163.0.tar.gz

The main Codex CLI executable is built for Linux musl. The complete package also contains the upstream native voice runtime and shared libraries; their original layout, source metadata and license notices are retained. The upstream release does not provide the historical `x86_64-unknown-linux-gnu` CLI asset for this version. Runtime behavior still requires verification on the target Linux server.

## Offline Python wheels

Downloaded from the official Python Package Index using `pip download --only-binary=:all:` for CPython 3.12, Linux x86_64, manylinux2014 / manylinux_2_17 / manylinux_2_28 compatibility. No packages were installed into the packaging host for this operation.

- `blinker-1.9.0-py3-none-any.whl` — SHA-256 `ba0efaa9080b619ff2f3459d1d500c57bddea4a6b424b60a91141db6fd2f08bc`
- `click-8.5.0-py3-none-any.whl` — SHA-256 `255bc9599cf7748b4b1a446ccc735421bd08a2ae529a8b88597d3de5664ee360`
- `flask-3.1.2-py3-none-any.whl` — SHA-256 `ca1d8112ec8a6158cc29ea4858963350011b5c846a414cdb7a954aa9e967d03c`
- `itsdangerous-2.2.0-py3-none-any.whl` — SHA-256 `c6242fc49e35958c8b15141343aa660db5fc54d4f13a1db01a3f5891b98700ef`
- `jinja2-3.1.6-py3-none-any.whl` — SHA-256 `85ece4451f492d0c13c5dd7c13a64681a86afae63a5f347908daf103ce6d2f67`
- `markupsafe-3.0.4-cp312-cp312-manylinux2014_x86_64.manylinux_2_17_x86_64.manylinux_2_28_x86_64.whl` — SHA-256 `8e124f974786f831d6043728e38296969d3579db8896fe004682f5758e613581`
- `pyyaml-6.0.3-cp312-cp312-manylinux2014_x86_64.manylinux_2_17_x86_64.manylinux_2_28_x86_64.whl` — SHA-256 `ba1cc08a7ccde2d2ec775841541641e4548226580ab850948cbfda66a1befcdc`
- `waitress-3.0.2-py3-none-any.whl` — SHA-256 `c56d67fd6e87c2ee598b76abdd4e96cfad1f24cacdea5078d382b1f9d7b5ed2e`
- `werkzeug-3.1.9-py3-none-any.whl` — SHA-256 `6392e50c78460ba618e5b21f08a71f59c99ce99cdc6cf6e3dd7e6ccca8754fab`

The wheel archives retain their original metadata and licenses. Human-readable copies of license / notice files were also extracted under `wheels/licenses/` without modifying the wheels. Licensing terms and redistribution requirements remain those of the individual upstream components. In particular, distribution of GPL-licensed Mihomo binaries is subject to its corresponding-source requirements; the exact version's source and build instructions are available from the upstream link above.

No subscription URL, subscription token, OpenAI account token, API credential, deployment password, or conversation history is included in this release package.

## Bundled corresponding Mihomo source

The unmodified official tag source archive is bundled at `source/mihomo-v1.19.32.tar.gz`, including the upstream README, license, build files and release workflows.

- Source URL: https://github.com/MetaCubeX/mihomo/archive/refs/tags/v1.19.32.tar.gz
- Archive size: 1269041 bytes
- SHA-256: `ab130b7fab3893d01aa44c0d39be34a26da716c1d36832fbac9fa054a1d862a0`


## Codex complete package layout and bundled helpers

Codex now uses the complete official `codex-package-x86_64-unknown-linux-musl.tar.gz` release, preserved under `bin/linux-amd64/codex-package/`. The prior standalone `bin/linux-amd64/codex` copy has been removed to avoid duplicate executables. Preserve the complete package directory when installing or copying; `bin/codex` must execute from this layout so that the package metadata and helper discovery continue to work.

- Official archive: https://github.com/openai/codex/releases/download/rust-v0.163.0/codex-package-x86_64-unknown-linux-musl.tar.gz
- Archive size: 159424486 bytes
- Archive SHA-256: `582646c6bf0561eb0de540f7fc85b9121b0091a826bc6e96830b7f1722122182`
- The archive has no enclosing directory: its members start with `bin/`, `codex-package.json`, `codex-path/` and `codex-resources/`.
- All original package files, executable flags and individual hashes are recorded in the Codex `package_files` list in `deploy/assets-manifest.json`. Extraction rejected absolute paths, `..`, backslashes, links, device entries, duplicate names and excessive sizes.

| Included helper | Confirmed upstream version / source | Original license and metadata retained |
| --- | --- | --- |
| `bin/codex-code-mode-host` | Codex rust-v0.163.0 | `bin/linux-amd64/codex.LICENSE` and `codex.NOTICE` |
| `codex-path/rg` | ripgrep 15.2.0, https://github.com/BurntSushi/ripgrep/tree/15.2.0 | `bin/linux-amd64/codex-licenses/rg.LICENSE-MIT` and `rg.UNLICENSE` (MIT or Unlicense) |
| `codex-resources/bwrap` | bubblewrap 0.11.2 as vendored by Codex rust-v0.163.0, https://github.com/openai/codex/tree/rust-v0.163.0/codex-rs/vendor/bubblewrap | `bin/linux-amd64/codex-licenses/bwrap.COPYING` (LGPL-2.0-or-later) |
| `codex-resources/voice/` | Upstream source commits, component versions, source archives and hashes are in its original `manifest.json` and `sources.json` | Original `NOTICE.md`, `licenses/`, `manifest.json`, `runtime.json`, and `sources.json` are all preserved |

The bubblewrap upstream project is https://github.com/containers/bubblewrap. The precise Codex-vendored source, local modifications and release build steps are available in https://github.com/openai/codex/archive/refs/tags/rust-v0.163.0.tar.gz; see `codex-rs/vendor/bubblewrap/` and `.github/workflows/rust-release.yml`. Use that version's vendored source when reproducing the bundled helper. The corresponding-source and redistribution terms in the retained LGPL apply to that helper separately from Codex's Apache license.

The complete layout supplies the daemon's required local package resources; it does not guarantee that every Linux host permits the namespaces or other operating-system facilities used by the sandbox. No authentication data or deployment secrets were accessed during this packaging repair.

## Bundled Codex and bubblewrap corresponding source

The unmodified official Codex tag source is bundled at `source/codex-rust-v0.163.0.tar.gz`. It includes the exact `codex-rs/vendor/bubblewrap/` sources, Codex modifications, COPYING license and the release workflow build recipe.

- Source URL: https://github.com/openai/codex/archive/refs/tags/rust-v0.163.0.tar.gz
- Archive size: 17505520 bytes
- SHA-256: `5990ddb133184ce5e470c741d14f1eeb24a0b6cd36685ef78fb772cf83cab7fc`

The official executable package layout has not been changed. Additional attribution copies reside outside it under `bin/linux-amd64/codex-licenses/`.
