# Connect the home and voice

> **In one sentence.** The engine talks to Home Assistant through its API (address + token), controls the home with a
> phrase catalog (`ha_commands.json`), and receives voice **through the Home Assistant conversation agent**: this
> agent (`moteur_ia_conversation`) **is not in this public repository**. This page says what can be done today with
> the repository alone, and what cannot.

Without Home Assistant, skip this page: chat, the API and the agents work ([01](01_install.md)).

Related pages: [02 Understand](02_understand.md) · [03 Settings](03_settings.md) · [05 Operate](05_operate.md).
References: [`docs/architecture/carte_decision_vocale.md`](../../architecture/carte_decision_vocale.md) (French only; exact order of the voice
shortcuts) and the public display firmware
[Axellum/M5-Tab5-ESPHome-LVGL](https://github.com/Axellum/M5-Tab5-ESPHome-LVGL).

**Tried for this guide**: the engine API without Home Assistant (`/api/ha/health`, `/api/vocal/simulate`,
`/api/vocal/config`, `/api/setup/diagnostics`). **Not tried**: any connection to a real Home Assistant, any
display, any real command of the home.

---

## 1. Who does what, and where each piece is

| Link | Role | Where to find it |
|---|---|---|
| Display or voice satellite | Detects "Okay Nabu", sends the voice to Home Assistant, plays the answer | Public firmware: [Axellum/M5-Tab5-ESPHome-LVGL](https://github.com/Axellum/M5-Tab5-ESPHome-LVGL). Another Home Assistant Assist satellite can play this role (not tried). |
| Assist pipeline | Voice → text (STT), conversation agent, text → voice (TTS) | Home Assistant (Settings → Voice assistants) |
| **Conversation agent `moteur_ia_conversation`** | Sends the recognized text to the engine and returns its answer | **Absent from this repository** (§ 5) |
| Engine | Picks the path, controls the home or queries a model | This repository |
| Local fallback | A few vital commands served by Home Assistant alone when the engine is off | `deploy/ha_local_fallback/` (§ 7) |

The engine does not receive the audio: it receives the **text** that has already been transcribed.

---

## 2. Connect Home Assistant

### 2.1 The three variables

1. In Home Assistant: your profile → **Security** → **Long-lived access tokens** → create a token.
2. In the engine's `.env` ([03 § 2.3](03_settings.md)):

```ini
HASS_URL=https://192.168.1.20:8123
HASS_TOKEN=<the token>
```

3. Restart the engine (the `.env` is read only at startup).

`HASS_URL` has **no** default value in the code (`core/ha_url.py`): without it, no call is sent (a
warning says so in the log). Be careful: `.env.example` contains `HASS_URL=http://192.168.1.10:8123`, which
the install script copies; **replace it**. The old names `HA_URL` and `HA_TOKEN` are accepted as a fallback.

### 2.2 HTTPS and certificates

| Situation | Setting |
|---|---|
| Valid certificate | nothing (`HA_VERIFY_TLS=true` by default) |
| Self-signed certificate | `HA_CA_BUNDLE=/path/to/ca.pem` (**preferable**), or `HA_VERIFY_TLS=false` (turns verification off) |
| You target an IP, but the certificate is issued for a name | `HA_TLS_SERVER_HOSTNAME=my-domain.example` (the engine then verifies the chain **and** this name) |

### 2.3 Check

```powershell
$h = @{ Authorization = "Bearer $cle" }     # $cle: see 01_install.md, § 7.3
Invoke-RestMethod http://127.0.0.1:8000/api/ha/health -Headers $h
```

```bash
curl -s -H "Authorization: Bearer $CLE" http://127.0.0.1:8000/api/ha/health
```

The response contains `url`, `token_configured`, `verify_tls`, `ca_bundle`, `tls_server_hostname`, `reachable`,
`latency_ms`, `version`, `entity_count`, `domains` and `error`. Without a token, **observed**:
`"reachable": false` and `"error": "Token Home Assistant absent du .env du moteur (HASS_TOKEN/HA_TOKEN) — aucune requête
tentée."` (Home Assistant token missing from the engine's .env — no request attempted). The response with a real Home Assistant (version, number of
entities) was **not** seen: "—".

Other checks: the **Avancé** (Advanced) **→ Installation** screen ("Home Assistant" group: token, URL, reachability);
the **Utiliser** (Use) **→ Domotique & vocal** (Home automation and voice) screen ("test Home Assistant live", scope and history of commands);
`GET /api/ha/entities`, `GET /api/ha/state/{id}` and `POST /api/ha/control` (see `/docs`, the exact body is not
reproduced here).

---

## 3. Tell the engine what to control: `ha_commands.json`

Copy the template then **replace the `entity_id` values with yours**:

```powershell
Copy-Item ha_commands.json.example ha_commands.json
```

The file is a catalog of commands (15 in the example):

```json
{
  "commands": [
    {
      "phrases": ["allume le salon", "allume la lumiere du salon", "mets la lumière du salon"],
      "service": "light.turn_on",
      "entity_id": "light.living_room"
    },
    {
      "phrases": ["ouvre le volet du salon"],
      "service": "script.blind_action",
      "entity_id": "",
      "service_data": { "action": "open" }
    }
  ]
}
```

(The phrases are French: « allume le salon » = "turn on the living room", « allume la lumiere du salon » = "turn on the living room light",
« mets la lumière du salon » = "put on the living room light", « ouvre le volet du salon » = "open the living room shutter".)

| Field | Role |
|---|---|
| `phrases` | Recognized phrases. The example file says they are compared « après normalisation du routeur (minuscules, sans accents finaux) » ("after router normalization (lowercase, no final accents)"): like the example, write each phrase with and without accents. |
| `service` | Home Assistant service called (`domain.service`). The example uses seven: `light.turn_on` / `turn_off`, `climate.turn_on` / `turn_off`, `switch.turn_on` / `turn_off`, `script.blind_action`. |
| `entity_id` | Targeted entity (can be empty if the service does not need one). |
| `service_data` | Data added to the call (optional). |

Rules to know:

- **Restart** after a change: the file is read when the router is created (`core/router.py:200-213`).
- A phrase from the catalog is served **without a language model** (110 to 196 ms on the engine side, on the original installation). Otherwise the engine tries
  fuzzy matching, then, depending on the mode, a model: see
  [`carte_decision_vocale.md`](../../architecture/carte_decision_vocale.md) (French only).
- The engine **does not use** the Home Assistant "Expose" list: what it is allowed to control is decided here
  (`ihm-v2/src/lib/demoVocale.ts`, setting « Changer ce que l'IA a le droit de piloter » ("Change what the AI is allowed to control")).
- Without `ha_commands.json`, the code falls back to room tables built into it (tried: « allume la lumière du salon »
  is still recognized with `light.living_room`). This fallback is meant for the original installation: define your own
  catalog.

---

## 4. Try without triggering anything: the simulation

`POST /api/vocal/simulate` answers the question "what would the engine do with this phrase?" **without running anything**:
no call to Home Assistant, no call to a model, no write (`api/routes/vocal_simulation.py`,
`services/vocal_simulation.py`). It is the test to do before connecting a real Home Assistant.

Body: `texte` (the text, 1 to 500 characters) and `mode` (`ha` or `chat`, `ha` by default). **On Windows PowerShell 5.1, send the
body as UTF-8 bytes**, otherwise an accented letter makes the request fail (tried: "There was an error parsing the
body"):

```powershell
$corps = @{ texte = "allume la lumière du salon"; mode = "ha" } | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/api/vocal/simulate -Headers $h `
  -ContentType "application/json; charset=utf-8" -Body ([System.Text.Encoding]::UTF8.GetBytes($corps))
```

```bash
curl -s -X POST http://127.0.0.1:8000/api/vocal/simulate \
  -H "Authorization: Bearer $CLE" -H "Content-Type: application/json" \
  -d '{"texte":"allume la lumière du salon","mode":"ha"}'
```

(« allume la lumière du salon » = "turn on the living room light".)

Responses **observed** (example `ha_commands.json`, without Home Assistant):

| Text | Mode | `chemin` (path) | `action_prevue` (planned action) | `llm_requis` (model required) |
|---|---|---|---|---|
| allume la lumière du salon ("turn on the living room light") | `ha` | `ha_command` | `light.turn_on` on `light.living_room` | `false` |
| quelle heure est-il ("what time is it") | `chat` | `discussion_datetime` | none | `false` |
| raconte-moi une blague ("tell me a joke") | `chat` | `discussion_chat` | none | `true` |

Other fields: `explication` (one sentence), `executerait_une_action`, `mode`, `simulation: true`. When the path depends on
the state of the home (a state question), the path returned is `indetermine` (undetermined): the simulation does not read Home Assistant.

The interface offers the same thing in **Découvrir** (Discover) **→ Comprendre le moteur** (Understand the engine) **→ « Rejouer une vraie demande »** (Replay a real request) (replay
of exchanges already recorded).

---

## 5. The `moteur_ia_conversation` component (absent from this repository)

In the original installation, it is a custom Home Assistant component. The public repository does **not** contain its
code (search for the name in the whole tree: only two interface texts mention it,
`ihm-v2/src/lib/demoVocale.ts:51` and `glossaire.ts:157`). I therefore describe **no installation procedure**: it would be
made up.

What it does, according to these texts and the API code: it receives the recognized text from Home Assistant, passes it to
`POST /api/execute` with a **mode** (`ha` for "Domotique" (home automation), `chat` for "Discussion" (conversation)), a timeout of **30 s** (home automation) or
**35 s** (conversation), the header `Authorization: Bearer <MOTEUR_API_KEY>`, then returns the `response` field to Home Assistant, which
has it read by its speech synthesis. The engine-side contract is the API's (example body; it is not the exact request
of the original component, which I have not seen):

```json
{
  "user_prompt": "allume la lumière du salon",
  "source": { "type": "voice", "mode": "ha", "tts_enabled": true, "device_id": "my_satellite", "conversation_id": "..." }
}
```

| If you want to… | Lead |
|---|---|
| Write your own conversation agent | A Home Assistant component that calls `POST /api/execute` with the contract above. Specification: [`docs/API_REFERENCE.md`](../../API_REFERENCE.md). |
| Try without a component | The engine also exposes `POST /v1/chat/completions` (OpenAI-compatible). If your Home Assistant offers an OpenAI-compatible conversation agent with a custom address, that is a lead. **Not tried, not guaranteed.** |
| Test the journey without voice | Use the Home Assistant chat (Assist, keyboard input) with the agent connected, or the simulation of § 4. |

---

## 6. The demo journey: "Okay Nabu" → answer

The **Découvrir** (Discover) **→ Démo vocale & maison** (Voice and home demo) screen details the eight steps; here is the summary (`ihm-v2/src/lib/demoVocale.ts`):

| No. | Step | Where | What happens |
|---|---|---|---|
| 1 | "Okay Nabu" | display | The display recognizes the wake word itself: no sound is sent before. |
| 2 | Audio sent | display → Home Assistant | Voice at 16 kHz, one channel. The display must be connected to Home Assistant. |
| 3 | Speech recognition | Home Assistant | The Assist pipeline transcribes (STT). **Most fragile link.** |
| 4 | Conversation agent | Home Assistant | The component of § 5 sends the text to the engine with a mode. |
| 5 | Engine: routing | engine | Shortcuts in a fixed order (command, state, time and weather), then only a model. The Planner is never reached by voice. |
| 6 | Home or model | Home Assistant or provider | An order goes to the home; a question reads a state; a conversation queries the cascade. |
| 7 | Speech synthesis | Home Assistant | The answer becomes a voice. |
| 8 | Playback | display | The display plays the voice, shows the text, goes back to listening. |

Three paths on the engine side: **command** (« Allume la lumière du salon », "Turn on the living room light"), **question** (« Quelle heure est-il ? », "What time is it?", « Quelle
température fait-il dans le salon ? », "What is the temperature in the living room?"), **conversation** (« Raconte-moi une blague », "Tell me a joke"). The conversation is limited to three
sentences without formatting, keeps the last five turns, can **not** control the home (read-only tools) and
answers « Désolé, je n'ai pas su répondre. Peux-tu reformuler ? » ("Sorry, I could not answer. Can you rephrase?") if all models fail or after 20 seconds.

Durations measured **on the project's original installation**, as an order of magnitude (yours will differ): a
command takes 110 to 196 ms on the engine side; a conversation about 3 s at the median; the full pass through Home Assistant
goes from 3 to 9 s, most of it being speech recognition. Measurements not reproduced for this guide: "—".

> **Voice controls the real home.** A test "just to see" can open a shutter. Warn the people present, or
> first try phrases that control nothing (« Quelle heure est-il ? », "What time is it?").

---

## 7. The local fallback: `deploy/ha_local_fallback/`

Purpose: if the engine is off, Home Assistant alone serves a **vital core** of commands (lights, shutter, air conditioning).

| File | Role |
|---|---|
| `custom_sentences/fr/tab5_core.yaml` | Phrases → intents (**generated**) |
| `tab5_local_fallback_intents.yaml` | Intents → Home Assistant services (**generated**) |
| `tab5_engine_health_failover.yaml` | Probe of `/healthz` + automatic pipeline switch |

The two generated files come from `ha_commands.json`: never edit them by hand, **regenerate** them:

```bash
python3 -m scripts.gen_ha_local_fallback
```

(Not tried in this guide; command from the `README.md` of the folder. The files provided contain the example entities
`climate.living_room`, `light.bedroom`… and must be regenerated with yours.)

Deployment in Home Assistant (steps from the `README.md` of the folder):

1. Copy `tab5_core.yaml` to `<HA config>/custom_sentences/fr/`.
2. Merge `tab5_local_fallback_intents.yaml` into the `intent_script:` block of Home Assistant.
3. Put `tab5_engine_health_failover.yaml` in `packages/` and **adapt it** (§ below).
4. Restart Home Assistant or reload the YAML and the intents.

**To adapt in `tab5_engine_health_failover.yaml`**:

- the engine address: the file contains `http://${DECK_HOST:-192.168.1.x}:8000/healthz`. This form **is not valid
  in Home Assistant YAML** (it is a shell substitution pattern): replace it by hand with
  `http://<your engine address>:8000/healthz`;
- `PIPELINE_LOCAL`: the **exact** label of the "local pipeline" option of the satellite's `select`
  (`select.m5stack_tab5_home_assistant_hmi_assistant` in the file; adapt it to your satellite).

How it works: the REST probe queries `/healthz` every 30 s; engine `off` or `unavailable` for more than 60 s → the current
pipeline is remembered, then the satellite switches to the local agent; engine `on` for more than 30 s → the preferred pipeline
is restored. `/healthz` is public (no key): a 200 code is enough.

The file refers to a `docs/voice_assistant.md` that does not exist in this repository.

---

## 8. Voice troubleshooting

| Symptom | Probable cause | Where to look |
|---|---|---|
| Nothing after "Okay Nabu" | Listening off, display disconnected from Home Assistant, word misheard | State of the display in Home Assistant (connection sensor), display log |
| « Erreur » (Error), the microphone turns red | No text recognized (STT) | Pipeline debugging in Home Assistant; move the microphone closer |
| « Je n'ai pas compris la commande domotique. » (I did not understand the home automation command.) | Home automation mode: phrase absent from the catalog, with no recognized action verb | Simulation (§ 4), then add the phrase to `ha_commands.json` |
| « Impossible de contacter le moteur d'agents pour le moment. » (Unable to reach the agent engine for now.) | Engine off, unreachable, or the 30/35 s timeout exceeded | `GET /healthz`; engine log |
| 403 error on the Home Assistant side | The key stored in Home Assistant differs from `MOTEUR_API_KEY` | Copy the key again, reload the integration |
| « Désolé, je n'ai pas su répondre. » (Sorry, I could not answer.) | Conversation: all models failed, or 20 s exceeded | Cascade and quotas: [05 Operate, § 4](05_operate.md) |
| Answer displayed but no sound | Volume, speaker, speech synthesis in error | Volume of the display; pipeline debugging |
| A question triggered a device | A misrecognized text looks like an order | Simulation of the exact text (§ 4); "a question is not an order" safeguard |

These symptoms come from the demo screen of the interface (`ihm-v2/src/lib/demoVocale.ts`) and were not reproduced with a
real Home Assistant.

Log of voice exchanges: `GET /api/vocal/audit`, `/api/vocal/stats`, `/api/vocal/conversations` (text, path taken,
latency). `GET /api/vocal/config` returns the model of the home automation agent and the presence (not the value) of secrets; observed on
an installation without a key: all `false`.
