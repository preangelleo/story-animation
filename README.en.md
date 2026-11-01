# Story Animation

[中文](README.md) · [Website](https://agentj.app/workflows/story-animation/) · [Workflow](distribution/scaffold/documentation/WORKFLOW.md) · [Providers](distribution/scaffold/documentation/PROVIDERS.md)

An open-source Agent J workflow for making narrated animated stories: research, core themes, fresh scripts, independent editing, consistent characters, configurable visuals, script-aligned captions, chapter cards and optional publishing.

[![Watch the accepted pilot](https://p.preangel.me/workflows/story-animation/v1.1.0/poster.jpg)](https://p.preangel.me/workflows/story-animation/v1.1.0/showcase-zh.mp4)

[Watch the approximately 18-minute pilot](https://p.preangel.me/workflows/story-animation/v1.1.0/showcase-zh.mp4) · [YouTube alternative](https://youtu.be/j7upT3jbVDo)

The R2 video has burned-in Simplified Chinese captions. The YouTube alternative has five selectable caption tracks (Simplified/Traditional Chinese, English, Japanese and Korean).

The pilot is a showcase, not an MIT-licensed media asset. It includes authorized cloned narration and provider-generated content; the code license does not grant rights to reuse the voice or redistribute the media.

## Give this prompt to Agent J

> Find **Story Animation** in Agent J Plaza: ID `story-animation`, version `1.1.0`. Read its manifest, README, skills and provider requirements. Preview installation in a new independent workspace and explain permissions, verification status and the native confirmation process. Check my models, image generation, narration, forced alignment, fonts and rendering environment. Use a secret card or my approved secret manager for credentials. Distinguish verified integrations from alternatives that need adaptation. Report missing setup, propose research and a production budget, and wait for my approval before paid work. Do not purchase services, inherit another channel's account, publish or enable automation. If parameters cannot meet my needs, propose source changes first.

## Install or inspect

Full Plaza downloads require an account with a paid seat. This repository is publicly readable and clonable. Third-party services have separate costs. The native command previews installation by default:

```sh
agentj plaza install story-animation --version 1.1.0 --workspace /your/new/story-workspace --harness codex
```

Replace the example path. Follow your installed CLI's `--help`, permission, digest and confirmation flow. Community availability does not imply official certification; do not bypass verification.

The byte-preserved distribution bundle is under `distribution/`; it is not an already configured production workspace. A read-only local capability check:

```sh
git clone https://github.com/preangelleo/story-animation.git
cd story-animation
python3 distribution/scaffold/bin/story-animation doctor
```

Install or copy the scaffold to your own independent workspace before real production. Read its `AGENTS.md`. Do not create private production data inside the public distribution tree. Offline checks do not prove production readiness.

## Validated stack and alternatives

| Capability | Accepted pilot / public implementation | Alternatives |
|---|---|---|
| Orchestration and images | Codex with built-in `image_gen`; thumbnail role configured as `gpt-6.1-sol` | Other harnesses or image providers need explicit adapters and verification of references and output contracts |
| Chinese script | OpenCode headless with Ark Agent Plan, `doubao-seed-2.1-pro` | Compatible endpoints require actual authentication, plan and quality validation; no silent metered fallback |
| Narration and alignment | Own ElevenLabs key and authorized voice; pilot used `eleven_v4`, public default is `eleven_multilingual_v2` | Fish Audio or Volcengine can be adapted, but are not advertised as validated drop-in integrations |
| Rendering | Python 3.11+, ffmpeg/ffprobe, Fontconfig, Node, HyperFrames 0.8.225 and browser dependencies | Supply fonts you have the right to use |
| Video inserts | Pilot: Seedance2.5; optional Replicate integration, up to three clips of about ten seconds | Model/version parameters do not guarantee provider availability, prices or access |
| YouTube | Optional own Google Cloud OAuth profile, explicit channel ID; captions need `youtube.force-ssl` | Publishing requires separate approval; credentials can expire or be revoked |

Fonts, private voice models, keys, source media, production databases and generated sound libraries are not bundled. Human listening review remains necessary for names, pronunciation and delivery.

## Parameters before code changes

- **Story and visuals:** language, duration targets, theme, seven weekday series and 26 editable style indices.
- **Brand and timing:** names, channel, time zone, publishing time and production lead days. Default D+2 at 20:00 Tokyo does not enable a timer.
- **Providers and budget:** author model/endpoint, voice/model, video model/version, clip count and duration, episode/month budgets. A configured budget is not spending approval.
- **Captions and thumbnails:** owned subtitle font and collection face; Simplified Chinese, Traditional Chinese, English, Japanese and Korean tracks; thumbnail role, style, references, title and aspect ratio. Subtitle size and box colors are currently code-defined, not universal CLI options.
- **Chapter cards:** title, number, two fonts, layout, positions, spacing, background/text/line colors, dimensions, duration, sound, gain and mute.
- **Storage and sounds:** local database/artifact locations and namespaces; lowercase-named cached sounds with generation only after budget approval when missing.

See [configuration.json](distribution/scaffold/documentation/configuration.json), [styles.json](distribution/scaffold/config/styles.json), [weekly_series.json](distribution/scaffold/config/weekly_series.json), and each command's `--help`. The Plaza installer currently exposes only `project_name`; other settings are configured after installation through JSON, environment variables or CLI options. Source, roles and skills are editable when parameters are insufficient. Retest changes while preserving privacy, budget authorization, evidence and independent review.

## Production flow

Research and evidence → 1–3 core themes → frozen materials → fresh narration-only script → independent editor → character references and scenes → narration and script-based forced alignment → semantic captions → optional video and restrained sound → whole-scene clips plus separate chapter clips → human acceptance → separately authorized publishing.

Static images stay still. Chapter clips enter only between complete scenes. Recognition helps check audio; it does not replace script text for captions. Separate evidence, attributed quotations and dramatization for real events.

## Tests and contributions

```sh
cd distribution/scaffold
python3 -m unittest discover -s tests -q
```

Tests use synthetic fixtures, not production acceptance. See [CONTRIBUTING.md](CONTRIBUTING.md), [SECURITY.md](SECURITY.md), [LICENSE](LICENSE) and the preserved [NOTICE](distribution/scaffold/NOTICE.md). MIT covers project-owned code and documentation. Third-party services, models, fonts, media, trademarks and source content retain their own rights and terms.
