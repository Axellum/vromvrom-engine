Version française : [../README.md](../README.md)

# AI Engine User Guide

This guide is for someone who is new to **vromvrom-engine** (the « Moteur IA », "AI Engine") and wants to install it at home,
understand what it does and configure it, **with or without Home Assistant**. It is the English translation of the French
guide. The [repository README](../../../README.md) gives the overview.

The AI Engine is a Python server. It receives requests (chat, API, MCP tools, voice through Home Assistant), picks the cheapest
path that is enough (rules first, then a model), acts, and counts tokens and dollars.

## The pages

| Page | Who it is for | Contents |
|---|---|---|
| [01 Install](01_install.md) | Everyone, first | Prerequisites, Windows and Linux installation, Docker, first start, first request, permanent service, MCP client |
| [02 Understand](02_understand.md) | Anyone who wants to know what happens when a request is sent | Request paths, model choice, agents, human approval, background loops, tour of the interface |
| [03 Settings](03_settings.md) | Anyone who tunes the engine | Every `.env` variable and every `config.json` key, with its default, its effect, and whether it needs a restart |
| [04 Home and voice](04_home_and_voice.md) | Anyone who has Home Assistant | Connecting Home Assistant, command catalog, no-effect simulation, voice, local fallback |
| [05 Operate](05_operate.md) | Anyone who keeps the engine running over time | Health, backup and restore, update, troubleshooting, security, **costs** |

## In which order to read

1. **[01 Install](01_install.md)**: an installation that answers `/healthz` and a first request.
2. **[05 Operate, § 6 (costs)](05_operate.md)**: before you add a provider key, know what consumes tokens on its own.
3. **[02 Understand](02_understand.md)**: to know what you are running.
4. **[03 Settings](03_settings.md)** when you want to change a behavior.
5. **[04 Home and voice](04_home_and_voice.md)** if you have Home Assistant. Without it, skip this page.

## Documentation already published (not repeated here)

| Document | For |
|---|---|
| [`README.md`](../../../README.md) | Overview, quick install, providers |
| [`STRATEGIES.md`](../../../STRATEGIES.md) | Which models to use, at what price (some configuration sections there are older than the code: see [03 § 6](03_settings.md)) |
| [`docs/API_REFERENCE.md`](../../API_REFERENCE.md) | The API routes |
| [`docs/ARCHITECTURE.md`](../../ARCHITECTURE.md) | The architecture (French only): a June 2026 snapshot that predates this guide ([02](02_understand.md) is current) |
| [`docs/architecture/carte_decision_vocale.md`](../../architecture/carte_decision_vocale.md) | The exact order of the voice shortcuts (French only) |
| [`ihm-v2/README.md`](../../../ihm-v2/README.md) | The web interface: development and build (French only) |

## How to read the figures and commands

- Every default, variable, route or menu comes from the code of this repository; the file is cited. **"—"** means unknown or not
  measured, and **"not tried"** means the thing was read in the code but not run.
- Windows commands are written for **PowerShell 5.1** (no `&&`, no `curl -H`: we use `Invoke-RestMethod`), with the bash
  equivalent for Linux and macOS.
- An address such as `192.168.1.20` or an entity such as `light.living_room` is an **example**: replace it with your own.
- The pages were checked by a real start of the engine on Windows 11 with Python 3.13 (without a provider key or Home
  Assistant). Linux, macOS, Docker and systemd units were not tried.
- **The dashboard itself is in French.** Menu names, screen titles and some on-screen messages are therefore quoted in French in
  this guide. The English meaning is given in parentheses on first use on each page, for example « Régler » (Configure).
  Likewise, the engine's voice matching is French: voice commands and example prompts stay in French, with an English gloss
  where it helps.

## One limit to know right away

The Home Assistant component that passes voice to the engine (`moteur_ia_conversation`) **is not in this repository**. Chat, the
API, the agents and the MCP server work without it; end-to-end voice needs this component, or an equivalent that you write
([04 § 5](04_home_and_voice.md)).
