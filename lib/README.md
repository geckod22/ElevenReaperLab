# Bundled JSON library

`json.lua` is an unmodified copy of [rxi/json.lua](https://github.com/rxi/json.lua), version 0.1.2.

- Upstream revision: `dbf4b2dd2eb7c23be2773c89eb059dadd6436f94`.
- Source: https://github.com/rxi/json.lua/blob/dbf4b2dd2eb7c23be2773c89eb059dadd6436f94/json.lua
- License: MIT, reproduced in `json.LICENSE` and in the source header.

Keep the copyright notice and license when distributing the library. No runtime download or separate installation is required.

The shared application reader in `erl_json.lua` adds structural validation around the decoder for configuration and model catalogs because upstream converts JSON null to Lua nil and does not retain the distinction between empty JSON arrays and objects.
