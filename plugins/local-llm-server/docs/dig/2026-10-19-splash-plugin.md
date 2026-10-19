# dig log: Managing Strata / Splash from SwiftBar

## Purpose, assumptions, and constraints
- Create a general-purpose `swiftbar-plugins` repository that can also contain non-LLM plugins. The scope of this work is status display, start/stop, and model selection for Strata and Splash.
- Keep this log's filename for continuity of the discussion. Q1–Q12 were worked out with Splash in mind; their applicability to Strata-specific configuration and download behavior has not been verified.
- This log began from the conversation before the fork and the handoff instructions in the new workspace.
- Confirmed on 2026-10-19: the only tracked repository file was `README.md`, containing just a heading. The working tree was clean. There were no additional `AGENTS.md` / `CLAUDE.md` files in the repository or the parent directories checked.
- Follow the global instructions in this order: dig → user approval → plan → user approval → implementation. Neither the design nor the implementation plan has been approved yet.
- The environment is M6 with 32GB, macOS 27.0.1, and Homebrew Splash 1.3.2. The user wants to leave memory for other applications and would like total usage to be around 12–15GB.
- An existing Splash instance is running in the parent Herdr pane. Treat it as an external process; do not stop it or take ownership without permission. Do not store model names or personal launch settings in the repository.
- A short test with the above settings produced approximately 25 tok/s for Japanese and 68–69 tok/s for code. Metal allocation was approximately 9.4GiB, with a startup peak of approximately 10.4GiB. Whole-process memory usage has not been checked, and the system memory pressure assessment was warning. Evaluation of long-form output and quality is incomplete.

## Decisions
- Leave Strata Menubar unchanged and retain it as is.
  - Source: the user's statements before the fork and the handoff instructions.
- Do not make the new repository LLM-specific; allow various SwiftBar plugins to be added.
  - Source (translated): “I might add various things in the future, unrelated to LLMs.”
- Allow both Strata and Splash to be controlled from SwiftBar. Do not change Strata Menubar's code.
  - Source: the request before dig and the correction to Q13 (translated): “Make it possible to control Strata from SwiftBar too.”
- Assume Strata Menubar is closed when using SwiftBar. Do not assume coordination or configuration synchronization for simultaneous use.
  - Source (translated): “I think Strata Menubar will be closed when I use SwiftBar.”
  - Exiting the application and stopping the server are separate actions; this is not permission to automatically stop or take ownership of an externally launched server.
- Select models from candidates registered in configuration (Q1 option A). However, the repository does not need to know specific models and must not embed a model list.
  - Source: the answer to Q1 approved registered-model selection and specified that particular models must not be embedded in the repository.
  - The location and schema for storing the model list remain undecided.
- Edit the local configuration file directly to maintain the model list (Q2 option A). Provide a menu action to open the configuration file.
  - Source: the answer to Q2 (translated): “A, but doesn't the JSON also need to specify where models are downloaded?”
- Checked the official Splash specification: launch by model ID; weights and drafts are managed in the Hugging Face cache. The default location is `~/.cache/huggingface/hub`. `HF_HUB_CACHE` can change the download cache, but does not move existing downloads. Splash's data directory retains links to the cache and other data.
  - Source: https://github.com/incoai/splash/blob/main/DEVELOPMENT.md#model-cache
  - Design implication: the plugin does not need to manage each model's GGUF file path. Cache location configuration was agreed in Q3.
- Allow the model download location to be specified with the optional shared setting `modelCacheDir`. When specified, pass it to Splash as `HF_HUB_CACHE`; when omitted, do not override the location. Do not move existing models.
  - Source: the answer to Q3 (translated): “a”.
- Specify per-model launch settings in `args` as an array of Splash CLI arguments. Pass arguments without evaluating them as a shell string. Prohibit duplicate specification of plugin-managed items such as model ID, host, and port.
  - Source: the answer to Q4 (translated): “a”.
