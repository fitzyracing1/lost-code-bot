"""One-off: seed queue.json with the 12 hand-verified findings from the 2026-10-24 audit."""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from lostcode import queue

D, O, L, N, V, H, T = ("langgenius/dify@fecf1a20ce45ff1ff8cca19653f0000cf891415c", "anomalyco/opencode@20a6cc72917586eb5314be075f96dbb21d16c480",
    "langflow-ai/langflow@aeb10d96bb2ea797696613af931e91ddf51f3320", "vercel/next.js@a831acd90b7bb80cd284d3e7727ab8e1a0756991",
    "microsoft/vscode@43f10d53daac979d803a3275d8596c283321266e", "NousResearch/hermes-agent@99fcd6944360925943688d30c39ec9a490ccdeb1",
    "tensorflow/tensorflow@14665b7f0b60bb47986eb566d8e51fc113c19a6a")
F = "flutter/flutter@2541e508405f5d9728286d6ed3307163a5406e08"
rows = [  # (repo@head, kind, file, line, ref, path, deleted_in, recovery, live_alt, conf, note)
 (D, "ci", ".github/workflows/cli-e2e.yml", 146, "test/e2e/suites/", "cli/test/e2e/suites", "a4524c215f299989e6488b8b3dc2c9c1b199489e", "96abb8ea220b5d17a2ef2958d94ffbf60d1cf950", None, 0.97, "whole suites tree deleted in difyctl v2 (#42608); workflow lines 146,192,259,311,376-383"),
 (O, "ci", ".github/workflows/publish.yml", 99, "./packages/opencode/script/build.ts", "packages/opencode/script/build.ts", "44b6938b2ad2895bdd89d5d5cf753fb01ca8e3dd", "fc7e4cf93e660ad5ddabb0c48ecc99e934ebfe95", "packages/cli/script/build.ts", 0.95, "packages/opencode removed; live equivalent packages/cli/script/build.ts"),
 (L, "ci", ".github/workflows/smoke-tests.yml", 55, "src/backend/tests/unit/components/languagemodels/test_openai_model.py", "src/backend/tests/unit/components/languagemodels/test_openai_model.py", "a42af78fe670e016d3698d76c2ad532a44dd2d00", "b565a14bdce5785f8e97b75c016247d6903e4e68", "src/bundles/openai/tests/test_openai_model.py", 0.97, "renamed in #13805"),
 (L, "ci", ".github/workflows/smoke-tests.yml", 56, "src/backend/tests/unit/components/agents/test_agent_component.py", "src/backend/tests/unit/components/agents/test_agent_component.py", "b1dc065feecc9f035f1bfd8197e0b178b8c57fdd", "11c0af4e35c921af13efeebff0be058f334b4f5d", "src/backend/tests/unit/components/models_and_agents/test_agent_component.py", 0.97, "renamed in #10180"),
 (L, "script", "src/frontend/package.json", 175, "scripts/lint-changed.sh", "src/frontend/scripts/lint-changed.sh", None, None, None, 0.8, "never committed; scripts lint:changed and lint:types:changed (L175, L178)"),
 (N, "doc", "packages/next/src/next-devtools/README.md", 81, "ui/styles/dark-theme.tsx", "packages/next/src/next-devtools/ui/styles/dark-theme.tsx", "13f413bb284e9e29729d051f557a01338a78c4cf", "49371dc364eecca41e52dfc5f6bf1b6cda3ed83d", "packages/next/src/next-devtools/dev-overlay/styles/dark-theme.css", 0.85, "moved in 0d7b49b, converted to CSS in #82778"),
 (V, "doc", "extensions/copilot/CONTRIBUTING.md", 273, "src/extension/prompts/node/agent/agentInstructions.tsx", "extensions/copilot/src/extension/prompts/node/agent/agentInstructions.tsx", None, None, "extensions/copilot/src/extension/prompts/node/agent/defaultAgentInstructions.tsx", 0.8, "never in vscode history; current file defaultAgentInstructions.tsx"),
 (N, "script", "examples/with-edgedb/package.json", 7, "dbschema/seed.ts", "examples/with-edgedb/dbschema/seed.ts", None, None, None, 0.7, "never committed (npm run seed)"),
 (F, "doc", "packages/flutter_tools/lib/src/android/README.md", 22, "FlutterExtention.kt", "packages/flutter_tools/gradle/src/main/kotlin/FlutterExtention.kt", None, None, "packages/flutter_tools/gradle/src/main/kotlin/FlutterExtension.kt", 0.9, "typo; real file FlutterExtension.kt"),
 (H, "doc", "scripts/build/README.md", 5, "docs/shared-bundle-builds.md", "docs/shared-bundle-builds.md", None, None, None, 0.7, "never committed"),
 (V, "doc", "build/agentHost/README.md", 22, "../../License.txt", "License.txt", None, None, "LICENSE.txt", 0.92, "case mismatch; GitHub paths are case-sensitive"),
 (T, "doc", "tensorflow/python/debug/examples/README.md", 6, "docs_src/guide/debugger.md", "tensorflow/docs_src/guide/debugger.md", "6c2bf6576321ad53ff1eb6d66b6efae2c93ef4e0", "abc8452394aeeecc1f3fef27f7098a5924bdd0e9", None, 0.75, "docs_src moved out of repo in 2018"),
]
q = queue.load("findings/queue.json")
fs = []
for rh, kind, file, line, ref, path, dele, rec, alt, conf, note in rows:
    repo, head = rh.split("@")
    ev = [f"https://github.com/{repo}/blob/{head}/{file}#L{line}", note]
    if dele:
        ev.append(f"removed in https://github.com/{repo}/commit/{dele}")
    fs.append(dict(repo=repo, kind=kind, file=file, line=line, ref=ref, path=path, context="seed: 2026-10-24 audit",
                   evidence=ev, recovery_sha=rec, deleted_in=dele, live_alternative=alt, confidence=conf, head_sha=head))
queue.merge(q, fs)
for it in q["items"].values():
    if it["state"] == "new" and it["finding"].get("context") == "seed: 2026-10-24 audit":
        queue.set_state(q, it["id"], "verified", "joshua-audit", "hand-checked in the 2026-10-24 audit")
queue.save(q, "findings/queue.json")
print(len(q["items"]))
