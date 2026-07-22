"""Prompt management — prompts live as versioned markdown files, not string literals.

Layout (one folder per agent, git is the version history):

    src/agent_app/prompts/
    └── assistant/
        ├── system.md            ← the static system prompt (cached prefix)
        └── <other>.md           ← optional extra variants/fragments

House rules (from genie-hv-eugenia's ``instructions.py``):
- The prompt file is **static** — it becomes the cached system prefix. Anything
  volatile (date, user context) goes through ``context.py`` as message-0 instead.
- Review prompt changes like code changes: they ship via git PRs, and a redeploy
  rolls them out (which also naturally re-warms the prompt cache).
- Per-model variants (optional, eugenia Issue #111 pattern): put an override at
  ``prompts/<agent>/variants/<model-slug>/system.md`` and pass ``model_slug=``;
  the loader falls back to the default file when no variant exists.
"""

from __future__ import annotations

from pathlib import Path

PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"


def load_prompt(
    prompt_name: str,
    variant: str = "system",
    model_slug: str | None = None,
    **variables: str,
) -> str:
    """Load ``prompts/<prompt_name>/<variant>.md`` and substitute ``{placeholders}``.

    Substitution is literal ``{key}`` replacement (not str.format), so prompts may
    freely contain braces/JSON without escaping.
    """
    candidates = []
    if model_slug:
        candidates.append(PROMPTS_DIR / prompt_name / "variants" / model_slug / f"{variant}.md")
    candidates.append(PROMPTS_DIR / prompt_name / f"{variant}.md")

    path = next((p for p in candidates if p.exists()), None)
    if path is None:
        raise FileNotFoundError(
            f"Prompt not found for agent '{prompt_name}' (variant '{variant}'). "
            f"Create {candidates[-1].relative_to(PROMPTS_DIR.parent.parent.parent)}"
        )

    text = path.read_text(encoding="utf-8")
    for key, value in variables.items():
        text = text.replace("{" + key + "}", str(value))
    return text