- When selecting a different model while a plugin-managed server is running, show a confirmation dialog and switch by stopping and restarting after approval. Explain that requests currently generating output will be interrupted. Do not stop or switch externally launched servers.
  - Source: the answer to Q5 (translated): “b”.
- Use `--offline` for normal Start and startup after model switching. For downloads/updates, provide a separate “Download/update and start” menu action that launches online after confirmation and delegates to Splash. Do not implement a plugin-specific downloader.
  - Source: the answer to Q6 (translated): “b”.
- Splash continues running after SwiftBar exits. After SwiftBar restarts, resume display and control of the same server that the plugin itself launched. Distinguish this from taking ownership of external servers; stopping requires an explicit Stop.
  - Source: the answer to Q7 (translated): “a”.
- Launch through the plugin only in response to explicit actions. Do not start automatically after Mac restart/login or restart automatically after the Splash server process exits. Do not change Splash's own internal engine recovery feature.
  - Source: the answer to Q8 (translated): “a”.
- Keep the persistent menu bar display icon-only. Distinguish running, stopped, starting, and error states with shapes or marks as well, rather than relying solely on color. Show details such as model names inside the menu.
  - Source: the answer to Q9 (translated): “a”.
- In the open menu, show status, model name, endpoint, Start/Stop, model selection, download/update, and links to chat/logs/configuration, plus generation speed, memory usage, and context limit. Download/update applies only to Splash.
  - Source: the answer to Q10 (translated): “b”, and Q17.
- For memory display, seek a value equivalent to the “Memory” column in Mac Activity Monitor, not Metal allocation. Do not substitute RSS or system-wide RAM usage. Consider OS-provided physical footprint; verify the collection method, permissions, and units, and compare measurements against Activity Monitor. Neither the collection method nor agreement has been verified yet.
  - Source: the answer to Q19 (translated): “I want it to show the same memory usage as when I look in Mac Activity Monitor.”
- Manage at most one Splash server at a time, using a shared port across all models. Switch through model selection; do not support simultaneous launches of multiple models. If an external Splash instance occupies the configured port, display it as externally running and do not start, stop, or switch it.
  - Source: the answer to Q11 (translated): “a”.
- Confirm Stop every time, regardless of processing state.
  - Reason: keep behavior simple by avoiding conditional branches based on whether processing is underway.
  - Source: the answer to Q12 (translated): “A should be fine, shouldn't it? Keep it simple.”
- Combine Strata and Splash into one menu bar icon and menu, with separate status, start/stop, and model selection for each. Structure the repository so non-LLM plugins can be added independently.
  - Source: the answer to Q14 (translated): “a”.
- Allow Strata and Splash to start and stop independently and run simultaneously. Do not impose startup restrictions, automatic stops, or switching based on the other engine running. The user decides when to start or stop them in light of memory usage.
  - Reason: keep things simple without adding coordination between engines.
  - Source: the answer to Q15 (translated): “b. Don't make it unnecessarily complicated. The user starts and stops them at their own risk.”
- Register Strata models using a display name and the path to an existing configuration JSON file. Manage model/engine settings such as expert cache and context in that referenced JSON, without SwiftBar-side overrides. Do not synchronize with Strata Menubar's saved settings.
  - Source: the answer to Q16 (translated): “a”.
  - Separately verify the launch contract for items needed for the plugin's connection management, such as the target host and port.
- For Strata, handle only startup, stopping, and selection of prepared models. Model downloads and initial preparation belong to Strata setup or Strata Menubar and must not run from SwiftBar. Retain Splash's “Download/update and start” action.
  - Source: the answer to Q17 (translated): “a”.
- Apply the shared server-operation rules to Strata as well. One server per engine; confirm Stop every time; confirm running-model changes before stopping and restarting. Continue running after SwiftBar exits and resume management of the same self-launched server after restart. Do not start automatically at login or restart automatically after abnormal termination. Display external servers only; do not stop or take ownership of them. Allow Strata and Splash to run simultaneously.
  - Source: the answer to Q18 (translated): “a”.
