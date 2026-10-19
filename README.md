# SwiftBar plugins

A personal collection of macOS menu bar plugins. Each plugin keeps its implementation, configuration, tests, and documentation in its own directory.

## Plugins

| Plugin | Purpose |
|---|---|
| [Local LLM Server](plugins/local-llm-server/README.md) | Monitor Strata and Splash, select models, and start or stop servers |

## Setup

Install [SwiftBar](https://github.com/swiftbar/SwiftBar). With Homebrew:

```fish
brew install --cask swiftbar
```

1. Follow each plugin's README to install dependencies and create your local configuration.
2. In SwiftBar, choose `Change Plugin Folder…` and select **`enabled/`** at the repository root.

Keep only relative symlinks to plugin entry scripts in `enabled/`. Git tracks these links, so you do not need to create them after cloning. You can move the repository as long as you preserve its internal directory layout.

SwiftBar searches the selected folder recursively. Do not select `plugins/` or the repository root: they contain helper code and documentation. Before changing an existing SwiftBar plugin folder, check where your other plugins live.

## Directory layout

```text
swiftbar-plugins/
├── README.md
├── enabled/
│   └── local-llm-server.5s.sh → ../plugins/local-llm-server/local-llm-server.5s.sh
└── plugins/
    └── local-llm-server/
        ├── README.md
        ├── AGENTS.md
        ├── local-llm-server.5s.sh
        ├── scripts/
        ├── config/
        │   ├── local-llm-server.example.json
        │   └── local-llm-server.json
        ├── tests/
        └── docs/
```

Git ignores local configuration files at `plugins/*/config/*.json` and tracks only `*.example.json`. See each plugin's README for model, log, and server-state storage locations.

The links in `enabled/` define the repository's shared set of enabled plugins. Adding or removing a link is a Git change. Removing a server-management plugin's link may leave its server running.

## Adding or changing plugins

Place the implementation, README, AGENTS.md, and any configuration examples or tests in `plugins/<name>/`. Add a relative symlink from `enabled/` to the entry script. Each plugin defines its own language, dependencies, and validation steps.

Follow the target plugin's `AGENTS.md` when developing. This repository has no root-level AGENTS.md or shared plugin implementation framework. Write documentation and code comments in English.