- Display memory for the inference engine alone, without adding the API server or other related processes. Compare against the “Memory” column of the corresponding engine process in Activity Monitor.
  - Source: the answer to Q20 (translated): “b is fine for that.”

## Out of scope
- Changes to Strata Menubar.
- Stopping or taking ownership of an external running Splash instance without permission.
- Implementation before approval of the design and plan.

## Undecided / pending
- Q13 was not approved because its scope was incorrect. Use Q19, covering both engines, for final design confirmation. The following supplementary proposals await approval.
  - Keep settings for `strata` and `splash` in one file, `~/.config/swiftbar-plugins/llm.json`. Store selected-model and ownership information in a separate user location, not in configuration.
  - Restrict both endpoints to `127.0.0.1`. Make ports configurable per engine, defaulting to Strata 8080 and Splash 8000. Treat identical ports as a configuration error.
  - Refresh status every 5 seconds. For Splash speed, use the output-token increment divided by elapsed time over the latest observation interval; for Strata, use the moving-window generation speed returned by the API, distinct from a cumulative average. Show `—` when not generating or when no valid observation exists.
  - Show `—` for unavailable monitoring values. Obtain memory from the OS and align it with Activity Monitor's metric. Target the inference engine alone, as agreed in Q20.
  - A normal start of an undownloaded model shows an error and guidance to the download action; do not automatically switch to online startup.
  - Confirmation for model switching or download/update also covers any necessary stopping and interruption. Cancellation changes neither selection nor running state. Do not launch the next server if stopping fails.
  - Set `plugins/` as SwiftBar's plugin folder. Place helper code and configuration examples outside it. Include only a configuration example with empty model lists in the repository.
- Technical matters to verify and specify in the plan:
  - Stable model-entry identification, configuration schema, and input validation. Prevent conflicts with plugin-managed CLI arguments (including `--offline`) and environment variables.
  - A safe way to track ownership of plugin-launched processes. Do not stop unrelated processes because of PID reuse or stale state files. Prevent duplicate actions.
  - Display and safe rejection when a port is occupied by something other than external Splash, when unresponsive, or when stopping fails.
  - Script runtime, server management independent of SwiftBar refresh/click actions, confirmation-dialog implementation, and log/state locations.
  - SwiftBar installation procedure. It was not found at `/Applications/SwiftBar.app`; check installation status, including other locations.
- Investigation of Strata startup:
  - Findings: Strata Menubar's `Strata/LaunchPlan.swift` launches `.venv/bin/python serve/server.py --engine strata --config <JSON> --host 127.0.0.1 --port <port>`. `RunConfig.swift` copies the original Strata configuration and overrides args, env, logs, and other items. Original model settings and Menubar's saved settings are not identical, so using the original JSON directly does not automatically inherit Menubar settings.
  - Only read-only inspection was performed; Strata Menubar was not modified.
- Q18 established agreement on shared operation rules for both engines. In Q19, confirm whether to finalize the design, including the display and configuration-file structure above, and proceed to planning.
- Checked Strata monitoring values in local source (no real server was started): `metrics()` in `serve/server.py` returns `live.tok_s` (moving-window speed) and `engine.max_context` at `/metrics`. `/health` also returns service, loaded, model, and max_context. `ram_used` in `serve/telemetry.py` is system-wide and cannot be compared to Splash's Metal allocation. No equivalent Metal usage API was found within the inspected scope.
- Q15 confirmed that both engines may run simultaneously. Assume one server within each engine and work out Strata configuration and model switching.
- Q21: finalize the design using Q19's supplementary proposal with Q20's engine-only memory display incorporated, and proceed to planning? Awaiting approval.
- The implementation plan has not been approved yet.

## Withdrawn / superseded
- Q10's Metal memory display was changed to Activity Monitor-equivalent memory display by the answer to Q19. The absence of equivalent Metal metrics in Strata is not a reason to skip investigating OS-side collection.
- Interpreting “retain Strata Menubar” as “only Splash will be handled by SwiftBar in this work” was incorrect. Follow the correction to Q13 and cover both engines.
